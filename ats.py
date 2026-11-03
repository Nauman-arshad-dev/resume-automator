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
WEAK_STARTS = ("responsible for", "worked on", "helped", "assisted", "was ", "participated in",
               "involved in", "duties included", "tasked with")
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
    sometimes glue a heading to the line below it.
    """
    doc = pdfium.PdfDocument(pdf_bytes)
    lines: list[str] = []
    try:
        for i in range(len(doc)):
            textpage = doc[i].get_textpage()
            chars = []
            for k in range(textpage.count_chars()):
                ch = textpage.get_text_range(k, 1)
                if ch and ch not in "\r\n":
                    left, bottom, right, top = textpage.get_charbox(k)
                    chars.append((ch, left, bottom, right, max(top - bottom, 1.0)))
            lines += _lines_from_chars(chars)
    finally:
        doc.close()
    return "\n".join(lines)


def _lines_from_chars(chars: list[tuple]) -> list[str]:
    rows: list[list] = []  # [baseline, height, chars]
    for c in sorted(chars, key=lambda c: (-c[2], c[1])):
        if rows and abs(rows[-1][0] - c[2]) <= 0.45 * max(rows[-1][1], c[4] if c[0] != " " else 0):
            rows[-1][2].append(c)
            rows[-1][1] = max(rows[-1][1], c[4] if c[0] != " " else 0)
        else:
            rows.append([c[2], c[4] if c[0] != " " else 1.0, [c]])
    lines = []
    for _, _, row in rows:
        segment, prev_right = "", None
        for ch, left, _, right, _ in sorted(row, key=lambda c: c[1]):
            if prev_right is not None and left - prev_right > COLUMN_GAP_PT:
                lines.append(segment.strip())
                segment = ""
            segment += ch  # word spaces come from pdfium's own generated space characters
            prev_right = max(right, left)
        lines.append(segment.strip())
    return [ln for ln in lines if ln]


def _has_keyword(text_lower: str, keyword: str) -> bool:
    k = keyword.lower().strip()
    return bool(k) and re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", text_lower) is not None


def check(resume: dict, pdf_bytes: bytes, job_keywords: list[str] | None = None, job_title: str = "") -> Report:
    text = pdf_text(pdf_bytes)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lower_lines = {ln.lower() for ln in lines}
    text_lower = text.lower()
    words = len(text.split())
    bullets = [b for job in resume["experience"] for b in job["bullets"]] + \
              [b for p in resume["projects"] for b in p["bullets"]]
    checks: list[Check] = []

    def add(group, name, ok, detail, fix, weight):
        checks.append(Check(group, name, bool(ok), detail if not ok else "Passed.", fix, weight))

    # Structure — what the parser can find in the PDF text
    add("Structure", "Contact details", EMAIL.search(text) and PHONE.search(text),
        "Email or phone number not found.", "Add your email and phone number to the contact line.", 4)
    for section, names in SECTIONS.items():
        found = any(n in lower_lines for n in names)
        weight = 4 if section in ("Summary", "Education") else 7
        add("Structure", f"{section} section", found, f"No \"{section}\" heading found in the PDF text.",
            f"Add a \"{section}\" section — or use an ✅ ATS-friendly template, whose headings parse cleanly.", weight)
    bullet_lines = sum(ln.startswith(("•", "-", "–", "·")) for ln in lines)
    add("Structure", "Bullet points", bullet_lines >= max(3, int(0.7 * len(bullets))),
        f"Only {bullet_lines} bullet lines found in the PDF text.",
        "Write each role as 3-6 short bullets, not paragraphs.", 6)
    add("Structure", "Dates for every role", all(j["dates"].strip() for j in resume["experience"]),
        "Some roles have no dates.", "Add start and end dates (e.g. Jan 2023 - Present) to every role.", 3)

    # Content — writing quality
    add("Content", f"Length {WORDS_MIN}-{WORDS_MAX} words", WORDS_MIN <= words <= WORDS_MAX,
        f"{words} words.",
        "Trim older or less relevant bullets and keep the strongest." if words > WORDS_MAX
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
    add("Content", "Results backed by numbers", with_numbers >= 3, f"Only {with_numbers} bullets include a number.",
        "Add real numbers (users, %, time saved, scale) to a few bullets — only ones you can stand behind.", 4)
    summary_words = len(resume["summary"].split())
    add("Content", "Short summary at the top", 15 <= summary_words <= 90,
        f"Summary is {summary_words} words." if summary_words else "No summary.",
        "Write a 2-3 line summary: title, years of experience, top skills.", 4)

    # Job match
    if job_title.strip():
        head = f"{resume['headline']} {resume['summary']}".lower()
        add("Job match", f"Mentions the job title \"{job_title}\"", job_title.lower() in head,
            "The exact job title is not in your headline or summary.",
            "Put the exact job title in your headline, if it truthfully describes you.", 6)
    keywords = list(dict.fromkeys(k.strip() for k in (job_keywords or []) if k.strip()))
    matched = [k for k in keywords if _has_keyword(text_lower, k)]
    missing = [k for k in keywords if k not in matched]
    if keywords:
        weight = 33.0
        c = Check("Job match", "Job keywords", not missing,
                  f"{len(matched)} of {len(keywords)} keywords found." if missing else "Passed.",
                  "Add the missing keywords where they are true (Skills, summary, the relevant bullet).", weight)
        c.earned = weight * len(matched) / len(keywords)
        checks.append(c)

    for c in checks:
        if c.name != "Job keywords":
            c.earned = c.weight if c.ok else 0.0
    score = round(100 * sum(c.earned for c in checks) / sum(c.weight for c in checks))
    return Report(score=score, words=words, checks=checks, matched=matched, missing=missing)
