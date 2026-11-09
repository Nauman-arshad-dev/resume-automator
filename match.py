"""
Job-match estimate: how well a resume covers a job's requirements, weighted by importance.

Uses the job analysis (each requirement has a category and a 1-5 weight) and
synonym-aware matching, so "JS" counts for "JavaScript" and "SEO" for "search
engine optimisation". The exact-wording coverage is reported too, because many
ATS products only match literal terms.

This is an internal estimate, not the score any employer's ATS will compute.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import ats
import evidence

DISCLAIMER = ("Internal estimate from this app's own checks. Employers' ATS products score differently, "
              "and no score guarantees an interview.")
CATEGORY_FACTOR = {"essential": 2.0, "domain": 1.5, "preferred": 1.0, "additional": 0.5}
CATEGORY_LABEL = {"essential": "Essential", "preferred": "Preferred", "domain": "Domain", "additional": "Additional"}


@dataclass
class Requirement:
    skill: str
    category: str
    weight: int
    found: bool          # under any name (synonyms, acronyms)
    exact: bool          # in the posting's exact wording
    status: str = ""     # evidence status against the profile


@dataclass
class Match:
    overall: int
    requirements: list[Requirement] = field(default_factory=list)
    title_match: bool = False
    coverage: dict[str, int] = field(default_factory=dict)   # category -> weighted % found
    counts: dict[str, tuple[int, int]] = field(default_factory=dict)  # category -> (found, total)
    exact_pct: int = 0
    synonym_pct: int = 0

    @property
    def missing(self) -> list[Requirement]:
        return [r for r in self.requirements if not r.found]


def category_of(skill: dict) -> str:
    cat = (skill.get("category") or "").strip().lower()
    if cat in CATEGORY_FACTOR:
        return cat
    return "essential" if (skill.get("importance") or "").lower().startswith("req") else "preferred"


def job_match(resume: dict, analysis: dict | None, job_title: str = "", extra_keywords: list[str] | None = None,
              master: str = "", confirmations: dict | None = None) -> Match:
    text_norm = evidence.normalise(ats.resume_text(resume))
    text_lower = ats.resume_text(resume).lower()
    master_norm = evidence.normalise(master) if master else ""
    reqs: list[Requirement] = []
    seen: set[str] = set()
    for s in (analysis or {}).get("skills", []):
        key = s["skill"].strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        names = [s["skill"], *s.get("synonyms", [])]
        found = any(evidence.mentions(text_norm, n, normalised=True) for n in names)
        weight = s.get("weight") or 3
        reqs.append(Requirement(s["skill"], category_of(s), max(1, min(5, int(weight))), found,
                                ats.has_keyword(text_lower, s["skill"]),
                                evidence.status(s["skill"], master, confirmations, master_norm) if master else ""))
    for k in extra_keywords or []:  # keywords added later, e.g. from an external checker
        if k.strip() and k.strip().lower() not in seen and k.strip().lower() != job_title.strip().lower():
            seen.add(k.strip().lower())
            reqs.append(Requirement(k, "additional", 2, evidence.mentions(text_norm, k, normalised=True),
                                    ats.has_keyword(text_lower, k),
                                    evidence.status(k, master, confirmations, master_norm) if master else ""))

    titles = [t for t in [job_title, *((analysis or {}).get("title_variants") or [])] if t.strip()]
    top = evidence.normalise(" ".join([resume["headline"], resume["summary"]] +
                                      [j["title"] for j in resume["experience"]]))
    title_match = any(evidence.mentions(top, t, normalised=True) for t in titles) if titles else False

    coverage, counts = {}, {}
    for cat in CATEGORY_FACTOR:
        group = [r for r in reqs if r.category == cat]
        if group:
            total = sum(r.weight for r in group)
            coverage[cat] = round(100 * sum(r.weight for r in group if r.found) / total)
            counts[cat] = (sum(r.found for r in group), len(group))
    total = sum(r.weight * CATEGORY_FACTOR[r.category] for r in reqs)
    req_score = 100 * sum(r.weight * CATEGORY_FACTOR[r.category] for r in reqs if r.found) / total if total else 0
    overall = round(0.85 * req_score + 15 * title_match) if reqs else (100 if title_match else 0)
    n = len(reqs) or 1
    return Match(overall=overall, requirements=reqs, title_match=title_match, coverage=coverage, counts=counts,
                 exact_pct=round(100 * sum(r.exact for r in reqs) / n),
                 synonym_pct=round(100 * sum(r.found for r in reqs) / n))


def as_text(m: Match) -> str:
    """Summary for a revision prompt."""
    lines = [f"Job-match estimate: {m.overall}/100 (title in headline/summary: {'yes' if m.title_match else 'no'})."]
    for cat, pct in m.coverage.items():
        found, total = m.counts[cat]
        lines.append(f"- {CATEGORY_LABEL[cat]} requirements: {found}/{total} ({pct}% by weight)")
    missing = [f"{r.skill} [{r.category}, {r.status or 'unknown'}]" for r in m.missing]
    if missing:
        lines.append("Requirements not shown (add only where the evidence rules allow): " + ", ".join(missing))
    return "\n".join(lines)
