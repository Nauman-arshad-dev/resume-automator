"""
Quality gate: the final validation before a resume is exported.

gate() combines the review checks (review.py), the evidence checks (evidence.py)
and its own checks for dates, duplicate bullets, keyword stuffing, buzzwords,
contact details and basic language slips. "serious" issues block export until
the candidate has reviewed them; "warning" issues are advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import ats
import evidence
import review

SERIOUS, WARNING = "serious", "warning"
STUFFING_LIMIT = 4  # a job keyword more often than this reads as stuffing


@dataclass
class Issue:
    severity: str
    area: str
    message: str


def _dates(text: str) -> tuple[tuple[int, int] | None, tuple[int, int] | None, bool]:
    """(start, end, ongoing) of 'Jan 2020 – Mar 2022' / '2019 – Present'."""
    pieces = review.DATE_SEP.split(text.strip(), maxsplit=1)
    start = review._point(pieces[0], end=False) if pieces[0].strip() else None
    tail = pieces[1] if len(pieces) > 1 else ""
    ongoing = bool(review.PRESENT.search(tail))
    end = None if ongoing else review._point(tail, end=True) if tail else None
    return start, end, ongoing


def date_issues(resume: dict) -> list[Issue]:
    issues, today = [], (date.today().year, date.today().month)
    entries = [("Experience", " at ".join(x for x in (j["title"], j["company"]) if x.strip()) or "a role", j["dates"])
               for j in resume["experience"]]
    entries += [("Education", e["degree"] or e["school"], e["dates"]) for e in resume["education"]]
    for area, name, dates in entries:
        if not dates.strip():
            if area == "Experience":
                issues.append(Issue(WARNING, "Dates", f"No dates for {name}."))
            continue
        start, end, _ = _dates(dates)
        if start is None:
            issues.append(Issue(WARNING, "Dates", f"Dates for {name} can't be read: \"{dates}\". "
                                                  "Use \"Mon YYYY – Mon YYYY\"."))
            continue
        if start > today and area == "Experience":
            issues.append(Issue(SERIOUS, "Dates", f"{name} starts in the future ({dates})."))
        if end and end < start:
            issues.append(Issue(SERIOUS, "Dates", f"{name} ends before it starts ({dates})."))
    ongoing = [j for j in resume["experience"] if _dates(j["dates"])[2]]
    if len(ongoing) > 2:
        issues.append(Issue(WARNING, "Dates", f"{len(ongoing)} roles are marked as current; check that's right."))
    return issues


def duplicate_bullets(resume: dict, threshold: float = 0.8) -> list[Issue]:
    bullets = [b for j in resume["experience"] for b in j["bullets"]] + [b for p in resume["projects"]
                                                                         for b in p["bullets"]]
    toks = [evidence.tokens(b) for b in bullets]
    issues = []
    for i in range(len(bullets)):
        for k in range(i + 1, len(bullets)):
            a, b = toks[i], toks[k]
            if a and b and len(a & b) / len(a | b) >= threshold:
                issues.append(Issue(WARNING, "Duplicates", f"Two bullets say nearly the same thing: "
                                                           f"\"{bullets[i][:70]}…\""))
    return issues


def language_issues(resume: dict) -> list[Issue]:
    issues = []
    texts = [resume["summary"]] + [b for j in resume["experience"] for b in j["bullets"]] + \
        [b for p in resume["projects"] for b in p["bullets"]]
    for t in texts:
        if m := re.search(r"\b(\w+)\s+\1\b", t, re.IGNORECASE):
            if not m.group(1).isdigit():
                issues.append(Issue(WARNING, "Language", f"Repeated word \"{m.group(0)}\" in: \"{t[:70]}…\""))
        if t and t[0].islower():
            issues.append(Issue(WARNING, "Language", f"Starts with a lowercase letter: \"{t[:70]}…\""))
    bullets = [b.strip() for j in resume["experience"] for b in j["bullets"] if b.strip()]
    if bullets:
        with_dot = sum(b.endswith(".") for b in bullets)
        if 0 < with_dot < len(bullets) and min(with_dot, len(bullets) - with_dot) >= 2:
            issues.append(Issue(WARNING, "Language", "Some bullets end with a full stop and some don't; "
                                                     "pick one style."))
    return issues


def gate(resume: dict, master: str, confirmations: dict | None, analysis: dict | None, settings: dict,
         job_skills: list[str] | None = None, pages: int | None = None, max_pages: int = 2) -> list[Issue]:
    """Every issue to review before export, serious first."""
    confirmations = confirmations or {}
    mode = settings.get("content_mode") or "Strict"
    strict = mode != "Flexible"
    job_skills = job_skills or []
    issues: list[Issue] = []

    for msg in review.review(resume, master, confirmations, analysis, settings):
        serious = "you said No" in msg or msg.startswith("Numbers not found")
        issues.append(Issue(SERIOUS if serious else WARNING, "Review", msg))

    # Contact details
    contact = " ".join(resume["contact"])
    if not ats.EMAIL.search(contact):
        issues.append(Issue(SERIOUS, "Contact", "No email address in the contact details."))
    if not ats.PHONE.search(contact):
        issues.append(Issue(WARNING, "Contact", "No phone number in the contact details."))
    if not resume["name"].strip():
        issues.append(Issue(SERIOUS, "Contact", "The resume has no name."))

    issues += date_issues(resume)

    # Evidence: skills and bullets the profile doesn't support
    if master:
        rows = evidence.skill_evidence(review.skill_items(resume), master, confirmations)
        unbacked = [r["skill"] for r in rows if r["status"] not in evidence.BACKED]
        if unbacked:
            issues.append(Issue(SERIOUS if strict else WARNING, "Evidence",
                                "Skills not shown in your profile and not confirmed: " + ", ".join(unbacked)
                                + (". Remove them or confirm them in step 3." if strict
                                   else ". Flexible mode lists them; be ready to discuss each one.")))
        for row in evidence.trace_bullets(resume, master, job_skills, confirmations):
            if row["unsupported_terms"]:
                issues.append(Issue(SERIOUS if strict else WARNING, "Evidence",
                                    f"Bullet claims {', '.join(row['unsupported_terms'])}, which your profile "
                                    f"doesn't show: \"{row['text'][:80]}…\""))
            elif row["status"] == "not found":
                issues.append(Issue(WARNING, "Evidence", f"Can't trace this to your profile; check it's accurate: "
                                                         f"\"{row['text'][:80]}…\""))

    # Keyword stuffing and buzzwords
    text_lower = ats.resume_text(resume).lower()
    stuffed = [k for k in dict.fromkeys(job_skills) if ats._count(text_lower, k) > STUFFING_LIMIT]
    if stuffed:
        issues.append(Issue(WARNING, "Keywords", "Repeated so often it reads as keyword stuffing: "
                                                 + ", ".join(stuffed) + "."))
    buzz = [b for b in ats.BUZZWORDS if b in text_lower]
    if buzz:
        issues.append(Issue(WARNING, "Language", "Empty buzzwords: " + ", ".join(buzz) + "."))

    issues += duplicate_bullets(resume) + language_issues(resume)
    if pages and pages > max_pages:
        issues.append(Issue(WARNING, "Layout", f"The PDF has {pages} pages; the target is {max_pages}. "
                                               "Try Text size: Small, or Auto-improve."))
    return sorted(issues, key=lambda i: i.severity != SERIOUS)
