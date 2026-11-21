"""Re-make the PDF of every resume / cover letter .docx in output/ that was edited after its PDF, has none, or
whose PDF isn't in Times New Roman.

Uses Microsoft Word (documents.to_pdf), so the PDF keeps Times New Roman exactly. Run with tools\\REFRESH_PDFS.bat.
Never submits anything and never changes the .docx files or the database.
"""
from __future__ import annotations

from common import ROOT
from documents import to_pdf


def _word_fonts(pdf) -> bool:
    """False when the PDF wasn't made with Times New Roman (e.g. LibreOffice swapped the font)."""
    try:
        data = pdf.read_bytes()
        return b"TimesNewRoman" in data or b"Times New Roman" in data
    except Exception:  # noqa: BLE001
        return True


def stale(out=None) -> list:
    out = out or ROOT / "output"
    found = []
    for docx in sorted(out.glob("*/*.docx")):   # job packets + _track_resumes
        if docx.name.startswith("~$"):
            continue                      # Word's lock file while a document is open
        pdf = docx.with_suffix(".pdf")
        if not pdf.exists() or pdf.stat().st_mtime < docx.stat().st_mtime or not _word_fonts(pdf):
            found.append(docx)
    return found


def main() -> int:
    todo = stale()
    if not todo:
        print("All PDFs are up to date.")
        return 0
    bad = 0
    for docx in todo:
        pdf = to_pdf(docx)
        print(("Made " if pdf else "FAILED (is Word installed and closed?) ") + str(docx.relative_to(ROOT)))
        bad += pdf is None
    print(f"\n{len(todo) - bad} PDF(s) made" + (f", {bad} failed." if bad else "."))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
