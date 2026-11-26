"""Custom questions on one-page application forms (Greenhouse, Lever). Added Nov 23, 2026.

fill.fill_greenhouse_lever() fills name / email / phone / LinkedIn and uploads the files. This module then answers
every other question it can from the same answer bank the Workday filler uses (answers.py):
  seed / learned -> filled, demographic -> "decline" (only when a decline option exists), how-did-you-hear -> the real
  source of the job. Anything else that is required (unknown, essay, terms/certify/relatives/salary...) is listed in
  the Control Center Paused card and the window waits for him (or Claude answering the card from his profile).
Never guesses (unclear option match = left blank), never ticks consent boxes, never submits: every click goes through
apply_window.safe_click. The form itself is the "submit screen"; only he clicks Submit.
"""
from __future__ import annotations

import re
import time

import ai_answers as ai
import answers as ab
import apply_window as aw
from common import CONFIG

# Lists the questions on the page. Each control (or radio / checkbox group) gets data-pipeline-q="<n>".
SCAN_JS = """() => {
  const vis = e => !!(e && (e.offsetParent !== null || (e.getClientRects && e.getClientRects().length)));
  const txt = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('[data-pipeline-q]').forEach(e => e.removeAttribute('data-pipeline-q'));
  const scope = document.querySelector('#application-form, form#application_form, form[action*="apply" i], .application-form, form') || document.body;
  const box = el => el.closest('.application-question, .field-wrapper, .field, .select__container, .text-input-wrapper, .checkbox, fieldset, li, div');
  const labelOf = el => {
    let t = '';
    if (el.labels && el.labels.length) t = txt(el.labels[0]);
    const lb = el.getAttribute('aria-labelledby');
    if (!t && lb) t = lb.split(/\\s+/).map(id => txt(document.getElementById(id))).filter(Boolean)[0] || '';
    if (!t) { const q = el.closest('.application-question'); if (q) t = txt(q.querySelector('.application-label, .text')); }
    if (!t) { const f = el.closest('fieldset'); if (f) t = txt(f.querySelector('legend')); }
    if (!t) t = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    return t;
  };
  const optLabel = i => { if (i.labels && i.labels.length) return txt(i.labels[0]);
      const p = i.closest('label'); return p ? txt(p) : (i.getAttribute('aria-label') || i.value || ''); };
  const out = [], groups = {};
  let n = 0;
  const ctrls = [...scope.querySelectorAll('input, select, textarea')].filter(e => !e.closest('#pipeline-banner'));
  for (const el of ctrls) {
    const type = (el.type || '').toLowerCase();
    if (type === 'file') {   // Nov 24: extra upload questions (transcript, writing sample) are listed for him, never filled
      if (el.files && el.files.length) continue;
      if (/resume|\\bcv\\b|cover[\\s_-]*letter/i.test((el.id || '') + ' ' + (el.name || ''))) continue;
      const w = el.closest('.field-wrapper, .application-question, fieldset, .field') || el.parentElement;
      const lab = txt(w && w.querySelector('label.label, .label, legend, .application-label, label'));
      if (!lab || /^attach$/i.test(lab) || /resume|\\bcv\\b|cover\\s*letter/i.test(lab)) continue;
      const idx = String(n++); el.setAttribute('data-pipeline-q', idx);
      out.push({idx, label: lab, kind: 'file', value: '', options: [], id: el.id || '', edu: false,
                required: /[*✱]/.test(lab) || el.required || el.getAttribute('aria-required') === 'true'});
      continue;
    }
    if (['hidden', 'submit', 'button', 'search', 'password', 'image', 'reset'].includes(type)) continue;
    if (/beecatcher|honeypot|hp_|bot_?trap/i.test((el.getAttribute('data-automation-id') || '') + ' ' + el.id + ' ' + el.name)) continue;
    if (type === 'radio' || type === 'checkbox') {
      if (!vis(el) && !vis(el.closest('label')) && !vis(el.parentElement)) continue;
      const fs = el.closest('fieldset, [role=group], [role=radiogroup], .application-question, .field-wrapper');
      const key = (type === 'radio' && el.name) ? 'r:' + el.name : (fs ? 'g:' + (fs.dataset.pipelineG || (fs.dataset.pipelineG = 'g' + n++)) : 'c:' + n++);
      let g = groups[key];
      if (!g) {
        let label = fs ? (txt(fs.querySelector('legend')) || txt(fs.querySelector('.application-label .text, .application-label, label.label, .label'))) : '';
        g = groups[key] = {idx: String(n++), els: [], label, type};
        out.push(g);
      }
      g.els.push(el);
      el.setAttribute('data-pipeline-q', g.idx);
      continue;
    }
    if (!vis(el) && !(el.getAttribute('role') === 'combobox' && vis(el.closest('[class*=control]')))) continue;
    const idx = String(n++);
    el.setAttribute('data-pipeline-q', idx);
    let kind = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : 'text';
    let value = el.value || '', options = [];
    if (kind === 'select') { options = [...el.options].map(o => txt(o)).filter(Boolean);
      const cur = el.selectedIndex >= 0 ? txt(el.options[el.selectedIndex]) : '';
      value = (cur && !/^(-+|(please )?(select|choose)\\b.*|none selected)$/i.test(cur) && el.options[el.selectedIndex].value !== '') ? cur : ''; }
    if (el.getAttribute('role') === 'combobox' || el.closest('.select__control, [class*=select__control]')) {
      kind = 'combobox';
      const c = el.closest('.select__control, [class*=control]') || el.parentElement;
      value = [...(c ? c.querySelectorAll('[class*=single-value], [class*=multi-value__label]') : [])].map(txt).join('; ');
    }
    if (el.readOnly && kind !== 'combobox') continue;
    out.push({idx, label: labelOf(el), kind, value: (value || '').trim(), options, id: el.id || '',
              edu: /^(school|degree|discipline|start-month|start-year|end-month|end-year)--\\d+$/.test(el.id || ''),
              required: el.required || el.getAttribute('aria-required') === 'true'});
  }
  return out.map(f => {
    if (!f.els) return f;
    const lab = f.label || '';
    const opts = f.els.map(optLabel);
    const on = f.els.filter(e => e.checked || e.getAttribute('aria-checked') === 'true');
    const kind = f.type === 'radio' ? 'radio' : (f.els.length > 1 ? 'checkgroup' : 'checkbox');
    return {idx: f.idx, label: lab || (kind === 'checkbox' ? opts[0] : ''), kind, options: opts,
            value: kind === 'checkbox' ? (on.length ? 'checked' : '') : on.map(optLabel).join('; '),
            required: /[*✱]/.test(lab) || f.els.some(e => e.required || e.getAttribute('aria-required') === 'true')};
  }).map(f => Object.assign(f, {required: f.required || /[*✱]/.test(f.label), label: f.label.replace(/[*✱]/g, '').trim()}))
    .filter(f => f.label && !/for robots only|if you('| a)re human|leave (this )?(field )?blank/i.test(f.label));
}"""

OPTIONS_SEL = '[role="listbox"] [role="option"], .select__menu [role="option"], [class*="select__option"]'


class FormFiller:
    def __init__(self, page, job: dict, on_pause=None, wait_minutes: float | None = None):
        self.page, self.job, self.on_pause = page, job, on_pause
        self.wait_s = 60 * (wait_minutes if wait_minutes is not None else CONFIG.get("workday_wait_minutes", 20))
        self.filled: dict = {}
        self.skipped: list = []
        self.left: list = []
        self.failed: list = []       # answers the bank HAD but the form wouldn't take (a filler problem, not his)

    def scan(self) -> list[dict]:
        return self.page.evaluate(SCAN_JS)

    def loc(self, f, sub: str = ""):
        base = f'[data-pipeline-q="{f["idx"]}"]'
        return self.page.locator(base + (sub or ""))

    def _click(self, loc, **kw):
        try:
            aw.safe_click(loc, self.page, timeout=3000, **kw)
        except aw.GuardError:
            raise
        except Exception:  # noqa: BLE001  (styled control over the real input)
            aw.safe_click(loc, self.page, force=True)

    def _options(self) -> tuple[list[str], object]:
        opts = self.page.locator(OPTIONS_SEL)
        try:
            n = opts.count()
            texts = [opts.nth(i).inner_text().strip() for i in range(n) if opts.nth(i).is_visible()]
        except Exception:  # noqa: BLE001
            return [], opts
        return texts, opts

    @staticmethod
    def _pick(answer: str, options: list[str], label: str = "", use_ai: bool = False):
        """pick_option, plus: a two-value answer ("Finance, Accounting") on a one-pick list takes the first value
        that matches an option exactly (both are true; nothing is guessed). use_ai: last try, Gemini picks among the
        shown options (Nov 26)."""
        choice = ab.pick_option(answer, options)
        if choice is None and "," in answer:
            low = {o.strip().lower(): o for o in options}
            for part in (x.strip() for x in answer.split(",")):
                if part.lower() in low:
                    return low[part.lower()]
        if choice is None and use_ai and label and answer and answer != "decline":
            choice = ai.pick(label, answer, options)
            if choice is not None:
                ai.note(label, f"Gemini chose '{choice}' for the answer '{answer}'")
        return choice

    def read_options(self, f: dict) -> list[str]:
        """Opens a combobox just to list its choices for the Paused card, then closes it. Selects nothing."""
        try:
            self._click(self.loc(f).first)
            self.page.wait_for_timeout(700)
            texts, _ = self._options()
            self.page.keyboard.press("Escape")
            return texts[:40]
        except aw.GuardError:
            raise
        except Exception:  # noqa: BLE001
            return []

    def fill_field(self, f: dict, answer: str) -> bool:
        kind = f["kind"]
        try:
            if kind in ("text", "textarea"):
                box = self.loc(f).first
                try:
                    box.fill(answer, timeout=5000)
                except Exception:  # noqa: BLE001  (masked phone boxes refuse fill(): click and type instead)
                    self._click(box)
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.type(answer, delay=40)
                box.press("Tab", timeout=5000)
            elif kind == "select":
                choice = self._pick(answer, f["options"], f["label"], True)
                if choice is None:
                    return False
                self.loc(f).first.select_option(label=choice)
            elif kind == "combobox":
                box = self.loc(f).first
                self._click(box)
                self.page.wait_for_timeout(700)
                texts, opts = self._options()
                choice = self._pick(answer, texts)
                if choice is None:                      # async list (school, city): type to search
                    box.fill(answer if answer != "decline" else "")
                    self.page.wait_for_timeout(1800)
                    texts, opts = self._options()
                    choice = self._pick(answer, texts)
                if choice is None and answer != "decline":     # Nov 26: re-open the full list, Gemini picks from it
                    box.fill("")
                    self.page.wait_for_timeout(600)
                    texts, opts = self._options()
                    choice = self._pick(answer, texts, f["label"], True)
                if choice is None:
                    box.fill("")
                    self.page.keyboard.press("Escape")
                    return False
                vis = [opts.nth(i) for i in range(opts.count()) if opts.nth(i).is_visible()]
                self._click(vis[texts.index(choice)])
                answer = choice
            elif kind in ("radio", "checkgroup"):
                choice = self._pick(answer, f["options"], f["label"], True)
                if choice is None:
                    return False
                self._click(self.loc(f).nth(f["options"].index(choice)))
                answer = choice
            else:
                return False
        except aw.GuardError:
            raise
        except Exception as exc:  # noqa: BLE001  (unexpected widget: leave it for him)
            print(f"  (couldn't fill '{f['label'][:50]}': {exc.__class__.__name__}: {str(exc).splitlines()[0][:120]})", flush=True)
            return False
        self.filled[f["label"]] = answer
        self.page.wait_for_timeout(250)
        return True

    def _item(self, f, cat, note=""):
        opts = f.get("options") or []
        if not opts and f["kind"] == "combobox" and cat != "never":
            opts = self.read_options(f)          # so the Paused card shows the real choices
        kind = "dropdown" if f["kind"] in ("select", "combobox") else f["kind"]   # the Paused card shows a pick list
        return {"label": f["label"], "kind": kind, "options": opts, "category": cat,
                "note": note, "idx": f["idx"]}

    def _fresh(self, key):
        """Re-scan and return the field (label, kind, n-th of that label). Answering one question can re-draw others
        (Nov 24, CRA: choosing the country re-draws the phone box), so tags from an earlier scan can't be trusted."""
        seen = {}
        for g in self.scan():
            k = (g["label"], g["kind"])
            seen[k] = seen.get(k, 0) + 1
            if (k[0], k[1], seen[k]) == key:
                return g
        return None

    def fill_all(self) -> list[dict]:
        pending = []
        keys, seen = [], {}
        first = self.scan()
        for g in first:
            k = (g["label"], g["kind"])
            seen[k] = seen.get(k, 0) + 1
            keys.append((k[0], k[1], seen[k]))
        # Nov 26: required questions the rules can't answer go to Gemini in one call (by label: fields re-draw)
        unk = [dict(g, idx=g["label"]) for g in first if g.get("required") and not g.get("value") and g["kind"] not in ("file", "checkbox")
               and not g.get("edu") and ab.classify(g["label"], g["kind"], self.job)[0] == "unknown"]
        solved = ai.solve(unk, self.job) if unk else {}
        for key in keys:
            f = self._fresh(key)
            if f is None or f["value"]:
                continue
            label = f["label"]
            if f.get("edu"):   # Greenhouse education block: "End date month/year" = graduation (Nov 24, CRA)
                m = re.match(r"^(start|end)-(month|year)--\d+$", f.get("id", ""))
                if m and m.group(1) == "end":
                    label = "graduation " + m.group(2)
                elif m and m.group(2) == "year":
                    start = str((ab.PROFILE.get("education") or {}).get("start", ""))
                    yr = re.search(r"20\d\d", start)
                    if yr and not f["value"]:
                        if not self.fill_field(f, yr.group(0)) and f["required"]:
                            pending.append(self._item(f, "unknown"))
                        continue
                    label = f["label"]
            if f["kind"] == "file":
                if f["required"]:
                    pending.append(self._item(f, "never", "upload this file yourself (only the resume and cover letter are attached automatically)"))
                else:
                    self.skipped.append(f["label"])
                continue
            cat, ans = ab.classify(label, f["kind"], self.job)
            if f["kind"] == "checkbox" and cat not in ("never",):
                cat = "never" if f["required"] else cat      # a lone box is a consent / opt-in box: his
            if cat == "eeo":
                if not self.fill_field(f, "decline") and f["required"]:
                    pending.append(self._item(f, cat, "pick an answer (decline if offered)"))
            elif cat in ("seed", "learned") and f["kind"] != "checkbox":
                if not self.fill_field(f, ans) and f["required"]:
                    self.failed.append(f["label"])
                    pending.append(self._item(f, "unknown", f"couldn't match '{ans}' to the options"))
            elif cat == "unknown" and label in solved and f["kind"] != "checkbox":
                g = solved[label]
                if self.fill_field(f, g["answer"]):
                    ai.note(label, f"Gemini: {g['key']}")
                    print(f"  Gemini answered '{label[:60]}' -> {g['key']}", flush=True)
                elif f["required"]:
                    pending.append(self._item(f, "unknown", f"couldn't match '{g['answer']}' to the options"))
            elif f["required"]:
                note = {"never": "yours to answer on the page", "essay": "essay: draft it with /answer in Claude"}.get(cat, "")
                pending.append(self._item(f, cat, note))
            else:
                self.skipped.append(f["label"])
        return pending

    def _resolved(self, items) -> bool:
        now = {ab.norm(f["label"]): f["value"] for f in self.scan()}
        return all(now.get(ab.norm(i["label"])) for i in items)

    def apply_answers(self, got: dict):
        fields = {ab.norm(f["label"]): f for f in self.scan()}
        for label, answer in got.items():
            f = fields.get(ab.norm(label))
            if f and not f["value"] and self.fill_field(f, answer):
                print(f"  Filled '{f['label']}' with your answer (saved for next time).", flush=True)

    def run(self) -> str:
        """'ready' = every question answered (only Submit left for him); else 'left: N'."""
        pending = self.fill_all()
        if pending:
            n = len(pending)
            reason = (f"{n} question{'s' if n > 1 else ''} need you on this form. Answer in the Control Center "
                      "(Apply tab) or on the page.")
            print(f"PAUSED ({self.job.get('company')}): {reason}", flush=True)
            try:
                import workday
                workday.debug_dump(self.page, "pause")
            except Exception:  # noqa: BLE001
                pass
            aw.show_banner(self.page, "greenhouse_lever", text="PAUSED: " + reason)
            ab.write_pending(self.job, "Application form", pending, waiting=reason)
            try:
                if self.on_pause:
                    self.on_pause(self, reason, pending)
                end = time.time() + self.wait_s
                while time.time() < end:
                    if self.page.is_closed():
                        return "closed"
                    got, cont = ab.take_inbox()
                    if got:
                        self.apply_answers(got)
                    if cont or self._resolved(pending):
                        break
                    self.page.wait_for_timeout(1000)
            finally:
                ab.clear_pending()
            self.fill_all()            # answers he saved may cover other questions too
        left = [f for f in self.scan() if f["required"] and not f["value"]]
        self.left = [f["label"] for f in left]
        return "ready" if not left else f"left: {len(left)}"


LAST: dict = {}     # details of the last run (fill.py records verification from it)


def fill(page, job: dict, on_pause=None, wait_minutes=None) -> tuple[str, dict, list]:
    ff = FormFiller(page, job, on_pause, wait_minutes)
    ai.reset_calls(); ai.DECIDED.clear()
    out = ff.run()
    failed = [x for x in ff.failed if x in ff.left]      # still blank at the end
    LAST.clear()
    LAST.update(failed=failed, left=list(ff.left))
    return out, ff.filled, ff.left
