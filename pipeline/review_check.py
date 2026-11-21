"""Review-page check (phase 2): before Shehab clicks Submit, read every field back and compare it to the profile
and the answer bank. green = matches, red = mismatch or a known Simplify/parser guess, yellow = filled by hand,
Workday's resume parser or Simplify (check it). Works on whatever filled the page: this code, Simplify or him.
Results: a panel on the page, a JSON next to the Review screenshot, and a summary line in the log.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import answers as ab
from common import PROFILE, ROOT

READ_JS = """() => {
  const vis = e => !!(e && e.offsetParent !== null);
  const heads = [...document.querySelectorAll('h1,h2,h3,h4,[data-automation-id*="sectionHeader" i]')].filter(vis);
  const sectionOf = el => { let s = ''; for (const h of heads) {
      if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) s = h.innerText.trim(); } return s; };
  let wraps = [...document.querySelectorAll('[data-automation-id^="formField-"], dl > div, .pipeline-pair')].filter(vis);
  wraps = wraps.filter(w => !wraps.some(o => o !== w && w.contains(o)));
  const out = [];
  for (const w of wraps) {
    const lab = w.querySelector('label, legend, dt, [data-automation-id="formLabel"]');
    const label = (lab ? lab.innerText : '').replace(/\\s+/g, ' ').trim();
    let value = (w.innerText || '').replace(/\\s+/g, ' ').trim();
    if (label && value.startsWith(label)) value = value.slice(label.length).trim();
    if (label) out.push({section: sectionOf(w), label, value});
  }
  return out;
}"""

PANEL_JS = """(r) => {
  let p = document.getElementById('pipeline-review-panel');
  if (!p) { p = document.createElement('div'); p.id = 'pipeline-review-panel';
    p.style.cssText = 'position:fixed;right:12px;bottom:12px;width:380px;max-height:55vh;overflow:auto;z-index:2147483646;' +
      'background:#fff;border:2px solid #b00020;border-radius:8px;box-shadow:0 4px 18px rgba(0,0,0,.3);' +
      'font:13px Segoe UI,Arial,sans-serif;padding:10px;color:#000';
    (document.body || document.documentElement).appendChild(p); }
  const col = {red: '#b00020', yellow: '#8a6d00', green: '#1b5e20'};
  p.innerHTML = '';
  const h = document.createElement('div'); h.style.fontWeight = '700';
  h.textContent = `Pipeline check: ${r.red} red, ${r.yellow} yellow, ${r.green} green. Fix red before you Submit.`;
  p.appendChild(h);
  for (const it of r.items) { if (it.color === 'green') continue;
    const d = document.createElement('div'); d.style.cssText = 'margin-top:6px;color:' + col[it.color];
    d.textContent = (it.color === 'red' ? 'RED ' : 'CHECK ') + it.label + ': ' + (it.value || '(blank)') + (it.why ? '  (' + it.why + ')' : '');
    p.appendChild(d); }
}"""


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def values_match(value: str, expected: str) -> bool:
    v, e = ab.norm(value), ab.norm(expected)
    if not v or not e:
        return False
    if v == e or e in v or v in e:
        return True
    if len(_digits(expected)) >= 7 and _digits(expected)[-10:] == _digits(value)[-10:]:
        return True
    if e in ("yes", "no"):
        return v.split()[0] == e
    return ab.pick_option(expected, [value]) is not None


def _titles():
    return {ab.norm(re.sub(r"\(.*?\)", "", x.get("title", ""))) for x in PROFILE.get("experience", [])}


def _orgs():
    return {ab.norm(x.get("organization", "")) for x in PROFILE.get("experience", [])}


def compare(pairs: list[dict], job: dict | None = None, filled: dict | None = None) -> list[dict]:
    """Pure function. pairs = [{section, label, value}]; filled = labels the code filled -> value."""
    filled = {ab.norm(k): v for k, v in (filled or {}).items()}
    has_start = bool(PROFILE.get("education", {}).get("start"))
    out = []
    for p in pairs:
        label, value, sec = p.get("label", ""), (p.get("value") or "").strip(), ab.norm(p.get("section", ""))
        q = ab.norm(label)
        if not value or ab.norm(value) in ("select one", "none", "no file chosen"):
            continue
        color, why = "yellow", "not filled by the pipeline: check it"
        cat, exp = ab.classify(label, job=job)
        if "education" in sec and re.search(r"^(from|start|start date|first year attended)$", q) and not has_start:
            color, why = "red", "education start date is not in your profile (a parser/Simplify guess)"
        elif cat in ("seed", "learned"):
            color, why = ("green", "") if values_match(value, exp) else ("red", f"expected {exp}")
        elif cat == "eeo":
            color, why = ("green", "") if ab.DECLINE.search(value) else ("yellow", "demographic answer: you chose decline by default")
        elif cat == "never":
            color, why = "yellow", "your own answer: read it once more"
        elif re.search(r"job title|position title|^title$", q):
            color, why = ("green", "") if ab.norm(re.sub(r"\(.*?\)", "", value)) in _titles() else ("yellow", "not a title from your profile")
        elif re.search(r"^company|employer", q):
            color, why = ("green", "") if any(o and (o in ab.norm(value) or ab.norm(value) in o) for o in _orgs()) else \
                ("yellow", "not an employer from your profile")
        elif q in filled:
            color, why = ("green", "") if values_match(value, filled[q]) else ("red", f"changed from {filled[q]}")
        out.append({"section": p.get("section", ""), "label": label, "value": value, "color": color, "why": why})
    return out


def summarize(items: list[dict]) -> dict:
    r = {c: sum(i["color"] == c for i in items) for c in ("red", "yellow", "green")}
    r["items"] = items
    return r


def run(page, job: dict, filled: dict | None = None) -> dict | None:
    """Read the Review page, show the panel, save JSON. Never clicks anything."""
    try:
        pairs = page.evaluate(READ_JS)
    except Exception:  # noqa: BLE001
        return None
    res = summarize(compare(pairs, job, filled))
    try:
        page.evaluate(PANEL_JS, res)
    except Exception:  # noqa: BLE001
        pass
    shots = ROOT / "output" / "_submissions"
    shots.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9]+", "", job.get("company") or "job")
    path = shots / f"{name}_review_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Review check for {job.get('company')}: {res['red']} red, {res['yellow']} to check, {res['green']} match.", flush=True)
    for it in res["items"]:
        if it["color"] == "red":
            print(f"  RED  {it['label']}: {it['value']} ({it['why']})", flush=True)
    return res
