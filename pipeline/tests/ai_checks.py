# Nov 26: Gemini answers layer (ai_answers.py). Run from offline_test.py via exec (uses its check() and IP).
import json as _json, re as _re, tempfile as _tf
from pathlib import Path as _P
import answers as ab, ai_answers as ai, review_check as rc2, workday as wdm2
import common as _common

_saved = (ab.BANK_PATH, ab.INBOX_PATH, ab.PENDING_PATH, ai.CACHE_PATH)
_d0 = _P(_tf.mkdtemp())
ab.BANK_PATH, ab.INBOX_PATH, ab.PENDING_PATH = _d0 / "answers.yaml", _d0 / "_inbox.json", _d0 / "_pending.json"
ai.CACHE_PATH = _d0 / "_ai_cache.json"
_PF = _common.PROFILE
_phone = _re.sub(r"\D", "", _PF["candidate"]["phone"])[-10:]
_first, _last = _PF["candidate"]["name"].split()[0], _PF["candidate"]["name"].split()[-1]
ab.save_answer("Address Line 1", "4410 Test Meadow Drive")
ab.save_answer("Are you available to work full-time for the 10-week summer internship?", "Yes")
ab.save_answer("Language fluency", "English")

# ---------- wording: near-identical saved questions, negation / number guards
check("ai: saved answer reused for a near-identical wording",
      ab.classify("Are you available to work full time for the 10 week summer internship program?")[0] == "learned")
check("ai: a saved answer is NOT reused when the new wording adds a negation",
      ab.classify("Are you not available to work full time for the 10 week summer internship?")[0] != "learned")
check("ai: a saved answer is NOT reused when the numbers differ (10-week vs 12-week)",
      ab.classify("Are you available to work full time for the 12 week summer internship?")[0] != "learned")
check("ai: 'able to work WITHOUT sponsorship?' no longer gets the sponsorship rule's No (goes to Gemini / him)",
      ab.classify("Will you be able to work without employer visa sponsorship?")[0] == "unknown")
check("ai: plain sponsorship question still answered by the rule",
      ab.classify("Will you now or in the future require visa sponsorship?") == ("seed", _PF["standard_answers"]["requires_sponsorship"]))

# ---------- a fake Gemini that answers like a careful model, and records every prompt
_prompts = []
_MAP = {"without employer visa sponsorship": ("requires_sponsorship", "opposite"),
        "describes your u.s. work authorization": ("work_authorized_us", "same"),
        "city of residence": ("city", "same"), "best phone number": ("phone", "same"),
        "ten week program": ("bank:1", "same"), "made-up": ("favorite_color", "same"), "unsure q": ("gpa", "low")}
def _fake_ask(prompt):
    _prompts.append(prompt)
    if prompt.startswith("A job-application question"):
        ans = _re.search(r'True answer: "(.*)"', prompt).group(1)
        opts = _json.loads(_re.search(r"Options: (\[.*\])", prompt).group(1))
        if ans == "Yes":
            hit = [o for o in opts if o.startswith("I am authorized")]
            return {"choice": hit[0] if hit else None}
        if ans == "No":
            hit = [o for o in opts if "not require" in o.lower()]
            return {"choice": hit[0] if hit else "Something not on the page"}
        return {"choice": None}
    out = []
    for n, lab in _re.findall(r'^(\d+)\. "(.*?)"', prompt, _re.M):
        key, pol = next((v for k, v in _MAP.items() if k in lab.lower()), ("none", "same"))
        conf = "low" if pol == "low" else "high"
        out.append({"id": int(n), "key": key, "polarity": "same" if pol == "low" else pol, "confidence": conf})
    return {"answers": out}
_real_ask = ai._ask
ai._ask = _fake_ask
ai.reset_calls()

_job = {"id": "a1", "company": "Novel Energy Co", "title": "Finance Intern", "source": "workday", "notes": ""}
_fields = [
    {"idx": 0, "label": "Will you be able to work without employer visa sponsorship?", "kind": "radio", "options": ["Yes", "No"], "required": True},
    {"idx": 1, "label": "Which best describes your U.S. work authorization?", "kind": "dropdown", "options": [], "required": True},
    {"idx": 2, "label": "Current city of residence", "kind": "text", "options": [], "required": True},
    {"idx": 3, "label": "Best phone number to reach you", "kind": "text", "options": [], "required": True},
    {"idx": 4, "label": "Name of the person who referred you", "kind": "text", "options": [], "required": True},
    {"idx": 5, "label": "Do you consent to a background check?", "kind": "radio", "options": ["Yes", "No"], "required": True},
    {"idx": 6, "label": "Why do you want to work at Novel Energy?", "kind": "textarea", "options": [], "required": True},
    {"idx": 7, "label": "What is your race/ethnicity?", "kind": "dropdown", "options": [], "required": True},
    {"idx": 8, "label": "A made-up question", "kind": "text", "options": [], "required": True},
    {"idx": 9, "label": "Unsure q about grades", "kind": "text", "options": [], "required": True},
    {"idx": 10, "label": "Can you commit to our ten week program?", "kind": "radio", "options": ["Yes", "No"], "required": True},
]
_sol = ai.solve(_fields, _job)
_pr = "\n".join(_prompts)
check("ai solve: one Gemini call for the whole page", len(_prompts) == 1, str(len(_prompts)))
check("ai solve: no private value ever sent (name, email, phone, ZIP, LinkedIn, saved address)",
      not ai.leaks(_pr) and _phone not in _pr and _first not in _pr and _last not in _pr
      and "4410 Test Meadow" not in _pr and _PF["candidate"]["email"] not in _pr, str(ai.leaks(_pr)))
check("ai solve: consent / essay / demographic questions are never sent",
      "background check" not in _pr and "Why do you want" not in _pr and "race" not in _pr.lower().split("questions on this page")[1])
check("ai solve: reversed wording handled ('able to work WITHOUT sponsorship' -> Yes)",
      _sol.get(0, {}).get("answer") == "Yes", str(_sol.get(0)))
check("ai solve: facts mapped (authorization Yes, city Houston), private phone typed by the code",
      _sol.get(1, {}).get("answer") == "Yes" and _sol.get(2, {}).get("answer") == "Houston" and _sol.get(3, {}).get("answer") == _phone, str(_sol))
check("ai solve: saved question matched by meaning reuses HIS answer",
      _sol.get(10, {}).get("answer") == "Yes" and "saved answer" in _sol.get(10, {}).get("key", ""), str(_sol.get(10)))
check("ai solve: 'none', an invented key and low confidence are all ignored (they go to the Paused card)",
      4 not in _sol and 8 not in _sol and 9 not in _sol and 5 not in _sol, str(sorted(_sol)))
_n = len(_prompts)
_sol2 = ai.solve(_fields, _job)
check("ai solve: second time comes from the cache (no new Gemini call), same answers",
      len(_prompts) == _n and {k: v["answer"] for k, v in _sol2.items()} == {k: v["answer"] for k, v in _sol.items()})
_opts = ["I am authorized to work in the U.S. for any employer", "I require sponsorship now or in the future"]
check("ai pick: 'Yes' -> the option that says it", ai.pick("Which best describes your U.S. work authorization?", "Yes", _opts) == _opts[0])
check("ai pick: an option that isn't on the page is rejected", ai.pick("Sponsorship status", "No", ["A", "B"]) is None)
_n = len(_prompts)
check("ai pick: a private value is never sent", ai.pick("Phone", _phone, ["x", "y"]) is None and len(_prompts) == _n)
check("ai pick: never-list / demographic questions never sent",
      ai.pick("Do you certify the above is true?", "Yes", ["I certify", "No"]) is None
      and ai.pick("Gender", "Male", ["Male", "Female"]) is None and len(_prompts) == _n)
def _down(prompt):
    raise RuntimeError("Gemini failed: 503 UNAVAILABLE")
ai._ask = _down
ai.CACHE_PATH = _d0 / "_ai_cache2.json"
check("ai: Gemini down -> no crash, nothing answered (Paused card takes over)", ai.solve(_fields, _job) == {} and
      ai.pick("Which best describes your U.S. work authorization?", "Yes", _opts) is None)
ai._ask = _fake_ask
ai.CACHE_PATH = _d0 / "_ai_cache3.json"

# ---------- browser: a Workday page with questions no rule knows (a company it has never seen)
try:
    from playwright.sync_api import sync_playwright as _sp3
    _paused = []
    def _him3(wf, reason, items):
        _paused.append([i["label"] for i in items])
        ab.queue_answer("", cont=True)
    with _sp3() as _p3:
        _b3 = _p3.chromium.launch(headless=True)
        _pg3 = _b3.new_page()
        _pg3.goto((IP / "pipeline" / "tests" / "fake_workday.html").resolve().as_uri())
        _pg3.evaluate("""() => { root.innerHTML = '<h2>Application Questions</h2>' +
          radios('nospon', 'Will you be able to work without employer visa sponsorship?', ['Yes', 'No'], true) +
          dd('wauth', 'Which best describes your U.S. work authorization?', ['I am authorized to work in the U.S. for any employer', 'I require sponsorship now or in the future'], true) +
          text('rcity', 'Current city of residence', true) +
          text('bphone', 'Best phone number to reach you', true) +
          text('refby', 'Name of the person who referred you', true); }""")
        _wf3 = wdm2.WorkdayFiller(_pg3, dict(_job, url=_pg3.url), lambda h, c: None, {}, _him3, pace=(0.0, 0.01), wait_minutes=0.2)
        ai.reset_calls(); ai.DECIDED.clear()
        _wf3.fill_page("Application Questions")
        _v = _pg3.evaluate("""() => ({nospon: (document.querySelector('input[name=nospon]:checked') || {}).value,
          wauth: document.getElementById('wauth').textContent, rcity: document.getElementById('rcity').value,
          bphone: document.getElementById('bphone').value, refby: document.getElementById('refby').value})""")
        check("browser ai: unseen Workday questions answered via Gemini (reversed sponsorship = Yes, authorization option, city, phone)",
              _v["nospon"] == "Yes" and _v["wauth"].startswith("I am authorized") and _v["rcity"] == "Houston"
              and _v["bphone"] == _phone, str(_v))
        check("browser ai: the question no fact answers (referral name) still pauses for him, left blank",
              _paused and _paused[0] == ["Name of the person who referred you"] and _v["refby"] == "", str(_paused))
        _items = rc2.compare([{"section": "Application Questions", "label": "Which best describes your U.S. work authorization?", "value": _v["wauth"]},
                              {"section": "Application Questions", "label": "Current city of residence", "value": "Houston"}], _job, _wf3.filled)
        check("browser ai: Review check shows every Gemini answer yellow, never green",
              all(i["color"] == "yellow" and "Gemini" in i["why"] for i in _items), str(_items))
        _b3.close()
except Exception as _exc3:  # noqa: BLE001
    if "playwright" in repr(_exc3).lower() or "executable" in repr(_exc3).lower():
        print("SKIP ai browser checks (Playwright or Chromium not available here):", repr(_exc3)[:120])
    else:
        check("browser ai: flow ran without crashing", False, repr(_exc3)[:400])

_src = (IP / "pipeline" / "ai_answers.py").read_text(encoding="utf-8")
check("ai static: ai_answers.py never touches the page (no click / fill / check)",
      not _re.search(r"\.(click|fill|check|set_checked|press)\(", _src))
ai._ask = _real_ask
ab.BANK_PATH, ab.INBOX_PATH, ab.PENDING_PATH, ai.CACHE_PATH = _saved
