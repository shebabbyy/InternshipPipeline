"""Re-make the PDF of every resume / cover letter .docx in output/ that was edited after its PDF (or has none).

Uses Microsoft Word (documents.to_pdf), so the PDF keeps Times New Roman exactly. Run with tools\\REFRESH_PDFS.bat.
Never submits anything and never changes the .docx files or the database.
"""
from __future__ import annotations

from common import ROOT
from documents import to_pdf


def stale(out=None) -> list:
    out = out or ROOT / "output"
    found = []
    for docx in sorted(out.glob("*/*.docx")):
        if docx.name.startswith("~$"):
            continue                      # Word's lock file while a document is open
        pdf = docx.with_suffix(".pdf")
        if not pdf.exists() or pdf.stat().st_mtime < docx.stat().st_mtime:
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
