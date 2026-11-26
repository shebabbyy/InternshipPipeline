"""Gemini answers the questions the fixed rules can't (Nov 26). Works the same on every Workday / Greenhouse / Lever
form: nothing here knows any company.

Two jobs, both called by the fillers:
  solve(fields, job)       when a page loads: every required question classify() left "unknown" goes to Gemini in
                           ONE call. Gemini only says WHICH FACT the question asks for (a key from answers.facts() or
                           one of his saved questions) and, for yes/no facts, whether the question is worded the
                           other way round. The code then types the real value. Gemini never writes free text.
  pick(label, answer, options)
                           a known answer that matches none of the shown options literally ("Yes" vs "I am
                           authorized to work in the U.S."): Gemini chooses one of the shown options or none.

Hard rules (checked in code, not just asked of Gemini):
  - never / demographic / how-did-you-hear / essay questions are never sent and never answered here.
  - Private values (name, email, phone, ZIP, LinkedIn) and his saved answers are never sent: only the fact names
    and his saved QUESTIONS. answers.facts() marks which.
  - Every reply is validated: unknown key, low confidence, an option that isn't on the page -> ignored (the
    question goes to the Paused card as before).
  - Gemini down, slow or no key -> nothing breaks; the question goes to the Paused card.
  - Everything Gemini decided is listed in the run log and shown yellow ("Gemini picked") on the Review check.
Results are cached in output/_ai_answers_cache.json (questions, keys and option texts only; no values)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import answers as ab
from common import CONFIG, ROOT, gemini_json

CACHE_PATH = ROOT / "output" / "_ai_answers_cache.json"
_calls = {"n": 0}
DECIDED: dict = {}     # norm(label) -> what Gemini decided this run (the Review check shows these yellow)


def enabled() -> bool:
    return bool(CONFIG.get("gemini_answers", True))


def _max_calls() -> int:
    return int(CONFIG.get("gemini_answers_max_calls", 20))


def reset_calls() -> None:
    _calls["n"] = 0


def note(label: str, text: str) -> None:
    DECIDED[ab.norm(label)] = text


# ---------------------------------------------------------------- cache
def _read_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _write_cache(c: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(c, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except Exception:  # noqa: BLE001
        pass


def _ck(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:16]


def _ask(prompt: str):
    """One quick Gemini call. Never waits minutes during an application (the daily scan may; this may not)."""
    if _calls["n"] >= _max_calls():
        raise RuntimeError("Gemini answer limit for this run reached")
    _calls["n"] += 1
    return gemini_json(prompt, retries=1, patient=False)


def _sendable(label: str, kind: str = "text") -> bool:
    q = ab.norm(label)
    return bool(q) and not (ab.NEVER.search(q) or ab.EEO.search(q) or ab.SOURCE.search(q)
                            or (kind == "text" and ab.ESSAY.search(q)) or kind in ("textarea", "file", "checkbox"))


def _private_values() -> list[str]:
    """Identity values + saved answers that look personal (an address, a number, an email, a long text).
    Short everyday saved answers ("English", "Houston, TX") are not secrets and may appear in page questions."""
    fl = ab.facts()
    public = {ab.norm(f["value"]) for f in fl if not f["private"]}
    vals = [f["value"] for f in fl if f["private"]]
    vals += [v for v in ab._read_bank().values()
             if (any(ch.isdigit() for ch in v) or "@" in v or len(v) >= 15) and ab.norm(v) not in public]
    return [v for v in vals if v and len(v.strip()) >= 3]


def leaks(prompt: str) -> list[str]:
    """Private values found in a prompt (must be empty; checked before every call and in the tests)."""
    low = prompt.lower()
    return [v for v in _private_values() if v.lower() in low]


# ---------------------------------------------------------------- 1) which fact does each question ask for?
SOLVE_PROMPT = """You map job-application form questions to facts about one applicant. You never invent answers.

FACTS (key: meaning [= value, or [private] when the value is hidden from you]):
{facts}

SAVED QUESTIONS (he answered these before; values hidden):
{bank}

QUESTIONS ON THIS PAGE ({company}, {title}):
{fields}

For each question, return the ONE fact key or saved-question key (bank:N) whose meaning is exactly what the question
asks. Rules:
- Use "none" if no key asks exactly the same thing, if the question is about something specific to this company
  (which office, practice area, team, program, referral name, start date, dates not in the facts), or if you are unsure.
- For a fact whose value is Yes or No, also say "same" if answering the question truthfully gives the same Yes/No
  as the fact, or "opposite" if the question is worded the other way round (e.g. fact "requires sponsorship = No",
  question "Are you able to work without sponsorship?" -> opposite). For other facts use "same".
- confidence "high" only if a careful human would pick the same key without hesitation.
Return JSON only: {{"answers": [{{"id": <id>, "key": "<key or none>", "polarity": "same|opposite", "confidence": "high|low"}}]}}"""


def solve(fields: list[dict], job: dict | None = None) -> dict:
    """fields: [{idx, label, kind, options?, required?}] the rules left unknown. -> {idx: {answer, key, note}}.
    Anything unsure is simply missing from the result."""
    if not enabled():
        return {}
    todo = [f for f in fields if _sendable(f.get("label", ""), f.get("kind", "text"))]
    if not todo:
        return {}
    fl = ab.facts(job)
    by_key = {f["key"]: f for f in fl}
    bank = ab._read_bank()
    bank_keys = {f"bank:{i}": q for i, q in enumerate(sorted(bank))}
    cache = _read_cache()
    sol, ask = {}, []
    for f in todo:
        ck = _ck("solve", ab.norm(f["label"]), f.get("kind"), f.get("options") or [])
        hit = cache.get(ck)
        if hit and (hit["key"] in by_key or hit.get("bank_q") in bank):
            sol[f["idx"]] = hit
        elif hit and hit["key"] == "none":
            continue
        else:
            ask.append((f, ck))
    if ask:
        lines = []
        for n, (f, _) in enumerate(ask):
            opts = [o for o in (f.get("options") or []) if o][:30]
            lines.append(f'{n}. "{f["label"]}" [{f.get("kind", "text")}]' + (f" options: {opts}" if opts else ""))
        prompt = SOLVE_PROMPT.format(
            facts="\n".join(f'- {x["key"]}: {x["desc"]} = ' + ("[private]" if x["private"] else x["value"]) for x in fl),
            bank="\n".join(f'- {k}: "{q}"' for k, q in bank_keys.items()) or "(none)",
            company=(job or {}).get("company", ""), title=(job or {}).get("title", ""), fields="\n".join(lines))
        bad = leaks(prompt)
        if bad:   # should be impossible; never send if it happens
            print(f"  (Gemini answers skipped: the prompt would contain {len(bad)} private value(s))", flush=True)
            return _resolve(sol, by_key, bank)
        try:
            reply = _ask(prompt)
        except Exception as exc:  # noqa: BLE001  (no key, busy, offline: the Paused card takes over)
            print(f"  (Gemini answers unavailable: {str(exc)[:120]})", flush=True)
            return _resolve(sol, by_key, bank)
        rows = reply.get("answers", []) if isinstance(reply, dict) else []
        for r in rows if isinstance(rows, list) else []:
            try:
                f, ck = ask[int(r.get("id"))]
            except (TypeError, ValueError, IndexError):
                continue
            key, pol, conf = str(r.get("key", "none")), str(r.get("polarity", "same")), str(r.get("confidence", "low"))
            if key == "none" or conf != "high":
                cache[ck] = {"key": "none"}
                continue
            if key in by_key:
                entry = {"key": key, "polarity": "opposite" if pol == "opposite" else "same"}
            elif key in bank_keys:
                entry = {"key": "bank", "bank_q": bank_keys[key], "polarity": "same"}
            else:
                cache[ck] = {"key": "none"}      # a key that doesn't exist: ignored (and not asked again)
                continue
            cache[ck] = entry
            sol[f["idx"]] = entry
        _write_cache(cache)
    return _resolve(sol, by_key, bank)


def _flip(v: str) -> str:
    return "No" if ab.norm(v) == "yes" else "Yes"


def _resolve(sol: dict, by_key: dict, bank: dict) -> dict:
    out = {}
    for idx, e in sol.items():
        if e.get("key") == "bank":
            q = e.get("bank_q")
            if q not in bank:
                continue
            out[idx] = {"answer": bank[q], "key": f"saved answer to '{q}'", "note": "Gemini matched a saved question"}
            continue
        fact = by_key.get(e.get("key"))
        if not fact:
            continue
        val = fact["value"]
        if e.get("polarity") == "opposite":
            if not ab.is_yes_no(val):
                continue      # only a yes/no fact can be turned round
            val = _flip(val)
        out[idx] = {"answer": val, "key": fact["key"] + (" (reversed)" if e.get("polarity") == "opposite" else ""),
                    "note": "Gemini matched the question to a profile fact"}
    return out


# ---------------------------------------------------------------- 2) which shown option means this answer?
PICK_PROMPT = """A job-application question, the applicant's true answer, and the options the form shows.
Question: "{label}"
True answer: "{answer}"
Options: {options}
Return the ONE option that states exactly the same thing as the true answer, copied character for character.
Return null if none does, if two could, or if you are unsure. JSON only: {{"choice": "<option or null>"}}"""


def pick(label: str, answer: str, options: list[str]) -> str | None:
    opts = [o for o in options if o and ab.norm(o) not in ("select one", "")]
    if not enabled() or not opts or not answer or answer == "decline" or len(opts) > 60:
        return None
    q = ab.norm(label)
    if ab.NEVER.search(q) or ab.EEO.search(q):
        return None
    if any(answer.strip().lower() == v.lower() for v in _private_values()):
        return None          # a private value is never sent
    ck = _ck("pick", q, ab.norm(answer), sorted(opts))
    cache = _read_cache()
    if ck in cache:
        c = cache[ck].get("choice")
        return c if c in opts else None
    prompt = PICK_PROMPT.format(label=label, answer=answer, options=json.dumps(opts, ensure_ascii=False))
    if leaks(prompt):
        return None
    try:
        reply = _ask(prompt)
    except Exception as exc:  # noqa: BLE001
        print(f"  (Gemini option pick unavailable: {str(exc)[:120]})", flush=True)
        return None
    c = reply.get("choice") if isinstance(reply, dict) else None
    c = c if isinstance(c, str) and c in opts else None
    cache[ck] = {"choice": c}
    _write_cache(cache)
    if c:
        print(f"  Gemini picked '{c}' for '{label[:60]}' (answer: {answer})", flush=True)
    return c
