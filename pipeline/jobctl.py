"""Small, safe changes to single jobs, used by the Control Center buttons. Every change takes the shared lock,
pulls Sheet edits first, and pushes the change to the Sheet afterwards. Nothing here submits or sends anything.

  python pipeline/jobctl.py --recheck [--dry-run]     apply the current filters to jobs not yet opened or applied
  python pipeline/jobctl.py --skip ID                 mark a job Skipped
  python pipeline/jobctl.py --build ID                (re)build the resume + cover letter for one job
  python pipeline/jobctl.py --applied ID              you submitted it: Applied + today's date + follow-up in 7 days,
                                                      then outreach drafts (never sent)
  python pipeline/jobctl.py --log-app --company C --title T [--link URL --date YYYY-MM-DD]
                                                      an application made outside the pipeline (Handshake, Gateway,
                                                      the company site): adds or finds the job and marks it Applied
"""
from __future__ import annotations

import argparse
import re
from datetime import date, timedelta

from common import (CONFIG, _dupe_key, db, is_campus, job_id, load_env, log, mark_dirty, meta_set, require_lock,
                    sync_to_sheet, upsert_job)

# Statuses a re-check may change: found or prepared, but never opened, applied, or decided by you.
RECHECK_STATUSES = ("NEW", "QUALIFIED", "PACKET_READY", "BLOCKED")


def recheck_reason(job: dict) -> str:
    """Why this job breaks the current rules, or '' if it is fine."""
    import discover
    title, loc = job.get("title") or "", (job.get("location") or "").strip()
    if not discover.relevant(title):
        return "title is off-target (skip words in config.yaml)"
    by_hand = (job.get("source") or "") == "added by hand"
    if loc and not by_hand and not is_campus(job) and not discover.near_houston(loc):
        return f"not in the Houston area ({loc[:60]})"
    score = job.get("score")
    if score is not None and job.get("status") in ("QUALIFIED", "PACKET_READY") and int(score) < CONFIG["qualify_score"]:
        return f"score {score} is below {CONFIG['qualify_score']}"
    return ""


def recheck(conn, dry_run: bool = False) -> int:
    marks = ",".join("?" * len(RECHECK_STATUSES))
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM jobs WHERE status IN ({marks})", RECHECK_STATUSES)]
    n = 0
    for job in rows:
        why = recheck_reason(job)
        if not why:
            continue
        n += 1
        log(f"  {'Would skip' if dry_run else 'Skipped'}: {job['company']} | {job['title']} ({why})")
        if not dry_run:
            mark_dirty(conn, job["id"], status="SKIPPED", notes=f"Re-check {date.today():%b %d}: {why}")
    log(f"Re-check: {n} of {len(rows)} jobs {'would be' if dry_run else 'were'} skipped under the current rules."
        + (" Nothing was changed (dry run)." if dry_run else " Packet folders were left on disk."))
    return n


def get(conn, jid: str) -> dict:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        raise SystemExit(f"No job with id {jid}.")
    return dict(row)


def log_app(conn, company: str, title: str, link: str = "", when: str = "") -> dict:
    """Mark an application made outside the pipeline as Applied (adds the job if it is new). Returns the job row."""
    company, title = company.strip()[:80], title.strip()[:120]
    if not company or not title:
        raise SystemExit("--log-app needs --company and --title")
    when = when if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when or "") else date.today().isoformat()
    upsert_job(conn, {"company": company, "title": title, "url": link.strip()[:300], "source": "added by hand"})
    key = _dupe_key(company, title)
    row = next((dict(r) for r in conn.execute("SELECT * FROM jobs") if _dupe_key(r["company"], r["title"]) == key), None)
    if row is None:
        row = get(conn, job_id(company, title))
    mark_dirty(conn, row["id"], status="APPLIED", notes=f"Logged by you {date.today():%Y-%m-%d}: applied {when}.")
    meta_set(conn, f"applied_{row['id']}", when)
    log(f"Logged application: {row['company']} | {row['title']} (applied {when})")
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recheck", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip")
    ap.add_argument("--build")
    ap.add_argument("--applied")
    ap.add_argument("--log-app", action="store_true")
    ap.add_argument("--company", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--link", default="")
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    load_env()
    if not a.dry_run:
        require_lock("job update")
    conn = db()
    if a.dry_run:
        recheck(conn, dry_run=True)
        return
    sync_to_sheet(conn)  # pull your Sheet edits first so this change doesn't fight them
    if a.recheck:
        recheck(conn)
    elif a.skip:
        job = get(conn, a.skip)
        mark_dirty(conn, job["id"], status="SKIPPED", notes=f"Skipped by you {date.today():%Y-%m-%d}")
        log(f"Skipped: {job['company']} | {job['title']}")
    elif a.build:
        import tailor
        job = get(conn, a.build)
        if job["status"] in ("APPLIED",):
            raise SystemExit("Already applied; not rebuilding.")
        mark_dirty(conn, job["id"], status="QUALIFIED")
        tailor.run(limit=1, job_id=job["id"])
    elif a.log_app:
        log_app(conn, a.company, a.title, a.link, a.date)
    elif a.applied:
        import outreach
        job = get(conn, a.applied)
        today = date.today()
        follow = today + timedelta(days=7)
        mark_dirty(conn, job["id"], status="APPLIED",
                   notes=f"Submitted by you {today:%Y-%m-%d}. Follow up around {follow:%b %d}.")
        meta_set(conn, f"applied_{job['id']}", today.isoformat())
        log(f"Applied: {job['company']} | {job['title']} (follow up around {follow:%b %d})")
        outreach.run()
    sync_to_sheet(conn)


if __name__ == "__main__":
    main()
