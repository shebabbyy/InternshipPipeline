"""Answer bank for application questions (Control Center v2, phase 2).

Order of lookup for a question label:
  1. never   : questions only Shehab answers (terms / consent / certify / signature, salary, criminal or background,
               non-compete, relatives at the company, legal attestations). Left blank, always.
  2. eeo     : demographic / voluntary self-identification. The code picks the "decline" option, nothing else.
  3. learned : answers he gave once (answers.yaml, written by this module). Reused everywhere.
  4. seed    : answers built from master_profile.json + the job (work authorization Yes, sponsorship No, GPA...).
  5. essay   : "why us" / "tell us about" text questions. Left blank and listed for him (drafting comes later).
  6. unknown : left blank, listed in the Control Center; his answer is saved to answers.yaml and reused.

answers.yaml (pipeline root) is private (git-ignored). The Control Center never writes it directly: it drops his
answers into output/_answers_inbox.json and this module folds them in (single writer).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import yaml

from common import CONFIG, PROFILE, ROOT

BANK_PATH = ROOT / "answers.yaml"
INBOX_PATH = ROOT / "output" / "_answers_inbox.json"
PENDING_PATH = ROOT / "output" / "_apply_pending.json"

NEVER = re.compile(
    r"\b(agree|terms|consent|certif|attest|acknowledg|electronic signature|e-?signature|sign(ature)? (here|below)|"
    r"type your (full )?name|full legal name to sign|salary|compensation|pay (rate|expectation|range)|hourly rate|"
    r"desired (pay|wage)|criminal|convicted|conviction|felony|misdemeanor|background check|drug (test|screen)|"
    r"non-?compete|non-?solicit|restrictive covenant|relatives?|family members?|related to (any|an) (employee|associate)|"
    r"politically exposed|government official|true and (complete|correct|accurate)|under penalty)", re.I)
EEO = re.compile(
    r"\b(gender|sex\b|race|racial|ethnicity|ethnic|hispanic|latino|veteran|disabilit|sexual orientation|"
    r"transgender|pronoun|lgbt|military spouse|self[- ]identif)", re.I)
ESSAY = re.compile(r"\b(why (are you|do you|would you|this|our|us)|why .* interest|describe|tell us|explain|"
                   r"what (interests|excites|motivates|makes)|cover letter|in (\d+|a few) (words|sentences)|elaborate)", re.I)
# "How did you hear / where did you find this job": always the real source of THIS posting (heard_from), never a
# saved answer, because the source differs per job (Nov 21: a saved "Company Website" would be wrong for LinkedIn jobs).
SOURCE = re.compile(r"how did you (first )?(hear|learn|find)|^source$|referral source|"
                    r"where did you (first )?(hear|find|see|locate|learn|come across)|(first )?(locate|found) this (position|job|role)",
                    re.I)
DECLINE = re.compile(r"(decline|do not wish|don.?t wish|not wish to|prefer not|choose not|do not want to answer|"
                     r"don.?t want to answer|not to (answer|disclose|self)|i don.?t know)", re.I)


def norm(text: str) -> str:
    t = re.sub(r"[*✱]", " ", str(text or "")).lower()
    t = re.sub(r"\(required\)|required", " ", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9&+/' ]", " ", t)).strip()


# ---------------------------------------------------------------- seed answers from the profile
def _first_last():
    parts = (PROFILE.get("candidate", {}).get("name") or "").split()
    return (parts[0], parts[-1]) if parts else ("", "")


def account_email() -> str:
    """Portal accounts always use Gmail (Nov 20 decision), even for campus roles."""
    return PROFILE.get("candidate", {}).get("email", "")


def heard_from(job: dict | None) -> str:
    """The real source of the posting, for 'How did you hear about us?'."""
    src = " ".join(str((job or {}).get(k) or "") for k in ("source", "notes")).lower()
    for word, answer in (("career fair", "Career Fair"), ("handshake", "Handshake"), ("gateway", "University Career Center"),
                         ("linkedin", "LinkedIn"), ("indeed", "Indeed"), ("glassdoor", "Glassdoor"),
                         ("zip_recruiter", "ZipRecruiter"), ("ziprecruiter", "ZipRecruiter")):
        if word in src:
            return answer
    return "Company Website"   # Workday / Greenhouse / Lever feeds = the company's own career site


def answer_variants(label: str, answer: str, job: dict | None = None) -> list[str]:
    """Other wordings of the SAME answer, tried when a pick list doesn't use ours (Nov 24, Protiviti: no 'Company
    Website' in its list). Only phrasing changes, never the fact: the company's own site stays the company's own site."""
    out = [answer]
    if SOURCE.search(norm(label)):
        co = ((job or {}).get("company") or "").strip()
        short = co.split()[0] if co else ""
        if answer == "Company Website":
            out += ["Company Career Site", "Company Careers Site", "Career Site", "Careers Site", "Careers Website",
                    "Career Website", "Corporate Website", "Company Website/Career Site", "Our Website", "Our Career Site"]
            out += [f"{x} {y}" for x in (co, short) if x for y in ("Website", "Career Site", "Careers", "Careers Website", ".com")]
            out += [f"{short}.com"] if short else []
        elif answer in ("LinkedIn", "Indeed", "Glassdoor", "ZipRecruiter", "Handshake"):
            out += [f"{answer} Job Posting", f"{answer} Job Board", f"Job Board - {answer}", f"Social Media - {answer}"]
    seen, res = set(), []
    for x in out:
        if x and x.lower() not in seen:
            seen.add(x.lower()); res.append(x)
    return res


def seed_rules(job: dict | None = None) -> list[tuple[str, str]]:
    """(regex on the normalized label, answer). First match wins. Only facts from master_profile + config."""
    c, e = PROFILE.get("candidate", {}), PROFILE.get("education", {})
    sa = PROFILE.get("standard_answers", {})
    first, last = _first_last()
    loc = c.get("location", "")
    zip_m = re.search(r"\b(\d{5})\b", loc)
    phone = re.sub(r"\D", "", c.get("phone", ""))[-10:]
    grad = e.get("graduation") or sa.get("graduation_date", "")
    year = (re.search(r"(20\d\d)", grad) or [""])[0] if grad else ""
    company = norm((job or {}).get("company", ""))
    worked_there = any(company and company in norm(x.get("organization", "")) for x in PROFILE.get("experience", []))
    yes, no = "Yes", "No"
    rules = [
        (r"^(legal )?(first|given) name$|^first name legal", first),
        (r"^(legal )?(last|family) name$|^surname$|^last name legal", last),
        (r"^(e ?mail|email address)$", account_email()),
        (r"^(phone|phone number|mobile|mobile phone|cell phone|primary phone)( number)?$", phone),
        (r"^phone (device )?type$|^device type$", "Mobile"),
        (r"^country phone code$", "United States of America (+1)"),
        (r"^(country|country region|country/region)$", "United States of America"),
        (r"^city$|^city/town$", "Houston"),
        (r"^(location|location city|current location|city and state|city state|where are you located)$", "Houston, Texas"),
        (r"^(full|legal full|full legal) name$|^name$", " ".join(x for x in (first, last) if x)),
        (r"^(state|state/province|province|region)$", "Texas"),
        (r"^(postal|zip)( code)?$|^zip/postal code$", zip_m.group(1) if zip_m else ""),
        (r"linkedin", c.get("linkedin", "")),
        (r"(legally )?(authori[sz]ed|eligible|permitted) to work|^work (eligibility|authori[sz]ation)( status)?$", sa.get("authorized_to_work_us", "")),
        (r"sponsor|visa (status|support)|work permit", sa.get("requires_sponsorship", "")),
        (r"(at least|over|older than) (the age of )?18|18 years", yes),
        (r"relocat", no),                                         # Houston only (decided)
        (r"(work|commute|located|live|reside|based) (in|at|near|from) .*houston|houston .*(office|location)", yes),
        (r"(in[- ]office|on[- ]site|onsite|hybrid|in person)", yes),
        (r"(come|go|be|work|report) in(to)? (the |their |our |your |a )?(designated |local )?office|office \d|\d days (a|per) week", yes),
        (r"(previously|ever|formerly|currently) (worked|employed|been employed)|former (employee|worker)|"
         r"current or former|previous worker", yes if worked_there else no),
        (r"^(expected |anticipated )?graduation month$", (re.search(r"[A-Za-z]+", grad) or [""])[0] if grad else ""),
        (r"(expected |anticipated )?graduation (date|month)|when (do|will) you graduate|degree completion date", grad),
        (r"graduation year|year of graduation|class of", year),
        (r"\bgpa\b|grade point", e.get("gpa", "")),
        (r"^(what (is|are) )?(your )?(current |intended |declared )?(major|majors|field of study|area of study|concentration|discipline)s?$",
         sa.get("majors", "").replace(";", ",")),
        (r"^(school|university|college|institution|school or university)( name)?$", "University of Houston"),
        (r"^degree( type| level)?$|highest (level of )?education|highest degree|degree (are you )?(pursuing|seeking)",
         "Bachelor of Business Administration"),
        (r"currently enrolled|current(ly a)? student|pursuing a (bachelor|degree)", yes),
        (r"class (standing|level|year)|year in school|academic (level|standing)", sa.get("class_year", "")),
        (r"(when|date) (can|could) you start|availability|available to start|which (term|season)", CONFIG.get("season", "")),
        (r"text message|sms|receive texts", no),
    ]
    return [(p, a) for p, a in rules if a]


# ---------------------------------------------------------------- learned answers (answers.yaml)
def _read_bank() -> dict:
    if not BANK_PATH.exists():
        return {}
    try:
        data = yaml.safe_load(BANK_PATH.read_text(encoding="utf-8")) or {}
        return {norm(k): str(v) for k, v in (data.get("answers") or {}).items() if str(v).strip()}
    except Exception:  # noqa: BLE001  (a broken file never stops an application)
        return {}


def _write_bank(bank: dict) -> None:
    head = ("# Your answers to application questions, saved the first time you answered each one.\n"
            "# Edit or delete lines freely. Private: never uploaded to GitHub.\n")
    tmp = BANK_PATH.with_suffix(".tmp")
    tmp.write_text(head + yaml.safe_dump({"answers": dict(sorted(bank.items()))}, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    tmp.replace(BANK_PATH)


def save_answer(question: str, answer: str) -> None:
    q, a = norm(question), str(answer or "").strip()
    if not q or not a or NEVER.search(q) or EEO.search(q) or SOURCE.search(q):
        return   # never stores attestations, salary, criminal, demographic answers, or the job source (per job)
    bank = _read_bank()
    bank[q] = a
    _write_bank(bank)


def queue_answer(question: str, answer: str = "", cont: bool = False) -> None:
    """Control Center side: drop an answer (or a Continue) into the inbox. The filler picks it up within a second."""
    INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        box = json.loads(INBOX_PATH.read_text(encoding="utf-8")) if INBOX_PATH.exists() else {}
    except Exception:  # noqa: BLE001
        box = {}
    box.setdefault("answers", {})
    if question and str(answer).strip():
        box["answers"][question] = str(answer).strip()
    if cont:
        box["continue"] = time.time()
    tmp = INBOX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(box, indent=1), encoding="utf-8")
    tmp.replace(INBOX_PATH)


def take_inbox() -> tuple[dict, bool]:
    """Filler side: read and clear the inbox; saves the answers to the bank. Returns (answers by label, continue?)."""
    if not INBOX_PATH.exists():
        return {}, False
    try:
        box = json.loads(INBOX_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}, False
    INBOX_PATH.unlink(missing_ok=True)
    got = {k: v for k, v in (box.get("answers") or {}).items() if str(v).strip()}
    for q, a in got.items():
        save_answer(q, a)
    return got, bool(box.get("continue"))


# ---------------------------------------------------------------- classify one question
def classify(label: str, kind: str = "text", job: dict | None = None) -> tuple[str, str]:
    """-> (category, answer). category: never | eeo | learned | seed | essay | unknown."""
    q = norm(label)
    if not q:
        return "unknown", ""
    if NEVER.search(q):
        return "never", ""
    if EEO.search(q):
        return "eeo", "decline"
    if SOURCE.search(q):
        return "seed", heard_from(job)
    bank = _read_bank()
    if q in bank:
        return "learned", bank[q]
    if "sponsor" in q and re.search(r"authori[sz]", q):
        return "unknown", ""   # "authorized without sponsorship?" vs "need sponsorship to stay authorized?": ask once
    for pat, ans in seed_rules(job):
        if re.search(pat, q):
            return "seed", ans
    if kind == "textarea" or ESSAY.search(q):
        return "essay", ""
    return "unknown", ""


def pick_option(answer: str, options: list[str]) -> str | None:
    """Map an answer to one of the visible options (dropdown / radio). None = no safe match."""
    opts = [o for o in options if o and norm(o) not in ("select one", "")]
    if not opts or not answer:
        return None
    if answer == "decline":
        hits = [o for o in opts if DECLINE.search(o)]
        return hits[0] if hits else None
    a = norm(answer)
    for o in opts:
        if norm(o) == a:
            return o
    if a in ("yes", "no"):
        hits = [o for o in opts if re.match(rf"^{a}\b", norm(o))]
        return hits[0] if len(hits) == 1 else None
    hits = [o for o in opts if norm(o).startswith(a) or (a.startswith(norm(o)) and len(norm(o)) >= 0.6 * len(a))]
    if len(hits) == 1:
        return hits[0]
    words = [w for w in a.split() if len(w) > 3]
    scored = sorted(((sum(w in norm(o) for w in words), o) for o in opts), reverse=True)
    if scored and scored[0][0] and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


# ---------------------------------------------------------------- pending questions (shown in the Control Center)
def write_pending(job: dict, page: str, items: list[dict], waiting: str = "") -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"job_id": job.get("id"), "company": job.get("company"), "title": job.get("title"), "page": page,
            "waiting": waiting, "items": items, "at": time.time()}
    tmp = PENDING_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDING_PATH)


def clear_pending() -> None:
    PENDING_PATH.unlink(missing_ok=True)


def read_pending() -> dict:
    try:
        data = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        return data if time.time() - data.get("at", 0) < 6 * 3600 else {}
    except Exception:  # noqa: BLE001
        return {}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show saved answers")
    ap.add_argument("--set", nargs=2, metavar=("QUESTION", "ANSWER"))
    ap.add_argument("--continue", dest="cont", action="store_true", help="tell a paused Workday fill to go on")
    ap.add_argument("--test", help="classify a question label")
    a = ap.parse_args()
    if a.set:
        queue_answer(a.set[0], a.set[1])
        print("Queued. The open application picks it up within a second (or the next one does).")
    if a.cont:
        queue_answer("", cont=True)
    if a.test:
        print(classify(a.test))
    if a.list:
        for k, v in _read_bank().items():
            print(f"{k}: {v}")
