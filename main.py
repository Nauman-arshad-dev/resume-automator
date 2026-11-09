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

Outputs (in --out-dir, default ./output): Firstname-Lastname-Role.pdf / .docx / .json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
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
API_TIMEOUT_SECONDS = 600

from prompts import (  # noqa: E402,F401 — prompts and schemas live in prompts.py; re-exported here
    ANALYSIS_PROMPT, ANALYSIS_SCHEMA, CONTENT_MODES, COVER_LETTER_PROMPT, COVER_LETTER_SCHEMA,
    RECRUITER_PROMPT, RECRUITER_SCHEMA, REQUIREMENT_CATEGORIES, RESPONSE_SCHEMA, RESUME_SCHEMA,
    REVISION_PROMPT, SENIORITY_LEVELS, TAILOR_PROMPT, revision_prompt, tailor_prompt,
)

JOB_TYPES = ("Remote", "Relocation", "Local / Onsite")
CONFIRM_LEVELS = ("Strong", "Basic", "Learning", "No")


def cover_letter_request(resume: dict, job_text: str, job_title: str = "", angle: str = "") -> tuple[str, str]:
    """(system prompt, user content) for a cover letter — for engines.run(..., schema=COVER_LETTER_SCHEMA)."""
    content = (
        f"<resume>\n{json.dumps(resume, ensure_ascii=False, separators=(',', ':'))}\n</resume>\n\n"
        f"<job_description>\n{job_text}\n</job_description>\n\n"
        f"{_title_block(job_title)}"
        + (f"<cover_letter_angle>{angle}</cover_letter_angle>\n\n" if angle else "")
        + "Write the cover letter following the rules."
    )
    return COVER_LETTER_PROMPT, content


def build_cover_letter_prompt(resume: dict, job_text: str, job_title: str = "", angle: str = "") -> str:
    return manual_prompt(*cover_letter_request(resume, job_text, job_title, angle), COVER_LETTER_SCHEMA)


def parse_cover_letter(text: str) -> dict:
    """Read a pasted cover-letter reply; plain text (no JSON) is accepted as the letter itself."""
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return normalize(_load_json(text[start:end + 1]), COVER_LETTER_SCHEMA)
        except json.JSONDecodeError:
            pass
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paragraphs:
        raise TailorError("Paste Claude's cover letter reply.")
    return {"greeting": "", "paragraphs": paragraphs, "closing": ""}


class TailorError(Exception):
    """Claude finished without a usable resume (refusal, truncated or unreadable output)."""


# Gender markers in (mostly German) job ads: "(m/w/d)", "(F/M/D)", "(all genders)"...
_GENDER_MARKER = re.compile(
    r"\s*[(\[]\s*(?:[mwfdxi*]\s*[/|,\s]\s*){1,3}[mwfdxi*]\s*[)\]]"
    r"|\s*[(\[]\s*(?:all genders?|gn|genderneutral|any gender|d/f/m)\s*[)\]]",
    re.IGNORECASE)


def clean_title(title: str) -> str:
    """The job title without the ad's gender marker: 'Engineer (F/M/D)' -> 'Engineer'."""
    return re.sub(r"\s{2,}", " ", _GENDER_MARKER.sub("", title)).strip(" -–|")


def _title_block(job_title: str) -> str:
    job_title = clean_title(job_title)
    return f"<target_job_title>{job_title}</target_job_title>\n\n" if job_title else ""


def _settings_text(settings: dict | None) -> str:
    """TARGET_SETTINGS as plain lines. settings: job_type, timezone, location, visa, must_include, must_exclude."""
    s = settings or {}
    job_type = s.get("job_type") or "not specified"
    lines = [f"job_type: {job_type}"]
    if job_type == "Remote":
        lines.append(f"candidate_time_zone: {s.get('timezone') or 'unknown (write: Open to remote work)'}")
    elif job_type == "Relocation":
        lines.append(f"relocation_to: {s.get('location') or 'the job location in the posting'}")
        lines.append(f"visa: {s.get('visa') or '(use that country usual skilled-work visa)'}")
    lines.append(f"page_limit: {s.get('page_limit') or 2}")
    lines.append(f"content_mode: {content_mode(s)}")
    for key in ("must_include", "must_exclude"):
        if s.get(key):
            lines.append(f"{key}: {s[key]}")
    return "\n".join(lines)


def content_mode(settings: dict | None) -> str:
    """"Strict" (evidence only, the default) or "Flexible" (keyword injection)."""
    mode = (settings or {}).get("content_mode")
    return mode if mode in CONTENT_MODES else "Strict"


def _confirmations_text(confirmations: dict | None, mode: str = "Strict") -> str:
    """confirmations: {key: {"skill", "level"}} with level one of CONFIRM_LEVELS."""
    if not confirmations:
        return ("(none given: inject every job skill)" if mode == "Flexible"
                else "(none given: use only what the profile shows)")
    return "\n".join(f"- {c['skill']}: {c['level']}" for c in confirmations.values())


def _job_block(job_text: str, job_title: str, settings: dict | None, analysis: dict | None,
               confirmations: dict | None) -> str:
    analysis_json = json.dumps(analysis, ensure_ascii=False, separators=(",", ":")) if analysis else "(not given)"
    return (
        f"<job_description>\n{job_text or '(not provided)'}\n</job_description>\n\n"
        f"{_title_block(job_title)}"
        f"<target_settings>\n{_settings_text(settings)}\n</target_settings>\n\n"
        f"<job_analysis>\n{analysis_json}\n</job_analysis>\n\n"
        f"<skill_confirmations>\n{_confirmations_text(confirmations, content_mode(settings))}\n"
        "</skill_confirmations>\n\n"
    )


def analysis_request(master_resume: str, job_text: str, job_title: str = "") -> tuple[str, str]:
    """(system prompt, user content) for step 1, analysing the job — use with ANALYSIS_SCHEMA."""
    content = (
        f"<master_resume>\n{master_resume}\n</master_resume>\n\n"
        f"<job_description>\n{job_text}\n</job_description>\n\n"
        f"{_title_block(job_title)}"
        "Analyse this job against the master resume following the instructions."
    )
    return ANALYSIS_PROMPT, content


def tailor_request(master_resume: str, job_text: str, job_title: str = "", settings: dict | None = None,
                   analysis: dict | None = None, confirmations: dict | None = None) -> tuple[str, str]:
    """(system prompt, user content) for step 2, the tailored resume — always built from the master."""
    content = (
        f"<master_resume>\n{master_resume}\n</master_resume>\n\n"
        f"{_job_block(job_text, job_title, settings, analysis, confirmations)}"
        "Tailor the resume for this job following the rules, then self-check it."
    )
    return tailor_prompt(content_mode(settings)), content


def revision_request(master_resume: str, job_text: str, resume: dict, ats_report: str, job_title: str = "",
                     settings: dict | None = None, analysis: dict | None = None,
                     confirmations: dict | None = None, progress: str = "") -> tuple[str, str]:
    """(system prompt, user content) for one ATS revision round. progress: e.g. "v1: 62, v2: 78"."""
    master = master_resume or "(not provided: treat the current resume as the source of facts)"
    current = json.dumps(resume, ensure_ascii=False, separators=(",", ":"))
    history = f"<score_history>{progress}</score_history>\n\n" if progress else ""
    content = (
        f"<master_resume>\n{master}\n</master_resume>\n\n"
        f"{_job_block(job_text, job_title, settings, analysis, confirmations)}"
        f"<current_resume>\n{current}\n</current_resume>\n\n"
        f"<ats_report>\n{ats_report}\n</ats_report>\n\n"
        f"{history}"
        "Revise the resume to fix the issues in the ATS report, following the rules."
    )
    return revision_prompt(content_mode(settings)), content


def recruiter_request(resume: dict, job_text: str, job_title: str = "",
                      analysis: dict | None = None) -> tuple[str, str]:
    """(system prompt, user content) for the simulated recruiter review — use with RECRUITER_SCHEMA."""
    analysis_json = json.dumps(analysis, ensure_ascii=False, separators=(",", ":")) if analysis else "(not given)"
    content = (
        f"<job_description>\n{job_text or '(not provided)'}\n</job_description>\n\n"
        f"{_title_block(job_title)}"
        f"<job_analysis>\n{analysis_json}\n</job_analysis>\n\n"
        f"<resume>\n{json.dumps(resume, ensure_ascii=False, separators=(',', ':'))}\n</resume>\n\n"
        "Review this resume for this job following the instructions."
    )
    return RECRUITER_PROMPT, content


def parse_recruiter(text: str) -> dict:
    """Read a pasted recruiter review (copy-paste mode)."""
    result = normalize(_reply_json(text), RECRUITER_SCHEMA)
    if not result["first_impression"] and not result["improvements"]:
        raise TailorError("The pasted JSON doesn't contain a recruiter review. Paste Claude's whole reply.")
    return result


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
    if kind == "integer":
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


# --------------------------------------------------------------------------- #
# Mode 1: Anthropic API (automatic, needs API credits)
# --------------------------------------------------------------------------- #

def _call_claude(system: str, content: str, api_key: str | None, model: str, effort: str,
                 schema: dict = RESPONSE_SCHEMA) -> tuple[dict, str]:
    client = anthropic.Anthropic(api_key=api_key or None, timeout=API_TIMEOUT_SECONDS, max_retries=2)

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
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
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = _load_json(text[text.find("{"):text.rfind("}") + 1])
        except json.JSONDecodeError:
            raise TailorError("Claude's answer was not valid JSON. Try again.") from None
    return normalize(data, schema), usage


# --------------------------------------------------------------------------- #
# Mode 2: Claude.ai subscription (free, copy-paste)
# --------------------------------------------------------------------------- #

def _skeleton(schema: dict):
    if schema["type"] == "object":
        return {k: _skeleton(s) for k, s in schema["properties"].items()}
    if schema["type"] == "array":
        return [_skeleton(schema["items"])]
    return ""


def manual_prompt(system: str, content: str, schema: dict = RESPONSE_SCHEMA) -> str:
    shape = json.dumps(_skeleton(schema), separators=(",", ":"))
    return (
        f"{system}\n\n"
        f"{content}\n\n"
        "Output format: reply with ONLY one JSON object (no commentary, no markdown code fences, "
        "no artifact), using straight ASCII double quotes (\"), never curly quotes, "
        "and never a double quote inside a text value (write 'single quotes' there instead), "
        f"in exactly this shape:\n{shape}"
    )


# Curly quotes that Word, notes apps and phone keyboards ("smart punctuation")
# substitute for JSON's straight double quote when a reply is copied through them.
_SMART_DOUBLE_QUOTES = str.maketrans({c: '"' for c in "“”„‟″"})
_WS = " \t\r\n"


def _escape_stray_quotes(text: str) -> str:
    """Escape double quotes Claude left unescaped inside a JSON string, e.g. "title "X" is wrong".

    A quote inside a string only ends it if what follows is JSON structure: } ] : or
    a comma followed by the next string/object/array (inside an object, that next
    string must be a key, i.e. followed by a colon). Anything else is part of the text.
    """
    out, in_string, stack, i, n = [], False, [], 0, len(text)
    while i < n:
        ch = text[i]
        if not in_string:
            in_string = ch == '"'
            if ch in "{[":
                stack.append(ch)
            elif ch in "}]" and stack:
                stack.pop()
        elif ch == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        elif ch == '"':
            j = i + 1
            while j < n and text[j] in _WS:
                j += 1
            nxt = text[j] if j < n else ""
            if nxt == ",":
                k = j + 1
                while k < n and text[k] in _WS:
                    k += 1
                ends = k >= n or text[k] in '"{['
                if ends and k < n and text[k] == '"' and stack and stack[-1] == "{":
                    key_end = text.find('"', k + 1)
                    p = key_end + 1
                    while 0 < p < n and text[p] in _WS:
                        p += 1
                    ends = key_end != -1 and p < n and text[p] == ":"
            else:
                ends = nxt in ("", "}", "]", ":")
            if ends:
                in_string = False
            else:
                out.append("\\")
        out.append(ch)
        i += 1
    return "".join(out)


def _load_json(text: str):
    """Parse JSON, then retry with repairs: stray inner quotes, then smart quotes + stray quotes."""
    candidates = [text, _escape_stray_quotes(text), _escape_stray_quotes(text.translate(_SMART_DOUBLE_QUOTES))]
    first_error = None
    for candidate in dict.fromkeys(candidates):  # unique, in order
        try:
            return json.loads(candidate, strict=False)  # strict=False: allow raw newlines in strings
        except json.JSONDecodeError as e:
            first_error = first_error or e
    raise first_error


def parse_claude_reply(text: str) -> dict:
    """Read a tailored resume from a pasted Claude reply (tolerates code fences, extra text and smart quotes)."""
    data = _reply_json(text)
    if "resume" not in data and "name" in data:  # a bare resume, e.g. a saved .json
        data = {"resume": data}
    result = normalize(data, RESPONSE_SCHEMA)
    if not result["resume"]["name"] and not result["resume"]["experience"]:
        raise TailorError("The pasted JSON doesn't contain a resume. Paste Claude's whole reply.")
    return result


def parse_analysis(text: str) -> dict:
    """Read a pasted job analysis (step 1 in copy-paste mode)."""
    result = normalize(_reply_json(text), ANALYSIS_SCHEMA)
    if not result["skills"] and not result["job_title"]:
        raise TailorError("The pasted JSON doesn't contain a job analysis. Paste Claude's whole reply.")
    return result


def _reply_json(text: str) -> dict:
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
    if not isinstance(data, dict):
        raise TailorError("The pasted reply is not a JSON object. Paste Claude's whole reply.")
    return data


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


MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def pdf_to_text(data: bytes) -> str:
    """Plain text of a text-based PDF resume; raises TailorError for scans and broken files."""
    import ats  # lazy: ats imports pypdfium2
    if not data.startswith(b"%PDF"):
        raise TailorError("This file is not a PDF.")
    try:
        text = ats.pdf_text(data)
    except Exception as e:  # pdfium raises its own error types for corrupt files
        raise TailorError(f"Could not read the PDF: {e}") from None
    if len(text.split()) < 40:
        raise TailorError("Almost no text could be read from this PDF; it may be a scan or an image. "
                          "Upload a DOCX or paste the text instead.")
    return text


def read_upload(name: str, data: bytes) -> str:
    """Text of an uploaded resume (.pdf, .docx, .json, .md, .txt), validated."""
    import io
    if len(data) > MAX_UPLOAD_BYTES:
        raise TailorError(f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return pdf_to_text(data)
    if suffix == ".docx":
        try:
            return docx_to_text(io.BytesIO(data))
        except Exception as e:  # not a real .docx (zip) file
            raise TailorError(f"Could not read the Word file: {e}") from None
    if suffix not in (".json", ".md", ".txt"):
        raise TailorError("Upload a .pdf, .docx, .json, .md or .txt file.")
    try:
        return prepare_master(data.decode("utf-8-sig"), suffix)
    except UnicodeDecodeError:
        raise TailorError("The file is not UTF-8 text.") from None
    except json.JSONDecodeError as e:
        raise TailorError(f"The JSON file is not valid: {e}") from None


def prepare_master(text: str, suffix: str) -> str:
    if suffix.lower() == ".json":
        # Re-serialise compactly: same content, fewer input tokens.
        return json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
    return text.strip()


def _read_master(path: Path) -> str:
    if path.suffix.lower() in (".docx", ".pdf"):
        return read_upload(path.name, path.read_bytes())
    return prepare_master(path.read_text(encoding="utf-8"), path.suffix)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40] or "resume"


def output_stem(resume: dict, role: str = "") -> str:
    """Recruiter-friendly file name: Firstname-Lastname-Role."""
    parts = [resume["name"] or "Resume"] + ([role] if role.strip() else [])
    return "-".join(_slug(p).replace("_", "-") for p in parts)


def word_count(resume: dict) -> int:
    """Words in every text field, roughly what an ATS checker counts."""
    def walk(value) -> int:
        if isinstance(value, dict):
            return sum(walk(v) for v in value.values())
        if isinstance(value, list):
            return sum(walk(v) for v in value)
        return len(str(value).split())
    return walk(resume)


def safe_filename(name: str) -> str:
    """User-typed file name without extension or characters Windows/macOS reject."""
    name = re.sub(r"\.(pdf|docx|json)$", "", name.strip(), flags=re.IGNORECASE)
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:120]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Tailor a resume to a job description with Claude.")
    parser.add_argument("--resume", default="master_resume.json",
                        help="Master resume (.json, .pdf, .docx, .md or .txt)")
    parser.add_argument("--job", default="job_feedback.txt", help="Job description and/or ATS feedback")
    parser.add_argument("--out-dir", default="output", help="Output folder (e.g. a Google Drive for desktop folder)")
    parser.add_argument("--label", default="", help="Role for the file name (default: --title)")
    parser.add_argument("--template", default="classic", choices=list(render.TEMPLATES), help="Resume template")
    parser.add_argument("--page", default="A4", choices=list(render.PAGE_SIZES), help="Page size")
    parser.add_argument("--title", default="", help="Exact job title from the posting")
    parser.add_argument("--job-type", default="Local / Onsite", choices=list(JOB_TYPES),
                        help="Sets the line under your contact details")
    parser.add_argument("--timezone", default="", help="Remote jobs: your time zone, e.g. UTC+5")
    parser.add_argument("--relocate-to", default="", help="Relocation jobs: city and country, e.g. Berlin, Germany")
    parser.add_argument("--visa", default="", help="Relocation jobs: visa name (default: that country's usual one)")
    parser.add_argument("--mode", default="Strict", choices=list(CONTENT_MODES),
                        help="Strict = only what your resume shows (default); Flexible = every job keyword")
    parser.add_argument("--pages", default=2, type=int, choices=[1, 2], help="Page limit")
    parser.add_argument("--engine", default="auto", choices=["auto", "claude-code", "api"],
                        help="claude-code = your Claude subscription via Claude Code; api = ANTHROPIC_API_KEY "
                             "(auto: Claude Code if installed, else the API)")
    args = parser.parse_args()

    import engines  # imports this module, so import lazily

    engine = args.engine
    if engine == "auto":
        engine = "claude-code" if engines.find_claude_cli() else "api"
    if engine == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit('ANTHROPIC_API_KEY is not set. In PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."\n'
                 "Or install Claude Code and use your Claude subscription: --engine claude-code")

    resume_path, job_path = Path(args.resume), Path(args.job)
    for p in (resume_path, job_path):
        if not p.is_file():
            sys.exit(f"File not found: {p}")

    master = _read_master(resume_path)
    job = job_path.read_text(encoding="utf-8").strip()
    if not job:
        sys.exit(f"{job_path} is empty. Paste the job description and/or ATS feedback into it.")

    print(f"Tailoring {resume_path.name} against {job_path.name} ...")
    config = {"engine": "code" if engine == "claude-code" else "api", "model_cli": "sonnet", "model_api": MODEL,
              "effort": EFFORT, "api_key": os.environ.get("ANTHROPIC_API_KEY")}
    settings = {"job_type": args.job_type, "timezone": args.timezone, "location": args.relocate_to,
                "visa": args.visa, "content_mode": args.mode, "page_limit": args.pages}
    try:
        # No skill confirmations on the command line: Strict mode uses only what the resume shows
        result, usage = engines.run(config, *tailor_request(master, job, args.title, settings))
        print(usage)
    except engines.EngineError as e:
        sys.exit(str(e))

    import review  # imports this module, so import lazily
    skills = [k for k in result["job_keywords"] if k.strip().lower() != clean_title(args.title).lower()]
    resume, fixes = review.fix_resume(result["resume"], skills, {}, master, args.mode)
    for note in fixes:
        print(f"  fixed: {note}")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / output_stem(resume, args.label or args.title)

    stem.with_suffix(".pdf").write_bytes(render.build_pdf(resume, args.template, page=args.page))
    render.build_docx(resume, stem.with_suffix(".docx"), args.template, page=args.page)
    stem.with_suffix(".json").write_text(json.dumps(resume, indent=2, ensure_ascii=False), encoding="utf-8")

    report = result["report"]
    print("\nKeywords covered:")
    for k in report["keywords_covered"] or [{"keyword": "(none)", "where": ""}]:
        print(f"  + {k['keyword']}  ->  {k['where']}")
    if report["gaps"]:
        print("\nGaps (not claimed; the web app can ask you about each one):")
        for g in report["gaps"]:
            print(f"  - {g['skill']}: {g['plan']}")

    print(f"\nSaved: {stem}.pdf / .docx / .json")


if __name__ == "__main__":
    main()
