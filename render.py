"""
Resume templates: render a tailored resume as PDF, Word (.docx) and preview images.

PDF designs are HTML/CSS templates in ./templates (Jinja2), printed to PDF by a
headless Chromium browser via Playwright. On Windows this uses the Microsoft
Edge that ships with the OS, so no browser download is needed; elsewhere it
uses Chrome, or Playwright's Chromium (`python -m playwright install chromium`).

Word output is a simplified single-column version that keeps the template's
font, alignment and accent colour (Word can't reproduce every CSS layout).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

TEMPLATE_DIR = Path(__file__).with_name("templates")

# id -> display info. "ats": single-column layout that applicant tracking systems parse reliably.
# "font" / "align" are used for the simplified Word version.
TEMPLATES = {
    "classic": {"label": "Classic", "about": "Traditional serif, centered header", "accent": "#1F2937", "ats": True, "font": "Georgia", "align": "C"},
    "modern": {"label": "Modern", "about": "Clean sans-serif, bold accent headings", "accent": "#1F4E79", "ats": True, "font": "Arial", "align": "L"},
    "executive": {"label": "Executive", "about": "Dark header band, leadership style", "accent": "#14304D", "ats": True, "font": "Georgia", "align": "L"},
    "minimal": {"label": "Minimal", "about": "Airy, section labels in the margin", "accent": "#111111", "ats": False, "font": "Arial", "align": "L"},
    "compact": {"label": "Compact", "about": "Dense two-column skills, fits one page", "accent": "#7A1F2B", "ats": True, "font": "Arial", "align": "L"},
    "sidebar": {"label": "Sidebar", "about": "Colored sidebar with skills & education", "accent": "#24476B", "ats": False, "font": "Arial", "align": "L"},
    "timeline": {"label": "Timeline", "about": "Career on a vertical timeline", "accent": "#0E7C66", "ats": False, "font": "Arial", "align": "L"},
    "bold": {"label": "Bold", "about": "Big name, skill tags, side panel", "accent": "#D9480F", "ats": False, "font": "Arial", "align": "L"},
}
PAGE_SIZES = {"A4": "A4", "US Letter": "Letter"}
MUTED_COLOR = (91, 97, 104)


class RenderError(Exception):
    """No browser available to print the PDF."""


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------- #
# HTML (Jinja2)
# --------------------------------------------------------------------------- #

_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
_URL = re.compile(r"^(https?://)?(www\.)?[\w-]+(\.[\w-]+)+(/\S*)?$", re.IGNORECASE)


def _link(item: str) -> Markup:
    """Contact item as a clickable link when it is an email address or URL."""
    text = item.strip()
    if _EMAIL.match(text):
        return Markup('<a href="mailto:{0}">{0}</a>').format(text)
    if _URL.match(text) and "." in text.split("/")[0]:
        href = text if text.lower().startswith("http") else f"https://{text}"
        return Markup('<a href="{0}">{1}</a>').format(href, text)
    return escape(text)


def _split_items(text: str) -> list[str]:
    """Split "a, b (c, d); e" on commas/semicolons that are not inside brackets."""
    items, depth, cur = [], 0, ""
    for ch in text:
        depth += ch in "([{"
        depth -= ch in ")]}"
        if ch in ",;" and depth <= 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    return [i.strip() for i in items if i.strip()]


def _skill_groups(skills: list[str]) -> list[dict]:
    """["Backend: Node.js, Java", "Git"] -> [{label: "Backend", items: [...]}, {label: "", items: ["Git"]}]"""
    groups, loose = [], []
    for s in skills:
        label, sep, rest = s.partition(":")
        if sep and rest.strip() and len(label) <= 40 and "(" not in label:
            groups.append({"label": label.strip(), "items": _split_items(rest)})
        else:
            loose.extend(_split_items(s))
    if loose:
        groups.append({"label": "", "items": loose})
    return groups


_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html"]),
                   trim_blocks=True, lstrip_blocks=True)
_env.filters["link"] = _link
_env.filters["skill_groups"] = _skill_groups


def build_html(resume: dict, template: str = "classic", accent: str | None = None, page: str = "A4") -> str:
    return _env.get_template(f"{template}.html").render(
        r=resume, accent=accent or TEMPLATES[template]["accent"], page_size=PAGE_SIZES[page],
    )


# --------------------------------------------------------------------------- #
# PDF (headless browser)
# --------------------------------------------------------------------------- #

def _launch(playwright):
    errors = []
    for channel in (None, "msedge", "chrome"):  # Playwright's Chromium, then installed browsers
        try:
            return playwright.chromium.launch(channel=channel) if channel else playwright.chromium.launch()
        except Exception as e:  # browser not installed in this location
            errors.append(f"{channel or 'chromium'}: {str(e).splitlines()[0]}")
    raise RenderError(
        "No browser found to create the PDF. Install Microsoft Edge or Google Chrome, "
        "or run:  python -m playwright install chromium\n" + "\n".join(errors)
    )


def html_to_pdfs(htmls: list[str]) -> list[bytes]:
    """Print several HTML documents to PDF with a single browser launch."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            pdfs = []
            for html in htmls:
                page.set_content(html, wait_until="load")
                pdfs.append(page.pdf(prefer_css_page_size=True, print_background=True))
            return pdfs
        finally:
            browser.close()


def build_pdf(resume: dict, template: str = "classic", accent: str | None = None, page: str = "A4") -> bytes:
    return html_to_pdfs([build_html(resume, template, accent, page)])[0]


def build_all_pdfs(resume: dict, page: str = "A4") -> dict[str, bytes]:
    """Every template with its default accent colour (for the design gallery)."""
    pdfs = html_to_pdfs([build_html(resume, t, None, page) for t in TEMPLATES])
    return dict(zip(TEMPLATES, pdfs))


def pdf_page_images(pdf_bytes: bytes, scale: float = 1.5, max_pages: int | None = None) -> list[bytes]:
    """PNG image of each PDF page, for previews."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        images = []
        for i in range(len(doc) if max_pages is None else min(len(doc), max_pages)):
            buf = io.BytesIO()
            doc[i].render(scale=scale).to_pil().save(buf, format="PNG")
            images.append(buf.getvalue())
        return images
    finally:
        doc.close()


def pdf_page_count(pdf_bytes: bytes) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(doc)
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Word (.docx) — simplified single-column version of the chosen template
# --------------------------------------------------------------------------- #

def _bottom_border(paragraph, color: str) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, val in {"w:val": "single", "w:sz": "6", "w:space": "1", "w:color": color.lstrip("#")}.items():
        bottom.set(qn(key), val)
    borders.append(bottom)
    p_pr.append(borders)


def build_docx(resume: dict, path, template: str = "classic", accent: str | None = None, page: str = "A4") -> None:
    """path may be a file path or a writable binary stream (e.g. io.BytesIO)."""
    t = TEMPLATES[template]
    accent = accent or t["accent"]
    accent_color = RGBColor(*_rgb(accent))
    muted = RGBColor(*MUTED_COLOR)
    align = WD_ALIGN_PARAGRAPH.CENTER if t["align"] == "C" else WD_ALIGN_PARAGRAPH.LEFT

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = t["font"]
    normal.font.size = Pt(10)
    for section in doc.sections:
        section.page_width, section.page_height = (Mm(210), Mm(297)) if page == "A4" else (Inches(8.5), Inches(11))
        section.top_margin = section.bottom_margin = Pt(40)
        section.left_margin = section.right_margin = Pt(50)

    def heading(title: str) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(3)
        run = p.add_run(title.upper())
        run.bold = True
        run.font.size = Pt(11)
        run.font.color.rgb = accent_color
        _bottom_border(p, accent)

    def entry(left: str, right: str = "", sub: str = "") -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(0)
        p.add_run(left).bold = True
        if right:
            p.add_run(f"  |  {right}")
        if sub:
            s = doc.add_paragraph()
            s.paragraph_format.space_after = Pt(1)
            run = s.add_run(sub)
            run.italic = True
            run.font.color.rgb = muted

    def bullets(items: list[str]) -> None:
        for item in items:
            p = doc.add_paragraph(item, style="List Bullet")
            p.paragraph_format.space_after = Pt(1)

    name = doc.add_paragraph()
    name.alignment = align
    name.paragraph_format.space_after = Pt(0)
    run = name.add_run(resume["name"])
    run.bold = True
    run.font.size = Pt(22)
    run.font.color.rgb = accent_color

    if resume["headline"]:
        headline = doc.add_paragraph()
        headline.alignment = align
        headline.paragraph_format.space_after = Pt(0)
        headline.add_run(resume["headline"]).bold = True
    if resume["contact"]:
        contact = doc.add_paragraph()
        contact.alignment = align
        contact.add_run("  |  ".join(resume["contact"])).font.color.rgb = muted

    if resume["summary"]:
        heading("Summary")
        doc.add_paragraph(resume["summary"])
    if resume["skills"]:
        heading("Skills")
        for group in _skill_groups(resume["skills"]):
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(1)
            if group["label"]:
                p.add_run(f"{group['label']}: ").bold = True
            p.add_run(", ".join(group["items"]))
    if resume["experience"]:
        heading("Experience")
        for job in resume["experience"]:
            entry(job["title"], job["dates"], ", ".join(x for x in (job["company"], job["location"]) if x))
            bullets(job["bullets"])
    if resume["projects"]:
        heading("Projects")
        for proj in resume["projects"]:
            entry(proj["name"])
            bullets(proj["bullets"])
    if resume["education"]:
        heading("Education")
        for edu in resume["education"]:
            entry(edu["degree"], edu["dates"], edu["school"])
            bullets(edu["details"])
    if resume["certifications"]:
        heading("Certifications")
        bullets(resume["certifications"])

    doc.save(path)
