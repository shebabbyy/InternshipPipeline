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
    if (['hidden', 'file', 'submit', 'button', 'search', 'password', 'image', 'reset'].includes(type)) continue;
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
    out.push({idx, label: labelOf(el), kind, value: (value || '').trim(), options,
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
    .filter(f => f.label);
}"""

OPTIONS_SEL = '[role="listbox"] [role="option"], .select__menu [role="option"], [class*="select__option"]'


class FormFiller:
    def __init__(self, page, job: dict, on_pause=None, wait_minutes: float | None = None):
        self.page, self.job, self.on_pause = page, job, on_pause
        self.wait_s = 60 * (wait_minutes if wait_minutes is not None else CONFIG.get("workday_wait_minutes", 20))
        self.filled: dict = {}
        self.skipped: list = []
        self.left: list = []

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

    def fill_field(self, f: dict, answer: str) -> bool:
        kind = f["kind"]
        try:
            if kind in ("text", "textarea"):
                box = self.loc(f).first
                box.fill(answer)
                box.press("Tab")
            elif kind == "select":
                choice = ab.pick_option(answer, f["options"])
                if choice is None:
                    return False
                self.loc(f).first.select_option(label=choice)
            elif kind == "combobox":
                box = self.loc(f).first
                self._click(box)
                self.page.wait_for_timeout(700)
                texts, opts = self._options()
                choice = ab.pick_option(answer, texts)
                if choice is None:                      # async list (school, city): type to search
                    box.fill(answer if answer != "decline" else "")
                    self.page.wait_for_timeout(1800)
                    texts, opts = self._options()
                    choice = ab.pick_option(answer, texts)
                if choice is None:
                    box.fill("")
                    self.page.keyboard.press("Escape")
                    return False
                vis = [opts.nth(i) for i in range(opts.count()) if opts.nth(i).is_visible()]
                self._click(vis[texts.index(choice)])
                answer = choice
            elif kind in ("radio", "checkgroup"):
                choice = ab.pick_option(answer, f["options"])
                if choice is None:
                    return False
                self._click(self.loc(f).nth(f["options"].index(choice)))
                answer = choice
            else:
                return False
        except aw.GuardError:
            raise
        except Exception:  # noqa: BLE001  (unexpected widget: leave it for him)
            return False
        self.filled[f["label"]] = answer
        self.page.wait_for_timeout(250)
        return True

    def _item(self, f, cat, note=""):
        return {"label": f["label"], "kind": f["kind"], "options": f.get("options") or [], "category": cat,
                "note": note, "idx": f["idx"]}

    def fill_all(self) -> list[dict]:
        pending = []
        for f in self.scan():
            if f["value"]:
                continue
            cat, ans = ab.classify(f["label"], f["kind"], self.job)
            if f["kind"] == "checkbox" and cat not in ("never",):
                cat = "never" if f["required"] else cat      # a lone box is a consent / opt-in box: his
            if cat == "eeo":
                if not self.fill_field(f, "decline") and f["required"]:
                    pending.append(self._item(f, cat, "pick an answer (decline if offered)"))
            elif cat in ("seed", "learned") and f["kind"] != "checkbox":
                if not self.fill_field(f, ans) and f["required"]:
                    pending.append(self._item(f, "unknown", f"couldn't match '{ans}' to the options"))
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


def fill(page, job: dict, on_pause=None, wait_minutes=None) -> tuple[str, dict, list]:
    ff = FormFiller(page, job, on_pause, wait_minutes)
    out = ff.run()
    return out, ff.filled, ff.left
