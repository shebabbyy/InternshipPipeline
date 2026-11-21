# Phase 1 checks (apply window foundation). Run from offline_test.py via exec: uses its check(), IP, fill.
# Also runnable alone:  python pipeline/tests/phase1_checks.py
import os as _os, re as _re, sys as _sys
from pathlib import Path as _P
if "check" not in globals():
    _root = _P(__file__).resolve().parents[2]
    _sys.path.insert(0, str(_root / "pipeline"))
    _fails = []
    def check(name, cond, extra=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
        if not cond:
            _fails.append(name)
    IP = _root
import apply_window as aw

# router
_cases = {
    "https://protiviti.wd5.myworkdayjobs.com/en-US/Protiviti_Careers/job/Houston/Intern_R1": "workday",
    "https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/job/1": "workday",
    "https://boards.greenhouse.io/acme/jobs/1": "greenhouse_lever",
    "https://job-boards.greenhouse.io/acme/jobs/1": "greenhouse_lever",
    "https://jobs.lever.co/acme/1": "greenhouse_lever",
    "https://careers-acme.icims.com/jobs/1/job": "other_ats",
    "https://acme.taleo.net/careersection/2/jobdetail.ftl": "other_ats",
    "https://career4.successfactors.com/sfcareer/x": "other_ats",
    "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/x": "other_ats",
    "https://www.linkedin.com/jobs/view/123": "job_board",
    "https://www.indeed.com/viewjob?jk=1": "job_board",
    "https://acme.com/careers/1": "manual",
    "": "manual", "not a url": "manual",
    "https://fakemyworkdayjobs.com/x": "manual",            # suffix match must be on a dot boundary
    "https://greenhouse.io.evil.com/x": "manual",
}
_bad = {u: aw.route(u) for u, want in _cases.items() if aw.route(u) != want}
check("phase1 router: tool picked from the URL", not _bad, str(_bad))

# click guard (pure rules)
B = lambda **k: {"tag": "button", "type": "", "role": "", "text": "", "aria": "", "aid": "", "label": "", **k}
_refuse = [B(text="Submit"), B(text="Submit Application"), B(text="Send application"), B(text="Apply"), B(text="Apply Now"),
           B(text="Finish"), B(aid="bottom-navigation-submit-button", text="x"), B(aria="Submit"),
           B(tag="input", type="checkbox", label="I agree to the Terms and Conditions"),
           B(tag="input", type="checkbox", label="I certify that the information provided is true and complete"),
           B(role="checkbox", label="I consent to the privacy notice"),
           B(tag="input", type="radio", label="I acknowledge the statement above")]
check("phase1 guard: refuses submit/apply/finish and consent boxes", all(aw.blocked_reason(i) for i in _refuse),
      str([i for i in _refuse if not aw.blocked_reason(i)]))
check("phase1 guard: refuses Next on the Review page only",
      aw.blocked_reason(B(aid="bottom-navigation-next-button", text="Next"), on_review=True)
      and not aw.blocked_reason(B(aid="bottom-navigation-next-button", text="Next"), on_review=False))
_allow = [B(text="Next"), B(text="Sign In"), B(text="Create Account"), B(text="Upload"), B(text="Add Another"),
          B(aid="adventureButton", text="Apply"), B(aid="applyManually", text="Apply Manually"),
          B(tag="input", type="checkbox", label="I currently work here")]
check("phase1 guard: allows normal steps (incl. Workday's Apply that only starts the form)",
      not any(aw.blocked_reason(i) for i in _allow), str([(i, aw.blocked_reason(i)) for i in _allow if aw.blocked_reason(i)]))

# static: pipeline code only clicks through the guard
_fill_src = (IP / "pipeline" / "fill.py").read_text(encoding="utf-8")
_aw_src = (IP / "pipeline" / "apply_window.py").read_text(encoding="utf-8")
check("phase1 static: fill.py never calls .click( directly", not _re.search(r"\.click\(", _fill_src),
      str(_re.findall(r".*\.click\(.*", _fill_src)))
check("phase1 static: apply_window has exactly one raw click, inside safe_click", _aw_src.count(".click(") == 1
      and "    locator.click(**kw)" in _aw_src)
check("phase1 static: fill.py uses the router, session and watcher",
      "aw.route(" in _fill_src and "aw.open_session(" in _fill_src and "watcher.tick()" in _fill_src)

check("audit: account verification hint names the routed email, not a fixed inbox", "cougarnet email" not in _fill_src)
_tl = (IP / "pipeline" / "tailor.py").read_text(encoding="utf-8")
check("audit: tailoring refuses jobs below qualify_score (except hand-added / single job)",
      'CONFIG["qualify_score"]' in _tl and '"added by hand"' in _tl)

# real Chrome launch arguments
_args = aw.chrome_args("chrome.exe", "https://x")
check("phase1 chrome: dedicated profile + local debug port, never the default profile",
      any(a.startswith("--user-data-dir=") and a.endswith(str(aw.profile_dir())) for a in _args)
      and f"--remote-debugging-port={aw.cdp_port()}" in _args and "--load-extension" not in " ".join(_args)
      and "apply_profile" in str(aw.profile_dir()))
check("phase1 chrome: headless/tests keep the old Playwright launch",
      isinstance(aw.open_session(None, headless=True, legacy_launch=lambda p, h: "legacy"), aw.Session)
      and aw.open_session(None, headless=True, legacy_launch=lambda p, h: "legacy").ctx == "legacy")
_gi = (IP / ".gitignore")
if _gi.exists():
    check("phase1 repo: apply_profile/ is git-ignored", "apply_profile" in _gi.read_text(encoding="utf-8"))

# browser: guard + banner on a live page (needs Playwright + Chromium)
try:
    from playwright.sync_api import sync_playwright as _sp
    _html = """<html><body><h2>Review</h2><button id=s>Submit</button><button id=n>Next</button>
      <label><input type=checkbox id=t> I agree to the terms</label>
      <label><input type=checkbox id=w> I currently work here</label></body></html>"""
    with _sp() as _p:
        _b = _p.chromium.launch(headless=True)
        _pg = _b.new_page()
        _pg.set_content(_html)
        _refused = []
        for _sel in ("#s", "#t"):
            try:
                aw.safe_click(_pg.locator(_sel), _pg)
            except aw.GuardError:
                _refused.append(_sel)
        aw.safe_click(_pg.locator("#w"), _pg)
        check("browser: guard refuses Submit + terms box, allows a normal box",
              _refused == ["#s", "#t"] and _pg.is_checked("#w") and not _pg.is_checked("#t"), str(_refused))
        check("browser: Review page detected and red banner shown",
              aw.show_banner(_pg, "workday") and "Only YOU click Submit" in _pg.inner_text("#pipeline-banner"))
        _pg.set_content("<html><body><h1>Job</h1></body></html>")
        check("browser: normal page gets the yellow route banner",
              not aw.show_banner(_pg, "other_ats") and "Simplify Autofill" in _pg.inner_text("#pipeline-banner"))
        _b.close()
except Exception as _exc:  # noqa: BLE001
    print("SKIP phase1 browser checks (Playwright or Chromium not available here):", repr(_exc)[:120])

if "_fails" in globals():
    print("\nFAILED: " + ", ".join(_fails) if _fails else "\nALL PHASE 1 CHECKS PASSED")
