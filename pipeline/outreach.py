"""Stage 4: outreach DRAFTS. Nothing is ever sent by this script; you review, personalize and send.

  python pipeline/outreach.py              drafts for every application marked Applied (run by APPLY.bat)
  python pipeline/outreach.py --network    NETWORK.bat: drafts for a person at a company you have NOT applied to
  python pipeline/outreach.py --due        list follow-ups that are due

Each contact gets a small folder with:
  1_LINKEDIN_NOTE.txt   connection note, under LinkedIn's 300-character limit
  2_EMAIL_DRAFT.eml     double-click: opens as an editable draft in Outlook (classic or new) + the same text as .txt
  3_FOLLOW_UP.txt       send about a week later if there is no reply
  4_THANK_YOU.txt       send the same day after a call
Every draft is also logged in the "Outreach" tab of the Google Sheet (and outreach_log.csv) so you can
track who you contacted. Set Status to "Sent" there and the daily summary reminds you when a follow-up is due.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote_plus

import requests

from common import CONFIG, PROFILE, ROOT, ask, db, load_env, log, mark_dirty, meta_get, meta_set, spreadsheet, worksheet

RECRUITER_WORDS = ("recruit", "talent", "campus", "university", "early career", "human resources", "hr ")
OUTREACH_TAB = "Outreach"
OUTREACH_HEADER = ["Drafted", "Company", "Contact", "Type", "Status", "Follow up on", "Find UH alumni", "Folder", "My Notes"]
C, E = PROFILE["candidate"], PROFILE["education"]
FIRST = C["name"].split()[0]
INTRO = f"a junior at UH Bauer double majoring in Finance and Accounting ({E['gpa']} GPA)"
SIGN = f"Thanks,\n{C['name']}\n{C['phone']} | {C.get('email_campus') or C['email']}"


def alumni_link(company: str) -> str:
    return f"https://www.linkedin.com/school/university-of-houston/people/?keywords={quote_plus(company)}"


def hunter_contacts(conn, domain: str) -> list[dict]:
    key = os.environ.get("HUNTER_API_KEY")
    if not key or not domain:
        return []
    cached = meta_get(conn, f"hunter_{domain}")
    if cached:
        return json.loads(cached)
    try:
        data = requests.get("https://api.hunter.io/v2/domain-search",
                            params={"domain": domain, "limit": 10}, headers={"X-API-KEY": key}, timeout=30).json()
        emails = data.get("data", {}).get("emails", [])
        people = [{"name": f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(), "email": e["value"],
                   "position": e.get("position") or "", "verified": (e.get("verification") or {}).get("status") == "valid"}
                  for e in emails if any(w in (e.get("position") or "").lower() for w in RECRUITER_WORDS)]
    except Exception:  # noqa: BLE001
        return []
    meta_set(conn, f"hunter_{domain}", json.dumps(people))
    return people


# ---------------------------------------------------------------- message text
def messages(company: str, role: str = "", first_name: str = "") -> dict:
    """All four messages. role='' means pure networking (no application yet).
    Only facts from master_profile.json are used; the [bracket] line is yours to write."""
    hi = f"Hi {first_name}," if first_name else "Hi [first name],"
    if role:
        subject = f"UH Bauer junior: {role} at {company}"
        ask_line = (f"I'm {C['name']}, {INTRO}. I just applied for the {role} role and would really value "
                    f"10 minutes to hear how you got started at {company} and what the team looks for in interns.")
        note = (f"{hi} I'm a UH Bauer junior studying finance and accounting. I just applied for the {role} role at "
                f"{company} and would value hearing about your experience there. Open to a short chat? Thanks, {FIRST}")
        if len(note) > 300:
            note = (f"{hi} I'm a UH Bauer junior in finance and accounting. I just applied for an internship at "
                    f"{company} and would value hearing about your experience there. Open to a short chat? Thanks, {FIRST}")
    else:
        subject = f"UH Bauer junior interested in {company}"
        ask_line = (f"I'm {C['name']}, {INTRO}. I'm interested in {company} and would really value 10 minutes "
                    "to hear how you got started there and what you'd suggest for a student hoping to intern on a team like yours.")
        note = (f"{hi} I'm a UH Bauer junior studying finance and accounting. I'm interested in {company} and would "
                f"value hearing about your path there. Would you be open to a short chat? Thanks, {FIRST}")
    note = note[:300]
    email = (f"{hi}\n\n[One specific line about them, e.g. saw you went from UH Bauer to {company}'s ___ team.]\n\n"
             f"{ask_line}\n\nWould you be open to a quick call sometime in the next couple of weeks?\n\n{SIGN}\n")
    follow = (f"{hi}\n\nI wanted to follow up on my note from last week. I know this is a busy time of year, so no "
              f"pressure at all. If a quick call is easier in a few weeks, I'm happy to work around your schedule.\n\n{SIGN}\n")
    thanks = (f"{hi}\n\nThank you for taking the time to talk with me today. [One thing they said that stuck with you, "
              f"and one thing you will do because of it.]\n\nI'll keep you posted on how recruiting goes.\n\n{SIGN}\n")
    return {"subject": subject, "note": note, "email": email, "follow_up": follow, "thank_you": thanks}


def eml_draft(path: Path, to: str, subject: str, body: str, attachment: str | None) -> Path:
    msg = EmailMessage()
    msg["X-Unsent"] = "1"  # opens as an editable draft in Outlook (classic and new)
    msg["From"] = C.get("email_campus") or C["email"]
    if to:
        msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment and Path(attachment).exists():
        data = Path(attachment).read_bytes()
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=Path(attachment).name)
    path.write_bytes(bytes(msg))
    return path


def write_kit(folder: Path, company: str, role: str, first_name: str, to: str, attachment: str | None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    m = messages(company, role, first_name)
    (folder / "1_LINKEDIN_NOTE.txt").write_text(
        f"{m['note']}\n\n({len(m['note'])} of 300 characters. Find a UH alum: {alumni_link(company)})", encoding="utf-8")
    eml = eml_draft(folder / "2_EMAIL_DRAFT.eml", to, m["subject"], m["email"], attachment)
    (folder / "2_EMAIL_DRAFT.txt").write_text(f"Subject: {m['subject']}\n\n{m['email']}", encoding="utf-8")
    (folder / "3_FOLLOW_UP.txt").write_text(f"Subject: Re: {m['subject']}\n\n{m['follow_up']}", encoding="utf-8")
    (folder / "4_THANK_YOU.txt").write_text(f"Subject: Thank you\n\n{m['thank_you']}", encoding="utf-8")
    write_send(folder, to, m["subject"], m["email"])
    return eml


def write_send(folder: Path, to: str, subject: str, body: str) -> None:
    """What the Open in Outlook / Open in Gmail buttons pre-fill. Nothing is sent: you click Send yourself."""
    (folder / "send.json").write_text(json.dumps({"to": to, "subject": subject, "body": body,
                                                  "from": C.get("email_campus") or C["email"]}, indent=1), encoding="utf-8")


def cold_messages(company: str, first_name: str = "", fit: str = "") -> dict:
    """Cold email about internships. Facts only from master_profile.json; fit = one sentence from prospects.yaml.
    Asks about the team's internship plans and for 10 minutes, never directly for a job or referral."""
    hi = f"Hi {first_name}," if first_name else "Hi [first name],"
    subject = f"UH Bauer junior: Summer 2027 internships at {company}"
    fit = fit or ("At CVS, where I'm a Lead Pharmacy Technician, I resolve denied insurance claims and billing discrepancies "
                  "directly with insurance providers.")
    email = (f"{hi}\n\nI'm {C['name']}, {INTRO}. {fit}\n\n"
             f"Is your team planning finance or accounting internships for summer 2027? If so, I'd be grateful for "
             f"10 minutes to hear how students usually get started at {company} and what you look for.\n\n"
             f"I'm happy to send my resume.\n\n{SIGN}\n")
    follow = (f"{hi}\n\nI wanted to follow up on my note about summer 2027 internships at {company}. I know this is a busy "
              f"time of year, so no pressure. If someone else on your team handles interns, I'd be glad to reach out to them "
              f"instead.\n\n{SIGN}\n")
    thanks = (f"{hi}\n\nThank you for taking the time to talk with me today. [One thing they said that stuck with you, "
              f"and one thing you will do because of it.]\n\nI'll keep you posted on how recruiting goes.\n\n{SIGN}\n")
    note = (f"{hi} I'm a UH Bauer junior in finance and accounting, interested in summer 2027 internships at {company}. "
            f"Would you be open to a short chat about how students get started there? Thanks, {FIRST}")[:300]
    return {"subject": subject, "note": note, "email": email, "follow_up": follow, "thank_you": thanks}


def write_cold_kit(folder: Path, company: str, first_name: str, to: str) -> Path:
    import prospects
    folder.mkdir(parents=True, exist_ok=True)
    m = cold_messages(company, first_name, prospects.fit_line(company))
    (folder / "1_LINKEDIN_NOTE.txt").write_text(
        f"{m['note']}\n\n({len(m['note'])} of 300 characters. Find a UH alum: {alumni_link(company)})", encoding="utf-8")
    eml = eml_draft(folder / "2_EMAIL_DRAFT.eml", to, m["subject"], m["email"], None)
    (folder / "2_EMAIL_DRAFT.txt").write_text(f"Subject: {m['subject']}\n\n{m['email']}", encoding="utf-8")
    (folder / "3_FOLLOW_UP.txt").write_text(f"Subject: Re: {m['subject']}\n\n{m['follow_up']}", encoding="utf-8")
    (folder / "4_THANK_YOU.txt").write_text(f"Subject: Thank you\n\n{m['thank_you']}", encoding="utf-8")
    write_send(folder, to, m["subject"], m["email"])
    return eml


# ---------------------------------------------------------------- tracking
def log_outreach(company: str, contact: str, kind: str, folder: Path) -> None:
    row = [date.today().isoformat(), company, contact or "(pick a UH alum)", kind, "Drafted",
           (date.today() + timedelta(days=7)).isoformat(), alumni_link(company), str(folder), ""]
    path = ROOT / CONFIG["output_dir"] / "outreach_log.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        if fresh:
            writer.writerow(OUTREACH_HEADER)
        writer.writerow(row)
    try:
        sh = spreadsheet()
        if sh is not None:
            worksheet(sh, OUTREACH_TAB, OUTREACH_HEADER).append_row(row, value_input_option="RAW", table_range="A1")
    except Exception as exc:  # noqa: BLE001
        log(f"  Outreach tab not updated ({exc.__class__.__name__}); it is still in outreach_log.csv.")


def due_followups() -> list[str]:
    """Rows in the Outreach tab you marked Sent whose follow-up date has arrived."""
    try:
        sh = spreadsheet()
        if sh is None:
            return []
        rows = worksheet(sh, OUTREACH_TAB, OUTREACH_HEADER).get_all_values()[1:]
    except Exception:  # noqa: BLE001
        return []
    today, out = date.today().isoformat(), []
    for r in rows:
        r = r + [""] * (len(OUTREACH_HEADER) - len(r))
        if r[4].strip().lower() == "sent" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[5].strip()) and r[5].strip() <= today:
            out.append(f"{r[1]}: {r[2]} (follow-up was due {r[5].strip()}; text is in 3_FOLLOW_UP.txt)")
    return out


# ---------------------------------------------------------------- Control Center helpers
CONTACTS_CACHE = ROOT / CONFIG["output_dir"] / "_networking" / "contacts.json"


def _local_rows() -> list[list[str]]:
    path = ROOT / CONFIG["output_dir"] / "outreach_log.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [r for r in csv.reader(fh)][1:]


def export_contacts() -> int:
    """Copy the Outreach tab (or outreach_log.csv if the Sheet isn't reachable) to contacts.json for the page."""
    rows, source, url = None, "Sheet", ""
    try:
        sh = spreadsheet()
        if sh is not None:
            rows = worksheet(sh, OUTREACH_TAB, OUTREACH_HEADER).get_all_values()[1:]
            url = getattr(sh, "url", "") or ""
    except Exception as exc:  # noqa: BLE001
        log(f"Outreach tab not reachable ({exc.__class__.__name__}); using outreach_log.csv.")
    if rows is None:
        rows, source = _local_rows(), "outreach_log.csv"
    keys = ["drafted", "company", "contact", "type", "status", "follow_up", "alumni", "folder", "notes"]
    data = [dict(zip(keys, (r + [""] * len(keys))[:len(keys)])) for r in rows if any(c.strip() for c in r)]
    CONTACTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CONTACTS_CACHE.write_text(json.dumps({"updated": datetime.now().isoformat(timespec="minutes"), "source": source,
                                          "sheet_url": url, "contacts": data}, indent=1), encoding="utf-8")
    log(f"Contacts refreshed from the {source}: {len(data)} rows.")
    return len(data)


STATUS_CHOICES = ("Sent", "Followed up", "Replied", "Closed")


def mark_sent(folder: str, status: str = "Sent") -> bool:
    """Sent / Followed up: next touch in 7 days. Replied / Closed: no more follow-up reminders.
    Matched by the drafts folder (unique)."""
    status = status if status in STATUS_CHOICES else "Sent"
    follow = (date.today() + timedelta(days=7)).isoformat() if status in ("Sent", "Followed up") else ""
    done = False
    try:
        sh = spreadsheet()
        if sh is not None:
            ws = worksheet(sh, OUTREACH_TAB, OUTREACH_HEADER)
            for n, r in enumerate(ws.get_all_values()[1:], start=2):
                if len(r) > 7 and r[7].strip() == folder.strip():
                    ws.batch_update([{"range": f"E{n}:F{n}", "values": [[status, follow]]}], value_input_option="RAW")
                    done = True
                    break
    except Exception as exc:  # noqa: BLE001
        log(f"Outreach tab not updated ({exc.__class__.__name__}). Set Status to Sent in the Sheet yourself.")
    log((f"Marked {status}" + (f", follow up on {follow}." if follow else ".")) if done else
        "That contact was not found in the Outreach tab; nothing changed.")
    return done


# ---------------------------------------------------------------- modes
def run(limit: int = 8) -> int:
    """Drafts for applications you have actually submitted."""
    load_env()
    conn = db()
    jobs = [dict(r) for r in conn.execute(
        "SELECT * FROM jobs WHERE status='APPLIED' AND (outreach IS NULL OR outreach='') LIMIT ?", (limit,))]
    made = 0
    for job in jobs:
        domain = (CONFIG.get("company_domains") or {}).get(job["company"], "")
        target = next((p for p in hunter_contacts(conn, domain) if p["verified"]), None)
        to = target["email"] if target else ""
        first = target["name"].split()[0] if target and target["name"] else ""
        resume = job["resume"] or ""
        pdf = str(Path(resume).with_suffix(".pdf")) if resume else ""
        attachment = pdf if pdf and Path(pdf).exists() else (resume or None)
        packet = Path(resume).parent if resume and Path(resume).parent.exists() else \
            ROOT / CONFIG["output_dir"] / re.sub(r"[^A-Za-z0-9]+", "", job["company"])[:30]
        folder = packet / "outreach"
        eml = write_kit(folder, job["company"], job["title"], first, to, attachment)
        log_outreach(job["company"], target["name"] if target else "", "After applying", folder)
        mark_dirty(conn, job["id"], outreach=f"Drafts: {folder}. Find a UH alum: {alumni_link(job['company'])}")
        made += 1
        log(f"  Outreach drafts for {job['company']} -> {eml.parent}")
    log(f"Outreach: {made} draft kits created (none sent).")
    return made


def cold_one(company: str, first: str = "", last: str = "", to: str = "") -> Path:
    """One cold internship email kit with no prompts (Control Center Prospects)."""
    who = re.sub(r"[^A-Za-z0-9]+", "", f"{first}{last}") or f"contact{datetime.now():%m%d%H%M}"
    folder = ROOT / CONFIG["output_dir"] / "_networking" / (re.sub(r"[^A-Za-z0-9]+", "", company)[:30] or "Company") / f"cold_{who}"
    write_cold_kit(folder, company, first, to)
    log_outreach(company, f"{first} {last}".strip(), "Cold email", folder)
    print(f"Cold email drafted for {company}: {folder}")
    print("Open it from the Network tab (Open in Outlook / Gmail), read it, and click Send yourself. Nothing was sent.")
    try:
        export_contacts()
    except Exception:  # noqa: BLE001
        pass
    return folder


def network_one(company: str, first: str = "", last: str = "", to: str = "") -> Path:
    """One networking kit with no prompts (used by the Control Center form)."""
    who = re.sub(r"[^A-Za-z0-9]+", "", f"{first}{last}") or f"contact{datetime.now():%m%d%H%M}"
    folder = ROOT / CONFIG["output_dir"] / "_networking" / (re.sub(r"[^A-Za-z0-9]+", "", company)[:30] or "Company") / who
    write_kit(folder, company, "", first, to, None)
    log_outreach(company, f"{first} {last}".strip(), "Networking", folder)
    print(f"UH alumni at {company}: {alumni_link(company)}")
    print(f"Drafts saved: {folder}\nWrite the [bracket] line yourself before sending. Nothing was sent.")
    try:
        export_contacts()
    except Exception:  # noqa: BLE001
        pass
    return folder


def network() -> None:
    """Coffee-chat outreach to someone at a target company, no application needed."""
    print("Networking drafts. Nothing is sent. Find a real person first (LinkedIn > University of Houston > Alumni).\n")
    targets = CONFIG.get("target_companies") or []
    if targets:
        print("Your target companies: " + ", ".join(targets) + "\n")
    while True:
        company = ask("Company (or just Enter to finish): ")
        if not company:
            break
        print(f"  UH alumni at {company}: {alumni_link(company)}")
        first = ask("  Their first name (Enter to leave blank): ")
        last = ask("  Their last name (optional): ")
        to = ask("  Their email if you have it (optional): ")
        who = re.sub(r"[^A-Za-z0-9]+", "", f"{first}{last}") or f"contact{datetime.now():%m%d%H%M}"
        folder = ROOT / CONFIG["output_dir"] / "_networking" / (re.sub(r"[^A-Za-z0-9]+", "", company)[:30] or "Company") / who
        write_kit(folder, company, "", first, to, None)
        log_outreach(company, f"{first} {last}".strip(), "Networking", folder)
        print(f"  Drafts saved: {folder}\n  Write the [bracket] line yourself before sending.\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", action="store_true")
    ap.add_argument("--due", action="store_true")
    ap.add_argument("--company", help="with --network: no prompts, make the kit for this company")
    ap.add_argument("--first", default="")
    ap.add_argument("--last", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--export", action="store_true", help="refresh the Control Center's contact list")
    ap.add_argument("--sent", help="drafts folder of the contact you just messaged")
    ap.add_argument("--status", default="Sent", help="with --sent: Sent | Followed up | Replied | Closed")
    ap.add_argument("--cold", action="store_true", help="with --company: cold internship email instead of a networking note")
    args = ap.parse_args()
    load_env()
    if args.export:
        export_contacts()
        raise SystemExit(0)
    if not args.due:
        from common import require_lock
        require_lock("network draft" if args.network else "outreach drafts")
    if args.sent:
        mark_sent(args.sent, args.status)
        export_contacts()
    elif args.cold and args.company:
        cold_one(args.company.strip()[:80], args.first.strip()[:40], args.last.strip()[:40], args.email.strip()[:120])
    elif args.network and args.company:
        network_one(args.company.strip()[:80], args.first.strip()[:40], args.last.strip()[:40], args.email.strip()[:120])
    elif args.network:
        network()
    elif args.due:
        print("\n".join(due_followups()) or "No follow-ups due.")
    else:
        run()
