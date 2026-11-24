# Phase 2 checks (Workday filler, answer bank, Review-page check). Run from offline_test.py via exec (uses its
# check() and IP). Also runnable alone:  python pipeline/tests/phase2_checks.py
import os as _os, re as _re, sys as _sys, json as _json, shutil as _sh, tempfile as _tf
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
import answers as ab, review_check as rc, gmail_verify as gv, workday as wdm, apply_window as aw2
import common as _common

_ab_path_saved = ab.BANK_PATH
ab.BANK_PATH = _P(_tf.mkdtemp()) / "answers.yaml"     # never touch a real answer bank
ab.INBOX_PATH = ab.BANK_PATH.parent / "_answers_inbox.json"
ab.PENDING_PATH = ab.BANK_PATH.parent / "_apply_pending.json"

_job = {"id": "t1", "company": "Tokio Marine HCC", "title": "Accounting Intern", "source": "workday", "notes": ""}
_C = lambda q, kind="text", job=_job: ab.classify(q, kind, job)
_P_ = _common.PROFILE
_first = _P_["candidate"]["name"].split()[0]

# ---------- answer bank: categories
_cases = {
    "First Name": ("seed", _first),
    "Are you legally authorized to work in the United States?": ("seed", _P_["standard_answers"]["authorized_to_work_us"]),
    "Will you now or in the future require sponsorship for employment visa status?": ("seed", _P_["standard_answers"]["requires_sponsorship"]),
    "Are you at least 18 years of age?": ("seed", "Yes"),
    "Are you willing to relocate?": ("seed", "No"),
    "What is your GPA?": ("seed", _P_["education"]["gpa"]),
    "Expected Graduation Date": ("seed", _P_["education"]["graduation"]),
    "Have you previously worked for Tokio Marine HCC?": ("seed", "No"),
    "How did you hear about us?": ("seed", "Company Website"),
    "Where did you first locate this position?": ("seed", "Company Website"),
    "What is your current major?": ("seed", _P_["standard_answers"]["majors"].replace(";", ",")),
    "What is your highest degree obtained/pursuing?": ("seed", "Bachelor of Business Administration"),
    "What are your salary expectations?": ("never", ""),
    "Have you ever been convicted of a felony?": ("never", ""),
    "I certify that the information provided is true and complete": ("never", ""),
    "Do you have relatives employed by the company?": ("never", ""),
    "Gender": ("eeo", "decline"), "Please select your ethnicity": ("eeo", "decline"), "Veteran Status": ("eeo", "decline"),
    "Why do you want to work at Tokio Marine HCC?": ("essay", ""),
    "Address Line 1": ("unknown", ""),
    "Are you authorized to work in the US without sponsorship?": ("unknown", ""),   # ambiguous: ask him once
}
_bad = {q: _C(q) for q, want in _cases.items() if _C(q) != want}
check("phase2 answers: questions sorted into seed / never / eeo / essay / unknown", not _bad, str(_bad))
check("phase2 answers: how-did-you-hear follows the real source",
      ab.heard_from({"source": "linkedin"}) == "LinkedIn" and ab.heard_from({"notes": "met at career fair"}) == "Career Fair")
check("phase2 answers: portal account email is always Gmail (also campus jobs)",
      ab.account_email() == _P_["candidate"]["email"] and "cougarnet" not in ab.account_email())

# learned answers + guards
ab.save_answer("Address Line 1", "1 Test Street")
ab.save_answer("What are your salary expectations?", "50000")
ab.save_answer("Gender", "Male")
check("phase2 answers: his answer is saved and reused", _C("Address Line 1 *") == ("learned", "1 Test Street"))
check("phase2 answers: never stores salary / demographic answers",
      _C("What are your salary expectations?")[0] == "never" and "salary" not in ab.BANK_PATH.read_text(encoding="utf-8")
      and "gender" not in ab.BANK_PATH.read_text(encoding="utf-8"))
ab.save_answer("Where did you first locate this position?", "Company Website")
check("phase2 answers: job source is never saved; always the real source of each job (Nov 21)",
      "locate" not in ab.BANK_PATH.read_text(encoding="utf-8")
      and ab.classify("Where did you first locate this position?", "text", {"source": "linkedin"}) == ("seed", "LinkedIn"))
ab.queue_answer("Preferred Name", "Shay")
ab.queue_answer("", cont=True)
_got, _cont = ab.take_inbox()
check("phase2 answers: Control Center inbox -> bank (single writer)", _got == {"Preferred Name": "Shay"} and _cont
      and not ab.INBOX_PATH.exists() and _C("Preferred Name") == ("learned", "Shay"))
ab.BANK_PATH.unlink(missing_ok=True)

# option picking
_opts_ok = (ab.pick_option("Yes", ["Select One", "Yes", "No"]) == "Yes"
            and ab.pick_option("No", ["Yes, I will", "No, I will not"]) == "No, I will not"
            and ab.pick_option("decline", ["Male", "Female", "I do not wish to answer"]) == "I do not wish to answer"
            and ab.pick_option("decline", ["Male", "Female"]) is None
            and ab.pick_option("United States of America", ["Canada", "United States of America"]) == "United States of America"
            and ab.pick_option("Bachelor of Business Administration", ["High School", "Bachelor's Degree", "Master's Degree"]) == "Bachelor's Degree"
            and ab.pick_option("Finance, Accounting", ["Finance", "Accounting"]) is None
            and ab.pick_option("Mobile", ["Landline", "Mobile"]) == "Mobile")
check("phase2 answers: options matched safely (no guess when unclear)", _opts_ok)

# ---------- Gmail verification link: same Workday host only
_mail = ('Click <a href="https://tmhcc.wd1.myworkdayjobs.com/en-US/careers/activate/abc123?x=1">verify</a> '
         'or https://evil.example.com/verify/abc and https://tmhcc.wd1.myworkdayjobs.com/en-US/careers/job/1')
check("phase2 gmail: picks the verify link on the same Workday host, nothing else",
      gv.pick_link(_mail, "tmhcc.wd1.myworkdayjobs.com") == "https://tmhcc.wd1.myworkdayjobs.com/en-US/careers/activate/abc123?x=1"
      and gv.pick_link(_mail, "other.wd1.myworkdayjobs.com") is None)
_gsrc = (IP / "pipeline" / "gmail_verify.py").read_text(encoding="utf-8")
check("phase2 gmail: read-only (no send / delete / flag), off without an app password",
      "readonly=True" in _gsrc and "BODY.PEEK" in _gsrc and not _re.search(r"\bm\.(store|expunge|append|send|copy|move|uid)\(", _gsrc)
      and "smtplib" not in _gsrc and ("GMAIL_APP_PASSWORD" not in _os.environ) <= (not gv.enabled()))

# ---------- Review check (pure)
_pairs = [{"section": "My Information", "label": "First Name", "value": _first},
          {"section": "My Information", "label": "Phone Number", "value": _P_["candidate"]["phone"]},
          {"section": "My Information", "label": "How Did You Hear About Us?", "value": "Indeed"},
          {"section": "Education", "label": "From", "value": "2019"},      # wrong whether or not his start is known
          {"section": "Questions", "label": "Gender", "value": "I do not wish to answer"},
          {"section": "Questions", "label": "Are you willing to relocate?", "value": "Yes"},
          {"section": "Questions", "label": "Which office?", "value": "Houston"}]
_r = {i["label"]: i["color"] for i in rc.compare(_pairs, _job)}
check("phase2 review: green match / red mismatch + wrong or guessed start year / yellow unknown",
      _r == {"First Name": "green", "Phone Number": "green", "How Did You Hear About Us?": "red", "From": "red",
             "Gender": "green", "Are you willing to relocate?": "red", "Which office?": "yellow"}, str(_r))

# ---------- guard + static rules
_B = lambda **k: {"tag": "button", "type": "", "role": "", "text": "", "aria": "", "aid": "", "label": "", **k}
check("phase2 guard: Create Account / Sign In buttons allowed, terms box still refused",
      not aw2.blocked_reason(_B(aid="createAccountSubmitButton", text="Create Account"))
      and not aw2.blocked_reason(_B(aid="signInSubmitButton", text="Sign In"))
      and aw2.blocked_reason(_B(tag="input", type="checkbox", aid="createAccountCheckbox", label="I agree to the Terms and Conditions"))
      and aw2.blocked_reason(_B(aid="pageFooterNextButton", text="Submit")))
for _f in ("workday.py", "review_check.py", "answers.py", "gmail_verify.py"):
    _src = (IP / "pipeline" / _f).read_text(encoding="utf-8")
    check(f"phase2 static: {_f} never clicks directly (only aw.safe_click)", not _re.search(r"(?<!self)\.click\(", _src),
          str(_re.findall(r".*(?<!self)\.click\(.*", _src)))
_wsrc = (IP / "pipeline" / "workday.py").read_text(encoding="utf-8")
check("phase2 static: Workday filler never ticks boxes or presses keys to submit",
      ".check(" not in _wsrc and ".set_checked(" not in _wsrc and "keyboard.press(\"Enter\")" not in _wsrc
      and "    def click(self, loc):\n        aw.safe_click(loc, self.page)" in _wsrc)
_fsrc = (IP / "pipeline" / "fill.py").read_text(encoding="utf-8")
check("phase2 safety: Workday auto-fill only runs in real Chrome (never the Playwright fallback window)",
      "if use_wd and not (sess.cdp or headless):" in _fsrc)
_gi2 = (IP / ".gitignore").read_text(encoding="utf-8") if (IP / ".gitignore").exists() else "answers.yaml"
check("phase2 repo: answers.yaml is private (git-ignored)", "answers.yaml" in _gi2)

# ---------- My Experience plan (Nov 21: the section was left empty on the first live run)
_plan = wdm.experience_plan(_P_)
_w0 = _plan["work"][0] if _plan["work"] else {}
check("phase2 experience: plan comes from the profile (titles, dates, current job, verified bullets only, school start only if stated)",
      _w0.get("title") and "(" not in _w0["title"] and _w0.get("company") == _P_["experience"][0]["organization"]
      and _re.match(r"^\d\d/20\d\d$", _w0.get("start", "")) and all(
          b["text"] not in "\n".join(w["description"] for w in _plan["work"])
          for e in _P_["experience"] for b in e["bullets"] if b.get("verified") is not True)
      and _plan["edu"] and _plan["edu"][0]["gpa"] == _P_["education"]["gpa"]
      and wdm.experience_answer("edu", "From", "text", dict(_plan["edu"][0], start_year="", start="")) is None
      and wdm.experience_answer("edu", "From", "date", dict(_plan["edu"][0], start="")) is None
      and wdm.experience_answer("work", "I currently work here", "checkbox", _w0) == ("tick" if _w0.get("current") else None),
      str(_plan)[:400])

# ---------- browser: whole flow on a fake Workday site (needs Playwright + Chromium)
try:
    from playwright.sync_api import sync_playwright as _sp
    _resume = _P(_tf.mkdtemp()) / "resume.pdf"
    _resume.write_bytes(b"%PDF-1.4 test")
    _cover = _resume.parent / "cover.pdf"
    _cover.write_bytes(b"%PDF-1.4 cover")
    _seen = []
    _seen_exp = []

    def _him(wf, reason, items):          # what Shehab does during each pause
        _seen.append((reason, [i["label"] for i in items], ab.read_pending().get("company")))
        if "terms box" in reason:
            wf.page.check("#terms")
        elif "Address Line 1" in [i["label"] for i in items]:
            ab.queue_answer("Address Line 1", "1 Test Street")      # typed in the Control Center
        elif any(i["label"] == "From" for i in items):           # school start year: he types it on the page
            _seen_exp.extend(items)
            wf.page.fill("#edu1-first-y", "2024")
            ab.queue_answer("", cont=True)
        elif "I certify" in " ".join(i["label"] for i in items):
            wf.page.check("#cert")
        elif "I consent" in " ".join(i["label"] for i in items):
            wf.page.check("#vterms")

    with _sp() as _p:
        _b = _p.chromium.launch(headless=True)
        _pg = _b.new_page()
        _pg.goto((IP / "pipeline" / "tests" / "fake_workday.html").resolve().as_uri())
        _vault = {}
        _pwfn = lambda host, create: _vault.setdefault(host, "Pw-test-1!") if create else _vault.get(host)
        _wf = wdm.WorkdayFiller(_pg, dict(_job, url=_pg.url), _pwfn, {"resume": str(_resume), "cover": str(_cover)}, _him, pace=(0.0, 0.01), wait_minutes=0.3)
        _out = _wf.run()
        _S = _pg.evaluate("window.S")
        _d = _S["data"]
        check("browser phase2: walks Apply -> account -> pages -> stops at Review", _out == "review" and _S["step"] == "review", _out)
        check("browser phase2: never submitted", not _S["submitted"])
        check("browser phase2: how-did-you-hear list without 'Company Website' -> the company's own career-site wording (Nov 24 Protiviti)",
              "Career Site" in str(_S.get("hearSel") or ""), str(_S.get("hearSel")))
        check("browser phase2: invisible reCAPTCHA badge + hidden challenge frame are not a CAPTCHA pause (Nov 23)",
              not any("CAPTCHA" in s_[0] for s_ in _seen), str([s_[0] for s_ in _seen]))
        check("browser phase2: Workday's empty noCaptchaWrapper is not a CAPTCHA; the bot-trap field stays empty (Nov 23)",
              not any("CAPTCHA" in s_[0] for s_ in _seen) and not _S.get("bee"), str(_S.get("bee")))
        _pg2 = _b.new_page()
        _pg2.set_content('<iframe title="reCAPTCHA" style="width:304px;height:78px" src="about:blank#https://www.google.com/recaptcha/api2/anchor?k=x&size=normal"></iframe>')
        check("browser phase2: a visible reCAPTCHA checkbox still counts as a CAPTCHA", _pg2.evaluate(wdm.STATE_JS)["captcha"] is True)
        _pg2.close()
        check("browser phase2: Sign In landing page (Nov 23 Baker Hughes): cookies declined, 'Sign in with email', "
              "then Create Account; resume uploaded on 'Autofill with Resume'",
              _S.get("cookies") == "declined" and _S.get("afFiles") == ["resume.pdf"], str({k: _S.get(k) for k in ("cookies", "afFiles", "step")}))
        check("browser phase2: account made with Gmail + vault password, terms box ticked by him",
              _S["account"] == ab.account_email() and _S["pw"] == "Pw-test-1!" and _seen and "terms box" in _seen[0][0])
        check("browser phase2: My Information filled from the profile",
              _d.get("first") == _first and _d.get("city") == "Houston" and _d.get("country") == "United States of America"
              and _d.get("dtype") == "Mobile" and _d.get("prev") == "No" and _d.get("cv") == 2
              and _d.get("phone", "").endswith(_re.sub(r"\D", "", _P_["candidate"]["phone"])[-4:]), str(_d))
        check("browser phase2: unknown question paused, his Control Center answer filled + saved",
              _d.get("addr") == "1 Test Street" and _C("Address Line 1") == ("learned", "1 Test Street"))
        _X = _S.get("exp") or {}
        _pw = _plan["work"]
        check("browser phase2: My Experience: one work entry per profile job, current job ticked, no end date",
              len(_X.get("work", [])) == len(_pw) and _X["work"][0]["title"] == _pw[0]["title"]
              and _X["work"][0]["company"] == _pw[0]["company"] and _X["work"][0]["from"] == _pw[0]["start"]
              and _X["work"][0]["cur"] == _pw[0]["current"] and (_X["work"][0]["to"] is None) == _pw[0]["current"]
              and _X["work"][0]["desc"] == _pw[0]["description"]
              and all(_X["work"][i]["to"] == _pw[i]["end"] for i in range(len(_pw)) if not _pw[i]["current"]), str(_X)[:600])
        _e = (_X.get("edu") or [{}])[0]
        check("browser phase2: My Experience: education filled, majors picked, start year only from him",
              _e.get("school") == ["University of Houston"] and _e.get("deg") == "Bachelor's Degree"
              and _e.get("fos") == _plan["edu"][0]["majors"] and _e.get("gpa") == _P_["education"]["gpa"]
              and _e.get("first") == (_plan["edu"][0]["start_year"] or "2024") and _e.get("last") == _plan["edu"][0]["grad_year"], str(_e))
        check("browser phase2: My Experience: school start year from the profile, else paused for him (never guessed, never saved)",
              (len(_seen_exp) == 0 if _plan["edu"][0]["start_year"] else (len(_seen_exp) == 1 and _seen_exp[0]["category"] == "never"))
              and _C("From")[0] != "learned", str(_seen_exp))
        check("browser phase2: My Experience: language + skills from the profile, fluent box left alone",
              [(l["lang"], l["native"]) for l in _X.get("lang", [])] == [(x["language"], False) for x in _plan["lang"]]
              and all(l["speak"] for l in _X.get("lang", [])) and _X.get("skills") == _plan["skills"], str(_X)[:600])
        check("browser phase2: resume AND cover letter go into Workday's one Resume/CV box, once each",
              _S.get("cvFiles") == ["resume.pdf", "cover.pdf"], str(_S.get("cvFiles")))
        check("browser phase2: questions answered from the bank; salary / essay / unknown optional left blank",
              _d.get("auth") == "Yes" and _d.get("spon") == "No" and _d.get("gpa") == _P_["education"]["gpa"]
              and _d.get("sal") == "" and _d.get("why") == "" and _d.get("rel") == "", str(_d))
        check("browser phase2: demographic questions declined", _d.get("gender") == "I do not wish to answer"
              and _d.get("vet") == "I do not wish to self-identify", str(_d))
        check("browser phase2: certify + consent boxes left to him (pauses listed them in the Control Center file)",
              any("I certify" in " ".join(s[1]) for s in _seen) and any("I consent" in " ".join(s[1]) for s in _seen)
              and all(s[2] == "Tokio Marine HCC" for s in _seen if s[1]), str(_seen))
        _res = rc.run(_pg, _job, _wf.filled)
        _col = {i["label"]: i["color"] for i in _res["items"]}
        check("browser phase2: Review check flags the wrong source; start year green only if it matches his, panel shown",
              _col.get("How Did You Hear About Us?") == "red" and _col.get("First Name") == "green"
              and _col.get("From") == ("green" if "2024" in (_P_["education"].get("start") or "") else "red")
              and _col.get("What is your GPA?") == "green"
              and f'{1 + (_col.get("From") == "red")} red' in _pg.inner_text("#pipeline-review-panel"), str(_col))
        _refused = False
        try:
            aw2.safe_click(_pg.locator("#submit"), _pg)
        except aw2.GuardError:
            _refused = True
        check("browser phase2: Submit on Review refused by the guard", _refused and not _pg.evaluate("window.S.submitted"))
        _b.close()
except Exception as _exc:  # noqa: BLE001
    if "playwright" in repr(_exc).lower() or "executable" in repr(_exc).lower():
        print("SKIP phase2 browser checks (Playwright or Chromium not available here):", repr(_exc)[:120])
    else:
        check("browser phase2: flow ran without crashing", False, repr(_exc)[:300])

ab.BANK_PATH = _ab_path_saved
if "_fails" in globals():
    print("\nFAILED: " + ", ".join(_fails) if _fails else "\nALL PHASE 2 CHECKS PASSED")
