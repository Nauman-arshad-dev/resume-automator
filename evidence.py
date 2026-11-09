"""
Evidence and accuracy checks: is every claim in a tailored resume backed by the profile?

Deterministic (no AI), so it is a safety net whatever the model wrote:
    status(term, ...)      verified | confirmed | needs confirmation | unsupported | excluded
    allowed(term, ...)     may this requirement appear in the resume, given the content mode?
    skill_evidence(...)    one row per Skills item
    trace_bullets(...)     one row per bullet: the closest profile line and how much of the bullet it supports
    mentions(text, term)   keyword match that understands synonyms, acronyms, spelling variants

"verified"           the profile (master resume) shows it
"confirmed"          the candidate confirmed it (Strong / Basic) although the profile doesn't show it
"needs confirmation" not in the profile and not answered yet
"reworded"           a description the profile supports in other words (every name in it is in the profile)
"unsupported"        not in the profile and answered Learning (not claimable)
"excluded"           the candidate answered No
"""

from __future__ import annotations

import re
from functools import lru_cache

# Groups of names that ATS systems and recruiters treat as the same requirement.
SYNONYM_GROUPS: list[tuple[str, ...]] = [
    ("javascript", "js", "ecmascript"), ("typescript", "ts"), ("node.js", "nodejs", "node"),
    ("react", "react.js", "reactjs"), ("vue", "vue.js", "vuejs"), ("angular", "angularjs", "angular.js"),
    ("next.js", "nextjs"), ("express", "express.js", "expressjs"), (".net", "dotnet", "asp.net"),
    ("c#", "csharp", "c sharp"), ("golang", "go"), ("postgresql", "postgres", "psql"),
    ("mssql", "sql server", "microsoft sql server"), ("mongodb", "mongo"),
    ("amazon web services", "aws"), ("google cloud platform", "google cloud", "gcp"), ("microsoft azure", "azure"),
    ("kubernetes", "k8s"), ("ci/cd", "continuous integration", "continuous delivery", "continuous deployment"),
    ("machine learning", "ml"), ("artificial intelligence", "ai"), ("natural language processing", "nlp"),
    ("large language models", "llm", "llms"), ("rest", "restful", "rest api", "restful api", "rest apis",
                                                 "restful apis", "restful services"),
    ("user experience", "ux"), ("user interface", "ui"), ("ui/ux", "ux/ui"),
    ("search engine optimization", "seo"), ("search engine marketing", "sem"), ("pay per click", "ppc", "pay-per-click"),
    ("customer relationship management", "crm"), ("key performance indicators", "kpi", "kpis"),
    ("return on investment", "roi"), ("business intelligence", "bi"), ("power bi", "powerbi"),
    ("microsoft excel", "excel", "ms excel"), ("microsoft office", "ms office", "office 365", "microsoft 365"),
    ("google analytics", "ga4"), ("accounts payable", "ap"), ("accounts receivable", "ar"),
    ("generally accepted accounting principles", "gaap", "us gaap"),
    ("international financial reporting standards", "ifrs"), ("certified public accountant", "cpa"),
    ("chartered accountant", "aca", "acca"), ("enterprise resource planning", "erp"),
    ("project management professional", "pmp"), ("project management", "project manager"),
    ("customer service", "customer support", "customer care"), ("service level agreement", "sla", "slas"),
    ("customer satisfaction", "csat"), ("net promoter score", "nps"),
    ("human resources", "hr"), ("applicant tracking system", "ats"), ("learning and development", "l&d"),
    ("electronic health records", "ehr", "emr", "electronic medical records"),
    ("quality assurance", "qa"), ("test automation", "automated testing"),
    ("infrastructure as code", "iac"), ("site reliability engineering", "sre"),
    ("object-oriented programming", "oop", "object oriented programming"),
    ("agile", "scrum", "kanban"), ("b2b", "business-to-business"), ("b2c", "business-to-consumer"),
    ("software as a service", "saas"), ("data visualization", "data visualisation", "dashboards"),
    ("extract, transform, load", "etl"), ("application programming interface", "api", "apis"),
]
STOPWORDS = set("""a an and are as at be by for from has have in into is it its of on or that the their this to was
were will with using use used via per across over under within our we you your they them than then also more most
new key all any each both""".split())
CLAIMABLE = ("Strong", "Basic")
BACKED = ("verified", "reworded", "confirmed")
AMBIGUOUS = {"go", "ar", "ap", "ts", "ai", "ui", "bi", "hr", "qa", "ml", "sem", "aca", "node", "rest", "api",
             "apis", "agile", "scrum", "kanban", "dashboards", "ats", "ga4", "sla", "slas"}


# --------------------------------------------------------------------------- #
# Text normalisation and synonym-aware matching
# --------------------------------------------------------------------------- #

def normalise(text: str) -> str:
    """Lowercase, British -> American spelling, hyphens as spaces, curly quotes straightened."""
    t = text.lower().replace("’", "'").replace("–", " ").replace("—", " ")
    t = t.replace("isation", "ization")  # optimisation -> optimization
    t = re.sub(r"(?<=[a-z])yse\b", "yze", t)  # analyse -> analyze
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)
    return t


@lru_cache(maxsize=4096)
def variants(term: str) -> frozenset[str]:
    """Every name a term may appear under: 'Search Engine Optimization (SEO)' -> {..., 'seo'}."""
    base = normalise(term).strip()
    out = {base}
    if m := re.match(r"(.+?)\s*\((.+)\)$", base):  # "full term (ACRONYM)"
        out |= {m.group(1).strip(), m.group(2).strip()}
    for v in list(out):
        if re.search(r"[a-z]\.?js$", v):           # react.js / reactjs -> react
            out.add(re.sub(r"\.?js$", "", v))
        if v.endswith("s") and len(v) > 4 and not v.endswith("ss"):
            out.add(v[:-1])                          # apis -> api, dashboards -> dashboard
        if v.startswith("microsoft "):
            out.add(v[len("microsoft "):])
    for group in SYNONYM_GROUPS:
        if out & set(group):
            out |= set(group)
    return frozenset(v for v in out if v)


def _find(text_norm: str, phrase: str) -> bool:
    plural = r"(?:e?s)?" if len(phrase) >= 3 and phrase[-1].isalpha() else ""  # "VPC" also matches "VPCs"
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + plural + r"(?![a-z0-9])", text_norm) is not None


def mentions(text: str, term: str, normalised: bool = False) -> bool:
    """Does text mention term under any of its names? Pass normalised=True if text came from normalise()."""
    text_norm = text if normalised else normalise(text)
    for v in variants(term):
        # ambiguous short aliases ("go to market", "AR" = augmented reality) only count for the term itself
        if v in AMBIGUOUS and v != normalise(term).strip():
            continue
        if _find(text_norm, v):
            return True
    return False


# --------------------------------------------------------------------------- #
# Claims
# --------------------------------------------------------------------------- #

def _level(confirmations: dict, term: str) -> str | None:
    c = (confirmations or {}).get(term.strip().lower())
    if c:
        return c["level"]
    for key, c in (confirmations or {}).items():  # confirmed under another name ("JS" for "JavaScript")
        if key in variants(term):
            return c["level"]
    return None


def status(term: str, master: str, confirmations: dict | None, master_norm: str | None = None) -> str:
    level = _level(confirmations or {}, term)
    if level == "No":
        return "excluded"
    master_norm = master_norm if master_norm is not None else normalise(master)
    if mentions(master_norm, term, normalised=True):
        return "verified"
    words = tokens(term)
    if len(words) >= 2 and words <= _master_tokens(master_norm):  # "stakeholder reporting" from separate mentions
        return "verified"
    if level in CLAIMABLE:
        return "confirmed"
    if level == "Learning":
        return "unsupported"
    if _reworded(term, master_norm):
        return "reworded"
    return "needs confirmation"


def named_parts(term: str) -> list[str]:
    """Words that look like product, standard or acronym names: 'Next.js', 'IFRS', '16', 'CSAT', 'SAP'.

    Plain words ('budget management', 'paid growth') are descriptions, which the candidate may
    word differently from the profile."""
    words = re.findall(r"[\w.+#/$-]+", term)
    return [w for i, w in enumerate(words)
            if re.search(r"\d|[.+#/$]", w) or re.search(r"[A-Z]", w[1:]) or (i > 0 and w[:1].isupper())
            or (len(words) == 1 and w[:1].isupper())]


def _reworded(term: str, master_norm: str) -> bool:
    """A description the profile supports in other words: every name in it is in the profile, and at
    least half of its content words are ('Dashboard development' from 'Built Power BI dashboards')."""
    if any(not mentions(master_norm, w, normalised=True) for w in named_parts(term)):
        return False
    words = tokens(term)
    return len(words) >= 2 and len(words & _master_tokens(master_norm)) / len(words) >= 0.5


@lru_cache(maxsize=8)
def _master_tokens(master_norm: str) -> frozenset[str]:
    return frozenset(tokens(master_norm))


def allowed(term: str, master: str, confirmations: dict | None, mode: str = "Strict",
            master_norm: str | None = None) -> bool:
    """May the requirement appear in the resume? Strict: backed by the profile or confirmed. Flexible: anything but No."""
    s = status(term, master, confirmations, master_norm)
    if mode == "Flexible":
        return s != "excluded"
    return s in BACKED


def skill_evidence(skill_items: list[tuple[str, str]], master: str, confirmations: dict | None) -> list[dict]:
    """One row per Skills item: {"category", "skill", "status"}."""
    master_norm = normalise(master)
    return [{"category": label, "skill": item, "status": status(_bare(item), master, confirmations, master_norm)}
            for label, item in skill_items]


def _bare(item: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", item).strip() or item


# --------------------------------------------------------------------------- #
# Bullet traceability
# --------------------------------------------------------------------------- #

def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+#.]*[a-z0-9+#]|[a-z0-9]", normalise(text))
    return {_stem(w) for w in words if w not in STOPWORDS and (len(w) >= 3 or w.isdigit() or "+" in w or "#" in w)}


def _profile_lines(master: str) -> list[str]:
    lines = []
    for raw in master.splitlines():
        for part in re.split(r"(?<=[.;])\s+(?=[A-Z])", raw):
            part = part.strip(" •-*\t")
            if len(part.split()) >= 3:
                lines.append(part)
    return lines


def trace_bullets(resume: dict, master: str, job_skills: list[str] | None = None,
                  confirmations: dict | None = None) -> list[dict]:
    """Every bullet with the profile line that supports it best.

    support = share of the bullet's content words found in that profile line (or, when the
    bullet merges several lines, in the whole profile). status: "verified" (support >= 0.6),
    "reworded" (0.35-0.6: check it), "not found" (< 0.35). unsupported_terms: job requirements the
    bullet mentions that the profile doesn't show and the candidate didn't confirm as Strong.
    """
    lines = _profile_lines(master)
    line_tokens = [tokens(ln) for ln in lines]
    master_tokens = set().union(*line_tokens) if line_tokens else set()
    master_norm = normalise(master)
    rows = []
    entries = [("Summary", resume.get("summary", ""), [resume.get("summary", "")])] if resume.get("summary") else []
    entries += [(j["company"] or j["title"], "", j["bullets"]) for j in resume["experience"]]
    entries += [(p["name"], "", p["bullets"]) for p in resume["projects"]]
    for where, _, bullets in entries:
        for bullet in bullets:
            bt = tokens(bullet)
            if not bt:
                continue
            best, best_line = 0.0, ""
            for ln, lt in zip(lines, line_tokens):
                share = len(bt & lt) / len(bt)
                if share > best:
                    best, best_line = share, ln
            overall = len(bt & master_tokens) / len(bt)
            support = max(best, overall * 0.85)
            terms = [s for s in (job_skills or []) if mentions(bullet, s)
                     and status(s, master, confirmations, master_norm) not in ("verified", "reworded")
                     and _level(confirmations or {}, s) != "Strong"]
            st = "verified" if support >= 0.6 else "reworded" if support >= 0.35 else "not found"
            rows.append({"where": where, "text": bullet, "source": best_line, "support": round(support, 2),
                         "status": st, "unsupported_terms": terms})
    return rows
