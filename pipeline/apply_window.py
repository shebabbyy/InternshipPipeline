"""Apply window foundation (Control Center v2, phase 1).

1. Real Chrome, not a Playwright-launched Chrome. Chrome is started as a normal program with its own profile
   (apply_profile/) and a local debugging port, then Playwright attaches with connect_over_cdp. Because Chrome
   is not launched by Playwright, the Web Store works there, so Simplify can be installed once by hand
   (Control Center: Simplify setup). Chrome 136+ refuses debugging on the default profile, so it is always the
   dedicated one. Tests (PIPELINE_HEADLESS) and apply_browser: playwright keep the old launch.
2. Router: picks the tool from the application URL. He never chooses.
3. Click guard: every click the code makes goes through safe_click(). It refuses Submit / Apply / Finish /
   Send application, Workday's Next button on the Review page, and any terms / consent / certify box.
4. Banner: a strip at the top of the page saying what to do; on a Review page it says only he clicks Submit,
   and a screenshot is saved to output/_submissions/.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from common import CONFIG, ROOT, log

# ---------------------------------------------------------------- router
WORKDAY_HOSTS = ("myworkdayjobs.com", "myworkdaysite.com", "myworkday.com")
GH_LEVER_HOSTS = ("greenhouse.io", "lever.co")
JOB_BOARD_HOSTS = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "simplyhired.com", "monster.com",
                   "google.com", "joinhandshake.com", "wayup.com", "jobright.ai", "builtin.com", "dice.com", "careerbuilder.com")
OTHER_ATS_HOSTS = ("icims.com", "taleo.net", "successfactors.com", "successfactors.eu", "sapsf.com", "oraclecloud.com",
                   "brassring.com", "jobvite.com", "smartrecruiters.com", "ashbyhq.com", "workable.com", "adp.com",
                   "ultipro.com", "ukg.com", "paylocity.com", "paycomonline.net", "avature.net", "phenompeople.com",
                   "eightfold.ai", "csod.com", "dayforcehcm.com")

BANNERS = {
    "workday": "Workday. Auto-fill for Workday arrives in the next update. For now: click Simplify Autofill, "
               "then check every field. Only you click Submit.",
    "greenhouse_lever": "Filled from your profile. Check every field, answer any extra questions, then you click Submit.",
    "other_ats": "Click Simplify Autofill (the code never clicks it), then check every field. Only you click Submit.",
    "job_board": "Job-board link. Click the employer's Apply button; the pipeline will resolve these links "
                 "to the company site in a later update.",
    "manual": "Unknown site. Apply by hand or with Simplify. Only you click Submit.",
}
REVIEW_BANNER = "REVIEW PAGE. Check every field against your resume. Only YOU click Submit. Screenshot saved."


def _host(url: str) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _on(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def route(url: str) -> str:
    """'workday' | 'greenhouse_lever' | 'other_ats' | 'job_board' | 'manual'. Decided by the URL only."""
    host = _host(url)
    path = (urlparse(url or "").path or "").lower()
    if not host:
        return "manual"
    if _on(host, WORKDAY_HOSTS) or (_on(host, ("workday.com",)) and "/recruiting/" in path):
        return "workday"
    if _on(host, GH_LEVER_HOSTS):
        return "greenhouse_lever"
    if _on(host, OTHER_ATS_HOSTS):
        return "other_ats"
    if _on(host, JOB_BOARD_HOSTS):
        return "job_board"
    # local test pages use folder names like /greenhouse.io/form.html
    if host in ("127.0.0.1", "localhost") and ("greenhouse.io" in path or "lever.co" in path):
        return "greenhouse_lever"
    return "manual"


# ---------------------------------------------------------------- click guard
class GuardError(RuntimeError):
    """The code tried to click something only Shehab may click."""


SUBMIT_TEXT = re.compile(r"^\s*(submit( my)?( application)?|send( my)? application|apply( now| for this job)?|"
                         r"finish|complete( application)?|confirm( and)? submit)\s*$", re.I)
CONSENT_TEXT = re.compile(r"\b(agree|terms|consent|certif|attest|acknowledg|accept|authori[sz]e|privacy (policy|notice)|"
                          r"declare|true and (complete|correct|accurate)|electronic signature)", re.I)
# Workday buttons the code is allowed to press even though their text says Apply (they only START an application)
ALLOWED_AUTOMATION_IDS = {"adventureButton", "applyManually", "autofillWithResume", "useMyLastApplication",
                          "createAccountLink", "signInLink", "utilityButtonSignIn"}
WORKDAY_NEXT_IDS = {"bottom-navigation-next-button", "pageFooterNextButton"}

_INFO_JS = """el => {
  const lab = [];
  if (el.labels) for (const l of el.labels) lab.push(l.innerText || '');
  const by = el.getAttribute('aria-labelledby');
  if (by) for (const id of by.split(/\\s+/)) { const n = document.getElementById(id); if (n) lab.push(n.innerText || ''); }
  const wrap = el.closest('label, [data-automation-id*="checkbox" i], fieldset');
  if (wrap) lab.push(wrap.innerText || '');
  return {tag: el.tagName.toLowerCase(), type: (el.getAttribute('type') || '').toLowerCase(),
          role: (el.getAttribute('role') || '').toLowerCase(), text: (el.innerText || el.value || '').trim().slice(0, 200),
          aria: el.getAttribute('aria-label') || '', aid: el.getAttribute('data-automation-id') || '',
          label: lab.join(' ').trim().slice(0, 400)};
}"""


def blocked_reason(info: dict, on_review: bool = False) -> str:
    """Why the code must not click this element ('' = allowed). Pure function, tested offline."""
    aid = info.get("aid") or ""
    words = " ".join(x for x in (info.get("text"), info.get("aria")) if x).strip()
    is_box = info.get("type") in ("checkbox", "radio") or info.get("role") in ("checkbox", "radio", "switch")
    if is_box and CONSENT_TEXT.search(" ".join((words, info.get("label") or ""))):
        return "terms/consent/certify box: only you tick these"
    if aid in ALLOWED_AUTOMATION_IDS:
        return ""
    if on_review and (aid in WORKDAY_NEXT_IDS or re.match(r"^\s*(next|continue|save and continue)\s*$", words, re.I)):
        return "Next on the Review page submits the application"
    if "submit" in aid.lower():
        return "submit button"
    for part in (info.get("text") or "", info.get("aria") or ""):
        if part and SUBMIT_TEXT.match(part):
            return f"final '{part.strip()}' button"
    return ""


def safe_click(locator, page=None, **kw) -> None:
    """The only way pipeline code clicks. Raises GuardError instead of clicking a forbidden element."""
    info = locator.evaluate(_INFO_JS)
    reason = blocked_reason(info, on_review=bool(page is not None and is_review_page(page)))
    if reason:
        raise GuardError(f"Refused to click ({reason}).")
    locator.click(**kw)


# ---------------------------------------------------------------- review page + banner
_REVIEW_JS = """() => {
  const vis = e => !!(e && e.offsetParent !== null);
  const q = s => [...document.querySelectorAll(s)].filter(vis);
  if (q('[data-automation-id="reviewJobApplicationPage"], [data-automation-id*="review" i][data-automation-id*="page" i]').length) return true;
  const step = q('[data-automation-id="progressBarActiveStep"]').map(e => e.innerText.toLowerCase()).join(' ');
  if (/\\breview\\b/.test(step)) return true;
  const heads = q('h1, h2, h3').map(e => e.innerText.trim().toLowerCase());
  const submit = q('button, input[type=submit]').some(b => /^\\s*submit( application)?\\s*$/i.test(b.innerText || b.value || ''));
  return heads.some(h => h === 'review' || h.startsWith('review your application')) && submit;
}"""

_BANNER_JS = """(t) => {
  let b = document.getElementById('pipeline-banner');
  if (!b) { b = document.createElement('div'); b.id = 'pipeline-banner';
    b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;pointer-events:none;' +
      'font:600 14px Segoe UI,Arial,sans-serif;padding:6px 12px;text-align:center;opacity:.95';
    (document.body || document.documentElement).appendChild(b); }
  b.style.background = t.review ? '#b00020' : '#ffd54f'; b.style.color = t.review ? '#fff' : '#000';
  b.textContent = 'Pipeline: ' + t.text;
}"""


def is_review_page(page) -> bool:
    try:
        return bool(page.evaluate(_REVIEW_JS))
    except Exception:  # noqa: BLE001
        return False


def show_banner(page, kind: str, review: bool | None = None) -> bool:
    """Draw or refresh the banner. Returns True when the page is a Review page."""
    if review is None:
        review = is_review_page(page)
    try:
        page.evaluate(_BANNER_JS, {"text": REVIEW_BANNER if review else BANNERS.get(kind, BANNERS["manual"]), "review": review})
    except Exception:  # noqa: BLE001  (page navigating)
        pass
    return review


def review_screenshot(page, job: dict) -> Path | None:
    shots = ROOT / "output" / "_submissions"
    shots.mkdir(parents=True, exist_ok=True)
    path = shots / f"{re.sub(r'[^A-Za-z0-9]+', '', job.get('company') or 'job')}_review_{datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return path
    except Exception:  # noqa: BLE001
        return None


class Watcher:
    """Called about once a second while the window is open: keeps banners current, screenshots each Review page once."""

    def __init__(self):
        self.tabs: dict = {}   # page -> (job, shot_taken)

    def add(self, page, job):
        self.tabs[page] = [job, False]

    def tick(self):
        for page, state in list(self.tabs.items()):
            if page.is_closed():
                self.tabs.pop(page, None)
                continue
            review = show_banner(page, route(page.url))
            if review and not state[1]:
                shot = review_screenshot(page, state[0])
                state[1] = True
                if shot:
                    print(f"Review page reached for {state[0].get('company')}; screenshot {shot.name}. Only you click Submit.",
                          flush=True)


# ---------------------------------------------------------------- real Chrome over CDP
def profile_dir() -> Path:
    return ROOT / CONFIG.get("apply_profile_dir", "apply_profile")


def cdp_port() -> int:
    return int(CONFIG.get("cdp_port", 9222))


def find_chrome() -> str | None:
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).exists():
        return env
    cands = []
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(var):
            cands.append(Path(os.environ[var]) / "Google" / "Chrome" / "Application" / "chrome.exe")
    cands.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    for c in cands:
        if c.exists():
            return str(c)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def chrome_args(chrome: str, url: str | None = None) -> list[str]:
    args = [chrome, f"--user-data-dir={profile_dir()}", f"--remote-debugging-port={cdp_port()}",
            "--no-first-run", "--no-default-browser-check",
            "--start-maximized", "--new-window"]
    return args + ([url] if url else [])


def cdp_alive(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port()}/json/version", timeout=timeout) as r:
            return "webSocketDebuggerUrl" in json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def start_chrome(url: str | None = None, wait_s: float = 20.0) -> bool:
    """Start the apply profile's Chrome (or reuse one already running). True when the debug port answers."""
    if cdp_alive():
        return True
    chrome = find_chrome()
    if not chrome:
        log("Apply window: Google Chrome not found. Install Chrome or set CHROME_PATH.")
        return False
    profile_dir().mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(chrome_args(chrome, url), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    end = time.time() + wait_s
    while time.time() < end:
        if cdp_alive(0.5):
            return True
        time.sleep(0.5)
    log("Apply window: Chrome started but its debug port never answered. Close every apply-window Chrome and try again.")
    return False


class Session:
    """What fill.py uses: .ctx (pages live here) and .close() (disconnects; leaves real Chrome to him)."""

    def __init__(self, ctx, browser=None, cdp=False):
        self.ctx, self.browser, self.cdp = ctx, browser, cdp

    def connected(self) -> bool:
        return self.browser.is_connected() if self.browser is not None else True

    def close(self):
        try:
            (self.browser.close() if self.cdp else self.ctx.close())   # CDP: disconnect only
        except Exception:  # noqa: BLE001
            pass


def open_session(p, headless: bool = False, legacy_launch=None) -> Session:
    mode = CONFIG.get("apply_browser", "cdp")
    if headless or mode != "cdp":
        return Session(legacy_launch(p, headless))
    if not start_chrome():
        log("Apply window: falling back to the old Playwright window (Simplify won't be there).")
        return Session(legacy_launch(p, headless))
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port()}")
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return Session(ctx, browser, cdp=True)


def simplify_installed() -> bool:
    ext = profile_dir() / "Default" / "Extensions" / CONFIG.get("simplify_extension_id", "")
    return ext.is_dir() and any(ext.iterdir())


SIMPLIFY_STORE = "https://chromewebstore.google.com/detail/" + CONFIG.get("simplify_extension_id", "")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--open-profile", action="store_true", help="open the apply profile's Chrome (to install Simplify / sign in)")
    ap.add_argument("--route", help="print the tool chosen for a URL")
    a = ap.parse_args()
    if a.route:
        print(route(a.route))
    elif a.open_profile:
        chrome = find_chrome()
        if not chrome:
            print("Google Chrome not found.")
            sys.exit(1)
        if cdp_alive():
            print("The apply window is already open. Close it first, then run this again.")
            sys.exit(1)
        profile_dir().mkdir(parents=True, exist_ok=True)
        print("Opening the apply window's Chrome. Install Simplify (Add to Chrome) and sign in to Simplify with")
        print("shehabby05@gmail.com, then close the window. Installed now: " + ("yes" if simplify_installed() else "no"))
        subprocess.Popen(chrome_args(chrome, SIMPLIFY_STORE))
