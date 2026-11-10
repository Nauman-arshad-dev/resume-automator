"""
Built-in ATS check: scores a rendered resume the way common ATS checkers do.

Structure checks read the PDF's text layer (what an applicant tracking system
actually sees); writing and keyword checks read the text too, so the score
reflects the chosen template. Every failed check carries a fix, and
Report.as_text() is the report handed to Claude for the next revision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pypdfium2 as pdfium

SECTIONS = {
    "Summary": ("summary", "professional summary", "profile"),
    "Skills": ("skills", "technical skills", "core skills"),
    "Experience": ("experience", "work experience", "professional experience"),
    "Education": ("education",),
}
BUZZWORDS = (
    "team player", "hard worker", "hardworking", "hard-working", "results-driven", "results-oriented",
    "go-getter", "self-starter", "detail-oriented", "think outside the box", "synergy", "go-to person",
    "dynamic individual", "best of breed",
)
WEAK_STARTS = ("responsible for", "worked on", "worked with", "helped", "assisted", "was ", "participated in",
               "involved in", "duties included", "tasked with", "handled", "in charge of")
PRONOUNS = re.compile(r"\bI\b|\bI'm\b|\bI've\b|(?i:\b(?:me|my|mine|myself)\b)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
WORDS_MIN, WORDS_MAX = 350, 800


@dataclass
class Check:
    group: str      # "Structure" | "Content" | "Job match"
    name: str
    ok: bool
    detail: str
    fix: str
    weight: float
    earned: float = 0.0


@dataclass
class Report:
    score: int
    words: int
    checks: list[Check]
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def as_text(self) -> str:
        lines = [f"Built-in ATS check: {self.score}/100 ({self.words} words)."]
        if self.failed:
            lines.append("Issues to fix:")
            lines += [f"- {c.name}: {c.detail} Fix: {c.fix}" for c in self.failed]
        if self.missing:
            lines.append("Job keywords missing from the resume (add only where truthful): " + ", ".join(self.missing))
        return "\n".join(lines)


COLUMN_GAP_PT = 25  # a horizontal gap this wide starts a new text segment (column / right-aligned date)


def pdf_text(pdf_bytes: bytes) -> str:
    """The PDF's text as an ATS parser would read it: one line per baseline, split at column gaps.

    Built from character positions rather than pdfium's own line heuristics, which
    sometimes glue a heading to the line below it. Uses pdfium's "loose" boxes (the
    font's full line height), so @, g, digits and hyphens on one line share a center.
    """
    doc = pdfium.PdfDocument(pdf_bytes)
    lines: list[str] = []
    try:
        for i in range(len(doc)):
            textpage = doc[i].get_textpage()
            chars = []
            for k in range(textpage.count_chars()):
                ch = textpage.get_text_range(k, 1)
                if not ch or ch in "\r\n￾﻿\x02":
                    continue
                if ch.isspace():  # pdfium's generated spaces have no real box: sit after the previous char
                    if chars and not chars[-1][0].isspace():
                        _, _, center, right, height = chars[-1]
                        chars.append((" ", right, center, right, height))
                    continue
                left, bottom, right, top = textpage.get_charbox(k, loose=True)
                chars.append((ch, left, (top + bottom) / 2, right, max(top - bottom, 1.0)))
            lines += _lines_from_chars(chars)
    finally:
        doc.close()
    return "\n".join(lines)


def _lines_from_chars(chars: list[tuple]) -> list[str]:
    rows: list[list] = []  # [center, height, chars]
    for c in sorted(chars, key=lambda c: (-c[2], c[1])):
        if rows and abs(rows[-1][0] - c[2]) <= 0.4 * min(rows[-1][1], c[4]):
            rows[-1][2].append(c)
        else:
            rows.append([c[2], c[4], [c]])
    lines = []
    for _, _, row in rows:
        segment, prev_right = "", None
        for ch, left, _, right, height in sorted(row, key=lambda c: (c[1], c[0] != " ")):
            gap = left - prev_right if prev_right is not None else 0
            if gap > COLUMN_GAP_PT:
                lines.append(segment.strip())
                segment = ""
            elif gap > 0.3 * height and not segment.endswith(" ") and ch != " ":
                segment += " "  # a visual gap with no space character, e.g. between flex items
            if not (ch == " " and segment.endswith(" ")):
                segment += ch
            prev_right = max(right, left)
        lines.append(segment.strip())
    return [ln for ln in lines if ln]


def _has_keyword(text_lower: str, keyword: str) -> bool:
    k = keyword.lower().strip()
    return bool(k) and re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", text_lower) is not None


has_keyword = _has_keyword  # public name, used by review.py and the app


def _count(text_lower: str, keyword: str) -> int:
    k = keyword.lower().strip()
    return len(re.findall(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", text_lower)) if k else 0


def resume_text(resume: dict) -> str:
    def walk(value):
        if isinstance(value, dict):
            return " \n".join(walk(v) for v in value.values())
        if isinstance(value, list):
            return " \n".join(walk(v) for v in value)
        return str(value)
    return walk(resume)


def keyword_coverage(text: str, keywords: list[str]) -> int:
    """Percent of keywords found in text (e.g. the master resume, for the before/after meter)."""
    keywords = [k for k in dict.fromkeys(k.strip() for k in keywords) if k]
    if not keywords:
        return 0
    return round(100 * sum(_has_keyword(text.lower(), k) for k in keywords) / len(keywords))


def count_words(text: str) -> int:
    """Words as checkers count them: bullets and separators like • · | - are not words."""
    return sum(1 for w in text.split() if any(ch.isalnum() for ch in w))


def trim_to_length(resume: dict, keywords: list[str], max_words: int = WORDS_MAX - 40) -> tuple[dict, int]:
    """Length safety net: drop the least important bullets until the resume fits.

    Takes the last bullet of projects first, then of the oldest roles, keeping 3 bullets
    in the two most recent roles and 1 everywhere else, and never a bullet holding the
    only mention of a job keyword. max_words leaves room for headings. Returns
    (resume, bullets removed); the input is not changed.
    """
    resume = {**resume,
              "experience": [{**j, "bullets": list(j["bullets"])} for j in resume["experience"]],
              "projects": [{**p, "bullets": list(p["bullets"])} for p in resume["projects"]]}
    entries = [(p, 1) for p in reversed(resume["projects"])] + \
              [(j, 3 if i < 2 else 1) for i, j in reversed(list(enumerate(resume["experience"])))]
    removed = 0
    while count_words(resume_text(resume)) > max_words:
        text = resume_text(resume).lower()
        for entry, keep in entries:
            bullets = entry["bullets"]
            idx = next((i for i in range(len(bullets) - 1, keep - 1, -1)
                        if all(_count(text, k) > _count(bullets[i].lower(), k)
                               for k in keywords if _has_keyword(bullets[i].lower(), k))), None)
            if idx is not None:
                del bullets[idx]
                removed += 1
                break
        else:
            break  # nothing left that is safe to remove
    return resume, removed


def words_for_pages(pages: int) -> tuple[int, int]:
    """Word range checkers expect for a 1- or 2-page resume."""
    return (250, 600) if pages <= 1 else (WORDS_MIN, WORDS_MAX)


def check(resume: dict, pdf_bytes: bytes, job_keywords: list[str] | None = None, job_title: str = "",
          job_text: str = "", one_column: bool = True, page_limit: int = 2) -> Report:
    words_min, words_max = words_for_pages(page_limit)
    text = pdf_text(pdf_bytes)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lower_lines = {ln.lower() for ln in lines}
    text_lower = text.lower()
    words = count_words(text)
    bullets = [b for job in resume["experience"] for b in job["bullets"]] + \
              [b for p in resume["projects"] for b in p["bullets"]]
    checks: list[Check] = []

    def add(group, name, ok, detail, fix, weight):
        checks.append(Check(group, name, bool(ok), detail if not ok else "Passed.", fix, weight))

    # Structure — what the parser can find in the PDF text
    add("Structure", "Text-based PDF", words >= 50, "Almost no text could be read — this looks like an image.",
        "Export a text-based PDF or DOCX, never a screenshot, scan or photo.", 4)
    add("Structure", "One-column layout", one_column,
        "This design uses columns, a sidebar or a timeline, which some ATS parsers read out of order.",
        "Switch to an ✅ ATS-friendly one-column template (Classic, Modern, Executive, Compact).", 5)
    add("Structure", "Contact details", EMAIL.search(text) and PHONE.search(text),
        "Email or phone number not found.", "Put your email and phone in the body, not the page header or footer.", 4)
    for section, names in SECTIONS.items():
        found = any(n in lower_lines for n in names)
        weight = 4 if section in ("Summary", "Education") else 7
        add("Structure", f"{section} section", found, f"No \"{section}\" heading found in the PDF text.",
            f"Add a \"{section}\" section — or use an ✅ ATS-friendly template, whose headings parse cleanly.", weight)
    bullet_lines = sum(ln.startswith(("•", "-", "–", "·")) for ln in lines)
    add("Structure", "Bullet points", bullet_lines >= max(3, int(0.7 * len(bullets))),
        f"Only {bullet_lines} bullet lines found in the PDF text.",
        "Write each role as 3-6 short bullets, not paragraphs.", 6)
    jobs = resume["experience"]
    bad_counts = [j["company"] or j["title"] for i, j in enumerate(jobs)
                  if len(j["bullets"]) > 6 or (i < 2 and len(j["bullets"]) < 3)]
    add("Structure", "3-6 bullets per role", not bad_counts,
        "Wrong number of bullets at: " + ", ".join(bad_counts) + "." if bad_counts else "",
        "Give recent roles 3-6 bullets (older roles 1-3), most relevant first.", 3)
    add("Structure", "Dates for every role", all(j["dates"].strip() for j in resume["experience"]),
        "Some roles have no dates.", "Add start and end dates (e.g. Jan 2023 - Present) to every role.", 3)

    # Content — writing quality
    add("Content", f"Length {words_min}-{words_max} words", words_min <= words <= words_max,
        f"{words} words.",
        "Trim older or less relevant bullets and keep the strongest." if words > words_max
        else "Add more detail to recent roles: what you built, with which tools, and the result.", 8)
    pronouns = PRONOUNS.findall(text)
    add("Content", "No first-person pronouns", not pronouns, f"Found {len(pronouns)} (I / me / my).",
        "Start bullets with the action instead of I/my.", 4)
    buzz = [b for b in BUZZWORDS if b in text_lower]
    add("Content", "No empty buzzwords", not buzz, "Found: " + ", ".join(buzz) + ".",
        "Delete buzzwords and show the skill with a concrete example.", 3)
    weak = [b for b in bullets if b.lower().startswith(WEAK_STARTS)]
    add("Content", "Bullets start with action verbs", not weak, f"{len(weak)} bullets start weakly "
        f"(e.g. \"{weak[0][:40]}…\")." if weak else "", "Start with verbs like Built, Led, Designed, Reduced.", 3)
    with_numbers = sum(bool(re.search(r"\d", b)) for b in bullets)
    add("Content", "Results backed by numbers", with_numbers >= 5, f"Only {with_numbers} bullets include a number "
        "(checkers like Jobscan want at least 5).",
        "Use Action verb + what you did + measurable result, e.g. \"Reduced page load time by 35% by "
        "splitting bundles\". Real numbers only: users, revenue, time saved, tickets, accuracy.", 4)
    summary_words = len(resume["summary"].split())
    add("Content", "Short summary at the top", 15 <= summary_words <= 90,
        f"Summary is {summary_words} words." if summary_words else "No summary.",
        "Write a 2-3 line summary: title, years of experience, top skills.", 4)

    # Job match
    if job_title.strip():
        head = f"{resume['headline']} {resume['summary']}".lower()
        same = lambda t: re.sub(r"[\s-]+", " ", t.lower())  # noqa: E731 — "Full-Stack" = "Full Stack"
        add("Job match", f"Mentions the job title \"{job_title}\"", same(job_title) in same(head),
            "The exact job title is not in your headline or summary.",
            "Put the exact job title in your headline, if it truthfully describes you.", 6)
    keywords = list(dict.fromkeys(k.strip() for k in (job_keywords or []) if k.strip()))
    matched = [k for k in keywords if _has_keyword(text_lower, k)]
    missing = [k for k in keywords if k not in matched]
    if keywords:
        top = f"{resume['headline']} {resume['summary']}".lower()
        up_front = [k for k in keywords if _has_keyword(top, k)]
        need = min(3, len(keywords))
        add("Job match", "Top skills up front", len(up_front) >= need,
            f"Only {len(up_front)} job keywords in your headline and summary.",
            "Put the job's most important skills in the summary and the first bullets.", 4)

        weight = 33.0
        c = Check("Job match", "Job keywords", not missing,
                  f"{len(matched)} of {len(keywords)} keywords found." if missing else "Passed.",
                  "Add the missing keywords where they are true (Skills, summary, the relevant bullet).", weight)
        c.earned = weight * len(matched) / len(keywords)
        checks.append(c)

        # External checkers also compare how often a term appears: a keyword the posting
        # repeats should appear at least twice in the resume.
        job_lower = job_text.lower()
        stressed = [k for k in keywords if _count(job_lower, k) >= 2] if job_lower else []
        thin = [k for k in stressed if _count(text_lower, k) < 2]
        if stressed:
            weight = 8.0
            c = Check("Job match", "Repeated keywords", not thin,
                      f"{len(thin)} keywords the posting repeats appear only once or never: "
                      + ", ".join(thin[:12]) + "." if thin else "Passed.",
                      "Mention each of these at least twice: in Skills and in a bullet or the summary.", weight)
            c.earned = weight * (len(stressed) - len(thin)) / len(stressed)
            checks.append(c)

    for c in checks:
        if c.name not in ("Job keywords", "Repeated keywords"):
            c.earned = c.weight if c.ok else 0.0
    score = round(100 * sum(c.earned for c in checks) / sum(c.weight for c in checks))
    return Report(score=score, words=words, checks=checks, matched=matched, missing=missing)
