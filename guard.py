"""
Source guard: every tailored resume may say only what the master profile, the candidate's saved
answers and their confirmed skills say.

enforce() runs after every Claude reply and repairs what can be repaired mechanically:
  1. the availability line comes from this job's Job Type only; stale relocation/visa text is removed
  2. job-ad work areas, departments and hiring steps are never skills
  3. master skills the resume itself uses are kept in Skills; Basic answers go under "Working knowledge"
  4. bullets that contradict a saved answer are corrected ("worked solo", "built for clients")
  5. counting numbers that aren't in the sources ("2 LLM APIs") are removed
  7. job-ad tags and bracket explanations are removed; the headline is real, short and one line
  9. every link in the profile is in the contact line

checks() then lists what is still new compared with the sources, as numbered checks 1-7. Serious
items block export until the candidate fixes or approves them. new_words() highlights, for the
"diff vs master" view, every word of the resume that isn't in the sources.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import evidence
import main
import render

SERIOUS, WARNING = "serious", "warning"
HEADLINE_MAX = 95
SKILLS_MAX = 36  # restored profile skills stop here; job-mentioned ones first
CHECK_NAMES = {
    1: "Location line matches this job",
    2: "Skills come from your profile or your answers",
    3: "Your relevant skills are kept",
    4: "Nothing contradicts your saved answers",
    5: "Every number is in your profile or answers",
    6: "No experience you haven't confirmed",
    7: "No job-ad text, brackets or missing links",
}

# --------------------------------------------------------------------------- #
# 1. Availability line: from the Job Type, never from an earlier job
# --------------------------------------------------------------------------- #

# "Open to relocation to Auckland/Wellington (AEWV sponsorship)", "Open to remote work (UTC+5)",
# "Visa sponsorship required", "Willing to relocate"...
AVAILABILITY = re.compile(
    r"open to (?:remote work|relocation|relocate)[^|\n]*|willing to relocate[^|\n]*|"
    r"(?:requires?|needs?|seeking)?\s*(?:visa|work permit)\s+sponsorship[^|\n]*|"
    r"relocating to [^|\n]*|available for relocation[^|\n]*",
    re.IGNORECASE)
STALE_WORDS = re.compile(r"\b(relocat\w*|sponsorship|visa|work permit|open to remote)\b", re.IGNORECASE)

VISAS = {  # that country's usual skilled-work visa, for "Open to relocation to <place> (<visa> sponsorship)"
    "germany": "EU Blue Card", "deutschland": "EU Blue Card", "austria": "EU Blue Card",
    "united kingdom": "Skilled Worker visa", "uk": "Skilled Worker visa", "england": "Skilled Worker visa",
    "scotland": "Skilled Worker visa", "united states": "H-1B visa", "usa": "H-1B visa", "us": "H-1B visa",
    "netherlands": "Highly Skilled Migrant visa", "canada": "work permit",
    "new zealand": "AEWV", "australia": "Skills in Demand visa", "ireland": "Critical Skills Employment Permit",
    "saudi arabia": "work visa", "united arab emirates": "work visa", "uae": "work visa", "qatar": "work visa",
    "sweden": "work permit", "denmark": "work permit", "norway": "work permit", "finland": "work permit",
    "poland": "work permit", "portugal": "work visa", "spain": "work visa", "france": "Talent Passport",
    "japan": "work visa", "singapore": "Employment Pass",
}


def availability_line(settings: dict | None, analysis: dict | None = None) -> str:
    """The line under the contact details, from this job's settings only."""
    s = settings or {}
    job_type = s.get("job_type") or ""
    if job_type == "Remote":
        tz = (s.get("timezone") or "").strip()
        return f"Open to remote work ({tz})" if tz else "Open to remote work"
    if job_type == "Relocation":
        place = (s.get("location") or (analysis or {}).get("job_location") or "").strip().strip(".")
        if not place:
            return "Open to relocation"
        visa = (s.get("visa") or "").strip()
        if not visa:
            country = place.split(",")[-1].strip().lower()
            visa = VISAS.get(country) or VISAS.get(place.lower()) or "work visa"
        visa = re.sub(r"\s*sponsorship$", "", visa, flags=re.IGNORECASE)
        return f"Open to relocation to {place} ({visa} sponsorship)"
    return ""


def clean_master(master: str) -> tuple[str, list[str]]:
    """The profile without availability lines copied from an earlier tailored resume.

    Returns (cleaned text, the removed pieces)."""
    removed: list[str] = []

    def drop(m: re.Match) -> str:
        removed.append(m.group(0).strip(" |"))
        return ""

    lines = []
    for line in master.splitlines():
        new = AVAILABILITY.sub(drop, line)
        if new != line:
            new = re.sub(r"(\s*\|\s*)+$", "", re.sub(r"\|\s*\|", "|", new)).rstrip()
            if not new.strip(" |"):
                continue
        lines.append(new)
    return "\n".join(lines), [r for r in removed if r]


# --------------------------------------------------------------------------- #
# 2. What is a skill
# --------------------------------------------------------------------------- #

# Hiring-process steps, company work areas and job families from a job ad: never the candidate's skills.
NOT_A_SKILL = re.compile(
    r"\b(assessments?|interviews?|hiring|recruit\w*|onboarding|talent pool|community|department|"
    r"training data|frontier models?|rl environments?|work areas?|our (?:team|clients|customers)|"
    r"take-home|screening|application process|fresh graduates?|job seekers?)\b",
    re.IGNORECASE)
JOB_FAMILIES = {
    "software", "data", "design", "product", "security", "infrastructure", "qa", "engineering", "development",
    "software engineering", "software development", "full stack development", "full-stack development",
    "fullstack development", "ai/ml", "ai", "ml", "technology", "it", "operations", "research", "evals",
    "big data", "machine learning", "data science",
}


def is_skill(term: str) -> bool:
    """False for a job ad's work areas, departments, job families and hiring steps."""
    t = term.strip().lower()
    return bool(t) and t not in JOB_FAMILIES and not NOT_A_SKILL.search(t)


def split_requirements(analysis: dict) -> dict:
    """Move a job analysis' non-skills (work areas, hiring steps) out of skills into not_skills."""
    skills = analysis.get("skills") or []
    keep = [s for s in skills if is_skill(s["skill"])]
    moved = [s["skill"] for s in skills if not is_skill(s["skill"])]
    return {**analysis, "skills": keep,
            "not_skills": list(dict.fromkeys(list(analysis.get("not_skills") or []) + moved))}


# --------------------------------------------------------------------------- #
# Sources: the profile, its skills and links, and the saved answers
# --------------------------------------------------------------------------- #

LINK = re.compile(
    r"(?<![@\w.])(?:https?://[^\s|,;()<>]+|(?:www\.)?(?:linkedin\.com|github\.com|gitlab\.com|bitbucket\.org|"
    r"behance\.net|dribbble\.com|kaggle\.com|medium\.com|stackoverflow\.com|huggingface\.co)/[^\s|,;()<>]+|"
    r"www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s|,;()<>]*)?)",
    re.IGNORECASE)
_HEADING = re.compile(r"^\s*[A-Z][A-Z &/-]{2,40}:?\s*$")
_TOP_LEVEL_COMMA = re.compile(r",(?![^()]*\))")  # commas outside brackets
_TOP_LEVEL_SEMI = re.compile(r";(?![^()]*\))")


def _link_key(link: str) -> str:
    return re.sub(r"^(https?://)?(www\.)?", "", link.strip().lower()).rstrip("/.")


def master_links(master: str) -> list[str]:
    """Every profile link (LinkedIn, GitHub, portfolio...), in order, without duplicates."""
    found: dict[str, str] = {}
    for m in LINK.finditer(master):
        link = m.group(0).rstrip(".")
        found.setdefault(_link_key(link), link)
    return list(found.values())


def master_skills(master: str) -> list[tuple[str, str]]:
    """(category, item) for each skill line of the profile's skills section(s)."""
    out: list[tuple[str, str]] = []
    in_skills = False
    for line in master.splitlines():
        text = line.strip()
        if _HEADING.match(text):
            in_skills = "SKILL" in text.upper() or "TECHNOLOG" in text.upper()
            continue
        if not in_skills or ":" not in text:
            continue
        label, _, rest = text.partition(":")
        for chunk in _TOP_LEVEL_SEMI.split(rest):
            parent, colon, children = chunk.partition(":")  # "Amazon Web Services (AWS): EC2, Lambda"
            names = [parent] if colon else _TOP_LEVEL_COMMA.split(parent)
            for item in (n.strip(" .") for n in names):
                if item and len(item.split()) <= 6:
                    out.append((label.strip(), item))
    return out


def sources_text(master: str, answers: list[dict] | None) -> str:
    """Everything the resume may draw facts from."""
    return master + "\n" + "\n".join(a["answer"] for a in answers or [] if a.get("answer"))


# --------------------------------------------------------------------------- #
# 4. Saved answers that override the profile
# --------------------------------------------------------------------------- #

SOLO = re.compile(r"\b(solo|alone|by myself|on my own|sole (?:developer|engineer)|only (?:developer|engineer)|"
                  r"one-person|no team|without a team|directly with the (?:ceo|founder|owner|client))\b", re.I)
TEAM_CLAIM = re.compile(r"\b(multi-team|cross-team|cross-functional|teams?|teammates|team members|"
                        r"pull requests?|code reviews?|reviewed (?:code|pull requests)|mentor\w*|"
                        r"collaborat\w+|standards consistent)\b", re.I)
CLIENT_WORK = re.compile(r"\bfor (?:clients?|customers?|a client)\b|\bclient (?:projects?|work|products?)\b", re.I)
OWN_CLAIM = re.compile(r"\b(launched|founded|co-founded|bootstrapped|my own|own products?|own saas)\b", re.I)
UNKNOWN_NUMBERS = re.compile(r"\b(?:don'?t|do not|didn'?t) (?:know|track|have)\b|\bno (?:user )?(?:numbers|metrics|data)\b"
                             r"|\bunknown\b|\bnot sure\b", re.I)
USER_COUNT = re.compile(r"\b\d[\d,.]*\s*[kKmM]?\+?\s*(?:users|customers|subscribers|downloads|installs|"
                        r"clients|accounts|members)\b", re.I)
# The whole phrase, so nothing dangles: "with 5,000 users", "serving 2k+ customers"
USER_PHRASE = re.compile(r",?\s*\b(?:with|serving|reaching|for|by|used by|across)?\s*(?:over|about|around|nearly)?\s*"
                         + USER_COUNT.pattern[2:], re.I)


@dataclass
class Rule:
    answer: str
    kind: str                       # "solo" | "for_clients" | "no_numbers"
    scope: list[str] = field(default_factory=list)  # employers / projects it is about; [] = everywhere


def _entities(text: str, resume: dict, master: str) -> list[str]:
    names = [j["company"] for j in resume["experience"] if j["company"].strip()]
    names += [re.split(r"[:(–-]", p["name"])[0].strip() for p in resume["projects"] if p["name"].strip()]
    names += re.findall(r"^\s*([A-Z][\w .&]+?)\s*[:|·]", master, re.MULTILINE)
    low = text.lower()
    found = []
    for name in names:
        core = name.strip().lower()
        if len(core) >= 3 and re.search(rf"\b{re.escape(core)}\b", low) and core not in (f.lower() for f in found):
            found.append(name.strip())
    return found


def answer_rules(answers: list[dict] | None, resume: dict, master: str = "") -> list[Rule]:
    rules = []
    for a in answers or []:
        text = f"{a.get('question', '')} {a.get('answer', '')}"
        ans = a.get("answer", "")
        scope = _entities(text, resume, master)
        if SOLO.search(ans):
            rules.append(Rule(ans, "solo", scope))
        if CLIENT_WORK.search(ans):
            rules.append(Rule(ans, "for_clients", scope))
        if UNKNOWN_NUMBERS.search(ans):
            rules.append(Rule(ans, "no_numbers", scope))
    return rules


def _in_scope(rule: Rule, where: str, text: str) -> bool:
    if not rule.scope:
        return True
    hay = f"{where} {text}".lower()
    return any(re.search(rf"\b{re.escape(s.lower())}\b", hay) for s in rule.scope)


def _clauses(bullet: str) -> list[str]:
    return [c for c in re.split(r",\s+(?=[a-z])|\s+and\s+(?=[a-z]+ed\b|[a-z]+s\b)|;\s*", bullet) if c.strip()]


def _without_team(bullet: str) -> str | None:
    """The bullet without its team claims; None when the main clause itself is a team claim."""
    parts = _clauses(bullet)
    if not parts or TEAM_CLAIM.search(parts[0]):
        return None
    kept = [p for p in parts if not TEAM_CLAIM.search(p)]
    text = ", ".join(p.strip().rstrip(".") for p in kept)
    return text[0].upper() + text[1:] + "." if bullet.rstrip().endswith(".") else text[0].upper() + text[1:]


def _for_clients(bullet: str, rule: Rule) -> str:
    fixed = re.sub(r"\b(launched|founded|co-founded|bootstrapped)\b",
                   lambda m: "Built" if m.group(0)[0].isupper() else "built", bullet, flags=re.I)
    fixed = re.sub(r"\bmy own\s+|\bown\s+(?=products?|saas)", "", fixed, flags=re.I)
    if not re.search(r"\bclients?\b", fixed, re.I):
        anchor = None
        if verb := re.search(r"\bbuilt\b", fixed, re.I):  # end of the "built <what>" phrase
            tail = re.match(r"[^,.;(]*(?:\([^)]*\))?[^,.;(]*?(?=\s+(?:covering|that|which|with|using|to|in|on|"
                            r"for|across)\b|[,.;]|$)", fixed[verb.end():])
            anchor = verb.end() + tail.end() if tail and tail.end() else None
        if anchor is None:
            names = [s for s in rule.scope if s.lower() in fixed.lower()]
            anchor = (fixed.lower().rfind(names[-1].lower()) + len(names[-1])) if names else None
        if anchor is None:
            fixed = fixed.rstrip(".") + " for clients" + ("." if fixed.endswith(".") else "")
        else:
            fixed = fixed[:anchor] + " for clients" + fixed[anchor:]
    return re.sub(r"\s{2,}", " ", fixed)


def _sections(resume: dict):
    """(where, list holding bullets) for every experience and project entry."""
    for j in resume["experience"]:
        yield j["company"] or j["title"], j["bullets"]
    for p in resume["projects"]:
        yield p["name"], p["bullets"]


def contradictions(resume: dict, rules: list[Rule]) -> list[tuple[str, str, Rule]]:
    """(where, bullet, rule) for every bullet that contradicts a saved answer."""
    out = []
    for where, bullets in _sections(resume):
        for b in bullets:
            for rule in rules:
                if not _in_scope(rule, where, b):
                    continue
                bad = ((rule.kind == "solo" and TEAM_CLAIM.search(b)) or
                       (rule.kind == "for_clients" and OWN_CLAIM.search(b)) or
                       (rule.kind == "no_numbers" and USER_COUNT.search(b)))
                if bad:
                    out.append((where, b, rule))
    for rule in rules:  # the summary may not claim your own products either
        if rule.kind == "for_clients" and OWN_CLAIM.search(resume["summary"]):
            out.append(("Summary", resume["summary"], rule))
    return out


# --------------------------------------------------------------------------- #
# 5. Numbers
# --------------------------------------------------------------------------- #

# A standalone number: not part of S3, EC2, HTML5, GPT-4, Java17; with an optional %, k/M or +
NUM = re.compile(r"(?<![\w.\-/])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(\s?%|[kKmMbB](?![a-z])|\+)?(?![\w/])")
NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                "eight": "8", "nine": "9", "ten": "10", "dozen": "12", "dozens": "12"}
YEARS_CLAIM = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs)\b", re.I)


def _base(n: str) -> str:
    return n.replace(",", "")


def _source_sentences(sources: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.;!?])\s+|\n", sources) if s.strip()]


def number_backed(number: str, sentence: str, sources: str, span: int | None = None) -> bool:
    """A number is backed when the same standalone number is in a source sentence about the same thing."""
    base = _base(number)
    if span is not None and re.search(rf"\b{re.escape(base)}\s*\+?\s*(?:years?|yrs)", sentence, re.I):
        return int(float(base)) <= span
    if re.fullmatch(r"(19|20)\d\d", base):  # a year: just has to be in the sources
        return any(_base(m.group(1)) == base for m in NUM.finditer(sources))
    context = evidence.tokens(NUM.sub(" ", sentence))
    for src in _source_sentences(sources):
        if any(_base(m.group(1)) == base for m in NUM.finditer(src)):
            if not context or context & evidence.tokens(src):
                return True
    return False


def body_texts(resume: dict) -> list[tuple[str, str]]:
    """(where, text) for the headline, summary, every bullet and project bullet."""
    out = [("Headline", resume["headline"]), ("Summary", resume["summary"])]
    for where, bullets in _sections(resume):
        out += [(where, b) for b in bullets]
    return [(w, t) for w, t in out if t.strip()]


def unbacked_numbers(resume: dict, sources: str, span: int | None) -> list[tuple[str, str, str]]:
    """(where, number, text) for each number that isn't in the sources."""
    out = []
    for where, text in body_texts(resume):
        for m in NUM.finditer(text):
            if not number_backed(m.group(1), text, sources, span):
                out.append((where, m.group(0).strip(), text))
    return out


def _drop_number(text: str, number: str) -> str:
    """'Integrated 2 LLM APIs' -> 'Integrated LLM APIs'; '1 large dataset' -> 'a large dataset'."""
    base = re.escape(number.strip())
    one = re.match(r"1(?!\d)", number.strip())
    repl = "a " if one else ""
    new = re.sub(rf"(?<![\w.\-/]){base}\s+(?=[A-Za-z])", repl, text, count=1)
    if new == text:
        new = re.sub(rf"\s*(?<![\w.\-/]){base}(?![\w/])", "", text, count=1)
    new = re.sub(r"\ba ([aeiouAEIOU])", r"an \1", new) if one else new
    return re.sub(r"\s{2,}", " ", new).strip()


def _is_count(number: str) -> bool:
    """A small bare count ('3 AI coding tools'), as opposed to a metric ('40%', '2M', '300+')."""
    return bool(re.fullmatch(r"\d{1,2}", number.strip())) and int(number) <= 20


# --------------------------------------------------------------------------- #
# 7. Job-ad tags, bracket explanations, the headline
# --------------------------------------------------------------------------- #

BUCKET_LABELS = ("working knowledge", "familiar with", "basics", "basic", "currently learning", "learning",
                 "exposure", "other", "additional", "additional skills")
WORK_MODEL = re.compile(r"^\s*(?:remote|hybrid|on-?site|onsite|fully remote)\s+", re.I)
PAREN = re.compile(r"\s*\(([^()]*)\)")


def _paren_backed(content: str, master_norm: str) -> bool:
    """A bracket may only hold things the profile has: '(OpenAI, Claude)' yes, '(QA)', '(via X)' no."""
    parts = [p.strip() for p in re.split(r",|;|/|\band\b|\be\.g\.", content) if p.strip()]
    if not parts:
        return False
    return all(evidence.mentions(master_norm, p, normalised=True) or NUM.fullmatch(p) for p in parts)


def clean_brackets(text: str, master_norm: str) -> tuple[str, list[str]]:
    removed = []

    def repl(m: re.Match) -> str:
        if _paren_backed(m.group(1), master_norm):
            return m.group(0)
        removed.append(m.group(0).strip())
        return ""

    new = PAREN.sub(repl, main._GENDER_MARKER.sub("", text))
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+([,.;])", r"\1", new)).strip(), removed


def clean_headline(headline: str, master: str, confirmations: dict, master_norm: str) -> str:
    """'<real title family> | 3-5 real skills', under HEADLINE_MAX characters."""
    segments = [s.strip() for s in headline.split("|") if s.strip()]
    if not segments:
        return headline
    title = WORK_MODEL.sub("", main.clean_title(segments[0])).strip()
    title = title[0].upper() + title[1:] if title else title
    skills = []
    for seg in segments[1:]:
        for item in re.split(r",|&|\band\b", seg):
            item = item.strip()
            if item and evidence.status(item, master, confirmations, master_norm) in ("verified", "confirmed") \
                    and is_skill(item) and item.lower() not in (s.lower() for s in skills):
                if evidence._level(confirmations or {}, item) != "Basic":
                    skills.append(item)
    skills = skills[:5]

    def build(items: list[str]) -> str:
        return " | ".join([title] + ([", ".join(items[:2])] if items[:2] else [])
                          + ([", ".join(items[2:])] if items[2:] else []))
    while skills and len(build(skills)) > HEADLINE_MAX:
        skills.pop()
    out = build(skills)
    return out if len(out) <= HEADLINE_MAX else out[:HEADLINE_MAX].rsplit(" ", 1)[0]


# --------------------------------------------------------------------------- #
# enforce(): every mechanical fix, in order
# --------------------------------------------------------------------------- #

def _skill_groups(resume: dict) -> list[dict]:
    return [{"label": g["label"], "items": list(g["items"])} for g in render._skill_groups(resume["skills"])]


def _write_groups(groups: list[dict]) -> list[str]:
    wk = [g for g in groups if g["label"].lower() == "working knowledge"]
    rest = [g for g in groups if g["label"].lower() != "working knowledge"]
    return [(f"{g['label']}: " if g["label"] else "") + ", ".join(g["items"]) for g in rest + wk if g["items"]]


def enforce(resume: dict, master: str, answers: list[dict] | None, confirmations: dict | None,
            settings: dict | None, analysis: dict | None = None, job_text: str = "") -> tuple[dict, list[str]]:
    """Return (fixed copy, notes) — see the module docstring."""
    confirmations = confirmations or {}
    master, _ = clean_master(master)
    master_norm = evidence.normalise(master)
    sources = sources_text(master, answers)
    notes: list[str] = []
    r = {**resume, "contact": list(resume["contact"]), "skills": list(resume["skills"]),
         "experience": [{**j, "bullets": list(j["bullets"])} for j in resume["experience"]],
         "projects": [{**p, "bullets": list(p["bullets"])} for p in resume["projects"]]}

    # 1. Availability from this job's settings; nothing left over from another job
    line = availability_line(settings, analysis)
    if (r.get("availability") or "") != line:
        notes.append(f"Location line set from the job type: \"{line or 'none (local job)'}\".")
    r["availability"] = line
    contact = [c for c in r["contact"] if not (AVAILABILITY.search(c) or STALE_WORDS.search(c))]
    if len(contact) != len(r["contact"]):
        notes.append("Contact: removed relocation/visa text from an earlier job.")
    sentences = re.split(r"(?<=[.!?])\s+", r["summary"])
    kept = [s for s in sentences if not (AVAILABILITY.search(s) or STALE_WORDS.search(s))]
    if len(kept) != len(sentences):
        notes.append("Summary: removed relocation/visa wording (the location line covers it).")
        r["summary"] = " ".join(kept)

    # 9. Every profile link in the contact line
    have = {_link_key(m.group(0)) for c in contact for m in LINK.finditer(c)}
    added = [ln for ln in master_links(master) if _link_key(ln) not in have]
    if added:
        notes.append("Contact: added your profile links " + ", ".join(added) + ".")
    r["contact"] = contact + added

    # 7. Brackets and job-ad tags in the summary, bullets and skills; a real, short headline
    removed_brackets: list[str] = []
    r["summary"] = WORK_MODEL.sub("", r["summary"])  # "Remote Full Stack Developer with..." -> "Full Stack..."
    r["summary"], gone = clean_brackets(r["summary"], master_norm)
    removed_brackets += gone
    for _, bullets in _sections(r):
        for i, b in enumerate(bullets):
            bullets[i], gone = clean_brackets(b, master_norm)
            removed_brackets += gone
    headline = clean_headline(r["headline"], master, confirmations, master_norm)
    if headline != r["headline"].strip():
        notes.append(f"Headline: kept your real title and skills, under {HEADLINE_MAX} characters: \"{headline}\".")
    r["headline"] = headline

    # 2-3. Skills: no job-ad text, Basic answers under Working knowledge, every used master skill kept
    groups = _skill_groups(r)
    dropped: list[str] = []
    basic: list[str] = []
    misplaced: list[str] = []  # real skills Claude put under "Working knowledge"
    for g in groups:
        bucket = g["label"].lower() in BUCKET_LABELS
        items = []
        for item in g["items"]:
            clean, gone = clean_brackets(item, master_norm)
            removed_brackets += gone
            lvl = evidence._level(confirmations, clean)
            st = evidence.status(clean, master, confirmations, master_norm)
            if not clean or not is_skill(clean) or st not in evidence.BACKED:
                dropped.append(clean or item)
            elif lvl == "Basic" and st == "confirmed":
                basic.append(clean)
            elif bucket:
                misplaced.append(clean)
            else:
                items.append(clean)
        g["items"] = [] if bucket else items
    groups = [g for g in groups if g["label"].lower() not in BUCKET_LABELS]
    if misplaced:
        home = next((g for g in groups if re.search(r"tool|technolog", g["label"], re.I)), None)
        if home is None:
            home = {"label": "Tools & Technologies", "items": []}
            groups.append(home)
        home["items"] += misplaced
    if basic:
        groups.append({"label": "Working knowledge", "items": list(dict.fromkeys(basic))})
    if dropped:
        notes.append("Skills: removed items that are job-ad text or not in your profile/answers: "
                     + ", ".join(dict.fromkeys(dropped)) + ".")
    if removed_brackets:
        notes.append("Removed bracket explanations: " + ", ".join(dict.fromkeys(removed_brackets)) + ".")

    master_items = master_skills(master)
    category_of = {}
    for category, item in master_items:
        category_of.setdefault(item.lower(), category)
    for g in groups:  # a category named after the job ("AI/ML") gets the profile's own name for it
        if g["label"] and (not is_skill(g["label"]) or g["label"].lower() in JOB_FAMILIES):
            g["label"] = next((category_of[i.lower()] for i in g["items"] if i.lower() in category_of), "Skills")

    body = " ".join(t for _, t in body_texts(r))
    listed = " ".join(i for g in groups for i in g["items"])
    restored = []
    candidates = []
    context = f"{listed} {body} {job_text}"
    position: dict[str, int] = {}
    seen_items = []
    for category, item in master_items:
        rank_in_category = position[category] = position.get(category, -1) + 1
        name = re.sub(r"\s*\(.*?\)", "", item).strip()
        core = re.sub(r"\s+(?:api|apis|framework|platform|database)$", "", name, flags=re.I)
        in_context = any(evidence.mentions(context, t) for t in {item, name, core})
        listed_now = evidence.mentions(listed, item) or evidence.mentions(listed, name)
        used = evidence.mentions(body, item) or evidence.mentions(body, name) or \
            (core != name and evidence.mentions(body, core))
        wanted = bool(job_text) and (evidence.mentions(job_text, name) or evidence.mentions(job_text, item))
        seen_items.append((category, item, rank_in_category, is_skill(name) and not listed_now, wanted, used,
                           in_context))
    # A profile category is relevant when the resume or the job uses any of its skills
    relevant = {c for c, *_, in_context in seen_items if in_context}
    for category, item, rank_in_category, open_slot, wanted, used, _ in seen_items:
        # job-mentioned first, then used in a bullet, then the top skills of each relevant category
        if open_slot and (wanted or used or category in relevant):
            # the job's own terms first; then by the order the profile lists them (its own priority)
            candidates.append((0 if wanted else 1, rank_in_category, 0 if used else 1, category, item))
    room = max(0, SKILLS_MAX - sum(len(g["items"]) for g in groups) - len(basic))
    for _, _, _, category, item in sorted(candidates)[:room]:
        siblings = [i for c, i in master_items if c == category and i != item]
        target = next((g for g in groups if g["label"].lower() != "working knowledge"
                       and any(evidence.mentions(" ".join(g["items"]), s) for s in siblings)), None)
        if target is None:
            target = next((g for g in groups if g["label"] == category), None)
        if target is None:
            target = {"label": category, "items": []}
            groups.append(target)
        target["items"].append(item)
        restored.append(item)
    if restored:
        notes.append("Skills: kept your relevant profile skills: " + ", ".join(restored) + ".")
    r["skills"] = _write_groups(groups)

    # 4. Saved answers override the profile
    for rule in answer_rules(answers, r, master):
        for where, bullets in _sections(r):
            for i, b in enumerate(list(bullets)):
                if not _in_scope(rule, where, b):
                    continue
                if rule.kind == "solo" and TEAM_CLAIM.search(b):
                    new = _without_team(b)
                    notes.append(f"{where}: removed team wording, because you said: \"{rule.answer}\".")
                    if new is None:
                        bullets[i] = ""
                    else:
                        bullets[i] = new
                elif rule.kind == "for_clients" and (OWN_CLAIM.search(b) or (
                        rule.scope and any(s.lower() in b.lower() for s in rule.scope)
                        and not re.search(r"\bclients?\b", b, re.I))):
                    bullets[i] = _for_clients(b, rule)
                    notes.append(f"{where}: says these were built for clients, because you said: \"{rule.answer}\".")
                elif rule.kind == "no_numbers" and USER_COUNT.search(b):
                    bullets[i] = re.sub(r"\s{2,}", " ", USER_PHRASE.sub("", b)).strip()
                    notes.append(f"{where}: removed user numbers, because you said: \"{rule.answer}\".")
            bullets[:] = [b for b in bullets if b.strip()]
        if rule.kind == "for_clients" and OWN_CLAIM.search(r["summary"]):
            r["summary"] = " ".join(_for_clients(s, rule) if OWN_CLAIM.search(s) else s
                                    for s in re.split(r"(?<=[.!?])\s+", r["summary"]))
            notes.append(f"Summary: says your products were built for clients, because you said: \"{rule.answer}\".")

    # 5. Counting numbers that aren't in the sources
    span = _span(r)
    removed_numbers = []
    for where, bullets in [("Summary", None)] + list(_sections(r)):
        texts = [r["summary"]] if bullets is None else bullets
        for i, t in enumerate(texts):
            new = t
            for m in list(NUM.finditer(t)):
                if _is_count(m.group(0)) and not number_backed(m.group(1), t, sources, span):
                    new = _drop_number(new, m.group(0))
                    removed_numbers.append(m.group(0).strip())
            if bullets is None:
                r["summary"] = new
            else:
                bullets[i] = new
    if removed_numbers:
        notes.append("Removed counting numbers that aren't in your profile: "
                     + ", ".join(dict.fromkeys(removed_numbers)) + ".")
    return r, list(dict.fromkeys(notes))


def _span(resume: dict) -> int | None:
    import review  # local: review imports guard's neighbours
    return review.experience_years(resume["experience"])


# --------------------------------------------------------------------------- #
# checks(): what is still new, as numbered checks; serious items block export
# --------------------------------------------------------------------------- #

@dataclass
class Item:
    check: int
    severity: str
    message: str
    where: str = ""
    text: str = ""          # the resume text it is about (a bullet, a skill, a number)
    fix: str = ""           # "skill" | "bullet" | "number" | "" (approve only)
    value: str = ""         # the skill or number to remove

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.check}|{self.message}|{self.text}|{self.value}".encode()).hexdigest()[:12]


def checks(resume: dict, master: str, answers: list[dict] | None, confirmations: dict | None,
           settings: dict | None, analysis: dict | None = None, job_skills: list[str] | None = None) -> list[Item]:
    confirmations = confirmations or {}
    master, _ = clean_master(master)
    master_norm = evidence.normalise(master)
    sources = sources_text(master, answers)
    items: list[Item] = []

    # 1
    line = availability_line(settings, analysis)
    if (resume.get("availability") or "") != line:
        items.append(Item(1, SERIOUS, f"The location line should be \"{line or 'empty (local job)'}\" for this "
                                      f"job type, but it says \"{resume.get('availability') or ''}\"."))
    stale = [t for _, t in body_texts(resume) if STALE_WORDS.search(t)] + \
        [c for c in resume["contact"] if STALE_WORDS.search(c) or AVAILABILITY.search(c)]
    for t in stale:
        items.append(Item(1, SERIOUS, "Relocation/visa wording from another job.", text=t))

    # 2
    for _, item in [(g["label"], i) for g in render._skill_groups(resume["skills"]) for i in g["items"]]:
        st = evidence.status(item, master, confirmations, master_norm)
        if not is_skill(item):
            items.append(Item(2, SERIOUS, f"\"{item}\" is job-ad text, not a skill.", text=item, fix="skill",
                              value=item))
        elif st not in evidence.BACKED:
            items.append(Item(2, SERIOUS, f"\"{item}\" isn't in your profile and you haven't confirmed it.",
                              text=item, fix="skill", value=item))

    # 3
    listed = " ".join(resume["skills"])
    body = " ".join(t for _, t in body_texts(resume))
    missing = [i for _, i in master_skills(master) if is_skill(i) and evidence.mentions(body, i)
               and not evidence.mentions(listed, i)]
    if missing:
        items.append(Item(3, WARNING, "Profile skills your bullets use but Skills doesn't list: "
                          + ", ".join(dict.fromkeys(missing)) + "."))

    # 4
    for where, bullet, rule in contradictions(resume, answer_rules(answers, resume, master)):
        items.append(Item(4, SERIOUS, f"Contradicts your answer \"{rule.answer}\".", where, bullet, "bullet", bullet))

    # 5
    for where, number, text in unbacked_numbers(resume, sources, _span(resume)):
        items.append(Item(5, SERIOUS, f"\"{number}\" isn't in your profile or answers.", where, text, "number",
                          number))

    # 6
    real_skills = [s for s in job_skills or [] if is_skill(s)]
    for row in evidence.trace_bullets(resume, sources, real_skills, confirmations):
        if row["where"] == "Summary":
            continue
        if row["unsupported_terms"]:
            items.append(Item(6, SERIOUS, "Claims " + ", ".join(row["unsupported_terms"]) + ", which your profile "
                              "doesn't show and you haven't confirmed as Strong.", row["where"], row["text"],
                              "bullet", row["text"]))
        elif row["status"] == "not found":
            items.append(Item(6, SERIOUS, "Can't be traced to your profile or answers.", row["where"],
                              row["text"], "bullet", row["text"]))

    # 7
    if main._GENDER_MARKER.search(resume["headline"]) or len(resume["headline"]) > HEADLINE_MAX:
        items.append(Item(7, SERIOUS, f"Headline has a job-ad tag or is over {HEADLINE_MAX} characters.",
                          text=resume["headline"]))
    for where, text in body_texts(resume):
        for m in PAREN.finditer(text):
            if not _paren_backed(m.group(1), master_norm):
                items.append(Item(7, SERIOUS, f"Bracket explanation {m.group(0).strip()}.", where, text))
    have = {_link_key(m.group(0)) for c in resume["contact"] for m in LINK.finditer(c)}
    lost = [ln for ln in master_links(master) if _link_key(ln) not in have]
    if lost:
        items.append(Item(7, SERIOUS, "Missing profile links: " + ", ".join(lost) + "."))
    return items


def apply_fix(resume: dict, item: Item) -> dict:
    """Remove the skill, bullet or number an item is about."""
    r = {**resume, "skills": list(resume["skills"]),
         "experience": [{**j, "bullets": list(j["bullets"])} for j in resume["experience"]],
         "projects": [{**p, "bullets": list(p["bullets"])} for p in resume["projects"]]}
    if item.fix == "skill":
        groups = _skill_groups(r)
        for g in groups:
            g["items"] = [i for i in g["items"] if i != item.value]
        r["skills"] = _write_groups(groups)
    elif item.fix == "bullet":
        for _, bullets in _sections(r):
            bullets[:] = [b for b in bullets if b != item.value]
    elif item.fix == "number":
        if item.where == "Summary":
            r["summary"] = _drop_number(r["summary"], item.value)
        elif item.where == "Headline":
            r["headline"] = _drop_number(r["headline"], item.value)
        for _, bullets in _sections(r):
            bullets[:] = [_drop_number(b, item.value) if b == item.text else b for b in bullets]
    return r


def new_words(text: str, sources: str) -> list[tuple[str, bool]]:
    """(word, is_new) for each word: new = a content word or number that isn't in the sources."""
    src_tokens = evidence.tokens(sources)
    src_numbers = {_base(m.group(1)) for m in NUM.finditer(sources)}
    out = []
    for word in re.findall(r"\S+", text):
        core = word.strip(".,;:()[]\"'")
        num = NUM.fullmatch(core)
        if num:
            out.append((word, _base(num.group(1)) not in src_numbers))
            continue
        toks = evidence.tokens(core)
        out.append((word, bool(toks) and not toks <= src_tokens))
    return out
