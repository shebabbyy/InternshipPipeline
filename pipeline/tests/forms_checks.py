# Greenhouse / Lever custom-question checks (Nov 23). Run from offline_test.py via exec (uses its check() and IP).
import re as _re, tempfile as _tf
from pathlib import Path as _P
import answers as ab, form_questions as fq, apply_window as aw3
import common as _common

ab.BANK_PATH = _P(_tf.mkdtemp()) / "answers.yaml"
ab.INBOX_PATH = ab.BANK_PATH.parent / "_answers_inbox.json"
ab.PENDING_PATH = ab.BANK_PATH.parent / "_apply_pending.json"
_src = (IP / "pipeline" / "form_questions.py").read_text(encoding="utf-8")
check("forms static: form_questions.py never clicks directly (only aw.safe_click), never ticks boxes",
      not _re.search(r"(?<!self)\.click\(", _src) and ".check(" not in _src and ".set_checked(" not in _src
      and 'keyboard.press("Enter")' not in _src)
_fs = (IP / "pipeline" / "fill.py").read_text(encoding="utf-8")
check("forms wiring: fill.py runs the question filler after the Greenhouse/Lever basics", "form_questions.fill(" in _fs)
try:
    from playwright.sync_api import sync_playwright as _sp
    _job = {"id": "g1", "company": "Charles River Associates", "title": "Analyst Intern", "source": "linkedin", "notes": ""}
    _seen = []
    def _him(ff, reason, items):
        _seen.append([i["label"] for i in items])
        ab.queue_answer("Practice Preference #1", "Finance & Damages")
        ab.queue_answer("Location preference #1", "Houston")
        ff.page.check("#q_cert")                   # the certify box is his
        ab.queue_answer("", cont=True)
    with _sp() as _p:
        _b = _p.chromium.launch(headless=True)
        _pg = _b.new_page()
        _pg.goto((IP / "pipeline" / "tests" / "fake_greenhouse.html").resolve().as_uri())
        _pg.fill("#first_name", "Shehab"); _pg.fill("#last_name", "Moqbel"); _pg.fill("#email", "x@y.com")
        _out, _filled, _left = fq.fill(_pg, _job, on_pause=_him, wait_minutes=0.2)
        _d = _pg.evaluate("collect()")
        _P_ = _common.PROFILE
        check("browser forms: comboboxes / radios / text answered from the bank, how-did-you-hear = real source",
              _d["auth"] == "Yes" and _d["spon"] == "No" and _d["hear"] == "LinkedIn" and _d["gpa"] == _P_["education"]["gpa"]
              and _d["q18"] == "y" and _d["reloc"] == "No", str(_d))
        check("browser forms: async search boxes (city, school) typed and picked",
              _d["loc"] == "Houston, Texas, United States" and _d["school"] == "University of Houston", str(_d))
        check("browser forms: demographics declined", _d["gender"] == "Decline To Self Identify" and _d["vet"] == "I don't wish to answer", str(_d))
        check("browser forms: unknown required questions + certify box paused for him; his Control Center answers filled",
              _seen and set(_seen[0]) == {"Practice Preference #1", "Location preference #1", "I certify that the information above is true"}
              and _d["pref"] == "Finance & Damages" and _d["off"] == "Houston" and _out == "ready", str((_seen, _d, _out, _left)))
        check("browser forms: optional essay + optional checkboxes left blank, never submitted",
              _d["why"] == "" and _d["offices"] == 0 and not _pg.evaluate("S.submitted"), str(_d))
        _b.close()
except ImportError:
    print("SKIP browser forms (Playwright not installed)")
