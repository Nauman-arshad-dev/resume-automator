"""
Checks to review before exporting a tailored resume.

These catch what an ATS score can't: a skill the candidate never confirmed, a
duplicate skill, a headline that wraps, a location line left over from another
job, or a number that isn't in the master resume. Each check returns plain
messages for the app to show next to the download.
"""

from __future__ import annotations

import re

import ats
import render

SKILLED = ("Strong", "Basic")              # confirmation levels that may appear as skills
WORKING, LEARNING = "working knowledge", "currently learning"
HEADLINE_MAX = 95
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _clean(item: str) -> str:
    """'GCP (via strong AWS experience)' -> 'gcp'."""
    return re.sub(r"\s*\(.*?\)", "", item).strip().lower()


def _variants(item: str) -> list[str]:
    """'Amazon Web Services (AWS: S3, Lambda)' -> ['amazon web services', 'aws', 's3', 'lambda']."""
    inner = " ".join(re.findall(r"\((.*?)\)", item))
    inner = re.sub(r"\b(via|e\.g\.|basics?|strong|experience)\b", " ", inner, flags=re.I)
    return [_clean(item)] + [p.strip().lower() for p in re.split(r"[,:/;]", inner) if p.strip()]


def skill_items(resume: dict) -> list[tuple[str, str]]:
    """(category, item) for every entry in the Skills section."""
    return [(g["label"], item) for g in render._skill_groups(resume["skills"]) for item in g["items"]]


def _level(confirmations: dict, skill: str) -> str | None:
    c = confirmations.get(skill.strip().lower())
    return c["level"] if c else None


def ensure_confirmed_lines(resume: dict, confirmations: dict, job_skills: list[str]) -> dict:
    """Put Basic skills on the Working knowledge line and Learning ones on Currently learning, if missing.

    Only for this job's skills. The resume is copied, never changed in place.
    """
    text = " ".join(resume["skills"]).lower()
    lines = {WORKING: [], LEARNING: []}
    for skill in job_skills:
        level = _level(confirmations, skill)
        if level == "Basic" and not ats.has_keyword(text, skill):
            lines[WORKING].append(skill)
        elif level == "Learning" and not ats.has_keyword(text, skill):
            lines[LEARNING].append(skill)
    if not any(lines.values()):
        return resume
    skills = list(resume["skills"])
    for label, items in lines.items():
        if not items:
            continue
        idx = next((i for i, s in enumerate(skills) if s.lower().startswith(label)), None)
        if idx is None:
            skills.append(f"{label.capitalize()}: " + ", ".join(items))
        else:
            skills[idx] = skills[idx].rstrip(" ,") + ", " + ", ".join(items)
    return {**resume, "skills": skills}


def review(resume: dict, master: str, confirmations: dict, analysis: dict | None, settings: dict) -> list[str]:
    """Messages for the candidate to check before export (empty list: nothing to review)."""
    issues: list[str] = []
    master_lower = master.lower()
    transferable = {_clean(s["skill"]) for s in (analysis or {}).get("skills", []) if s["group"] == "B"}

    # Skills that neither the master resume nor the candidate's answers support
    seen: dict[str, str] = {}
    for label, item in skill_items(resume):
        name = _clean(item)
        if not name:
            continue
        if name in seen:
            issues.append(f"Duplicate skill: \"{item}\" is listed under both {seen[name] or 'Skills'} "
                          f"and {label or 'Skills'}.")
        seen.setdefault(name, label)
        if label.lower() == LEARNING or _level(confirmations, name) in ("No", "Learning"):
            continue  # Learning / No skills are checked below
        supported = (any(ats.has_keyword(master_lower, v) for v in _variants(item))
                     or _level(confirmations, name) in SKILLED or name in transferable)
        if not supported:
            issues.append(f"\"{item}\" is not in your master resume and you haven't confirmed it.")

    # Skills answered No / Learning (or never answered) must not appear as experience
    body = ats.resume_text({**resume, "skills": [s for s in resume["skills"]
                                                 if not s.lower().startswith(LEARNING)]}).lower()
    outside_skills = " ".join([resume["headline"], resume["summary"]]
                              + [b for j in resume["experience"] for b in j["bullets"]]).lower()
    for s in (analysis or {}).get("skills", []):
        skill, level = s["skill"], _level(confirmations, s["skill"])
        if s["group"] in ("A", "B") or ats.has_keyword(master_lower, skill):
            continue
        if level in (None, "No", "Learning") and ats.has_keyword(body, skill):
            answer = {"No": "said No to", "Learning": "are only learning"}.get(level, "haven't confirmed")
            issues.append(f"\"{skill}\" appears as experience, but you {answer} it.")
        elif level == "Basic" and ats.has_keyword(outside_skills, skill):
            issues.append(f"\"{skill}\" (Basic) appears in your headline, summary or a bullet; keep it on the "
                          "Working knowledge line unless you really used it there.")

    if len(resume["headline"]) > HEADLINE_MAX:
        issues.append(f"Headline is {len(resume['headline'])} characters, so it may wrap "
                      f"(keep it under {HEADLINE_MAX}).")

    issues += _location_issues(resume.get("availability", ""), settings)

    # Numbers that aren't in the master resume
    master_numbers = {n.replace(",", "") for n in NUMBER.findall(master)}
    texts = [resume["headline"], resume["summary"]] + [b for j in resume["experience"] for b in j["bullets"]] \
        + [b for p in resume["projects"] for b in p["bullets"]]
    new_numbers = sorted({n for t in texts for n in NUMBER.findall(t) if n.replace(",", "") not in master_numbers})
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
