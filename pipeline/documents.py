"""Builds Bauer-format resume and cover letter .docx files (one column, no tables,
no header/footer, contact info in the body, Times New Roman 11). PDF via Word if installed."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

FONT = "Times New Roman"


def _base_doc() -> Document:
    doc = Document()
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Inches(0.5)
        s.left_margin = s.right_margin = Inches(0.5)
    st = doc.styles["Normal"]
    st.font.name = FONT
    st.font.size = Pt(11)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    pf = st.paragraph_format
    pf.space_before = pf.space_after = Pt(0)
    return doc


def _para(doc, text="", bold=False, size=11, align=None, after=0, before=0):
    p = doc.add_paragraph()
    if align:
        p.alignment = align
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.space_before = Pt(before)
    if text:
        r = p.add_run(text)
        r.bold = bold
        r.font.size = Pt(size)
    return p


def _rule(p):
    """Bottom border under a section header (a paragraph border, not a table)."""
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in {"w:val": "single", "w:sz": "6", "w:space": "1", "w:color": "000000"}.items():
        bottom.set(qn(k), v)
    bdr.append(bottom)
    pPr.append(bdr)


def _header(doc, text):
    p = _para(doc, text.upper(), bold=True, before=6, after=2)
    _rule(p)


def _left_right(doc, left, right, bold_left=True):
    p = doc.add_paragraph()
    p.paragraph_format.tab_stops.add_tab_stop(Inches(7.5), WD_TAB_ALIGNMENT.RIGHT)
    r = p.add_run(left)
    r.bold = bold_left
    p.add_run("\t" + right)
    return p


def _bullet(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.35)
    p.paragraph_format.first_line_indent = Inches(-0.2)
    p.paragraph_format.tab_stops.add_tab_stop(Inches(0.35))  # wrapped lines line up with the first line's text
    p.add_run("•\t" + text)  # plain bullet character parses cleanly in every ATS


def build_resume(profile: dict, picks: dict, path: Path, email: str | None = None) -> Path:
    """picks = {"coursework": [...], "experience": {id: [bullets]}, "leadership": {id: [bullets]}, "skills_line": str}"""
    c, e = profile["candidate"], profile["education"]
    doc = _base_doc()
    _para(doc, c["name"], bold=True, size=14, align=WD_ALIGN_PARAGRAPH.CENTER)
    _para(doc, f"{c['phone']} • {c['location']} • {email or c['email']}", align=WD_ALIGN_PARAGRAPH.CENTER)
    _para(doc, c.get("linkedin_display") or c["linkedin"], align=WD_ALIGN_PARAGRAPH.CENTER)

    _header(doc, "Education")
    _left_right(doc, f"{e['school']}, {e['city']}", "")
    _left_right(doc, e["degree"], e["graduation"], bold_left=False)
    _para(doc, f"Cumulative GPA: {e['gpa']}")
    if picks.get("coursework"):
        _para(doc, "Relevant Coursework: " + ", ".join(picks["coursework"]))

    def entries(group):
        for item in profile[group]:
            bullets = picks[group].get(item["id"], [])
            if item.get("continues") and not bullets:
                continue                      # earlier role at the same employer with nothing selected
            if not item.get("continues"):     # "continues" = same employer as the entry above: no second header
                _left_right(doc, f"{item['organization']}, {item['location']}", item["dates"])
            _para(doc, item["title"])
            for b in bullets:
                _bullet(doc, b)

    _header(doc, "Experience")
    entries("experience")

    if profile.get("honors"):
        _header(doc, "Honors and Awards")
        for line in profile["honors"]:
            _para(doc, line)

    _header(doc, "Leadership & Activities")
    entries("leadership")

    _header(doc, "Skills & Certifications")
    sk = profile["skills"]
    lines = list(sk["languages"])
    if sk["technical"]:
        lines.append(picks.get("skills_line") or ", ".join(sk["technical"]))
    if sk.get("certifications"):
        lines.append(", ".join(sk["certifications"]))
    for line in lines:
        _para(doc, line)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


def build_cover_letter(profile: dict, company: str, role: str, paragraphs: list[str], path: Path,
                       email: str | None = None) -> Path:
    c = profile["candidate"]
    doc = _base_doc()
    for line in [c["name"], f"{c['phone']} | {email or c['email']}", c["location"], "", date.today().strftime("%B %d, %Y"), "",
                 "Hiring Team", company, ""]:
        _para(doc, line)
    _para(doc, "Dear Hiring Team,", after=8)
    for ptext in paragraphs:
        _para(doc, ptext, after=8)
    _para(doc, "Sincerely,")
    _para(doc, c["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


def to_pdf(docx_path: Path) -> Path | None:
    """Uses Microsoft Word through docx2pdf (Windows). Returns None if Word isn't available."""
    try:
        from docx2pdf import convert
        pdf = docx_path.with_suffix(".pdf")
        convert(str(docx_path), str(pdf))
        return pdf if pdf.exists() else None
    except Exception:  # noqa: BLE001
        return None


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", text)[:30] or "Company"
