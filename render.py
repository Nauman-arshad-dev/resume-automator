"""
Resume templates: render a tailored resume as PDF, Word (.docx) and preview images.

Every renderer takes the same `resume` dict (see main.RESUME_SCHEMA) plus a
template name, an optional accent colour and a page size.
"""

from __future__ import annotations

import functools
import io
import os
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# font: "serif" | "sans"; align: "C" (centered header) | "L"; size: body pt;
# gap: vertical spacing multiplier; caps: upper-case section titles; rule: line under titles.
TEMPLATES = {
    "Classic": {"font": "serif", "align": "C", "accent": "#222222", "size": 10.5, "name_size": 22, "gap": 1.0, "caps": True, "rule": True},
    "Modern": {"font": "sans", "align": "L", "accent": "#1F4E79", "size": 10, "name_size": 24, "gap": 1.0, "caps": True, "rule": True},
    "Compact": {"font": "sans", "align": "L", "accent": "#333333", "size": 9, "name_size": 18, "gap": 0.6, "caps": True, "rule": True},
    "Minimal": {"font": "sans", "align": "C", "accent": "#555555", "size": 10, "name_size": 22, "gap": 1.2, "caps": False, "rule": False},
}
PAGE_SIZES = {"A4": "A4", "US Letter": "Letter"}
DOCX_FONTS = {"serif": "Georgia", "sans": "Arial"}
TEXT_COLOR = (34, 34, 34)
MUTED_COLOR = (90, 90, 90)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------- #
# Fonts: use a Unicode TrueType font from the OS so bullets, dashes and accents
# render; fall back to the PDF built-in fonts (Latin-1 only) if none is found.
# --------------------------------------------------------------------------- #

# (regular, bold, italic, bold-italic) file names, tried in order.
_FONT_FILES = {
    "sans": [
        ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
        ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf", "Arial Bold Italic.ttf"),
        ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
        ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
    ],
    "serif": [
        ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
        ("Georgia.ttf", "Georgia Bold.ttf", "Georgia Italic.ttf", "Georgia Bold Italic.ttf"),
        ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf", "LiberationSerif-BoldItalic.ttf"),
        ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf", "DejaVuSerif-BoldItalic.ttf"),
    ],
}
_ASCII = str.maketrans({"•": "-", "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "\u00a0": " "})


def _font_dirs() -> list[Path]:
    if sys.platform == "win32":
        return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]


@functools.lru_cache(maxsize=None)
def _font_index() -> dict[str, Path]:
    index: dict[str, Path] = {}
    for folder in _font_dirs():
        for root, _, files in os.walk(folder):
            for f in files:
                index.setdefault(f.lower(), Path(root) / f)
    return index


@functools.lru_cache(maxsize=None)
def _find_fonts(family: str) -> tuple[Path, ...] | None:
    index = _font_index()
    for names in _FONT_FILES[family]:
        paths = tuple(index.get(n.lower()) for n in names)
        if all(paths):
            return paths
    return None


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

def build_pdf(resume: dict, template: str = "Classic", accent: str | None = None, page: str = "A4") -> bytes:
    t = TEMPLATES[template]
    accent_rgb = _rgb(accent or t["accent"])
    size, gap = t["size"], t["gap"]
    lh = size * 1.4  # line height (pt)

    pdf = FPDF(unit="pt", format=PAGE_SIZES[page])
    pdf.set_margins(50, 40, 50)
    pdf.set_auto_page_break(True, margin=40)
    pdf.add_page()

    fonts = _find_fonts(t["font"])
    if fonts:
        for style, path in zip(("", "B", "I", "BI"), fonts):
            pdf.add_font("body", style, str(path))
        family, bullet = "body", "•"
        clean = str
    else:
        family, bullet = ("times" if t["font"] == "serif" else "helvetica"), "-"
        def clean(s: str) -> str:
            return s.translate(_ASCII).encode("latin-1", "replace").decode("latin-1")

    W = pdf.epw
    NEXT = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT, "align": "L"}

    def font(style: str = "", sz: float = size, color=TEXT_COLOR) -> None:
        pdf.set_font(family, style, sz)
        pdf.set_text_color(*color)

    def section(title: str) -> None:
        pdf.ln(9 * gap)
        font("B", size + 1.5, accent_rgb)
        pdf.cell(W, lh + 1, clean(title.upper() if t["caps"] else title), **NEXT)
        if t["rule"]:
            pdf.set_draw_color(*accent_rgb)
            pdf.set_line_width(0.7)
            y = pdf.get_y() + 1
            pdf.line(pdf.l_margin, y, pdf.l_margin + W, y)
        pdf.ln(4 * gap)

    def paragraph(text: str) -> None:
        font()
        pdf.multi_cell(W, lh, clean(text), **NEXT)

    def entry(left: str, right: str = "", sub: str = "") -> None:
        pdf.ln(3 * gap)
        left, right = clean(left), clean(right)
        font()
        right_w = pdf.get_string_width(right) + 2 if right else 0
        font("B")
        if right and pdf.get_string_width(left) + right_w + 12 <= W:
            pdf.cell(W - right_w, lh, left)
            font()
            pdf.cell(right_w, lh, right, **{**NEXT, "align": "R"})
        else:
            pdf.multi_cell(W, lh, left, **NEXT)
            if right:
                font()
                pdf.cell(W, lh, right, **NEXT)
        if sub:
            font("I", color=MUTED_COLOR)
            pdf.multi_cell(W, lh, clean(sub), **NEXT)

    def bullets(items: list[str]) -> None:
        font()
        for item in items:
            pdf.set_x(pdf.l_margin + 6)
            pdf.cell(10, lh, bullet)
            pdf.multi_cell(W - 16, lh, clean(item), **NEXT)
            pdf.ln(gap)

    # Header
    font("B", t["name_size"], accent_rgb)
    header = {**NEXT, "align": t["align"]}
    pdf.cell(W, t["name_size"] * 1.25, clean(resume["name"]), **header)
    if resume["contact"]:
        font("", size - 0.5, MUTED_COLOR)
        pdf.multi_cell(W, lh, clean("  |  ".join(resume["contact"])), **header)

    if resume["summary"]:
        section("Summary")
        paragraph(resume["summary"])
    if resume["skills"]:
        section("Skills")
        paragraph(", ".join(resume["skills"]))
    if resume["experience"]:
        section("Experience")
        for job in resume["experience"]:
            entry(job["title"], job["dates"], ", ".join(x for x in (job["company"], job["location"]) if x))
            bullets(job["bullets"])
    if resume["projects"]:
        section("Projects")
        for proj in resume["projects"]:
            entry(proj["name"])
            bullets(proj["bullets"])
    if resume["education"]:
        section("Education")
        for edu in resume["education"]:
            entry(edu["degree"], edu["dates"], edu["school"])
            bullets(edu["details"])
    if resume["certifications"]:
        section("Certifications")
        bullets(resume["certifications"])

    return bytes(pdf.output())


def pdf_page_images(pdf_bytes: bytes, scale: float = 1.5) -> list[bytes]:
    """PNG image of each PDF page, for the in-app preview."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        images = []
        for i in range(len(doc)):
            buf = io.BytesIO()
            doc[i].render(scale=scale).to_pil().save(buf, format="PNG")
            images.append(buf.getvalue())
        return images
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Word (.docx)
# --------------------------------------------------------------------------- #

def _bottom_border(paragraph, color: str) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, val in {"w:val": "single", "w:sz": "6", "w:space": "1", "w:color": color.lstrip("#")}.items():
        bottom.set(qn(key), val)
    borders.append(bottom)
    p_pr.append(borders)


def build_docx(resume: dict, path, template: str = "Classic", accent: str | None = None, page: str = "A4") -> None:
    """path may be a file path or a writable binary stream (e.g. io.BytesIO)."""
    t = TEMPLATES[template]
    accent = accent or t["accent"]
    accent_color = RGBColor(*_rgb(accent))
    muted = RGBColor(*MUTED_COLOR)
    align = WD_ALIGN_PARAGRAPH.CENTER if t["align"] == "C" else WD_ALIGN_PARAGRAPH.LEFT
    gap = t["gap"]

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = DOCX_FONTS[t["font"]]
    normal.font.size = Pt(t["size"])
    for section in doc.sections:
        section.page_width, section.page_height = (Mm(210), Mm(297)) if page == "A4" else (Inches(8.5), Inches(11))
        section.top_margin = section.bottom_margin = Pt(40)
        section.left_margin = section.right_margin = Pt(50)

    def heading(title: str) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10 * gap)
        p.paragraph_format.space_after = Pt(3 * gap)
        run = p.add_run(title.upper() if t["caps"] else title)
        run.bold = True
        run.font.size = Pt(t["size"] + 1.5)
        run.font.color.rgb = accent_color
        if t["rule"]:
            _bottom_border(p, accent)

    def entry(left: str, right: str = "", sub: str = "") -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(4 * gap)
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
            p.paragraph_format.space_after = Pt(1 * gap)

    name = doc.add_paragraph()
    name.alignment = align
    name.paragraph_format.space_after = Pt(0)
    run = name.add_run(resume["name"])
    run.bold = True
    run.font.size = Pt(t["name_size"])
    run.font.color.rgb = accent_color

    if resume["contact"]:
        contact = doc.add_paragraph()
        contact.alignment = align
        contact.add_run("  |  ".join(resume["contact"])).font.color.rgb = muted

    if resume["summary"]:
        heading("Summary")
        doc.add_paragraph(resume["summary"])
    if resume["skills"]:
        heading("Skills")
        doc.add_paragraph(", ".join(resume["skills"]))
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
