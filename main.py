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
    "availability": _STR,  # e.g. "Open to remote work (UTC+5)", "" for local jobs
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

_INT = {"type": "integer"}

# Step 1: analyse the job against the master resume, before anything is written.
ANALYSIS_SCHEMA = _obj({
    "job_title": _STR,
    "title_variants": _STR_LIST,
    "company": _STR,
    "job_location": _STR,  # city / country of the role as the posting states it, "" if not stated
    "skills": {"type": "array", "items": _obj({
        "skill": _STR,       # the posting's exact wording
        "importance": _STR,  # "required" | "nice to have"
        "mentions": _INT,
        "group": _STR,       # "A" in the master resume | "B" transferable | "unconfirmed"
        "evidence": _STR,    # A: where in the resume; B: the candidate's equivalent skill
        "meaning": _STR,     # what the term means at this company, when not obvious
    })},
    "responsibilities": _STR_LIST,
    "domain_keywords": _STR_LIST,
    "seniority": _STR,
})

ANALYSIS_PROMPT = """You are an expert recruiter and ATS analyst. Analyse ONE job description against the candidate's master resume, before any resume is written.

Return:
- job_title: the exact job title from the posting (never a section heading such as "About the Role"); title_variants: 1-4 close variants.
- company and job_location: as the posting states them; empty strings if not stated. Never guess.
- skills: every hard skill in the posting (languages, frameworks, databases, cloud, tools, platforms, methods, certifications), in the posting's exact wording and spelling, most important first. importance: "required" or "nice to have". mentions: how many times the posting mentions it. meaning: one short phrase on what the term means at this company when that is not obvious (e.g. "static analysis" at a mobile-app data company means analysing app binaries at scale, not linting pull requests), otherwise "".
  group, judged strictly against the master resume:
  - "A": clearly shown in the master resume. evidence = the role or project that shows it.
  - "B": not named, but the candidate has a clear equivalent (the job says NestJS and the resume has Node.js/Express; the job says GCP and the resume has AWS). evidence = the equivalent from the resume.
  - "unconfirmed": not in the master resume and no clear equivalent. The candidate will be asked about it. evidence = "".
- responsibilities: the 5-8 main things the person will do.
- domain_keywords: industry, product type and scale terms (e.g. fintech, payments, data pipelines, high-throughput).
- seniority: years of experience, leadership, ownership or mentoring signals, in one line."""

RESPONSE_SCHEMA = _obj({
    "resume": RESUME_SCHEMA,
    "job_keywords": _STR_LIST,  # every job skill + the title, used by the built-in ATS check
    "report": _obj({  # Part B: shown in the app, never exported
        "keywords_covered": {"type": "array", "items": _obj({"keyword": _STR, "where": _STR})},
        "location_line_used": _STR,
        "skill_groups": {"type": "array", "items": _obj({"skill": _STR, "group": _STR, "note": _STR})},
        "gaps": {"type": "array", "items": _obj({"skill": _STR, "plan": _STR})},
        "interview_prep": {"type": "array", "items": _obj({"skill": _STR, "talking_point": _STR})},
        "cover_letter_angle": _STR,
    }),
})

TAILOR_PROMPT = """You are an expert resume writer and ATS optimization engine. Your only goal is to turn the candidate's resume into the strongest possible match for ONE specific job, so it scores highly in ATS scans, passes a 10-second recruiter screen and gets the candidate an interview.

You receive the MASTER_RESUME (the only base: never reuse text, headlines, locations or visa lines from any other job), the JOB_DESCRIPTION, the TARGET_SETTINGS, the JOB_ANALYSIS (job skills already classified) and the candidate's SKILL_CONFIRMATIONS.

Skill groups. Every job skill is in exactly one group:
- A. Strong: shown in the master resume, or confirmed "Strong" by the candidate. Feature it prominently: headline, summary, bullets, Skills.
- B. Transferable: the candidate has an equivalent. Use the job's exact word next to the real one, e.g. "Node.js (Express, NestJS-style modular architecture)" or "GCP (via strong AWS experience)", and highlight the shared concepts.
- C. Basic: confirmed "Basic". Put it in Skills on a "Working knowledge: X, Y" line only; never in a bullet.
- D. Not known: confirmed "No" or "Learning", or not confirmed at all. Never present it as experience anywhere. If confirmed "Learning", add it to a "Currently learning: X" Skills line. List every D skill under report.gaps.

ATS rules:
- Mirror the job's exact terms and spelling ("PostgreSQL", not only "Postgres"; "CI/CD"; "RESTful APIs"). Give the full form and the abbreviation once where useful, e.g. "Amazon Web Services (AWS)".
- Every required skill in groups A-C appears in Skills. The top 5-8 required skills of groups A-B also appear in the headline or summary and in at least one experience bullet.
- Repeat the most important keywords 2-3 times across the resume, in context. Never repeat a word more than 3-4 times, and never write keyword blocks.
- Never change the job titles in the Experience section. The headline may use the job's title family when the candidate's real title is close (e.g. the job says "Backend Engineer" and the candidate is a Full Stack Engineer with heavy backend work: "Backend-Focused Full Stack Engineer").
- Write dates as "Mon YYYY – Mon YYYY" (only the format changes, never the dates).

availability (the line shown under the contact details), built fresh from TARGET_SETTINGS only:
- Remote: "Open to remote work (UTC+X)" with the candidate's time zone from the settings. Never mention relocation or visas.
- Relocation: "Open to relocation to <city, country> (<visa> sponsorship)", with the visa name from the settings or, if it is empty, that country's usual skilled-work visa (e.g. Germany: EU Blue Card; United Kingdom: Skilled Worker visa; United States: H-1B visa; Netherlands: Highly Skilled Migrant visa; Canada: work permit).
- Local / Onsite: an empty string.
contact holds only the email, phone, location and links from the master resume.

headline: one line under 95 characters: "<Title aligned to job> | <Top skill> | <Top skill> | <Top skill> | <Domain or strength>", using the strongest required skills in groups A or B.

summary: 3-4 sentences in a confident, human tone: who the candidate is in the job's language (years of experience, title family, core stack for this job); the strongest achievement relevant to this job; two or three more matching strengths (scale, domain, ownership, tools); optionally a work style the posting asks for (remote/async, AI-assisted development, startup pace). Don't copy sentences from the posting. No buzzwords (passionate, rockstar, results-driven) and no first-person pronouns.

Experience:
- Keep every employer, job title and date exactly as in the master resume, in reverse chronological order.
- Reorder bullets so the most job-relevant come first: 3-6 bullets for recent roles, 1-3 for older or less relevant ones.
- Rewrite bullets in the job's vocabulary when the meaning matches: Action verb + what was built or done + technology + result or impact. Expand a bullet only with detail that is true (what the system did, who used it, what problem it solved, which tech).
- Keep real numbers exactly. Never invent numbers; with no real number, describe the impact in words ("reduced manual reporting work", "used daily by the client's operations team"). No filler counts such as "1 dataset", "2 APIs", "3 tools".
- Never start with "Responsible for", "Worked on" or "Helped".

skills: 5-8 categories ordered by relevance to this job (e.g. Languages, Backend, Frontend, Databases, Cloud & DevOps, AI & Developer Tools), each written "Category: a, b, c" with the job's required skills first, in the job's spelling; then the "Working knowledge: ..." and "Currently learning: ..." lines if needed. No duplicates across categories, no uncategorised keyword dump, no soft-skills line.

Projects: most relevant first; the top 2-3 may get one short line showing the matching tech or domain, if true. Education and certifications: copy them exactly; never create certificates or degrees.

Length: 1-2 pages, 450-750 words for under 10 years of experience. Cut the least relevant bullets and projects first, never whole roles. Use empty strings or lists for fields the master resume does not have.

Apply TARGET_SETTINGS must_include items where they are true, and never include must_exclude items.

report (shown in the app, never exported):
- keywords_covered: each job keyword now in the resume and where (headline / summary / bullet at <company> / skills).
- location_line_used: the availability line, or "none (local job)".
- skill_groups: every job skill with its final group (A, B, C or D) and a short note (B: the equivalent used; C/D: the candidate's answer).
- gaps: for each group D skill, a short honest plan (e.g. "Build a small Rust CLI this weekend, then it can move to Working knowledge").
- interview_prep: the 3-5 skills most likely to be asked about, with one talking point each, from the resume.
- cover_letter_angle: one or two sentences on how to address the gaps honestly.
job_keywords: every skill from the job analysis in the posting's wording, plus the job title.

Self-check before answering: the availability line matches THIS job's settings with nothing carried over; the headline is one line with the job's top skills; every A-C required skill is in Skills and the top ones are also in the summary or bullets; C skills are only on the Working knowledge line; no D skill is claimed as experience; no duplicate skills, no keyword dump, no invented or filler numbers; employers, titles and dates unchanged; standard section names."""

REVISION_PROMPT = f"""You are revising a resume you tailored earlier for the same job, using an ATS checker report. You receive the master resume (the only source of facts), the job description, the target settings, the job analysis, the skill confirmations, the current tailored resume (JSON) and the ATS report. Return the complete revised resume and an updated report.

Revision rules:
- Fix every content issue in the report: length, bullets, pronouns, buzzwords, the summary, the job title missing from the headline or summary, weak bullet starts.
- Missing keywords: add those in groups A-C as the tailoring rules say. Never add a group D keyword (unconfirmed, "No" or "Learning") as experience, even when a checker report lists it; keep it under report.gaps instead.
- An external checker's report may list missing hard skills: treat them as job skills under the same group rules, and add them to job_keywords.
- Ignore checks about headings, columns, tables, fonts or file format: the app's templates handle those. If a report names a job title that is a parsing mistake (e.g. "About the Role"), use the real title.
- Too few bullets with numbers: use only numbers that are in the master resume; never invent them.
- Keep everything the report does not flag. If score_history shows the score dropped after the last round, undo that kind of change.

The tailoring rules still apply:

{TAILOR_PROMPT}"""

JOB_TYPES = ("Remote", "Relocation", "Local / Onsite")
CONFIRM_LEVELS = ("Strong", "Basic", "Learning", "No")

COVER_LETTER_SCHEMA = _obj({
    "greeting": _STR,     # e.g. "Dear Hiring Manager,"
    "paragraphs": _STR_LIST,
    "closing": _STR,      # e.g. "Kind regards,"
})

COVER_LETTER_PROMPT = """You are an expert career writer. Write a cover letter for the job, based on the candidate's tailored resume.

Rules:
- Use only facts from the resume: employers, titles, projects, skills and numbers exactly as written there. Never invent achievements or metrics.
- 3-4 short paragraphs, 220-330 words in total:
  1. The exact job title and company (from the job description), and one sentence on why this role fits the candidate.
  2-3. Two or three concrete achievements from the resume that match the job's main requirements, using the job's own keywords and spelling.
  4. A short close: enthusiasm for the company's work (only what the job description says about it) and a request for an interview.
- Confident and specific, plain words. No first line like "I am writing to apply", no buzzwords (team player, hard worker, results-driven, passionate go-getter), no flattery or clichés.
- greeting: "Dear Hiring Manager," unless the job description names the hiring person. Never use placeholders like [Company] or [Name]: if the company name is not given, write around it.
- If a cover_letter_angle is given, use it to address skill gaps briefly and honestly (e.g. a related strength and a fast learning plan); never claim a skill the resume doesn't show.
- closing: e.g. "Kind regards,". The app adds the candidate's name below it."""


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


def _title_block(job_title: str) -> str:
    return f"<target_job_title>{job_title}</target_job_title>\n\n" if job_title.strip() else ""


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
    for key in ("must_include", "must_exclude"):
        if s.get(key):
            lines.append(f"{key}: {s[key]}")
    return "\n".join(lines)


def _confirmations_text(confirmations: dict | None) -> str:
    """confirmations: {key: {"skill", "level"}} with level one of CONFIRM_LEVELS."""
    if not confirmations:
        return "(none given: treat every unconfirmed skill as group D)"
    return "\n".join(f"- {c['skill']}: {c['level']}" for c in confirmations.values())


def _job_block(job_text: str, job_title: str, settings: dict | None, analysis: dict | None,
               confirmations: dict | None) -> str:
    analysis_json = json.dumps(analysis, ensure_ascii=False, separators=(",", ":")) if analysis else "(not given)"
    return (
        f"<job_description>\n{job_text or '(not provided)'}\n</job_description>\n\n"
        f"{_title_block(job_title)}"
        f"<target_settings>\n{_settings_text(settings)}\n</target_settings>\n\n"
        f"<job_analysis>\n{analysis_json}\n</job_analysis>\n\n"
        f"<skill_confirmations>\n{_confirmations_text(confirmations)}\n</skill_confirmations>\n\n"
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
    return TAILOR_PROMPT, content


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
    return REVISION_PROMPT, content


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
    client = anthropic.Anthropic(api_key=api_key or None)

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
    return normalize(json.loads(text), schema), usage


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
    parser.add_argument("--resume", default="master_resume.json", help="Master resume (.json, .docx, .md or .txt)")
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
                "visa": args.visa}
    try:
        # No skill confirmations on the command line: skills the resume doesn't show stay group D
        result, usage = engines.run(config, *tailor_request(master, job, args.title, settings))
        print(usage)
    except engines.EngineError as e:
        sys.exit(str(e))

    resume = result["resume"]
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
