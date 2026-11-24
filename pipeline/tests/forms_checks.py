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
        import fill as _fill
        _tmpd = _P(_tf.mkdtemp())
        (_tmpd / "resume.pdf").write_bytes(b"%PDF-1.4"); (_tmpd / "cover.pdf").write_bytes(b"%PDF-1.4")
        _nup = _fill.upload_files(_pg, str(_tmpd / "resume.pdf"), str(_tmpd / "cover.pdf"))
        _fs = _pg.evaluate("S.files")
        check("browser forms: resume -> resume box, cover letter -> cover-letter box, other file questions untouched "
              "(even though the resume box is replaced after its upload; Nov 24 CRA bug)",
              _nup == 2 and _fs.get("resume") == ["resume.pdf"] and _fs.get("cover_letter") == ["cover.pdf"] and not _fs.get("question_9"), str(_fs))
        _out, _filled, _left = fq.fill(_pg, _job, on_pause=_him, wait_minutes=0.2)
        _d = _pg.evaluate("collect()")
        _P_ = _common.PROFILE
        check("browser forms: comboboxes / radios / text answered from the bank, how-did-you-hear = real source",
              _d["auth"] == "Yes" and _d["spon"] == "No" and _d["hear"] == "LinkedIn" and _d["gpa"] == _P_["education"]["gpa"]
              and _d["q18"] == "y" and _d["reloc"] == "No", str(_d))
        check("browser forms: phone filled even though choosing the country re-draws the phone box (Nov 24 CRA)",
              _d["country"] == "United States" and _d["phone"] and _d["phone"][-4:] == _re.sub(r"\D", "", _P_["candidate"]["phone"])[-4:], str(_d))
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

# ---------------- Nov 24: code hygiene + Gemini busy retry
import warnings as _w, types as _T
_bad = []
for _f in sorted((IP / "pipeline").glob("*.py")):
    try:
        with _w.catch_warnings():
            _w.simplefilter("error")
            compile(_f.read_text(encoding="utf-8"), str(_f), "exec")
    except (SyntaxError, SyntaxWarning, DeprecationWarning) as _e:
        _bad.append(f"{_f.name}: {_e}")
check("hygiene: every pipeline .py compiles with warnings as errors (no invalid escapes in embedded JS)", not _bad, str(_bad))
_calls = []
class _C:
    class models:
        @staticmethod
        def generate_content(model, contents, config):
            _calls.append(model)
            if len(_calls) < 7:
                raise Exception("503 UNAVAILABLE This model is currently experiencing high demand")
            return _T.SimpleNamespace(text='{"ok": 1}')
class _Ty:
    @staticmethod
    def GenerateContentConfig(**k): return k
_sl = _common.time.sleep
_common.time.sleep = lambda s: None
try:
    _r1 = _common._gemini_round(_C, _Ty, ["a", "b"], "p", 3)
    _r2 = _common._gemini_round(_C, _Ty, ["a", "b"], "p", 3)
finally:
    _common.time.sleep = _sl
check("gemini: a round where every model is only busy (503) reports 'overloaded' so the caller waits and retries; the next round succeeds",
      _r1[0] is False and _r1[2] is True and _r2 == (True, {"ok": 1}), str((_r1, _r2)))
_src_c = (IP / "pipeline" / "common.py").read_text(encoding="utf-8")
check("gemini: busy rounds come from config (gemini_overload_rounds / gemini_overload_wait_s)",
      "gemini_overload_rounds" in _src_c and "gemini_overload_wait_s" in _src_c)

# ---------------- Nov 24: answers learned from the live CRA form
_j = {"company": "Charles River Associates", "title": "Analyst Intern", "source": "linkedin", "notes": ""}
_grad = (_common.PROFILE.get("education") or {}).get("graduation", "")
check("answers: 'come into the office 3-4 days per week?' -> Yes (in-office is OK)",
      ab.classify("CRA asks colleagues to work in their designated office 3-4 days per week on average. Are you available to come into the office 3-4 days per week?", "combobox", _j) == ("seed", "Yes"))
check("answers: Greenhouse education 'graduation month' -> the month only; 'graduation year' -> the year",
      ab.classify("graduation month", "combobox", _j)[1] in _grad and ab.classify("graduation month", "combobox", _j)[1].isalpha()
      and ab.classify("graduation year", "text", _j)[1] in _grad, str((_grad, ab.classify("graduation month", "combobox", _j))))
check("answers: 'Work Eligibility' -> work authorization answer", ab.classify("Work Eligibility", "combobox", _j)[0] == "seed")
check("forms: two majors on a one-pick list -> the first exact match; nothing else guessed",
      fq.FormFiller._pick("Finance, Accounting", ["Accounting", "Economics", "Finance"]) == "Finance"
      and fq.FormFiller._pick("Finance, Accounting", ["Business", "Economics"]) is None)
