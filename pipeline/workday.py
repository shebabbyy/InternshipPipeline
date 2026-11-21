"""Workday filler (Control Center v2, phase 2). Deterministic: Workday's data-automation-id attributes + question
labels, no AI. Runs inside the apply window opened by fill.py and stops at the Review page. Never submits.

Flow: Apply (adventureButton) -> Autofill with Resume / Apply Manually -> sign in or create the account (unique
password per Workday site in Windows Credential Manager, email = Gmail) -> every page: fill each field from the answer
bank (answers.py), upload the resume, decline demographic questions -> Next -> ... -> stop at Review.

Pauses (yellow banner "PAUSED", a beep, the question list in the Control Center) instead of guessing:
  terms / consent / certify boxes, signatures, salary, criminal/background, anything unknown or essay-type, CAPTCHAs,
  the email-verification link (unless Gmail read-only is set up), Workday validation errors.
A pause ends when he answers in the Control Center, does it on the page, or presses Continue. Every click goes
through apply_window.safe_click.
"""
from __future__ import annotations

import random
import re
import sys
import time
from urllib.parse import urlparse

import answers as ab
import apply_window as aw
import gmail_verify
from common import CONFIG, PROFILE, log


class Stop(Exception):
    """End the automatic part; the window stays open for him."""


SCAN_JS = """(opt) => {
  opt = opt || {};
  const vis = e => !!(e && (e.offsetParent !== null || (e.getClientRects && e.getClientRects().length)));
  const txt = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('[data-pipeline-idx]').forEach(e => e.removeAttribute('data-pipeline-idx'));
  let wraps = [...document.querySelectorAll('[data-automation-id^="formField-"], fieldset')].filter(vis)
      .filter(w => !w.closest('#pipeline-review-panel'));
  wraps = wraps.filter(w => !wraps.some(o => o !== w && w.contains(o)));
  if (opt.onlyNew) wraps = wraps.filter(w => !w.hasAttribute('data-pipeline-seen'));
  const optLabel = i => { if (i.labels && i.labels.length) return txt(i.labels[0]);
      const p = i.closest('label'); return p ? txt(p) : (i.getAttribute('aria-label') || ''); };
  const out = [];
  wraps.forEach((w, n) => {
    w.setAttribute('data-pipeline-idx', String(n));
    let lab = w.querySelector('legend');
    if (!lab || !txt(lab)) lab = [...w.querySelectorAll('label')].find(l => { const t = l.control;
        return !(t && (t.type === 'radio' || t.type === 'checkbox')); }) || null;
    let label = txt(lab);
    const radios = [...w.querySelectorAll('input[type=radio]')];
    const boxes = [...w.querySelectorAll('input[type=checkbox], [role=checkbox]')];
    const dd = w.querySelector('button[aria-haspopup="listbox"]');
    const ms = w.querySelector('[data-automation-id="multiselectInputContainer"] input, input[data-automation-id="searchBox"]');
    const file = w.querySelector('input[type=file]');
    const month = w.querySelector('[data-automation-id="dateSectionMonth-input"]');
    const ta = w.querySelector('textarea');
    const inp = w.querySelector('input:not([type=radio]):not([type=checkbox]):not([type=file]):not([type=hidden])');
    let kind = '', value = '', options = [], ctl = null;
    if (radios.length) { kind = 'radio'; options = radios.map(optLabel); const c = radios.find(r => r.checked); value = c ? optLabel(c) : ''; ctl = radios[0]; }
    else if (boxes.length > 1) { kind = 'checkgroup'; options = boxes.map(optLabel);
      value = boxes.filter(b => b.checked || b.getAttribute('aria-checked') === 'true').map(optLabel).join('; '); ctl = boxes[0]; }
    else if (boxes.length === 1) { kind = 'checkbox'; options = [optLabel(boxes[0])];
      value = (boxes[0].checked || boxes[0].getAttribute('aria-checked') === 'true') ? 'checked' : ''; ctl = boxes[0];
      if (!label) label = options[0]; }
    else if (dd) { kind = 'dropdown'; value = txt(dd); if (/^select one$/i.test(value)) value = ''; ctl = dd; }
    else if (ms) { kind = 'multiselect'; value = [...w.querySelectorAll('[data-automation-id="selectedItem"]')].map(txt).join('; '); ctl = ms; }
    else if (file) { kind = 'file'; value = [...w.querySelectorAll('[data-automation-id*="file-upload-item-name" i], [data-automation-id="fileName"]')].map(txt).join('; '); ctl = file; }
    else if (month) { kind = 'date'; const y = w.querySelector('[data-automation-id="dateSectionYear-input"]');
      value = [month.value, y ? y.value : ''].filter(Boolean).join('/'); ctl = month; }
    else if (ta) { kind = 'textarea'; value = ta.value; ctl = ta; }
    else if (inp) { kind = 'text'; value = inp.value; ctl = inp;
      if (inp.readOnly || inp.disabled) kind = 'readonly'; }
    if (!label && ctl) label = ctl.getAttribute('aria-label') || '';
    if (!kind || kind === 'readonly' || !label) return;
    const required = /[*✱]/.test(label) || (ctl && (ctl.getAttribute('aria-required') === 'true' || ctl.required)) ||
                     !!w.querySelector('[aria-required="true"]');
    if (opt.tagExp) w.setAttribute('data-pipeline-exp', '1');
    out.push({idx: n, label: label.replace(/[*✱]/g, '').trim(), kind, value: (value || '').trim(), options, required: !!required,
              multi: !!(file && file.multiple),
              exp: w.hasAttribute('data-pipeline-exp')});
  });
  return out;
}"""

STATE_JS = """() => {
  const vis = e => !!(e && (e.offsetParent !== null || (e.getClientRects && e.getClientRects().length)));
  const q = s => [...document.querySelectorAll(s)].filter(vis);
  const txt = e => (e ? (e.innerText || '') : '').replace(/\\s+/g, ' ').trim();
  const body = (document.body ? document.body.innerText : '').toLowerCase();
  const frames = [...document.querySelectorAll('iframe')].map(f => (f.src || '') + ' ' + (f.title || '')).join(' ').toLowerCase();
  const errs = q('[data-automation-id="errorMessage"], [data-automation-id="inputAlert"], [role=alert]').map(txt).filter(Boolean);
  return {
    adventure: q('[data-automation-id="adventureButton"]').length > 0,
    start: q('[data-automation-id="applyManually"], [data-automation-id="autofillWithResume"]').length > 0,
    email: q('input[data-automation-id="email"], input[type=email]').length > 0,
    password: q('input[type=password]').length,
    verify_pw: q('input[data-automation-id="verifyPassword"]').length > 0,
    terms: q('input[data-automation-id="createAccountCheckbox"]').map(e => e.checked)[0],
    create_link: q('[data-automation-id="createAccountLink"]').length > 0,
    captcha: /recaptcha|hcaptcha|turnstile|arkose|funcaptcha|captcha/.test(frames) || q('[data-automation-id*="captcha" i]').length > 0,
    verify_email: /verify your (email|account)|verification (email|link)|check your email|activate your account/.test(body),
    errors: errs.slice(0, 6),
    step: txt(q('[data-automation-id="progressBarActiveStep"]')[0]),
    heading: txt(q('h2, h1')[0]),
    path: location.pathname,
    fields: q('[data-automation-id^="formField-"]').length,
  };
}"""

NEXT_IDS = ("pageFooterNextButton", "bottom-navigation-next-button")

# My Experience page (Nov 21 fix): Work Experience / Education / Languages start EMPTY behind an "Add" button, so the
# field scan found nothing there. ADD_JS lists the visible Add buttons with the section they belong to. Workday's
# button says "Add" for an empty section and "Add Another" once it has an entry (then the code leaves it alone).
ADD_JS = """() => {
  const vis = e => !!(e && (e.offsetParent !== null || (e.getClientRects && e.getClientRects().length)));
  const txt = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('[data-pipeline-add]').forEach(e => e.removeAttribute('data-pipeline-add'));
  const btns = [...document.querySelectorAll('button, [role=button]')].filter(vis).filter(b => {
    const t = txt(b), a = b.getAttribute('aria-label') || '', id = b.getAttribute('data-automation-id') || '';
    return /^add( another)?$/i.test(t) || /^add\\b/i.test(a) || ['Add', 'add-button', 'addAnother'].includes(id); });
  return btns.map((b, i) => {
    b.setAttribute('data-pipeline-add', String(i));
    const aria = b.getAttribute('aria-label') || '';
    let sec = /^add\\s+\\S/i.test(aria) ? aria.replace(/^add\\s+(another\\s+)?/i, '') : '';
    for (let n = b.parentElement; n && n !== document.body && !sec; n = n.parentElement) {
      const h = n.querySelector('h2, h3, h4, legend, [role=heading]'); if (h && txt(h)) sec = txt(h); }
    return {i, text: txt(b), aria, section: sec};
  });
}"""
MARK_SEEN_JS = """() => { document.querySelectorAll('[data-automation-id^="formField-"], fieldset')
  .forEach(w => w.setAttribute('data-pipeline-seen', '1')); }"""
SECTIONS = (("work", r"work experience|employment|work history|professional experience"), ("edu", r"education"),
            ("lang", r"language"), ("skills", r"skill"))


def _section_key(text: str) -> str:
    t = (text or "").lower()
    return next((k for k, pat in SECTIONS if re.search(pat, t)), "")


def _mm_yyyy(text: str) -> str:
    mm, yy = _month_year(text)
    return f"{mm}/{yy}" if yy else ""


def experience_plan(profile: dict) -> dict:
    """What goes into My Experience, only from master_profile.json (verified bullets only, no start year for school)."""
    work = []
    for e in profile.get("experience", []):
        title, dates = e.get("title", ""), e.get("dates", "")
        m = re.match(r"^(.*?)\s*\(([^)]*\d{4}[^)]*)\)\s*$", title)        # "Lead ... (July 2026 – Present)"
        if m:
            title, dates = m.group(1).strip(), m.group(2)
        parts = re.split(r"\s*[–—-]\s*", dates) if dates else []
        start, end = (parts + ["", ""])[:2]
        current = bool(re.search(r"present|current|now", end, re.I))
        bullets = [b["text"] for b in e.get("bullets", []) if b.get("verified") is True]
        work.append({"title": title, "company": e.get("organization", ""), "location": e.get("location", ""),
                     "start": _mm_yyyy(start), "end": "" if current else _mm_yyyy(end), "current": current,
                     "description": "\n".join(bullets)})
    ed = profile.get("education", {})
    school = (ed.get("school") or "").split(",")[-1].strip()
    deg = ed.get("degree", "")
    majors = re.split(r"\s*(?:,|\band\b|&)\s*", deg.split(" in ", 1)[1]) if " in " in deg else []
    grad = ed.get("graduation", "")
    edu = [{"school": school, "degree": deg.split(" in ")[0].strip(), "majors": [x for x in majors if x],
            "gpa": ed.get("gpa", ""), "grad": _mm_yyyy(grad), "grad_year": (re.search(r"20\d\d", grad) or [""])[0],
            # start year only when he stated it (education.start, e.g. "Fall 2024"); a month only if he gave one
            "start_year": (re.search(r"20\d\d", ed.get("start") or "") or [""])[0], "start": _mm_yyyy(ed.get("start") or "")}] \
        if school else []
    langs = []
    for t in profile.get("skills", {}).get("languages", []):
        m = re.match(r"^(\w+)\s+in\s+(\w+)", t)                    # "Conversational in Arabic"
        langs.append({"language": m.group(2), "level": m.group(1)} if m else {"language": t.strip(), "level": ""})
    return {"work": work, "edu": edu, "lang": langs, "skills": list(profile.get("skills", {}).get("technical", []))}


def experience_answer(key: str, label: str, kind: str, entry: dict):
    """Answer for one field of a My Experience entry. Returns a list of candidates to try in order, 'tick',
    or None (leave it: never invented, e.g. a school start year)."""
    q = ab.norm(label)
    if key == "work":
        if re.search(r"currently work|current(ly)? (employed|job|position|role)|i work here|still work", q):
            return "tick" if entry.get("current") else None
        if re.search(r"title|position|role$", q):
            return [entry["title"]]
        if re.search(r"company|employer|organi[sz]ation", q):
            return [entry["company"]]
        if re.search(r"location|city", q):
            return [entry["location"]]
        if re.search(r"^from\b|start", q):
            return [entry["start"]] if entry["start"] else None
        if re.search(r"^to\b|end( date)?$|^end", q):
            return [entry["end"]] if entry["end"] else None
        if re.search(r"description|responsibilit|duties|summary|accomplish", q):
            return [entry["description"]] if entry["description"] else None
    elif key == "edu":
        if re.search(r"school|university|college|institution", q):
            return [entry["school"]]
        if re.search(r"field of study|major|discipline|concentration|area of study", q):
            if kind == "multiselect":
                return ["+".join(entry["majors"])]                 # one selection per major
            return [" and ".join(entry["majors"])] if kind in ("text", "textarea") else list(entry["majors"])
        if re.search(r"degree", q):
            return [entry["degree"], "BBA", "Bachelor's Degree", "Bachelors Degree", "Bachelor's", "Bachelor"]
        if re.search(r"gpa|grade|overall result|average", q):
            return [entry["gpa"]]
        if re.search(r"^to\b|last year|end|graduat|expected|completion", q):
            return [entry["grad"] if kind == "date" else entry["grad_year"]]
        if re.search(r"^from\b|first year|start", q):
            v = entry.get("start") if kind == "date" else entry.get("start_year")
            return [v] if v else None                               # never invented: only education.start
        return None
    elif key == "lang":
        if re.search(r"native|fluent", q) and kind == "checkbox":
            return None
        if re.search(r"^language$|^languages?$|language name|select (a )?language", q):
            return [entry["language"]]
        if re.search(r"proficien|level|reading|writing|speaking|comprehension|overall|listening", q):
            lv = entry.get("level", "")
            return [lv, "Intermediate"] if lv.lower() == "conversational" else ([lv] if lv else None)
    elif key == "skills":
        if re.search(r"skill", q):
            return ["+".join(entry["skills"])]
    return None
DONE_TEXT = ("Workday: the pipeline filled what it could. Check every field (the panel on Review lists what to fix). "
             "Only you click Submit.")


def _beep():
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep()
        else:
            print("\a", end="", flush=True)
    except Exception:  # noqa: BLE001
        pass


def _month_year(text: str):
    months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(20\d\d)", text or "")
    if m and m.group(1).lower() in months:
        return f"{months.index(m.group(1).lower()) + 1:02d}", m.group(2)
    m = re.search(r"(\d{1,2})/(20\d\d)", text or "")
    return (f"{int(m.group(1)):02d}", m.group(2)) if m else (None, None)


class WorkdayFiller:
    def __init__(self, page, job: dict, password_fn, files: dict | None = None, on_pause=None,
                 pace=None, wait_minutes: float | None = None):
        self.page, self.job, self.password_fn = page, job, password_fn
        self.files = files or {}                     # {"resume": path, "cover": path}
        self.on_pause = on_pause                     # tests: simulate him during a pause
        self.pace = pace if pace is not None else tuple(CONFIG.get("workday_pace_s", [0.6, 1.4]))
        self.wait_s = 60 * (wait_minutes if wait_minutes is not None else CONFIG.get("workday_wait_minutes", 20))
        self.filled: dict = {}                       # label -> value the code entered (for the Review check)
        self.skipped: list = []                      # optional questions left blank
        self.exp_left: list = []                     # My Experience fields only he can fill (e.g. a school start year)
        self.started = time.time()
        self.auth_tries = 0
        self.host = (urlparse(page.url).hostname or "").lower()

    # ------------------------------------------------ small helpers
    def nap(self, k: float = 1.0):
        lo, hi = self.pace
        self.page.wait_for_timeout(int(1000 * k * random.uniform(lo, hi)))

    def state(self) -> dict:
        try:
            st = self.page.evaluate(STATE_JS)
        except Exception:  # noqa: BLE001  (navigating)
            self.page.wait_for_timeout(800)
            st = self.page.evaluate(STATE_JS)
        st["review"] = aw.is_review_page(self.page)
        return st

    def sig(self) -> str:
        try:
            st = self.state()
        except Exception:  # noqa: BLE001
            return ""
        return "|".join(str(st.get(k)) for k in ("step", "heading", "path", "review", "password", "start", "adventure"))

    def banner(self, text: str):
        try:
            self.page.evaluate(aw._BANNER_JS, {"text": text, "review": False})
        except Exception:  # noqa: BLE001
            pass

    def first(self, *selectors):
        for sel in selectors:
            loc = self.page.locator(sel)
            try:
                for i in range(min(loc.count(), 4)):
                    if loc.nth(i).is_visible():
                        return loc.nth(i)
            except Exception:  # noqa: BLE001
                continue
        return None

    def click(self, loc):
        aw.safe_click(loc, self.page)
        self.nap()

    # ------------------------------------------------ pause (he takes over one step)
    def pause(self, reason: str, until=None, items: list | None = None, page_name: str = ""):
        print(f"PAUSED ({self.job.get('company')}): {reason}", flush=True)
        self.banner("PAUSED: " + reason)
        _beep()
        ab.write_pending(self.job, page_name, items or [], waiting=reason)
        if self.on_pause:
            self.on_pause(self, reason, items or [])
        end = time.time() + self.wait_s
        try:
            while time.time() < end:
                if self.page.is_closed():
                    raise Stop("window closed")
                got, cont = ab.take_inbox()
                if got and items:
                    self.apply_answers(got, items)
                if cont:
                    return
                try:
                    if until is not None and until():
                        return
                except Stop:
                    raise
                except Exception:  # noqa: BLE001
                    pass
                self.page.wait_for_timeout(1000)
            raise Stop(f"waited {int(self.wait_s / 60)} min at: {reason}")
        finally:
            ab.clear_pending()

    # ------------------------------------------------ entry
    def run(self) -> str:
        """'review' when it reached the Review page; otherwise a short reason it handed over."""
        self.banner("Workday: the pipeline is filling this application. Don't click until it pauses or reaches Review.")
        try:
            for _ in range(25):
                st = self.state()
                if st["captcha"]:
                    self.pause("CAPTCHA. Solve it yourself; the pipeline never does.", lambda: not self.state()["captcha"])
                    continue
                if st["review"]:
                    return "review"
                if st["password"]:
                    self.auth(st)
                    continue
                if st["start"]:
                    self.choose_start()
                    continue
                if st["adventure"]:
                    self.click(self.first('[data-automation-id="adventureButton"]'))
                    self.page.wait_for_timeout(2500)
                    continue
                if st["verify_email"] and not st["password"] and not st["fields"]:
                    self.verify_email()
                    continue
                name = st["step"] or st["heading"]
                self.fill_page(name)
                if not self.next_page(name):
                    return "stopped: no Next button"
            return "stopped: too many pages"
        except Stop as why:
            return f"stopped: {why}"
        except aw.GuardError as why:
            return f"stopped: {why}"
        finally:
            ab.clear_pending()
            self.banner(DONE_TEXT)

    # ------------------------------------------------ start + account
    def choose_start(self):
        want = CONFIG.get("workday_start", "resume")
        order = ["autofillWithResume", "applyManually"] if want == "resume" else ["applyManually", "autofillWithResume"]
        for aid in order:
            loc = self.first(f'[data-automation-id="{aid}"]')
            if loc is not None:
                self.click(loc)
                self.page.wait_for_timeout(2500)
                return
        raise Stop("no Autofill / Apply Manually choice found")

    def _fill_input(self, selectors, value):
        box = self.first(*selectors)
        if box is not None and not box.input_value():
            box.fill(value)
            self.nap(0.5)

    def auth(self, st: dict):
        self.auth_tries += 1
        if self.auth_tries > 4:
            self.pause("Sign in yourself (the pipeline tried 4 times).", lambda: not self.state()["password"])
            return
        create = st["verify_pw"]
        if not create:
            pw = self._password(create=False)
            if not pw and st["create_link"]:
                self.click(self.first('[data-automation-id="createAccountLink"]'))
                return
            if not pw:
                self.pause("No saved password for this Workday site. Sign in yourself.", lambda: not self.state()["password"])
                return
        else:
            if self._password(create=False) and self.first('[data-automation-id="signInLink"]') is not None:
                self.click(self.first('[data-automation-id="signInLink"]'))   # account already made earlier
                return
            pw = self._password(create=True)
            if not pw:
                self.pause("Password vault unavailable. Create the account yourself.", lambda: not self.state()["password"])
                return
        self._fill_input(['input[data-automation-id="email"]', "input[type=email]"], ab.account_email())
        for box in [b for b in self.page.locator("input[type=password]").all() if b.is_visible()]:
            box.fill(pw)
            self.nap(0.4)
        if create and st.get("terms") is False:
            self.pause("Tick the terms box on the Create Account form if you agree to them. The pipeline then clicks "
                       "Create Account.", lambda: self.state().get("terms") is not False)
        btn = self.first('[data-automation-id="createAccountSubmitButton"]' if create else '[data-automation-id="signInSubmitButton"]',
                         '[data-automation-id="click_filter"][aria-label]')
        if btn is None:
            role = self.page.get_by_role("button", name=re.compile(r"^\s*(create account|sign in)\s*$", re.I))
            btn = role.first if role.count() else None
        if btn is None:
            self.pause("Click Create Account / Sign In yourself.", lambda: not self.state()["password"])
            return
        self.click(btn)
        self.page.wait_for_timeout(4000)
        after = self.state()
        if after["verify_email"]:
            self.verify_email()
        elif after["password"] and after["errors"]:
            self.pause(f"Workday says: {after['errors'][0]}. Fix it yourself (Forgot password if needed).",
                       lambda: not self.state()["password"])

    def _password(self, create: bool):
        try:
            return self.password_fn(self.host, create)
        except Exception as exc:  # noqa: BLE001
            print(f"Password vault unavailable ({exc.__class__.__name__}).", flush=True)
            return None

    def verify_email(self):
        link = gmail_verify.find_link(self.host, self.started) if gmail_verify.enabled() else None
        if link:
            tab = self.page.context.new_page()
            try:
                tab.goto(link, timeout=60000)
                tab.wait_for_timeout(4000)
            finally:
                tab.close()
            print("Opened the Workday verification link from Gmail.", flush=True)
            self.page.goto(self.job.get("url") or self.page.url, timeout=60000)
            self.page.wait_for_timeout(3000)
            return
        self.pause(f"Workday sent a verification email to {ab.account_email()}. Click the link in that email, "
                   "then press Continue in the Control Center (Apply tab).")
        self.page.goto(self.job.get("url") or self.page.url, timeout=60000)
        self.page.wait_for_timeout(3000)

    # ------------------------------------------------ one page of questions
    def scan(self) -> list[dict]:
        return self.page.evaluate(SCAN_JS)

    def wrap(self, f):
        return self.page.locator(f'[data-pipeline-idx="{f["idx"]}"]')

    def fill_field(self, f: dict, answer: str) -> bool:
        w, kind = self.wrap(f), f["kind"]
        try:
            if kind in ("text", "textarea"):
                box = w.locator("textarea" if kind == "textarea" else
                                "input:not([type=radio]):not([type=checkbox]):not([type=file]):not([type=hidden])").first
                box.fill(answer)
                box.press("Tab")
            elif kind == "dropdown":
                self.click(w.locator('button[aria-haspopup="listbox"]').first)
                opts = self.page.locator('[role="listbox"] [role="option"], [data-automation-id="promptOption"]')
                opts.first.wait_for(state="visible", timeout=5000)
                texts = [t.strip() for t in opts.all_inner_texts()]
                choice = ab.pick_option(answer, texts)
                if choice is None:
                    self.page.keyboard.press("Escape")
                    return False
                self.click(opts.nth(texts.index(choice)))
            elif kind in ("radio", "checkgroup"):
                choice = ab.pick_option(answer, f["options"])
                if choice is None:
                    return False
                sel = "input[type=radio]" if kind == "radio" else "input[type=checkbox], [role=checkbox]"
                target = w.locator(sel).nth(f["options"].index(choice))
                try:
                    aw.safe_click(target, self.page, timeout=3000)
                except aw.GuardError:
                    raise
                except Exception:  # noqa: BLE001  (Workday hides the real input under a styled circle)
                    aw.safe_click(target, self.page, force=True)
            elif kind == "multiselect":
                box = w.locator('input').first
                box.fill(answer)
                box.press("Enter")
                self.page.wait_for_timeout(1500)
                opts = self.page.locator('[data-automation-id="promptOption"], [role="listbox"] [role="option"]')
                texts = [t.strip() for t in opts.all_inner_texts()] if opts.count() else []
                choice = ab.pick_option(answer, texts)
                if choice is None:
                    self.page.keyboard.press("Escape")
                    return False
                self.click(opts.nth(texts.index(choice)))
            elif kind == "date":
                mm, yy = _month_year(answer)
                if not yy:
                    return False
                w.locator('[data-automation-id="dateSectionMonth-input"]').first.fill(mm)
                year = w.locator('[data-automation-id="dateSectionYear-input"]')
                if year.count():
                    year.first.fill(yy)
            else:
                return False
        except aw.GuardError:
            raise
        except Exception:  # noqa: BLE001  (unexpected widget: leave it for him)
            return False
        self.filled[f["label"]] = answer
        self.nap(0.6)
        return True

    @staticmethod
    def _base(path: str) -> str:
        return (path or "").split("\\")[-1].split("/")[-1]

    def upload(self, f: dict, cover_field_on_page: bool = False) -> bool:
        """Uploads what this file field is missing. Workday usually has ONE 'Resume/CV' attachments box (multiple
        files allowed) and no cover-letter field: then the cover letter goes into that box too (Nov 21: Protiviti
        went out without it). Returns True when the field has what it needs."""
        lab = ab.norm(f["label"])
        have = ab.norm(f.get("value") or "")
        resume, cover = self.files.get("resume"), self.files.get("cover")
        missing = lambda p: p and ab.norm(self._base(p)) not in have
        if re.search(r"cover", lab):
            need = [cover] if missing(cover) else []
        else:
            need = [resume] if not have and resume else []
            if f.get("multi") and not cover_field_on_page and missing(cover):
                need.append(cover)
        if not need:
            return bool(have) or not f["required"]
        try:
            box = self.wrap(f).locator("input[type=file]").first
            for path in need:                    # one at a time: Workday adds each file to the list
                box.set_input_files(path)
                self.page.wait_for_timeout(3000)
            self.filled[f["label"]] = "; ".join(self._base(p) for p in need)
            return True
        except Exception:  # noqa: BLE001
            return bool(have)

    def fill_page(self, name: str):
        self.fill_experience()
        pending = []
        if self.exp_left:
            pending += self.exp_left
            self.exp_left = []
        fields = self.scan()
        cover_field = any(f["kind"] == "file" and re.search(r"cover", ab.norm(f["label"])) for f in fields)
        for f in fields:
            if f.get("exp"):
                continue        # My Experience entry fields: handled by fill_experience (never from the answer bank)
            if f["kind"] == "file":
                if not self.upload(f, cover_field) and f["required"]:
                    pending.append(self._item(f, "unknown", "upload your resume / cover letter"))
                continue
            if f["value"]:
                continue        # already filled (Workday's resume parser, Simplify or him): the Review check reads it
            cat, ans = ab.classify(f["label"], f["kind"], self.job)
            if cat == "eeo":
                if not self.fill_field(f, "decline") and f["required"]:
                    pending.append(self._item(f, cat, "pick an answer (decline if offered)"))
            elif cat in ("seed", "learned"):
                if not self.fill_field(f, ans) and f["required"]:
                    pending.append(self._item(f, "unknown", f"couldn't match '{ans}' to the options"))
            elif f["required"]:
                note = {"never": "yours to answer on the page", "essay": "essay: draft it with /answer in Claude"}.get(cat, "")
                pending.append(self._item(f, cat, note))
            else:
                self.skipped.append(f["label"])
        if pending:
            n = len(pending)
            self.pause(f"{n} question{'s' if n > 1 else ''} need you on '{name}'. Answer in the Control Center "
                       "(Apply tab) or on the page.", lambda: self._resolved(pending), pending, name)

    # ------------------------------------------------ My Experience (Add buttons -> entries from the profile)
    def add_buttons(self) -> list[dict]:
        try:
            return [dict(b, key=_section_key(b["section"])) for b in self.page.evaluate(ADD_JS)]
        except Exception:  # noqa: BLE001
            return []

    def fill_experience(self):
        """Adds Work Experience / Education / Languages entries (and Skills) when the section is empty."""
        btns = [b for b in self.add_buttons() if b["key"]]
        has_skills_box = False
        try:
            has_skills_box = any(f["kind"] == "multiselect" and re.search(r"skill", ab.norm(f["label"])) and not f["value"]
                                 and not f.get("exp") for f in self.scan())
        except Exception:  # noqa: BLE001
            pass
        if not btns and not has_skills_box:
            return
        plan = experience_plan(PROFILE)
        done = set()
        for b in btns:
            key = b["key"]
            if key in done:
                continue
            done.add(key)
            if re.search(r"another", f'{b["text"]} {b["aria"]}', re.I):
                print(f"  My Experience: '{b['section']}' already has entries; left as is.", flush=True)
                continue
            entries = ([{"skills": plan["skills"]}] if plan["skills"] else []) if key == "skills" else plan.get(key, [])
            for n, entry in enumerate(entries or []):
                btn = next((x for x in self.add_buttons() if x["key"] == key), None)
                if btn is None:
                    break
                self.page.evaluate(MARK_SEEN_JS)
                self.click(self.page.locator(f'[data-pipeline-add="{btn["i"]}"]'))
                self.page.wait_for_timeout(1500)
                self.fill_entry(key, entry, f'{b["section"]} {n + 1}')
        if has_skills_box and "skills" not in done and plan["skills"]:
            for f in self.scan():
                if f["kind"] == "multiselect" and re.search(r"skill", ab.norm(f["label"])) and not f["value"]:
                    for sk in plan["skills"]:
                        self.fill_field(f, sk)
                    break

    def fill_entry(self, key: str, entry: dict, where: str):
        new = lambda: self.page.evaluate(SCAN_JS, {"onlyNew": True, "tagExp": True})
        for f in new():                       # "I currently work here" first: it hides the To date
            if f["kind"] == "checkbox" and not f["value"] and experience_answer(key, f["label"], "checkbox", entry) == "tick":
                target = self.wrap(f).locator("input[type=checkbox], [role=checkbox]").first
                try:
                    aw.safe_click(target, self.page, timeout=3000)
                except aw.GuardError:
                    raise
                except Exception:  # noqa: BLE001  (styled box over a hidden input)
                    aw.safe_click(target, self.page, force=True)
                self.filled[f["label"]] = "checked"
                self.nap(0.6)
        for f in new():
            if f["kind"] in ("checkbox", "checkgroup", "file") or f["value"]:
                continue
            cands = experience_answer(key, f["label"], f["kind"], entry)
            ok = False
            if isinstance(cands, list):
                for c in [c for c in cands if c]:
                    if "+" in c and f["kind"] == "multiselect":
                        ok = all([self.fill_field(f, part) for part in c.split("+")])
                    elif "+" not in c:
                        ok = self.fill_field(f, c)
                    if ok:
                        break
            if not ok and f["required"]:
                self.exp_left.append(self._item(f, "never", f"{where}: fill this on the page (it is not in your profile)"
                                                if cands is None else f"{where}: pick it on the page"))
            elif not ok:
                self.skipped.append(f"{where}: {f['label']}")

    def _item(self, f, cat, note=""):
        return {"label": f["label"], "kind": f["kind"], "options": f.get("options") or [], "category": cat,
                "note": note, "idx": f["idx"]}

    def _resolved(self, items) -> bool:
        now = {ab.norm(f["label"]): f["value"] for f in self.scan()}
        return all(now.get(ab.norm(i["label"])) for i in items)

    def apply_answers(self, got: dict, items: list):
        fields = {ab.norm(f["label"]): f for f in self.scan()}
        for label, answer in got.items():
            f = fields.get(ab.norm(label))
            if f and not f["value"]:
                if self.fill_field(f, answer):
                    print(f"  Filled '{f['label']}' with your answer (saved for next time).", flush=True)

    # ------------------------------------------------ next page
    def next_page(self, name: str) -> bool:
        before = self.sig()
        btn = self.first(*[f'[data-automation-id="{i}"]' for i in NEXT_IDS])
        if btn is None:
            role = self.page.get_by_role("button", name=re.compile(r"^\s*(next|save and continue|continue)\s*$", re.I))
            btn = next((role.nth(i) for i in range(role.count()) if role.nth(i).is_visible()), None)
        if btn is None:
            return False
        try:
            if re.search(r"submit", btn.inner_text(timeout=2000) or "", re.I):
                raise Stop("reached the page with the Submit button (Review). Only you click Submit")
        except Stop:
            raise
        except Exception:  # noqa: BLE001
            pass
        self.click(btn)       # GuardError on the Review page; never reached because run() stops there first
        end = time.time() + 15
        while time.time() < end:
            self.page.wait_for_timeout(700)
            if self.sig() != before:
                return True
        st = self.state()
        if st["errors"]:
            self.pause(f"Workday shows an error on '{name}': {st['errors'][0]}. Fix it, then click Next yourself.",
                       lambda: self.sig() != before)
            return True
        return self.sig() != before


def daily_count(add: bool = False) -> int:
    """Workday fills today (cap: workday_daily_cap, default 10)."""
    import json
    from datetime import date
    from common import ROOT
    path = ROOT / "output" / "_workday_runs.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    today = date.today().isoformat()
    n = int(data.get(today, 0)) + (1 if add else 0)
    if add:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({today: n}), encoding="utf-8")
    return n


def fill(page, job: dict, password_fn, files: dict, on_pause=None) -> tuple[str, dict]:
    """Entry point for fill.py. Returns (outcome, filled fields)."""
    cap = int(CONFIG.get("workday_daily_cap", 10))
    if daily_count() >= cap:
        log(f"  {job['company']}: daily Workday cap ({cap}) reached; opened without auto-fill.")
        return "stopped: daily cap", {}
    daily_count(add=True)
    wf = WorkdayFiller(page, job, password_fn, files, on_pause)
    outcome = wf.run()
    log(f"  {job['company']}: Workday filler {outcome}. Filled {len(wf.filled)} fields"
        + (f"; left {len(wf.skipped)} optional blank" if wf.skipped else "") + ".")
    return outcome, wf.filled
