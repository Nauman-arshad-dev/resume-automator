"""
Mechanical clean-up and review of a tailored resume.

fix_resume() runs after every Claude answer and repairs the "fabrication tells"
reviewers spot at a glance, whatever the model wrote: hedges in brackets
("Vue.js (via Angular experience)"), the job ad's gender marker in the headline,
inflated years, duplicate or catch-all skills, unnormalised spelling and dates.

review() lists what still needs a human look before export: a skill the
candidate said No to, a duplicate employer, a dropped section, a headline that
wraps, a location line that doesn't match the job type, numbers that aren't in
the master resume.
"""

from __future__ import annotations

import math
import re
from datetime import date

import ats
import main
import render

HEADLINE_MAX = 95
SKILLS_MAX = 20
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# A bracket that explains or hedges a skill: "(via strong AWS experience)", "(basics)", "(learning)"...
HEDGE = re.compile(r"\s*\((?=[^)]*\b(?:via|basics?|learning|familiar|exposure|equivalent|similar|transferable|"
                   r"adjacent|through|strong|experience|knowledge|hands-on|self-taught|course)\b)[^)]*\)",
                   re.IGNORECASE)
BUCKETS = ("working knowledge", "familiar with", "currently learning", "learning", "exposure", "basic",
           "basics", "other", "additional", "additional skills")
FULL_STACK = re.compile(r"\bfull[\s-]?stack\b", re.IGNORECASE)
YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(years?|yrs)\b", re.IGNORECASE)
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"), start=1)}
PRESENT = re.compile(r"present|current|now|today|heute|ongoing", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _clean(item: str) -> str:
    """'GCP (via strong AWS experience)' -> 'gcp'."""
    return re.sub(r"\s*\(.*?\)", "", item).strip().lower()


def _variants(item: str) -> list[str]:
    """'Amazon Web Services (AWS: S3, Lambda)' -> ['amazon web services', 'aws', 's3', 'lambda']."""
    inner = " ".join(re.findall(r"\((.*?)\)", item))
    return [_clean(item)] + [p.strip().lower() for p in re.split(r"[,:/;]", inner) if p.strip()]


def _level(confirmations: dict, skill: str) -> str | None:
    c = confirmations.get(skill.strip().lower())
    return c["level"] if c else None


def _dehedge(text: str) -> str:
    return re.sub(r"\s{2,}", " ", HEDGE.sub("", text)).strip()


def _full_stack(text: str) -> str:
    return FULL_STACK.sub(lambda m: "Full-Stack" if m.group(0)[0].isupper() else "full-stack", text)


DATE_SEP = re.compile(r"\s*[–—]\s*|\s+-\s+|\s+to\s+|\s+bis\s+|(?<=\d)-(?=\s*(?:\d{4}|[A-Za-z]))", re.IGNORECASE)


def _date_range(dates: str) -> str:
    """'Jan 2023 - Present', '2019-2021', '01/2020 to 03/2022' -> spaced en dash."""
    return DATE_SEP.sub(" – ", dates.strip(), count=1)


def _split_combined(item: str) -> list[str]:
    """'PostgreSQL/PostGIS' -> two skills; 'CI/CD', 'C/C++', 'UI/UX', 'HTML/CSS' stay one."""
    parts = [p.strip() for p in item.split("/")]
    if len(parts) > 1 and all(len(p) >= 4 and " " not in p for p in parts):
        return parts
    return [item]


def _point(text: str, end: bool) -> tuple[int, int] | None:
    """(year, month) from 'Jan 2023', '01/2023', '2023'; the year's last month for an end date."""
    if m := re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", text):
        return int(m.group(2)), MONTHS.get(m.group(1).lower(), 12 if end else 1)
    if m := re.search(r"(\d{1,2})\s*[/.]\s*(\d{4})", text):
        return int(m.group(2)), int(m.group(1))
    if m := re.search(r"(\d{4})", text):
        return int(m.group(1)), 12 if end else 1
    return None


def experience_years(experience: list[dict]) -> int | None:
    """Whole years from the earliest dated role to the latest end (or today); None if no dates."""
    starts, ends = [], []
    today = date.today()
    for job in experience:
        pieces = DATE_SEP.split(job["dates"].strip(), maxsplit=1)
        if start := _point(pieces[0], end=False):
            starts.append(start)
        tail = pieces[1] if len(pieces) > 1 else ""
        if PRESENT.search(tail):
            ends.append((today.year, today.month))
        elif end := _point(tail, end=True):
            ends.append(end)
    if not starts:
        return None
    (y0, m0), (y1, m1) = min(starts), max(ends or starts)
    return max(0, math.floor(((y1 - y0) * 12 + (m1 - m0) + 1) / 12))


def skill_items(resume: dict) -> list[tuple[str, str]]:
    """(category, item) for every entry in the Skills section."""
    return [(g["label"], item) for g in render._skill_groups(resume["skills"]) for item in g["items"]]


# --------------------------------------------------------------------------- #
# Automatic fixes
# --------------------------------------------------------------------------- #

def fix_resume(resume: dict, job_skills: list[str], confirmations: dict) -> tuple[dict, list[str]]:
    """Mechanical clean-up; returns (fixed copy, notes on what changed)."""
    notes: list[str] = []
    r = {**resume, "skills": list(resume["skills"]),
         "experience": [{**j, "bullets": list(j["bullets"])} for j in resume["experience"]],
         "education": [dict(e) for e in resume["education"]]}

    # Headline: no gender marker, no hedges
    headline = _dehedge(main._GENDER_MARKER.sub("", r["headline"]))
    if headline != r["headline"].strip():
        notes.append("Headline: removed the job ad's gender marker / bracket explanations.")
    r["headline"] = headline

    # Summary: no hedges, no inflated years
    summary = _dehedge(r["summary"])
    if summary != r["summary"].strip():
        notes.append("Summary: removed bracket explanations.")
    span = experience_years(r["experience"])
    if span:
        def cap(m: re.Match) -> str:
            return f"{span}+ years" if int(m.group(1)) > span else m.group(0)
        capped = YEARS.sub(cap, summary)
        if capped != summary:
            notes.append(f"Summary: years of experience capped at {span}+ (your dated roles span {span} years).")
        summary = capped
        r["headline"] = YEARS.sub(cap, r["headline"])
    r["summary"] = summary

    # Skills: real categories only, bare tokens, no duplicates, every job skill (except "No"), capped
    excluded = {k for k, c in confirmations.items() if c["level"] == "No"}
    groups, bucket_items, changed = [], [], set()
    for g in render._skill_groups(r["skills"]):
        items = []
        for item in g["items"]:
            clean = _dehedge(item)
            if clean != item:
                changed.add("bracket explanations")
            items += _split_combined(clean)
        if g["label"].strip().lower() in BUCKETS:
            bucket_items += items
            changed.add(f"the \"{g['label']}\" bucket")
        else:
            groups.append({"label": g["label"], "items": items})
    # Skills without a fitting category (old buckets, injected job skills) go to a real, plain category
    extra = next((g for g in groups if re.search(r"tool|technolog", g["label"], re.IGNORECASE)), None)
    if extra is None:
        extra = {"label": "Tools & Technologies", "items": []}
        groups.append(extra)
    extra["items"] += bucket_items

    seen, dupes = set(), []
    for g in groups:
        kept = []
        for item in g["items"]:
            key = item.strip().lower()
            if not key or key in excluded:
                continue
            if key in seen:
                dupes.append(item)
                continue
            seen.add(key)
            kept.append(item)
        g["items"] = kept
    if dupes:
        notes.append("Skills: removed duplicates: " + ", ".join(dict.fromkeys(dupes)) + ".")

    skills_text = " ".join(i for g in groups for i in g["items"]).lower()
    missing = [s for s in dict.fromkeys(job_skills) if s.strip().lower() not in excluded
               and not ats.has_keyword(skills_text, s)]
    if missing:
        extra["items"] += missing
        notes.append("Skills: added job skills that were missing: " + ", ".join(missing) + ".")

    job_lower = [s.lower() for s in job_skills]
    total = sum(len(g["items"]) for g in groups)
    for g in reversed(groups):  # cap the list: drop skills the job doesn't ask for, last categories first
        for item in reversed(list(g["items"])):
            if total <= SKILLS_MAX:
                break
            if not any(ats.has_keyword(item.lower(), k) or ats.has_keyword(k, item.lower()) for k in job_lower):
                g["items"].remove(item)
                total -= 1
                changed.add(f"skills the job doesn't ask for (kept {SKILLS_MAX})")
    if changed:
        notes.append("Skills: removed " + ", ".join(sorted(changed)) + ".")
    r["skills"] = [(f"{g['label']}: " if g["label"] else "") + ", ".join(g["items"]) for g in groups if g["items"]]

    # Spelling and dates
    before = (r["headline"], r["summary"], [j["title"] for j in r["experience"]])
    r["headline"], r["summary"] = _full_stack(r["headline"]), _full_stack(r["summary"])
    for j in r["experience"]:
        j["title"] = _full_stack(j["title"])
        j["bullets"] = [_full_stack(b) for b in j["bullets"]]
        j["dates"] = _date_range(j["dates"])
    for e in r["education"]:
        e["dates"] = _date_range(e["dates"])
    if before != (r["headline"], r["summary"], [j["title"] for j in r["experience"]]):
        notes.append("Spelling: \"Full-Stack\" normalised.")
    return r, notes


# --------------------------------------------------------------------------- #
# Review before export
# --------------------------------------------------------------------------- #

def review(resume: dict, master: str, confirmations: dict, analysis: dict | None, settings: dict) -> list[str]:
    """Messages for the candidate to check before export (empty list: nothing to review)."""
    issues: list[str] = []
    master_lower = master.lower()
    text_lower = ats.resume_text(resume).lower()

    # Skills the candidate said No to must not appear at all
    for key, c in confirmations.items():
        if c["level"] == "No" and ats.has_keyword(text_lower, c["skill"]) and not ats.has_keyword(master_lower, key):
            issues.append(f"\"{c['skill']}\" is in the resume, but you said No to it.")

    # Fabrication tells
    if main._GENDER_MARKER.search(resume["headline"]):
        issues.append("The headline still contains the job ad's gender marker, e.g. (m/f/d).")
    hedged = [item for _, item in skill_items(resume) if HEDGE.search(item)] + \
        ([resume["summary"]] if HEDGE.search(resume["summary"]) else [])
    if hedged:
        issues.append("Bracket explanations a recruiter reads as stretching: " + "; ".join(h[:60] for h in hedged))
    buckets = [g["label"] for g in render._skill_groups(resume["skills"]) if g["label"].lower() in BUCKETS]
    if buckets:
        issues.append("Catch-all skills line: " + ", ".join(buckets) + ". List those skills in a real category.")
    span = experience_years(resume["experience"])
    claimed = [int(m.group(1)) for m in YEARS.finditer(f"{resume['headline']} {resume['summary']}")]
    if span and claimed and max(claimed) > span:
        issues.append(f"Claims {max(claimed)}+ years, but your dated roles span {span} years.")

    # Skills hygiene
    seen: dict[str, str] = {}
    for label, item in skill_items(resume):
        name = _clean(item)
        if name in seen:
            issues.append(f"Duplicate skill: \"{item}\" (under {seen[name] or 'Skills'} and {label or 'Skills'}).")
        seen.setdefault(name, label)
    if len(seen) > SKILLS_MAX:
        issues.append(f"{len(seen)} skills listed; keep about 15-{SKILLS_MAX}, job skills first.")

    # Structure
    companies = [j["company"].strip().lower() for j in resume["experience"] if j["company"].strip()]
    repeated = sorted({c for c in companies if companies.count(c) > 1})
    if repeated:
        issues.append("Employer listed more than once: " + ", ".join(repeated)
                      + ". Merge the roles into one entry (e.g. \"Engineer → Senior Engineer\").")
    for section, field in (("education", "education"), ("certification", "certifications"),
                           ("languages", "languages")):
        if re.search(rf"\b{section}s?\b", master_lower) and not resume.get(field):
            issues.append(f"Your master resume has a {section.title()} section, but this version has none.")
    if len(resume["headline"]) > HEADLINE_MAX:
        issues.append(f"Headline is {len(resume['headline'])} characters, so it may wrap "
                      f"(keep it under {HEADLINE_MAX}).")
    issues += _location_issues(resume.get("availability", ""), settings)

    # Numbers that aren't in the master resume
    master_numbers = {n.replace(",", "") for n in NUMBER.findall(master)}
    texts = [resume["headline"], resume["summary"]] + [b for j in resume["experience"] for b in j["bullets"]] \
        + [b for p in resume["projects"] for b in p["bullets"]]
    year_claims = {m.group(1) for t in texts[:2] for m in YEARS.finditer(t)}
    new_numbers = sorted({n for t in texts for n in NUMBER.findall(t)
                          if n.replace(",", "") not in master_numbers and n not in year_claims})
    if new_numbers:
        issues.append("Numbers not found in your master resume: " + ", ".join(new_numbers)
                      + ". Keep them only if they are real.")
    return issues


def _location_issues(line: str, settings: dict) -> list[str]:
    job_type, low = settings.get("job_type"), line.lower()
    if job_type == "Remote":
        if "remote" not in low:
            return [f"Job type is Remote, but the line under your contact details is \"{line or '(empty)'}\"."]
        if any(w in low for w in ("relocat", "visa", "sponsor")):
            return [f"Remote job, but the location line mentions relocation or a visa: \"{line}\"."]
    elif job_type == "Relocation":
        place = (settings.get("location") or "").split(",")[0].strip().lower()
        if "relocat" not in low or (place and place not in low):
            return [f"Relocation job{' to ' + settings['location'] if place else ''}, but the location line is "
                    f"\"{line or '(empty)'}\"."]
    elif job_type and line:
        return [f"Local job, so no location line is needed, but the resume says \"{line}\"."]
    return []
