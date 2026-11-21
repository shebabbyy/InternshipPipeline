"""Builds your 3 master resumes (one per track) from master_profile.json.
Use them for Bauer Career Gateway (upload once; counselor approval takes ~2 business days),
for Simplify Copilot, and for most applications. Claude tailoring is only for top-priority roles.
Re-run whenever you add something to master_profile.json."""
from common import PROFILE, ROOT

C = PROFILE["candidate"]
from documents import build_resume, to_pdf
from tailor import local_picks

TRACKS = {
    "CorporateFinance": ("corporate_finance_fpa", "budget forecast variance fp&a financial planning expense records accuracy billing reconciliation"),
    "Banking": ("banking", "client relationship credit communication service banking wealth accuracy dispute"),
    "AccountingAdvisory": ("public_accounting", "reconciliation audit compliance accuracy records billing claims controls"),
}

if __name__ == "__main__":
    from common import require_lock
    require_lock("track resumes")
    out = ROOT / "output" / "_track_resumes"
    for name, (track, words) in TRACKS.items():
        picks = local_picks(words, track, "")
        for folder, email, use in ((out, C["email"], "applications, Simplify"),
                                   (out / "Gateway", C.get("email_campus") or C["email"], "Career Gateway, career fairs")):
            path = build_resume(PROFILE, picks, folder / f"Shehab-Moqbel-Resume-{name}.docx", email=email)
            pdf = to_pdf(path)
            print(f"Built {folder.name}/{path.name}" + (" + PDF" if pdf else " (PDF: open in Word and Save As PDF)") + f"  [{email}: {use}]")
    print(f"\nFolder: {out}")
