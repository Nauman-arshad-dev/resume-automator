r"""
Resume Tailor — tailor a master resume to a job description in one Claude call.

SETUP (VS Code terminal, Windows PowerShell)
---------------------------------------------
1. Create and activate a virtual environment:
       python -m venv .venv
       .\.venv\Scripts\Activate.ps1
   (macOS/Linux: python3 -m venv .venv && source .venv/bin/activate)

2. Install dependencies:
       pip install -r requirements.txt

3. Set your API key (get one at https://console.anthropic.com/settings/keys):
       Current session only:   $env:ANTHROPIC_API_KEY = "sk-ant-..."
       Permanently (new terminals):  setx ANTHROPIC_API_KEY "sk-ant-..."
   (macOS/Linux: export ANTHROPIC_API_KEY="sk-ant-...")

USAGE
-----
    python main.py
    python main.py --resume master_resume.json --job job_feedback.txt
    python main.py --resume resume.md --label "Acme Data Engineer"
    python main.py --out-dir "G:\My Drive\Resumes"     # Google Drive for desktop folder

Outputs (in --out-dir, default ./output):
    <name>_<label>_<date>.docx   tailored resume, ready to upload / open in Google Docs
    <name>_<label>_<date>.json   same content as JSON (can be reused as a master resume)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import anthropic
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

# Sonnet is a good cost/quality balance for rewriting bullets. Override with
# the RESUME_MODEL env var (e.g. "claude-haiku-5-5" for cheapest, "claude-opus-5-5" for best).
MODEL = os.environ.get("RESUME_MODEL", "claude-sonnet-5-5")
# Lower effort = fewer thinking tokens. "low" is cheapest; "high" is most careful.
EFFORT = os.environ.get("RESUME_EFFORT", "medium")
MAX_TOKENS = 16000

# --------------------------------------------------------------------------- #
# Resume schema — Claude's reply is constrained to exactly this JSON shape.
# --------------------------------------------------------------------------- #

def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}

RESUME_SCHEMA = _obj({
    "name": _STR,
    "contact": _STR_LIST,  # e.g. ["email", "phone", "city", "linkedin url"]
    "summary": _STR,
    "skills": _STR_LIST,
    "experience": {"type": "array", "items": _obj({
        "title": _STR,
        "company": _STR,
        "location": _STR,
        "dates": _STR,
        "bullets": _STR_LIST,
    })},
    "projects": {"type": "array", "items": _obj({
        "name": _STR,
        "bullets": _STR_LIST,
    })},
    "education": {"type": "array", "items": _obj({
        "degree": _STR,
        "school": _STR,
        "dates": _STR,
        "details": _STR_LIST,
    })},
    "certifications": _STR_LIST,
})

RESPONSE_SCHEMA = _obj({
    "resume": RESUME_SCHEMA,
    "keywords_added": {"type": "array", "items": _obj({
        "keyword": _STR,
        "where": _STR,
    })},
    "keywords_not_added": {"type": "array", "items": _obj({
        "keyword": _STR,
        "reason": _STR,
    })},
})

SYSTEM_PROMPT = """You are an expert resume writer who tailors resumes for ATS systems and human recruiters.

You receive a candidate's master resume and a job description and/or ATS feedback listing missing keywords. Return a tailored version of the resume.

Rules:
- Truthfulness comes first. Only use facts present in the master resume. Never invent employers, titles, dates, degrees, certifications, metrics, tools, or responsibilities.
- Add a missing keyword only where the existing experience genuinely supports it (for example, rephrasing "built data pipelines in Python" to mention "ETL" is fine; adding "Kubernetes" to a role that never mentions containers is not). Weave keywords into existing bullets, the summary, or the skills list in natural language; do not keyword-stuff.
- If a keyword cannot be added honestly, leave it out and list it under keywords_not_added with a short reason, so the candidate can decide whether it truly applies.
- Keep every role, project, and education entry, in the original order, with the original titles, companies, and dates. You may reorder bullets within a role to put the most relevant first, and reorder the skills list.
- Keep bullets concise (ideally one line, at most two), starting with strong action verbs. Preserve existing numbers exactly.
- Rewrite the summary to target this role in 2-3 sentences, using only facts from the resume.
- Use empty strings or empty lists for fields the master resume does not have.
- In keywords_added, list each keyword you integrated and where (e.g. "Skills", "Acme Corp bullet 2")."""


# --------------------------------------------------------------------------- #
# Claude call
# --------------------------------------------------------------------------- #

class TailorError(Exception):
    """Claude finished without a usable resume (refusal or truncated output)."""


def tailor_resume(
    master_resume: str,
    job_feedback: str,
    api_key: str | None = None,
    model: str = MODEL,
    effort: str = EFFORT,
) -> tuple[dict, str]:
    """Returns (result, usage summary). api_key=None reads ANTHROPIC_API_KEY."""
    client = anthropic.Anthropic(api_key=api_key or None)

    user_content = (
        f"<master_resume>\n{master_resume}\n</master_resume>\n\n"
        f"<job_description_and_feedback>\n{job_feedback}\n</job_description_and_feedback>\n\n"
        "Tailor the resume for this job following the rules."
    )

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        },
        # If a safety classifier declines the request, Anthropic re-runs it on a
        # recommended fallback model instead of returning a refusal.
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )

    if response.stop_reason == "refusal":
        raise TailorError("Claude declined this request. Check the input files and try again.")
    if response.stop_reason == "max_tokens":
        raise TailorError(f"Response was cut off at {MAX_TOKENS} tokens. Increase MAX_TOKENS in main.py.")

    text = "".join(block.text for block in response.content if block.type == "text")
    u = response.usage
    usage = f"Model: {response.model} | tokens in: {u.input_tokens}, out: {u.output_tokens}"
    return json.loads(text), usage


# --------------------------------------------------------------------------- #
# .docx rendering (single column, plain styles — ATS friendly)
# --------------------------------------------------------------------------- #

def _bottom_border(paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, val in {"w:val": "single", "w:sz": "6", "w:space": "1", "w:color": "808080"}.items():
        bottom.set(qn(key), val)
    borders.append(bottom)
    p_pr.append(borders)


def _section(doc: Document, title: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(3)
    run = p.add_run(title.upper())
    run.bold = True
    run.font.size = Pt(11.5)
    _bottom_border(p)


def _entry_header(doc: Document, left: str, right: str, sub: str = "") -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(0)
    p.add_run(left).bold = True
    if right:
        p.add_run(f"  |  {right}")
    if sub:
        s = doc.add_paragraph()
        s.paragraph_format.space_after = Pt(1)
        s.add_run(sub).italic = True


def _bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        p = doc.add_paragraph(item, style="List Bullet")
        p.paragraph_format.space_after = Pt(1)


def build_docx(resume: dict, path) -> None:
    """path may be a file path or a writable binary stream (e.g. io.BytesIO)."""
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Pt(40)
        section.left_margin = section.right_margin = Pt(50)

    name = doc.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name.paragraph_format.space_after = Pt(0)
    run = name.add_run(resume["name"])
    run.bold = True
    run.font.size = Pt(20)

    if resume["contact"]:
        contact = doc.add_paragraph(" | ".join(resume["contact"]))
        contact.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if resume["summary"]:
        _section(doc, "Summary")
        doc.add_paragraph(resume["summary"])

    if resume["skills"]:
        _section(doc, "Skills")
        doc.add_paragraph(", ".join(resume["skills"]))

    if resume["experience"]:
        _section(doc, "Experience")
        for job in resume["experience"]:
            company = ", ".join(x for x in (job["company"], job["location"]) if x)
            _entry_header(doc, job["title"], job["dates"], company)
            _bullets(doc, job["bullets"])

    if resume["projects"]:
        _section(doc, "Projects")
        for proj in resume["projects"]:
            _entry_header(doc, proj["name"], "")
            _bullets(doc, proj["bullets"])

    if resume["education"]:
        _section(doc, "Education")
        for edu in resume["education"]:
            _entry_header(doc, edu["degree"], edu["dates"], edu["school"])
            _bullets(doc, edu["details"])

    if resume["certifications"]:
        _section(doc, "Certifications")
        _bullets(doc, resume["certifications"])

    doc.save(path)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def docx_to_text(source) -> str:
    """Plain text of an existing .docx resume (path or binary stream)."""
    doc = Document(source)
    lines = [p.text for p in doc.paragraphs]
    for table in doc.tables:  # many resume templates lay out content in tables
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(line for line in lines if line.strip())


def prepare_master(text: str, suffix: str) -> str:
    if suffix.lower() == ".json":
        # Re-serialise compactly: same content, fewer input tokens.
        return json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
    return text.strip()


def _read_master(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return docx_to_text(path)
    return prepare_master(path.read_text(encoding="utf-8"), path.suffix)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40] or "resume"


def output_stem(resume: dict, label: str = "") -> str:
    parts = (_slug(resume["name"]), _slug(label) if label else "", date.today().isoformat())
    return "_".join(x for x in parts if x)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tailor a resume to a job description with Claude.")
    parser.add_argument("--resume", default="master_resume.json", help="Master resume (.json, .docx, .md or .txt)")
    parser.add_argument("--job", default="job_feedback.txt", help="Job description and/or ATS feedback")
    parser.add_argument("--out-dir", default="output", help="Output folder (e.g. a Google Drive for desktop folder)")
    parser.add_argument("--label", default="", help="Label for the output file name, e.g. the company")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit('ANTHROPIC_API_KEY is not set. In PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."')

    resume_path, job_path = Path(args.resume), Path(args.job)
    for p in (resume_path, job_path):
        if not p.is_file():
            sys.exit(f"File not found: {p}")

    master = _read_master(resume_path)
    job = job_path.read_text(encoding="utf-8").strip()
    if not job:
        sys.exit(f"{job_path} is empty. Paste the job description and/or ATS feedback into it.")

    print(f"Tailoring {resume_path.name} against {job_path.name} ...")
    try:
        result, usage = tailor_resume(master, job)
        print(usage)
    except TailorError as e:
        sys.exit(str(e))
    except anthropic.AuthenticationError:
        sys.exit("Invalid API key. Check ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        sys.exit("Rate limited by the API. Wait a minute and try again.")
    except anthropic.APIStatusError as e:
        sys.exit(f"API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the Anthropic API. Check your internet connection.")

    resume = result["resume"]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(resume, args.label)
    docx_path, json_path = out_dir / f"{stem}.docx", out_dir / f"{stem}.json"

    build_docx(resume, docx_path)
    json_path.write_text(json.dumps(resume, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\nKeywords added:")
    for k in result["keywords_added"] or [{"keyword": "(none)", "where": ""}]:
        print(f"  + {k['keyword']}  ->  {k['where']}")
    if result["keywords_not_added"]:
        print("\nNot added (no supporting experience — add manually only if true):")
        for k in result["keywords_not_added"]:
            print(f"  - {k['keyword']}: {k['reason']}")

    print(f"\nSaved: {docx_path}\n       {json_path}")


if __name__ == "__main__":
    main()
