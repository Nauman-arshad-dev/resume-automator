r"""
Resume Tailor — tailor a master resume to a job description in one Claude call.

This file holds the core logic and a command-line interface. For the web UI
(including the free copy-paste mode that uses a Claude.ai subscription), run:
    streamlit run app.py

CLI SETUP (VS Code terminal, Windows PowerShell)
------------------------------------------------
1. Create and activate a virtual environment:
       python -m venv .venv
       .\.venv\Scripts\Activate.ps1
   (macOS/Linux: python3 -m venv .venv && source .venv/bin/activate)

2. Install dependencies:
       pip install -r requirements.txt

3. Set your API key (needs API credits: https://console.anthropic.com/settings/billing):
       Current session only:   $env:ANTHROPIC_API_KEY = "sk-ant-..."
       Permanently (new terminals):  setx ANTHROPIC_API_KEY "sk-ant-..."
   (macOS/Linux: export ANTHROPIC_API_KEY="sk-ant-...")

CLI USAGE
---------
    python main.py
    python main.py --resume master_resume.json --job job_feedback.txt
    python main.py --resume resume.docx --label "Acme Data Engineer" --template Modern
    python main.py --out-dir "G:\My Drive\Resumes"     # Google Drive for desktop folder

Outputs (in --out-dir, default ./output): <name>_<label>_<date>.pdf / .docx / .json
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

import render

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


class TailorError(Exception):
    """Claude finished without a usable resume (refusal, truncated or unreadable output)."""


def _user_content(master_resume: str, job_feedback: str) -> str:
    return (
        f"<master_resume>\n{master_resume}\n</master_resume>\n\n"
        f"<job_description_and_feedback>\n{job_feedback}\n</job_description_and_feedback>\n\n"
        "Tailor the resume for this job following the rules."
    )


def normalize(value, schema: dict):
    """Coerce loosely-shaped JSON (e.g. pasted from claude.ai) into the exact schema shape."""
    kind = schema["type"]
    if kind == "object":
        value = value if isinstance(value, dict) else {}
        return {k: normalize(value.get(k), s) for k, s in schema["properties"].items()}
    if kind == "array":
        if isinstance(value, str):
            value = [value] if value.strip() else []
        return [normalize(v, schema["items"]) for v in value] if isinstance(value, list) else []
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


# --------------------------------------------------------------------------- #
# Mode 1: Anthropic API (automatic, needs API credits)
# --------------------------------------------------------------------------- #

def tailor_resume(
    master_resume: str,
    job_feedback: str,
    api_key: str | None = None,
    model: str = MODEL,
    effort: str = EFFORT,
) -> tuple[dict, str]:
    """Returns (result, usage summary). api_key=None reads ANTHROPIC_API_KEY."""
    client = anthropic.Anthropic(api_key=api_key or None)

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _user_content(master_resume, job_feedback)}],
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
    return normalize(json.loads(text), RESPONSE_SCHEMA), usage


# --------------------------------------------------------------------------- #
# Mode 2: Claude.ai subscription (free, copy-paste)
# --------------------------------------------------------------------------- #

def _skeleton(schema: dict):
    if schema["type"] == "object":
        return {k: _skeleton(s) for k, s in schema["properties"].items()}
    if schema["type"] == "array":
        return [_skeleton(schema["items"])]
    return ""


def build_manual_prompt(master_resume: str, job_feedback: str) -> str:
    """One prompt to paste into claude.ai; its reply is read back by parse_claude_reply()."""
    shape = json.dumps(_skeleton(RESPONSE_SCHEMA), separators=(",", ":"))
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"{_user_content(master_resume, job_feedback)}\n\n"
        "Output format: reply with ONLY one JSON object (no commentary, no markdown code fences, "
        "no artifact), using straight ASCII double quotes (\"), never curly quotes, "
        f"in exactly this shape:\n{shape}"
    )


# Curly quotes that Word, notes apps and phone keyboards ("smart punctuation")
# substitute for JSON's straight double quote when a reply is copied through them.
_SMART_DOUBLE_QUOTES = str.maketrans({c: '"' for c in "“”„‟″"})


def _load_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        fixed = text.translate(_SMART_DOUBLE_QUOTES)
        if fixed == text:
            raise
        return json.loads(fixed)


def parse_claude_reply(text: str) -> dict:
    """Read the JSON from a pasted Claude reply (tolerates code fences, extra text and smart quotes)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1:
        raise TailorError("No JSON found. Paste Claude's whole reply, starting with { and ending with }.")
    if end <= start:
        raise TailorError("The reply looks cut off. Ask Claude to \"continue\" and paste the full JSON.")
    try:
        data = _load_json(text[start:end + 1])
    except json.JSONDecodeError as e:
        near = e.doc[max(0, e.pos - 40):e.pos + 40].replace("\n", " ")
        raise TailorError(
            f"Claude's reply is not valid JSON ({e.msg}) near: …{near}…  "
            "Copy the reply with the copy button under Claude's message and paste it here directly. "
            "If the reply was cut off, ask Claude to \"continue\" and paste the full JSON."
        ) from None
    if "resume" not in data and "name" in data:  # a bare resume, e.g. a saved .json
        data = {"resume": data}
    result = normalize(data, RESPONSE_SCHEMA)
    if not result["resume"]["name"] and not result["resume"]["experience"]:
        raise TailorError("The pasted JSON doesn't contain a resume. Paste Claude's whole reply.")
    return result


# --------------------------------------------------------------------------- #
# Input / file name helpers
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


def safe_filename(name: str) -> str:
    """User-typed file name without extension or characters Windows/macOS reject."""
    name = re.sub(r"\.(pdf|docx|json)$", "", name.strip(), flags=re.IGNORECASE)
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:120]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Tailor a resume to a job description with Claude.")
    parser.add_argument("--resume", default="master_resume.json", help="Master resume (.json, .docx, .md or .txt)")
    parser.add_argument("--job", default="job_feedback.txt", help="Job description and/or ATS feedback")
    parser.add_argument("--out-dir", default="output", help="Output folder (e.g. a Google Drive for desktop folder)")
    parser.add_argument("--label", default="", help="Label for the output file name, e.g. the company")
    parser.add_argument("--template", default="Classic", choices=list(render.TEMPLATES), help="Resume template")
    parser.add_argument("--page", default="A4", choices=list(render.PAGE_SIZES), help="Page size")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit('ANTHROPIC_API_KEY is not set. In PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."\n'
                 "No API credits? Use the free copy-paste mode in the web UI: streamlit run app.py")

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
    stem = out_dir / output_stem(resume, args.label)

    stem.with_suffix(".pdf").write_bytes(render.build_pdf(resume, args.template, page=args.page))
    render.build_docx(resume, stem.with_suffix(".docx"), args.template, page=args.page)
    stem.with_suffix(".json").write_text(json.dumps(resume, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\nKeywords added:")
    for k in result["keywords_added"] or [{"keyword": "(none)", "where": ""}]:
        print(f"  + {k['keyword']}  ->  {k['where']}")
    if result["keywords_not_added"]:
        print("\nNot added (no supporting experience — add manually only if true):")
        for k in result["keywords_not_added"]:
            print(f"  - {k['keyword']}: {k['reason']}")

    print(f"\nSaved: {stem}.pdf / .docx / .json")


if __name__ == "__main__":
    main()
