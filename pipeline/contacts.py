"""Phase 4: who to email, and the queue Claude drafts from. NOTHING here sends an email.

  python pipeline/contacts.py --find "Memorial Hermann" [--source "UH alum"] [--kind cold]
        Hunter.io lookup of recruiting / finance contacts, added to the queue (needs HUNTER_API_KEY).
  python pipeline/contacts.py --find "Company" --first Ana --last Ruiz [--source "UH alum"]
        you already have a name (LinkedIn): Hunter's email finder, then the queue.
  python pipeline/contacts.py --add "Company" --name "Ana Ruiz" [--email a@b.com] [--title T] [--source "Career fair"]
        add a person by hand (no lookup).
  python pipeline/contacts.py --auto N      queue up to N top-ranked prospects you haven't contacted (Hunter lookups)
  python pipeline/contacts.py --followups   put due follow-ups (Sent 7+ days ago, no reply) in the queue
  python pipeline/contacts.py --remove ID   drop an item from the queue (state: skipped)
  python pipeline/contacts.py --list        print the queue

The queue is output/_outreach_queue.json. In the Claude project, /outreach reads it, drafts each email from your
profile, shows them all, and sends only the ones you approve. After sending, Claude writes
output/_outreach_events/<one file per email>.json; `outreach.py --fold` (daily scan, or Sync sent emails in the Control Center) records
them in the Sheet's Outreach tab.

Sender per contact (Nov 19 decision): UH alumni, Bauer / UH-connected people and career-fair contacts use the
school Outlook; everyone else uses Gmail.
Limits (config.yaml): outreach_weekly_cap new people per 7 days, outreach_per_company, outreach_company_gap_days,
hunter_monthly_cap lookups per month, outreach_skip companies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from common import CONFIG, PROFILE, ROOT, db, load_env, log, meta_get, meta_set

OUT = ROOT / CONFIG["output_dir"]
QUEUE_PATH = OUT / "_outreach_queue.json"
EVENTS_DIR = OUT / "_outreach_events"      # one small JSON file per event (Claude writes them; outreach.py --fold reads them)
HTTP = requests          # tests replace this with a fake (no network)
C = PROFILE["candidate"]

# a source containing one of these words means a UH-linked contact -> school Outlook
SCHOOL_WORDS = ("alum", "bauer", "uh ", "uh-", "cougar", "career fair", "beta alpha psi", "finance association", "professor")
SOURCES = ["UH alum", "Bauer / UH", "Career fair", "Other"]
RECRUITER_WORDS = ("recruit", "talent", "campus", "university", "early career", "early talent", "human resources", "hr ")
FINANCE_WORDS = ("finance", "financial", "accounting", "controller", "treasury", "fp&a", "revenue cycle", "audit", "credit")
KINDS = ("cold", "network", "follow_up", "after_apply")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


# ---------------------------------------------------------------- small helpers
def _limit(key: str, default: int) -> int:
    try:
        return int(CONFIG.get(key, default))
    except (TypeError, ValueError):
        return default


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def tag_sender(source: str = "", email: str = "") -> str:
    """'school' for UH-linked contacts (alumni, Bauer, career fair), else 'gmail'."""
    s = f" {source.lower().strip()} "
    if any(w in s for w in SCHOOL_WORDS) or re.search(r"@([\w.-]+\.)?uh\.edu$", email.lower()):
        return "school"
    return "gmail"


def sender_address(tag: str) -> str:
    return (C.get("email_campus") or C["email"]) if tag == "school" else C["email"]


def item_id(company: str, name: str, email: str, kind: str) -> str:
    base = f"{slug(company)}|{slug(email) or slug(name)}|{'fu' if kind == 'follow_up' else 'first'}"
    return hashlib.sha256(base.encode()).hexdigest()[:12]


def load_queue() -> dict:
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        if isinstance(data.get("items"), list):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"updated": "", "items": []}


def save_queue(data: dict) -> None:
    data["updated"] = datetime.now().isoformat(timespec="minutes")
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, QUEUE_PATH)


def history(company: str) -> list[str]:
    """Earlier touches at this company (from the Outreach tab cache or the local log), newest last."""
    import prospects
    rows: list[dict] = []
    try:
        rows = json.loads((OUT / "_networking" / "contacts.json").read_text(encoding="utf-8")).get("contacts", [])
    except (OSError, ValueError):
        try:
            import csv
            with open(OUT / "outreach_log.csv", newline="", encoding="utf-8-sig") as fh:
                rows = [{"drafted": r[0], "company": r[1], "contact": r[2], "type": r[3], "status": r[4]}
                        for r in list(csv.reader(fh))[1:] if len(r) > 4]
        except OSError:
            rows = []
    return [f"{r.get('drafted', '')}: {r.get('type', '')} to {r.get('contact', '')} ({r.get('status', '')})"
            for r in rows if prospects.same_company(r.get("company", ""), company)][-5:]


def applied_role(company: str) -> str:
    """Title of a job at this company you already applied for (or have a packet for), '' if none."""
    import prospects
    try:
        conn = db()
        for r in conn.execute("SELECT company, title, status FROM jobs WHERE status IN ('APPLIED','FILLED','PACKET_READY') "
                              "ORDER BY CASE status WHEN 'APPLIED' THEN 0 WHEN 'FILLED' THEN 1 ELSE 2 END"):
            if prospects.same_company(r["company"], company):
                return f"{r['title']} ({r['status'].lower().replace('_', ' ')})"
    except Exception:  # noqa: BLE001
        pass
    return ""


# ---------------------------------------------------------------- limits
def week_count(items: list[dict]) -> int:
    since = (date.today() - timedelta(days=7)).isoformat()
    return sum(1 for i in items if i["kind"] != "follow_up" and i["state"] != "skipped" and i.get("added", "") >= since)


def blocked_reason(items: list[dict], company: str, name: str, email: str, kind: str, force: bool = False) -> str:
    """Why this person can't be queued right now ('' = fine). Follow-ups skip the weekly cap and company gap."""
    import prospects
    if any(prospects.same_company(company, s) for s in (CONFIG.get("outreach_skip") or [])):
        return f"{company} is on your outreach_skip list"
    iid = item_id(company, name, email, kind)
    if any(i["id"] == iid and i["state"] != "skipped" for i in items):
        return "already in the queue or already handled"
    if email and any(i.get("email", "").lower() == email.lower() and i["kind"] == kind and i["state"] != "skipped" for i in items):
        return "that email address is already in the queue"
    if kind == "follow_up" or force:
        return ""
    mine = [i for i in items if prospects.same_company(i["company"], company) and i["state"] != "skipped" and i["kind"] != "follow_up"]
    if len(mine) >= _limit("outreach_per_company", 2):
        return f"already {len(mine)} people at {company} (outreach_per_company)"
    gap = _limit("outreach_company_gap_days", 21)
    last = max([i.get("sent", "") for i in mine if i.get("sent")] or [""])
    if last and last >= (date.today() - timedelta(days=gap)).isoformat():
        return f"{company} was emailed on {last} (gap is {gap} days)"
    if week_count(items) >= _limit("outreach_weekly_cap", 8):
        return f"weekly cap reached ({_limit('outreach_weekly_cap', 8)} new people per 7 days)"
    return ""


# ---------------------------------------------------------------- queue
def add_item(company: str, name: str = "", email: str = "", title: str = "", source: str = "Other", kind: str = "cold",
             email_status: str = "", note: str = "", folder: str = "", force: bool = False) -> tuple[dict | None, str]:
    """Add one person. Returns (item, '') or (None, reason)."""
    company, name, email = company.strip()[:80], name.strip()[:80], email.strip()[:120]
    if kind not in KINDS:
        kind = "cold"
    if not company:
        return None, "no company"
    if email and not EMAIL_RE.match(email):
        return None, f"'{email}' doesn't look like an email address"
    data = load_queue()
    why = blocked_reason(data["items"], company, name, email, kind, force)
    if why:
        return None, why
    import prospects
    tag = tag_sender(source, email)
    first = name.split()[0] if name else ""
    item = {"id": item_id(company, name, email, kind), "added": date.today().isoformat(), "state": "queued", "kind": kind,
            "company": company, "name": name, "first_name": first, "title": title[:100], "email": email,
            "email_status": email_status or ("unknown" if email else "none"), "source": source, "sender": tag,
            "sender_address": sender_address(tag), "fit_line": prospects.fit_line(company), "applied_role": applied_role(company),
            "history": history(company), "folder": folder, "note": note[:300], "sent": ""}
    data["items"].append(item)
    save_queue(data)
    log(f"Queued: {company} | {name or '(name needed)'} | {email or 'no email yet'} | sends from {tag}")
    return item, ""


def set_state(iid: str, state: str, **fields) -> bool:
    data = load_queue()
    for i in data["items"]:
        if i["id"] == iid:
            i["state"] = state
            i.update(fields)
            save_queue(data)
            return True
    return False


def refresh_followups() -> int:
    """Rows in the Outreach tab marked Sent whose next touch is due -> follow_up items (one follow-up per person)."""
    import outreach
    n = 0
    today = date.today().isoformat()
    try:
        rows = json.loads(outreach.CONTACTS_CACHE.read_text(encoding="utf-8")).get("contacts", [])
    except (OSError, ValueError):
        rows = []
    for r in rows:
        if r.get("status", "").strip().lower() != "sent" or not r.get("follow_up") or r["follow_up"] > today:
            continue
        key = r.get("folder", "")
        subj, sender, to = "", "", ""
        try:
            sj = json.loads((Path(key) / "send.json").read_text(encoding="utf-8"))
            subj, sender, to = sj.get("subject", ""), sj.get("from", ""), sj.get("to", "")
        except (OSError, ValueError):
            pass
        if key.startswith("queue:"):   # sent through Claude: take the details from the queue item
            src = next((i for i in load_queue()["items"] if i["id"] == key[6:]), None)
            if src:
                subj, sender, to = src.get("subject", ""), src.get("sender_address", ""), src.get("email", "")
        item, _why = add_item(r["company"], r.get("contact", ""), to, "", "UH contact" if sender == C.get("email_campus") else "Other",
                              "follow_up", note=f"Follow up on '{subj}' (sent {r.get('drafted', '')}). One follow-up only.", folder=key)
        if item:
            set_state(item["id"], "queued", subject=subj, followup_of=key)
            n += 1
    if n:
        log(f"Follow-ups: {n} added to the outreach queue.")
    return n


# ---------------------------------------------------------------- Hunter.io (free plan: 25 searches / 50 verifications a month)
def _hunter_key() -> str:
    return os.environ.get("HUNTER_API_KEY", "").strip()


def _month_key() -> str:
    return f"hunter_used_{date.today():%Y-%m}"


def hunter_budget(conn) -> int:
    return _limit("hunter_monthly_cap", 20) - int(meta_get(conn, _month_key(), 0) or 0)


def _hunter_call(conn, path: str, params: dict) -> tuple[dict | None, str]:
    """One Hunter call; counted against hunter_monthly_cap. Returns (data, '') or (None, reason)."""
    if not _hunter_key():
        return None, "no HUNTER_API_KEY in .env (free key at hunter.io/api-keys)"
    if hunter_budget(conn) <= 0:
        return None, f"monthly Hunter cap reached ({_limit('hunter_monthly_cap', 20)}; raise hunter_monthly_cap in config.yaml, the free plan has 25)"
    try:
        r = HTTP.get(f"https://api.hunter.io/v2/{path}", params=params, headers={"X-API-KEY": _hunter_key()}, timeout=30)
        meta_set(conn, _month_key(), int(meta_get(conn, _month_key(), 0) or 0) + 1)
        body = r.json()
    except Exception as exc:  # noqa: BLE001
        return None, f"Hunter not reachable ({exc.__class__.__name__})"
    if body.get("errors"):
        e = body["errors"][0]
        return None, f"Hunter says: {e.get('details') or e.get('id') or 'error'}"
    return body.get("data") or {}, ""


def rank_hunter(emails: list[dict]) -> list[dict]:
    """Personal addresses only, recruiters first, then finance people; verified before unverified."""
    out = []
    for e in emails:
        if e.get("type") == "generic" or not e.get("value"):
            continue
        pos = (e.get("position") or "").lower() + " " + (e.get("department") or "").lower()
        verdict = (e.get("verification") or {}).get("status") or ""
        status = "verified" if verdict == "valid" else ("risky" if verdict in ("accept_all", "webmail") else
                                                         ("likely" if (e.get("confidence") or 0) >= 90 else "unverified"))
        rec = any(w in pos for w in RECRUITER_WORDS)
        fin = any(w in pos for w in FINANCE_WORDS)
        if not (rec or fin):
            continue
        out.append({"name": f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(), "email": e["value"],
                    "title": (e.get("position") or "")[:100], "email_status": status, "rank": (0 if rec else 1,
                    {"verified": 0, "likely": 1, "risky": 2, "unverified": 3}[status], -(e.get("confidence") or 0))})
    return sorted(out, key=lambda c: c["rank"])


def domain_for(conn, company: str) -> str:
    import prospects
    for k, v in (CONFIG.get("company_domains") or {}).items():
        if prospects.same_company(k, company):
            return v
    return meta_get(conn, f"domain_{slug(company)}", "") or ""


def find_people(conn, company: str) -> tuple[list[dict], str]:
    """Hunter domain search for this company (domain from config/learned, else by company name). Cached 90 days."""
    cache_key = f"hunter2_{slug(company)}"
    cached = meta_get(conn, cache_key)
    if cached:
        try:
            stamp, people = json.loads(cached)
            if stamp >= (date.today() - timedelta(days=90)).isoformat():
                return people, ""
        except (ValueError, TypeError):
            pass
    domain = domain_for(conn, company)
    data, why = _hunter_call(conn, "domain-search", {"domain": domain} if domain else {"company": company, "limit": 10})
    if data is None:
        return [], why
    if data.get("domain") and not domain:
        meta_set(conn, f"domain_{slug(company)}", data["domain"])
    people = rank_hunter(data.get("emails") or [])
    for p in people:
        p.pop("rank", None)
    meta_set(conn, cache_key, json.dumps([date.today().isoformat(), people]))
    return people, ("" if people else f"Hunter has no recruiting or finance contacts for {company}")


def find_email(conn, company: str, first: str, last: str) -> tuple[dict | None, str]:
    """Hunter email finder for a person you found on LinkedIn."""
    domain = domain_for(conn, company)
    params = {"first_name": first, "last_name": last}
    params.update({"domain": domain} if domain else {"company": company})
    data, why = _hunter_call(conn, "email-finder", params)
    if data is None:
        return None, why
    if not data.get("email"):
        return None, f"Hunter couldn't find an email for {first} {last} at {company}"
    verdict = (data.get("verification") or {}).get("status") or ""
    status = "verified" if verdict == "valid" else ("likely" if (data.get("score") or 0) >= 90 else "unverified")
    return {"name": f"{first} {last}".strip(), "email": data["email"], "title": (data.get("position") or "")[:100],
            "email_status": status}, ""


def apollo_people(company: str) -> tuple[list[dict], str]:
    """OPTIONAL and untested live: Apollo people search (names and titles only, no emails). Apollo's docs say People
    Search is not available on free plans, so expect a refusal there; this never blocks anything."""
    key = os.environ.get("APOLLO_API_KEY", "").strip()
    if not key:
        return [], "no APOLLO_API_KEY (optional)"
    try:
        r = HTTP.post("https://api.apollo.io/api/v1/mixed_people/api_search",
                      headers={"x-api-key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"},
                      json={"q_organization_name": company, "person_titles": ["university recruiter", "campus recruiter", "talent acquisition"],
                            "include_similar_titles": True, "per_page": 5}, timeout=30)
        if r.status_code in (401, 402, 403, 422):
            return [], f"Apollo refused (HTTP {r.status_code}): people search needs a paid plan"
        people = [{"name": (p.get("first_name") or "") + " " + (p.get("last_name") or p.get("last_name_obfuscated") or ""),
                   "email": "", "title": p.get("title") or "", "email_status": "none"} for p in (r.json().get("people") or [])]
    except Exception as exc:  # noqa: BLE001
        return [], f"Apollo not reachable ({exc.__class__.__name__})"
    return [p for p in people if p["name"].strip()], ""


# ---------------------------------------------------------------- commands
def find(company: str, first: str = "", last: str = "", source: str = "Other", kind: str = "cold", limit: int = 1) -> list[dict]:
    """Look up people at one company and queue up to `limit` of them. Prints what happened; nothing is sent."""
    load_env()
    conn = db()
    added: list[dict] = []
    if first and last:
        person, why = find_email(conn, company, first, last)
        if person is None:
            print(f"{company}: {why}. Queue him/her without an email? Use --add --name \"{first} {last}\" (you add the address).")
            return []
        people = [person]
    else:
        people, why = find_people(conn, company)
        if not people:
            ap, why2 = apollo_people(company)
            if ap:
                print(f"{company}: Apollo suggests {', '.join(p['name'] + ' (' + p['title'] + ')' for p in ap[:3])}. No emails: find them on LinkedIn, "
                      "then run --find with --first/--last.")
            print(f"{company}: no one queued. {why}" + (f" Apollo: {why2}" if why2 else ""))
            return []
    for p in people:
        if len(added) >= limit:
            break
        item, why = add_item(company, p["name"], p["email"], p["title"], source, kind, p["email_status"], note="Found with Hunter.io")
        if item:
            added.append(item)
        else:
            print(f"  skipped {p['name'] or p['email']}: {why}")
    print(f"{company}: {len(added)} queued. Hunter lookups left this month: {max(0, hunter_budget(conn))}. In Claude, type /outreach.")
    return added


def auto(n: int) -> int:
    """Queue the top-ranked prospects (not contacted in 30 days, nothing queued yet), one person each."""
    import prospects
    load_env()
    done = 0
    for p in prospects.ranked():
        if done >= n:
            break
        if p["last_contact"] or any(prospects.same_company(i["company"], p["company"]) and i["state"] != "skipped" for i in load_queue()["items"]):
            continue
        if hunter_budget(db()) <= 0 or week_count(load_queue()["items"]) >= _limit("outreach_weekly_cap", 8):
            print("Stopping: Hunter budget or weekly cap reached.")
            break
        done += 1 if find(p["company"], source="Other", kind="cold") else 0
    return done


def render_list() -> str:
    items = load_queue()["items"]
    if not items:
        return "The outreach queue is empty."
    return "\n".join(f"{i['state']:8} {i['kind']:11} {i['company']} | {i.get('name') or '(name needed)'} | "
                     f"{i.get('email') or 'no email'} [{i.get('email_status')}] | from {i['sender']}" for i in items)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--find")
    ap.add_argument("--add")
    ap.add_argument("--auto", type=int)
    ap.add_argument("--followups", action="store_true")
    ap.add_argument("--remove")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--first", default="")
    ap.add_argument("--last", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--source", default="Other")
    ap.add_argument("--kind", default="cold")
    ap.add_argument("--force", action="store_true", help="ignore the weekly cap and company gap for this one person")
    a = ap.parse_args()
    load_env()
    if a.list:
        print(render_list())
        sys.exit(0)
    from common import require_lock
    require_lock("outreach queue")
    if a.find:
        find(a.find.strip()[:80], a.first.strip()[:40], a.last.strip()[:40], a.source.strip()[:40], a.kind)
    elif a.add:
        item, why = add_item(a.add, a.name or f"{a.first} {a.last}", a.email, a.title, a.source, a.kind, force=a.force)
        print("Queued." if item else f"Not queued: {why}")
    elif a.auto:
        print(f"Queued {auto(max(1, min(a.auto, 8)))} companies.")
    elif a.followups:
        import outreach
        outreach.export_contacts()
        refresh_followups()
    elif a.remove:
        print("Removed from the queue." if set_state(a.remove, "skipped") else "No such queue item.")
    else:
        ap.print_help()
