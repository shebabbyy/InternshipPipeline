"""Checks that everything is installed and tells you exactly what is left to set up.
Run by INSTALL.bat, by the Control Center "Test setup" button, or with:  .venv\\Scripts\\python.exe pipeline\\selftest.py
It reads nothing private aloud: it only says whether each key or file is present."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

OK, TODO, WARN = "[ OK ]", "[TODO]", "[WARN]"
results: list[tuple[str, str, str]] = []


def add(mark: str, what: str, how: str = "") -> None:
    results.append((mark, what, how))


def main() -> int:
    add(OK if sys.version_info >= (3, 10) else WARN, f"Python {sys.version.split()[0]}",
        "" if sys.version_info >= (3, 10) else "Python 3.10 or newer is needed")
    try:
        from common import CONFIG, PROFILE, ROOT, load_env
        load_env()
        add(OK, "config.yaml and master_profile.json load")
    except Exception as exc:  # noqa: BLE001
        add(WARN, "config.yaml / master_profile.json", f"could not be read: {exc}")
        return report()

    for mod, pkg, need in [("docx", "python-docx", "resumes and cover letters"), ("yaml", "pyyaml", "settings"),
                           ("requests", "requests", "career-site feeds"), ("google.genai", "google-genai", "scoring"),
                           ("gspread", "gspread", "Google Sheet"), ("playwright", "playwright", "applying"),
                           ("keyring", "keyring", "saved portal passwords (auto mode only)"),
                           ("docx2pdf", "docx2pdf", "PDF copies (needs Microsoft Word)"),
                           ("jobspy", "python-jobspy", "optional job-board search")]:
        try:
            importlib.import_module(mod)
            add(OK, f"{pkg} installed")
        except Exception:  # noqa: BLE001
            optional = pkg in ("python-jobspy", "docx2pdf", "keyring")
            add(WARN if optional else TODO, f"{pkg} missing ({need})",
                "optional: the rest works without it" if optional else "run INSTALL.bat again")

    # Documents build and pass the fact checker
    try:
        from documents import build_cover_letter, build_resume
        from tailor import local_cover, local_picks, validate
        with tempfile.TemporaryDirectory() as tmp:
            picks = local_picks("budget forecast accuracy reconciliation client", "corporate_finance_fpa", "")
            build_resume(PROFILE, picks, Path(tmp) / "r.docx")
            cover = local_cover("Test Company", "Finance Intern", "")
            build_cover_letter(PROFILE, "Test Company", "Finance Intern", cover, Path(tmp) / "c.docx")
            bullets = sum(picks["experience"].values(), []) + sum(picks["leadership"].values(), [])
            problems = validate(bullets + cover)
        add(OK if not problems else WARN, "Resume + cover letter build and pass the fact check",
            "" if not problems else f"fact check flagged: {problems[:3]}")
    except Exception as exc:  # noqa: BLE001
        add(WARN, "Resume builder", f"{exc.__class__.__name__}: {exc}")

    unverified = sum(1 for g in ("experience", "leadership") for e in PROFILE[g] for b in e["bullets"] if not b.get("verified"))
    if unverified:
        add(WARN, f"{unverified} resume bullets are marked unverified and are not used",
            'confirm the numbers in master_profile.json and change "verified": false to true')
    if PROFILE["candidate"].get("_check"):
        add(WARN, "LinkedIn link not confirmed", PROFILE["candidate"]["_check"] + ' Delete the "_check" line once confirmed.')

    add(OK if os.environ.get("GOOGLE_API_KEY") else TODO, "Gemini key in .env (scoring)",
        "" if os.environ.get("GOOGLE_API_KEY") else "START_HERE.md step 3: free key from aistudio.google.com/apikey into .env")
    creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "google_service_account.json"
    has_creds = creds.lstrip().startswith("{") or (Path(creds) if Path(creds).is_absolute() else ROOT / creds).exists()
    add(OK if has_creds else TODO, "Google Sheet key file",
        "" if has_creds else "START_HERE.md steps 4-5 (optional: without it everything stays in the local database)")
    from tailor import find_claude
    add(OK if find_claude() else TODO, "Claude Code (Claude-written tailoring for top matches)",
        "" if find_claude() else "optional: START_HERE.md step 6. Without it, tailoring uses the built-in method")
    from discover import watchlist_entries
    n = len(watchlist_entries())
    add(OK if n > 3 else TODO, f"Company watchlist: {n} working career-site links",
        "" if n > 3 else "double-click tools\\CHECK_WATCHLIST.bat to test the candidate links")
    from fill import simplify_installed
    add(OK if simplify_installed() else TODO, "Simplify Copilot in the APPLY window",
        "" if simplify_installed() else "press Simplify setup in the Control Center once (add it to Chrome, sign in to Simplify)")
    c = PROFILE["candidate"]
    add(OK, f"Emails: applications use {c['email']}; campus roles use {c.get('email_campus') or c['email']}")
    if not (ROOT / CONFIG["output_dir"] / "_track_resumes").exists():
        add(TODO, "Track resumes not built yet", "press Make track resumes in the Control Center")
    return report()


def report() -> int:
    print("=" * 60 + "\nSETUP CHECK\n" + "=" * 60)
    for mark, what, how in results:
        print(f"{mark} {what}" + (f"\n         -> {how}" if how else ""))
    todo = sum(1 for m, _, _ in results if m == TODO)
    print("=" * 60)
    print("Everything needed is in place." if not todo else f"{todo} item(s) left to set up (marked TODO above).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
