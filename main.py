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
    "headline": _STR,  # short professional title, e.g. "Backend Engineer | Node.js, AWS"
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
    "job_keywords": _STR_LIST,  # key terms of the job posting, used by the built-in ATS check
    "keywords_added": {"type": "array", "items": _obj({
        "keyword": _STR,
        "where": _STR,
    })},
    "keywords_not_added": {"type": "array", "items": _obj({
        "keyword": _STR,
        "reason": _STR,
        # One-click options for the candidate, used only if they confirm the keyword is true
        "suggestions": {"type": "array", "items": _obj({
            "level": _STR,  # "basic" (Skills only) or "experienced" (bullet in a role/project)
            "where": _STR,  # "Skills" or the role/project, e.g. "Syntivis AG"
            "text": _STR,   # the Skills entry or the ready-to-use bullet
        })},
    })},
})

STRICT_KEYWORD_RULES = """- Truthfulness comes first. Only use facts present in the master resume. Never invent employers, titles, dates, degrees, certifications, metrics, tools, or responsibilities.
- Add a missing keyword only where the existing experience genuinely supports it (for example, rephrasing "built data pipelines in Python" to mention "ETL" is fine; adding "Kubernetes" to a role that never mentions containers is not). Weave keywords into existing bullets, the summary, or the skills list in natural language; do not keyword-stuff.
- If a keyword cannot be added honestly, leave it out and list it under keywords_not_added with a short reason, so the candidate can decide whether it truly applies.
- For each keywords_not_added item, give 1-3 suggestions the candidate can pick with one click IF it is true for them: one with level "basic" (where "Skills", text = the Skills entry, e.g. "GCP (basics)"), and, when an existing role or project plausibly involved it, one or two with level "experienced" (where = that role's company or project name, text = a ready-to-use bullet for that entry, consistent with what the resume says about it, with no invented metrics). The candidate decides what is true; these are options, not claims."""

# The candidate opted in to matching every job keyword (they vouch for the skills).
# Hard, checkable facts stay fixed either way.
MAX_MATCH_RULES = """- Max ATS match is on: the candidate wants EVERY job keyword in the resume and vouches for having at least basic experience with each. Your goal is a 100% keyword match.
- Facts that stay fixed: employers, job titles, dates, degrees, schools, certifications and existing numbers. Never invent or change these, and never invent metrics.
- Add every job keyword to Skills, in the posting's exact wording. Then also weave each important keyword into the summary or a bullet of the most plausible role or project, at a level that fits that role (e.g. "Used Terraform to provision staging environments" in a DevOps-adjacent role), so it appears in context, not only in a list. New bullets are fine where needed; keep them plausible for that role's field and time.
- Keywords the posting repeats should appear 2-3 times (Skills plus one or two bullets or the summary); never more. Do not stuff lists of keywords into one bullet.
- Where the posting uses both an acronym and its full form (or the resume uses the other one), include both once, e.g. "Continuous Integration (CI/CD)".
- Credentials the resume doesn't have (a certification, degree, license or clearance): never claim them. Use the underlying skill instead (e.g. "Scrum" for "Certified ScrumMaster") and list the credential under keywords_not_added with the reason "credential", with no suggestions.
- keywords_not_added holds only those credentials; every other keyword goes in keywords_added."""

COMMON_RULES = """- Keep every role, project, and education entry, in the original order, with the original titles, companies, and dates. You may reorder bullets within a role to put the most relevant first, and reorder the skills list.
- Keep bullets concise (ideally one line, at most two), starting with strong action verbs. Preserve existing numbers exactly.
- Write every role as bullet points, never as paragraphs: 3-6 bullets for recent or relevant roles, 1-3 for older ones.
- Never use first-person pronouns (I, me, my) or empty buzzwords (team player, hard worker, results-driven, go-getter); show the skill with a concrete example instead.
- Keep the whole resume to roughly 450-800 words by trimming the least relevant bullets, not by dropping roles.
- Rewrite the summary to target this role in 2-3 sentences (title, years of experience, top skills), using only facts from the resume.
- headline is a short professional title line (under 90 characters, e.g. "Backend Engineer | Node.js, AWS") targeted to this role and based on the resume. If a target job title is given, start the headline with it verbatim and mention it in the summary. Put only contact details (email, phone, location, links) in contact, never a title or tagline.
- skills: plain-text entries; group related tools as "Category: tool, tool, tool".
- Use empty strings or empty lists for fields the master resume does not have.
- In keywords_added, list each keyword you integrated and where (e.g. "Skills", "Acme Corp bullet 2").
- job_keywords: the 25-40 keywords an ATS checker (like Jobscan) would scan this job posting for: every hard skill, tool, platform, programming language, framework, method, domain term and certification, plus the 3-6 most stressed soft skills (e.g. "communication", "stakeholder management"). Each 1-3 words, in the posting's own wording and spelling. Skip generic words like "team", "experience" or "ability". If no job description is given, return an empty list."""


def tailor_rules(max_match: bool = False) -> str:
    return "Rules:\n" + (MAX_MATCH_RULES if max_match else STRICT_KEYWORD_RULES) + "\n" + COMMON_RULES


def tailor_system(max_match: bool = False) -> str:
    return f"""You are an expert resume writer who tailors resumes for ATS systems and human recruiters.

You receive a candidate's master resume and a job description and/or ATS feedback listing missing keywords. Return a tailored version of the resume.

{tailor_rules(max_match)}"""


def revision_system(max_match: bool = False) -> str:
    keywords = ("- Missing keywords: add every one (Skills plus a fitting bullet or the summary), following the "
                "Max ATS match rules below. Only credentials go under keywords_not_added."
                if max_match else
                "- Missing keywords: add each one only where the master resume supports it, in natural language "
                "(skills, summary or the relevant bullet). List the rest under keywords_not_added with a reason.")
    return f"""You are an expert resume writer revising a resume you tailored earlier, using a report from an ATS (applicant tracking system) checker.

You receive the candidate's master resume (the source of facts), the job description, the current tailored resume (JSON) and the ATS checker report. Return the complete revised resume.

Revision rules:
- Fix every content issue in the report, for example: length (trim the least relevant bullets to reach the suggested word count), paragraphs instead of bullets, first-person pronouns, buzzwords, a missing or weak summary, the job title missing from the headline or summary, too few bullets with numbers (use numbers already in the master resume).
{keywords}
- An external checker's report may list hard skills, soft skills or other words it found missing, or words it wants mentioned more often. Treat every one of them as a job keyword, use its exact wording, and repeat it where the report asks for more mentions.
- Ignore checks about section headings, columns, tables, fonts or file format: the app's templates already use standard headings (Summary, Skills, Experience, Projects, Education, Certifications) and a parseable layout. Do not change content to compensate for them.
- If the report names a job title that is clearly a parsing mistake (e.g. "About the Role"), use the real title from the target job title or the job description instead.
- keywords_added lists the keywords integrated in this revision and where; keywords_not_added lists the report's keywords you did not add.
- Keep everything else from the current resume that the report does not flag.
- job_keywords: return the current job's keyword list (or extract it from the job description), plus every keyword an external checker's report says is missing.
- candidate_confirmed_keywords (if given) are skills the candidate personally confirms are true, with a note on where or how they used them. Treat them as facts from the master resume and add every one of them: if the note says "Skills only", add it only to Skills; if the note gives a suggested bullet for a role or project, add that bullet (lightly edited to fit) to that entry; if the note names a role or project, also word it naturally into one of that entry's bullets (or a new bullet there), and add it to Skills. Use the keyword's exact wording from the ATS report. Do not invent metrics, tools or details beyond the note, and do not list them under keywords_not_added.
- This may be one of several rounds. If a score_history is given and the score dropped after the last round, undo that kind of change instead of pushing further in the same direction.

These tailoring rules still apply:
{tailor_rules(max_match)}"""


TAILOR_RULES = tailor_rules()
SYSTEM_PROMPT = tailor_system()
REVISION_PROMPT = revision_system()


class TailorError(Exception):
    """Claude finished without a usable resume (refusal, truncated or unreadable output)."""


def _title_block(job_title: str) -> str:
    return f"<target_job_title>{job_title}</target_job_title>\n\n" if job_title.strip() else ""


def _user_content(master_resume: str, job_feedback: str, job_title: str = "") -> str:
    return (
        f"<master_resume>\n{master_resume}\n</master_resume>\n\n"
        f"<job_description_and_feedback>\n{job_feedback}\n</job_description_and_feedback>\n\n"
        f"{_title_block(job_title)}"
        "Tailor the resume for this job following the rules."
    )


def _revision_content(master_resume: str, job_feedback: str, job_title: str, resume: dict, ats_report: str,
                      progress: str = "", confirmed: list[dict] | None = None) -> str:
    master = master_resume or "(not provided: treat the current resume as the source of facts)"
    current = json.dumps(resume, ensure_ascii=False, separators=(",", ":"))
    history = f"<score_history>{progress}</score_history>\n\n" if progress else ""
    if confirmed:
        lines = "\n".join(f"- {c['keyword']}" + (f": {c['note']}" if c.get("note") else "") for c in confirmed)
        history += f"<candidate_confirmed_keywords>\n{lines}\n</candidate_confirmed_keywords>\n\n"
    return (
        f"<master_resume>\n{master}\n</master_resume>\n\n"
        f"<job_description>\n{job_feedback or '(not provided)'}\n</job_description>\n\n"
        f"{_title_block(job_title)}"
        f"<current_resume>\n{current}\n</current_resume>\n\n"
        f"<ats_report>\n{ats_report}\n</ats_report>\n\n"
        f"{history}"
        "Revise the resume to fix the issues in the ATS report, following the rules."
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
    job_title: str = "",
) -> tuple[dict, str]:
    """Returns (result, usage summary). api_key=None reads ANTHROPIC_API_KEY."""
    return _call_claude(SYSTEM_PROMPT, _user_content(master_resume, job_feedback, job_title), api_key, model, effort)


def revise_resume(
    master_resume: str,
    job_feedback: str,
    resume: dict,
    ats_report: str,
    api_key: str | None = None,
    model: str = MODEL,
    effort: str = EFFORT,
    job_title: str = "",
    progress: str = "",
    confirmed: list[dict] | None = None,
) -> tuple[dict, str]:
    """Revise an already-tailored resume using an ATS checker report.

    progress: score history, e.g. "v1: 62%, v2: 78%". confirmed: [{"keyword", "note"}] the
    candidate confirms are true although the master resume doesn't mention them.
    """
    content = _revision_content(master_resume, job_feedback, job_title, resume, ats_report, progress, confirmed)
    return _call_claude(REVISION_PROMPT, content, api_key, model, effort)


def _call_claude(system: str, content: str, api_key: str | None, model: str, effort: str) -> tuple[dict, str]:
    client = anthropic.Anthropic(api_key=api_key or None)

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": content}],
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


def tailor_request(master_resume: str, job_feedback: str, job_title: str = "",
                   max_match: bool = False) -> tuple[str, str]:
    """(system prompt, user content) for tailoring — for engines.run()."""
    return tailor_system(max_match), _user_content(master_resume, job_feedback, job_title)


def revision_request(master_resume: str, job_feedback: str, resume: dict, ats_report: str, job_title: str = "",
                     progress: str = "", confirmed: list[dict] | None = None,
                     max_match: bool = False) -> tuple[str, str]:
    """(system prompt, user content) for one ATS revision round — for engines.run()."""
    return revision_system(max_match), _revision_content(master_resume, job_feedback, job_title, resume,
                                                         ats_report, progress, confirmed)


def build_manual_prompt(master_resume: str, job_feedback: str, job_title: str = "", max_match: bool = False) -> str:
    """One prompt to paste into claude.ai; its reply is read back by parse_claude_reply()."""
    return _manual_prompt(*tailor_request(master_resume, job_feedback, job_title, max_match))


def build_revision_prompt(master_resume: str, job_feedback: str, resume: dict, ats_report: str,
                          job_title: str = "", progress: str = "", confirmed: list[dict] | None = None,
                          max_match: bool = False) -> str:
    """Copy-paste version of revise_resume()."""
    return _manual_prompt(*revision_request(master_resume, job_feedback, resume, ats_report, job_title,
                                            progress, confirmed, max_match))


def _manual_prompt(system: str, content: str) -> str:
    shape = json.dumps(_skeleton(RESPONSE_SCHEMA), separators=(",", ":"))
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
    parser.add_argument("--resume", default="master_resume.json", help="Master resume (.json, .docx, .md or .txt)")
    parser.add_argument("--job", default="job_feedback.txt", help="Job description and/or ATS feedback")
    parser.add_argument("--out-dir", default="output", help="Output folder (e.g. a Google Drive for desktop folder)")
    parser.add_argument("--label", default="", help="Label for the output file name, e.g. the company")
    parser.add_argument("--template", default="classic", choices=list(render.TEMPLATES), help="Resume template")
    parser.add_argument("--page", default="A4", choices=list(render.PAGE_SIZES), help="Page size")
    parser.add_argument("--title", default="", help="Exact job title from the posting")
    parser.add_argument("--max-match", action="store_true",
                        help="Add every job keyword, even ones the resume doesn't show yet (aims for 100%% match)")
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
    try:
        result, usage = engines.run(config, *tailor_request(master, job, args.title, args.max_match))
        print(usage)
    except engines.EngineError as e:
        sys.exit(str(e))

    resume = result["resume"]
    if args.max_match:
        import ats
        resume, _ = ats.ensure_keywords(resume, result["job_keywords"])
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
