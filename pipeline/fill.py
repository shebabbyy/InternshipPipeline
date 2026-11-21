"""Stage 3: open each ready application in a dedicated Chrome window and fill what can be filled safely.

submit_mode: manual (default)  You click Submit yourself in each tab. Afterwards the script asks which ones you
                               submitted and updates the Sheet for you.
submit_mode: approve           The script clicks Submit only after you type y for that exact application.
Passwords: never typed in assisted mode. In account_mode auto, a password is read from Windows Credential
Manager and typed for you. Email verification and CAPTCHAs are always yours.

* Greenhouse / Lever: fills name, email, phone, LinkedIn, uploads resume + cover letter.
* Workday / iCIMS / Taleo / others: opens the page and writes FILL_SHEET.txt next to the packet with every
  answer ready to paste. Sign in yourself (once) in this window; it stays signed in. Simplify Copilot or
  Claude in Chrome can help you finish these tabs.
"""
from __future__ import annotations

import os
import time
import re
from datetime import datetime
from pathlib import Path

from common import CONFIG, PROFILE, ROOT, ask, db, email_for, log, mark_dirty
import apply_window as aw   # phase 1: real Chrome over CDP, URL router, click guard, Review banner
from answers import account_email   # phase 2: portal accounts always use Gmail (Nov 20 decision)
import resolver   # phase 3: job-board links -> the employer's own site

CLOSED_WORDS = ("no longer accepting applications", "no longer available", "position has been filled",
                "job has been filled", "posting has expired", "job is no longer", "page you are looking for doesn't exist",
                "the job you are looking for")


def fill_sheet(job: dict) -> Path:
    c, e, a = PROFILE["candidate"], PROFILE["education"], PROFILE["standard_answers"]
    folder = Path(job["resume"]).parent
    lines = [
        f"FILL SHEET — {job['company']} | {job['title']}", f"Link: {job['url']}",
        *([f"Company link: {resolver.target(job)[0]}"] if resolver.target(job)[0] != job["url"] else []), "",
        f"First name: {c['name'].split()[0]}", f"Last name: {c['name'].split()[-1]}", f"Email: {email_for(job)}", f"Portal account email: {account_email()}", f"Phone: {c['phone']}",
        f"Location: {c['location']}", f"LinkedIn: {c['linkedin']}", "School: University of Houston",
        "Degree: Bachelor of Business Administration (BBA)", f"Majors: {a['majors']}",
        f"Graduation: {e['graduation']}", f"GPA: {e['gpa']}", f"Class year: {a['class_year']}",
        f"Authorized to work in the US: {a['authorized_to_work_us']}", f"Require sponsorship: {a['requires_sponsorship']}",
        f"Resume file: {job['resume']}", f"Cover letter file: {job['cover']}", "",
        "Short answers: ask Claude in this project with /answer and paste the question.",
        "DO NOT invent skills. Check every autofilled Workday field against the resume before Submit.",
    ]
    path = folder / "FILL_SHEET.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _first_visible(loc):
    try:
        for i in range(min(loc.count(), 5)):
            if loc.nth(i).is_visible():
                return loc.nth(i)
    except Exception:  # noqa: BLE001
        pass
    return None


def try_fill(page, selectors: list[str], value: str, labels: tuple[str, ...] = ()) -> bool:
    """Fill the first visible, still-empty field matching an exact label or a CSS selector."""
    candidates = [page.get_by_label(lab, exact=True) for lab in labels] + [page.locator(sel) for sel in selectors]
    for loc in candidates:
        box = _first_visible(loc)
        if box is None:
            continue
        try:
            if not box.input_value():
                box.fill(value)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def best_file(path: str | None) -> str | None:
    """Prefer the PDF next to a .docx when it exists."""
    if not path:
        return None
    pdf = Path(path).with_suffix(".pdf")
    return str(pdf) if pdf.exists() else (path if Path(path).exists() else None)


def fill_greenhouse_lever(page, job: dict) -> int:
    c = PROFILE["candidate"]
    first, last = c["name"].split()[0], c["name"].split()[-1]
    fields = [
        (["input[name='first_name']", "input#first_name", "input[autocomplete='given-name']"], first, ("First Name", "First Name *")),
        (["input[name='last_name']", "input#last_name", "input[autocomplete='family-name']"], last, ("Last Name", "Last Name *")),
        (["input[name='name']"], c["name"], ("Full name", "Full name✱")),              # Lever uses one name box
        (["input[name='email']", "input#email", "input[type='email']"], email_for(job), ("Email", "Email *")),
        (["input[name='phone']", "input#phone", "input[type='tel']"], c["phone"], ("Phone", "Phone *")),
        (["input[name='urls[LinkedIn]']", "input[name*='linkedin' i]", "input[aria-label*='LinkedIn' i]"], c["linkedin"],
         ("LinkedIn Profile", "LinkedIn URL", "LinkedIn")),
    ]
    filled = sum(try_fill(page, sels, val, labels) for sels, val, labels in fields)
    files = [f for f in (best_file(job["resume"]), best_file(job["cover"])) if f]
    try:
        uploads = page.locator("input[type=file]")
        for i in range(min(uploads.count(), len(files))):
            uploads.nth(i).set_input_files(files[i])
            filled += 1
    except Exception:  # noqa: BLE001
        pass
    return filled


SUBMIT_NAMES = re.compile(r"^\s*(submit|submit application|submit my application|send application|apply|finish)\s*$", re.I)
DONE_WORDS = ("thank you for applying", "application submitted", "application has been submitted",
              "we have received your application", "application received", "successfully submitted")


# ---------------------------------------------------------------- accounts
# account_mode (config.yaml):
#   assisted = fill email/name on sign-up and sign-in pages, then pause for YOU to set the password
#              (use Chrome's "Suggest strong password") and click Create Account / Sign In.
#   auto     = also generate a unique password per site, keep it in Windows Credential Manager
#              (never in a text file), type it, and click Create Account / Sign In after your "y".
#   Either way: email verification links and CAPTCHAs are always yours to do.
KEYRING_SERVICE = "ShehabInternshipPipeline"


def _site(page) -> str:
    from urllib.parse import urlparse
    return urlparse(page.url).netloc.lower()


def _visible(loc):
    return [loc.nth(i) for i in range(loc.count()) if loc.nth(i).is_visible()]


def account_page_type(page) -> str:
    """'create', 'signin', or '' based on visible password boxes."""
    try:
        pw = _visible(page.locator("input[type=password]"))
    except Exception:  # noqa: BLE001
        return ""
    if len(pw) >= 2:
        return "create"
    if len(pw) == 1:
        return "signin"
    return ""


def site_password(site: str, create: bool) -> str | None:
    import secrets
    import string

    import keyring
    pw = keyring.get_password(KEYRING_SERVICE, site)
    if pw:
        return pw
    if CONFIG.get("password_style", "unique") == "default":
        # one password you set yourself with SET_DEFAULT_PASSWORD.bat (stored in Windows Credential Manager)
        return keyring.get_password(KEYRING_SERVICE, "__default__")
    if not create:
        return None
    alphabet = string.ascii_letters + string.digits
    # upper + lower + digit + symbol guaranteed (Workday's usual rules), the rest random
    pw = "".join(secrets.choice(alphabet) for _ in range(14)) + secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ") + "a7!"
    keyring.set_password(KEYRING_SERVICE, site, pw)
    return pw


def _click_named(page, pattern) -> bool:
    try:
        btns = _visible(page.get_by_role("button", name=re.compile(pattern, re.I)))
        if len(btns) == 1:
            aw.safe_click(btns[0], page)   # refuses submit/consent buttons
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def handle_account(page, job) -> None:
    kind = account_page_type(page)
    if not kind:
        return
    mode = CONFIG.get("account_mode", "assisted")
    site = _site(page)
    c = PROFILE["candidate"]
    for sel in ("input[type=email]", "input[data-automation-id='email']", "input[name*='email' i]", "input[id*='email' i]"):
        boxes = _visible(page.locator(sel))
        if boxes:
            boxes[0].fill(account_email())
            break
    if kind == "create":
        try_fill(page, ["input[name*='firstName' i]", "input[name*='first_name' i]"], c["name"].split()[0], ("First Name",))
        try_fill(page, ["input[name*='lastName' i]", "input[name*='last_name' i]"], c["name"].split()[-1], ("Last Name",))
    print(f"\n>>> {job['company']}: this site needs you to {'CREATE AN ACCOUNT' if kind == 'create' else 'SIGN IN'} ({site}).")
    if mode == "auto":
        try:
            pw = site_password(site, create=(kind == "create"))
        except Exception as exc:  # noqa: BLE001
            pw = None
            print(f"    Password vault unavailable ({exc.__class__.__name__}); finish this one by hand.")
        if pw:
            for box in _visible(page.locator("input[type=password]")):
                box.fill(pw)
            if kind == "create":
                print("    Email + a unique password are filled (saved in Windows Credential Manager under")
                print(f"    '{KEYRING_SERVICE}' / {site}). Check any terms box yourself if you agree to them.")
                if ask("    Create the account now? [y/n]: ").lower() == "y":
                    _click_named(page, r"^\s*(create account|create an account|register|sign up)\s*$") or \
                        print("    Couldn't find one clear Create Account button; click it yourself.")
                    print(f"    Then click the verification link sent to {account_email()} if one arrives.")
            else:
                _click_named(page, r"^\s*(sign in|log in|login)\s*$") or print("    Click Sign In yourself.")
            page.wait_for_timeout(3000)
            return
        if kind == "signin":
            print("    No saved password for this site in the vault; sign in yourself.")
    else:
        print("    Your email is filled. Click the password box, use Chrome's 'Suggest strong password',")
        print("    then click Create Account / Sign In and verify your email if asked.")
    ask("    Press Enter here once you're signed in to continue...")


def find_submit(page):
    """Return the single visible final-submit button, or None if unclear."""
    try:
        buttons = page.get_by_role("button", name=SUBMIT_NAMES)
        visible = [buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible()]
        return visible[0] if len(visible) == 1 else None
    except Exception:  # noqa: BLE001
        return None


def approve_and_submit(conn, opened) -> None:
    """Go tab by tab. Nothing is submitted unless you type y for that exact application."""
    shots = ROOT / "output" / "_submissions"
    shots.mkdir(parents=True, exist_ok=True)
    print("\n" + "=" * 60 + "\nAPPROVAL MODE: finish each tab (sign in, answer questions, reach the final Review page),")
    print("then come back here. Nothing is submitted without your 'y'.\n" + "=" * 60)
    for job, page in opened:
        if page.is_closed():
            continue
        page.bring_to_front()
        print(f"\n>>> {job['company']} | {job['title']} (score {job['score']})")
        print(f"    Resume: {job['resume']}")
        ans = ""
        while ans not in ("y", "n", "q"):
            ans = ask("    Reached the final page and checked every field? Submit now? "
                      "[y = submit / n = skip / a = sign-in or create-account help / q = stop]: ", "q").lower()
            if ans == "a":
                handle_account(page, job)
                ans = ""
        if ans == "q":
            break
        if ans == "n":
            print("    Skipped. It stays as Filled and reopens next time you run APPLY.")
            continue
        button = find_submit(page)
        if button is None:
            print("    I can't find one clear Submit button on this page. Click Submit yourself in the tab.")
            if ask("    Did you submit it? [y/n]: ").lower() == "y":
                mark_dirty(conn, job["id"], status="APPLIED", notes=f"Submitted manually {datetime.now():%Y-%m-%d %H:%M}")
            continue
        try:
            aw.safe_click(button, page)   # always refused: only Shehab clicks Submit
        except aw.GuardError:
            print("    The pipeline never clicks Submit. Click Submit yourself in the tab.")
            if ask("    Did you submit it? [y/n]: ").lower() == "y":
                mark_dirty(conn, job["id"], status="APPLIED", notes=f"Submitted by you {datetime.now():%Y-%m-%d %H:%M}")
            continue
        page.wait_for_timeout(6000)
        text = (page.inner_text("body") or "").lower()
        shot = shots / f"{re.sub(r'[^A-Za-z0-9]+', '', job['company'])}_{datetime.now():%Y%m%d_%H%M}.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception:  # noqa: BLE001
            pass
        if any(w in text for w in DONE_WORDS):
            mark_dirty(conn, job["id"], status="APPLIED", notes=f"Submitted with approval {datetime.now():%Y-%m-%d %H:%M}. Proof: {shot.name}")
            print(f"    ✅ Submitted. Confirmation screenshot: {shot}")
        else:
            print("    ⚠️ Clicked Submit but didn't see a confirmation. Check the tab (missing field? another page?).")
            if ask("    Is it actually submitted? [y/n]: ").lower() == "y":
                mark_dirty(conn, job["id"], status="APPLIED", notes=f"Submitted with approval {datetime.now():%Y-%m-%d %H:%M}")
    ask("\nDone. Press Enter to close the browser (outreach drafts are made next).")


def confirm_manual(conn, opened) -> None:
    """Manual mode: you clicked Submit yourself. Record which ones went through so you don't have to edit the Sheet."""
    print("\n" + "=" * 60 + "\nAll tabs are open. NOTHING has been submitted by the script.")
    print("Work through each tab, click Submit yourself, then come back here.\n" + "=" * 60)
    ask("Press Enter when you're finished with the tabs...")
    for job, _ in opened:
        ans = ""
        while ans not in ("y", "n", "s"):
            ans = ask(f"  {job['company']} | {job['title']}: did you submit it? "
                      "[y = yes / n = not yet, reopen next time / s = skip this job for good]: ", "n").lower()
        if ans == "y":
            mark_dirty(conn, job["id"], status="APPLIED", notes=f"Submitted by you {datetime.now():%Y-%m-%d %H:%M}")
        elif ans == "s":
            mark_dirty(conn, job["id"], status="SKIPPED", notes="Skipped by you during APPLY")


def wait_for_window_close(ctx, watcher=None, session=None) -> None:
    """Control Center mode (no keyboard): keep the window open until you close it. Statuses stay 'Filled';
    you record what you submitted with the I submitted it button. Never clicks Submit, in any submit_mode."""
    print("\n" + "=" * 60 + "\nThe application is open. NOTHING has been submitted by the script.", flush=True)
    print("Review every field, click Submit yourself, then CLOSE the Chrome window and use the buttons in the "
          "Control Center (I submitted it / Not yet / Skip).\n" + "=" * 60, flush=True)
    started = time.time()
    while True:
        if session is not None and not session.connected():
            break   # real Chrome was closed
        pages = [pg for pg in ctx.pages if not pg.is_closed()]
        if not pages:
            break
        if watcher is not None:
            try:
                watcher.tick()   # banners + Review-page screenshot
            except Exception:  # noqa: BLE001
                pass
        try:
            pages[-1].wait_for_timeout(1000)   # any open tab keeps us waiting; closing one tab is fine
        except Exception:  # noqa: BLE001  (that tab closed in the meantime; look again)
            continue
    print(f"Apply window closed after {int(time.time() - started)} s. Now press I submitted it / Not yet / Skip.", flush=True)


def open_browser(p, headless: bool = False):
    """The pipeline's own Chrome window. Extensions stay ON (Playwright turns them off by default)
    so Simplify Copilot, installed once with SETUP_SIMPLIFY.bat, works here too."""
    profile_dir = ROOT / CONFIG["browser_profile_dir"]
    opts = dict(headless=headless, no_viewport=True, ignore_default_args=["--disable-extensions"],
                args=["--start-maximized", "--new-window"])   # always a normal, visible window
    try:
        return p.chromium.launch_persistent_context(str(profile_dir), channel="chrome", **opts)
    except Exception:  # noqa: BLE001  (Chrome not found -> bundled Chromium)
        return p.chromium.launch_persistent_context(str(profile_dir), **opts)


def simplify_installed() -> bool:
    if CONFIG.get("apply_browser", "cdp") == "cdp":
        return aw.simplify_installed()   # the real-Chrome apply profile (phase 1)
    ext = ROOT / CONFIG["browser_profile_dir"] / "Default" / "Extensions" / CONFIG.get("simplify_extension_id", "")
    return ext.is_dir() and any(ext.iterdir())


def run(limit: int = 6, job_id: str | None = None) -> int:
    conn = db()
    # New packets first, then anything opened earlier but never submitted. job_id = just that one.
    where, args = ("AND id=?", (job_id,)) if job_id else ("", ())
    jobs = [dict(r) for r in conn.execute(
        f"SELECT * FROM jobs WHERE status IN ('PACKET_READY','FILLED') {where} "
        "ORDER BY CASE status WHEN 'PACKET_READY' THEN 0 ELSE 1 END, score DESC LIMIT ?", (*args, limit))]
    ready = []
    for job in jobs:
        if not job["url"] or not str(job["url"]).startswith("http"):
            mark_dirty(conn, job["id"], status="BLOCKED", notes="No application link. Paste the link in the Sheet, then set Status back to Qualified.")
        elif not job["resume"] or not Path(job["resume"]).exists():
            mark_dirty(conn, job["id"], status="QUALIFIED", notes="Packet files missing on this computer; they will be rebuilt on the next run.")
        else:
            ready.append(job)
    if not ready:
        log("Fill: no packets waiting. (Run a scan first if you expected some.)")
        return 0
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Fill: Playwright is not installed. Run INSTALL.bat (in the InternshipPipeline folder), then try again.")
        return 0
    headless = bool(os.environ.get("PIPELINE_HEADLESS"))  # tests only; you always get a visible window
    with sync_playwright() as p:
        sess = aw.open_session(p, headless, open_browser)   # real Chrome + apply_profile (Simplify lives there)
        ctx = sess.ctx
        watcher = aw.Watcher()
        opened = []
        for job in ready:
            sheet = fill_sheet(job)
            # reuse Chrome's empty first tab instead of leaving a stray blank tab in front
            blank = [pg for pg in ctx.pages if not pg.is_closed() and pg.url in ("about:blank", "chrome://newtab/", "")]
            page = blank[0] if (blank and not opened) else ctx.new_page()
            go, hint = resolver.target(job)   # phase 3: the company's own link when one was found
            if go != job["url"]:
                log(f"  {job['company']}: opening the company link instead of the job board ({go[:90]})")
            try:
                page.goto(go, timeout=60000)
                page.wait_for_timeout(3000)
            except Exception as exc:  # noqa: BLE001
                mark_dirty(conn, job["id"], status="BLOCKED", notes=f"Page did not load ({exc.__class__.__name__}). Open the link yourself.")
                page.close()
                continue
            try:
                body = (page.inner_text("body", timeout=5000) or "").lower()
            except Exception:  # noqa: BLE001
                body = ""
            if not hint and any(w in body for w in CLOSED_WORDS):   # a careers search page is not the posting
                mark_dirty(conn, job["id"], status="SKIPPED", notes="Posting looks closed (page says it is no longer available).")
                log(f"  {job['company']}: posting looks closed; skipped.")
                page.close()
                continue
            kind = aw.route(page.url)   # after redirects
            use_wd = kind == "workday" and CONFIG.get("workday_autofill", True)
            if use_wd and not (sess.cdp or headless):
                # Nov 21: the Playwright fallback window carries automation flags (--no-sandbox, webdriver) that
                # sites can detect. Never auto-fill Workday from it; he applies by hand there.
                use_wd = False
                log(f"  {job['company']}: apply window isn't your real Chrome (an old apply window was still open), "
                    "so the Workday auto-fill is off. Close every apply-window Chrome, then press Open application again.")
            if not use_wd:   # the Workday filler handles its own sign-in / account
                try:
                    handle_account(page, job)
                except Exception as exc:  # noqa: BLE001
                    log(f"  {job['company']}: account step skipped ({exc.__class__.__name__})")
            n, filled = 0, {}
            if use_wd:
                import workday   # phase 2
                try:
                    outcome, filled = workday.fill(page, job, site_password,
                                                   {"resume": best_file(job["resume"]), "cover": best_file(job.get("cover"))})
                except Exception as exc:  # noqa: BLE001
                    outcome = f"stopped ({exc.__class__.__name__})"
                    log(f"  {job['company']}: Workday filler stopped ({exc.__class__.__name__}); finish by hand or with Simplify.")
                n = len(filled)
                note = (f"Workday: filled {n} fields, stopped at Review. Check the panel, then Submit yourself."
                        if outcome == "review" else
                        f"Workday: filled {n} fields, then handed over ({outcome}). Finish by hand or with Simplify.")
            elif kind == "greenhouse_lever":
                try:
                    n = fill_greenhouse_lever(page, job)
                except Exception as exc:  # noqa: BLE001
                    log(f"  {job['company']}: autofill skipped ({exc.__class__.__name__})")
            if not use_wd:
                note = (f"Auto-filled {n} fields. Review, answer any custom questions, then Submit." if n else
                        f"{hint or aw.BANNERS[kind]} Answers to paste: {sheet.name}.")
            mark_dirty(conn, job["id"], status="FILLED", notes=note)
            log(f"  {job['company']}: {note}")
            custom = hint if kind not in ("workday", "greenhouse_lever") else None
            aw.show_banner(page, kind, text=custom)
            watcher.add(page, job, filled, banner=custom)
            try:
                watcher.tick()   # if the filler reached Review: screenshot + field check right away
            except Exception:  # noqa: BLE001
                pass
            opened.append((job, page))
        if opened:
            try:
                opened[0][1].bring_to_front()   # show the application tab, not a blank one
            except Exception:  # noqa: BLE001
                pass
            print("If you don't see the window, look for a second Chrome icon on the taskbar.", flush=True)
        if opened and os.environ.get("PIPELINE_GUI"):
            wait_for_window_close(ctx, watcher, sess)   # Control Center: no keyboard, never submits
        elif opened and CONFIG.get("submit_mode", "manual") == "approve":
            approve_and_submit(conn, opened)
        elif opened:
            confirm_manual(conn, opened)
        sess.close()   # real Chrome: only disconnects; the window stays yours
    return len(opened)


if __name__ == "__main__":
    import argparse
    from common import require_lock
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", help="open only this job id")
    ap.add_argument("--limit", type=int, default=6)
    a = ap.parse_args()
    require_lock("apply")
    run(limit=a.limit, job_id=a.job)
