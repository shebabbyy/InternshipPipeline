"""Entry point.

  python pipeline/run_all.py --cloud    GitHub Actions each morning: watchlist feeds -> score -> Google Sheet
  python pipeline/run_all.py            Laptop: pull Sheet edits, find + score (if 12h+ since last),
                                        build tailored packets, push to Sheet, print summary
  python pipeline/run_all.py --apply    You, when ready: open + fill applications, record what you submitted,
                                        then create outreach drafts
  python pipeline/run_all.py --status   Just print the summary
  python pipeline/run_all.py --sync     Just sync the Google Sheet (pull your edits, push local changes)
  python pipeline/run_all.py --step discover|score|tailor|resolve|outreach   Run one stage only (Control Center)
  python pipeline/run_all.py --apply --job ID                        Open just one application

Every laptop mode takes the shared lock (pipeline.lock), so only one job runs at a time.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import date, datetime, timedelta

from common import CONFIG, db, load_env, log, meta_get, meta_set, require_lock, sync_to_sheet

import discover
import triage


def stage(name: str, fn, *args, **kwargs):
    """Run one stage. A failure in one stage never stops the others."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log(f"{name} failed ({exc.__class__.__name__}: {str(exc)[:150]}); continuing with the next step.")
        return None


def stamp_applied(conn) -> None:
    """Remember the day each job first showed as Applied (also when you set it in the Sheet)."""
    for r in conn.execute("SELECT id FROM jobs WHERE status='APPLIED'").fetchall():
        if not meta_get(conn, f"applied_{r['id']}"):
            meta_set(conn, f"applied_{r['id']}", date.today().isoformat())


def scan_plan(now: float, last: float, last_full: float, auto: bool, force: bool) -> str:
    """What this run should do: 'quit' (scheduled check, nothing due), 'none' (no discovery), 'light' (company career
    sites only) or 'full' (job boards too). Job boards at most once per full_scan_every_hours to protect LinkedIn."""
    every = float(CONFIG.get("scan_every_hours", 12)) * 3600
    full_every = float(CONFIG.get("full_scan_every_hours", 24)) * 3600
    if force:
        return "full"
    if auto:
        if now - last < every:
            return "quit"
        return "full" if now - last_full > full_every else "light"
    return ("full" if now - last_full > full_every else "light") if now - last > 12 * 3600 else "none"


def summary(conn) -> str:
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    top = conn.execute("SELECT company, title, score FROM jobs WHERE status='PACKET_READY' ORDER BY score DESC LIMIT 5").fetchall()
    lines = ["", "=" * 60, "INTERNSHIP PIPELINE SUMMARY", "=" * 60,
             f"Packets ready to apply: {counts.get('PACKET_READY', 0)}   Qualified waiting: {counts.get('QUALIFIED', 0)}   "
             f"Not scored yet: {counts.get('NEW', 0)}",
             f"Opened, not submitted: {counts.get('FILLED', 0)}   Applied: {counts.get('APPLIED', 0)}"]
    lines += [f"  - {r['company']}: {r['title']} (score {r['score']})" for r in top]
    # Applications 8 to 21 days old with no recorded follow-up yet
    lo, hi = (date.today() - timedelta(days=21)).isoformat(), (date.today() - timedelta(days=8)).isoformat()
    nudges = [f"  - {r['company']}: {r['title']}" for r in conn.execute("SELECT id, company, title FROM jobs WHERE status='APPLIED'")
              if lo <= (meta_get(conn, f"applied_{r['id']}") or "") <= hi]
    if nudges:
        lines += ["Applied 8+ days ago (worth a short follow-up to your contact there):"] + nudges[:8]
    import outreach
    due = stage("Follow-up check", outreach.due_followups) or []
    if due:
        lines += ["Networking follow-ups due:"] + [f"  - {d}" for d in due[:8]]
    last = float(meta_get(conn, "last_discovery", 0) or 0)
    lines += ["Last scan on this laptop: " + (datetime.fromtimestamp(last).strftime("%a %b %d, %I:%M %p") if last else "never (press Run Daily Scan in the Control Center)")]
    if counts.get("NEW") and not os.environ.get("GOOGLE_API_KEY"):
        lines += [f"{counts['NEW']} jobs can't be scored: no Gemini key in .env yet (START_HERE.md step 3)."]
    if counts.get("PACKET_READY") or counts.get("FILLED"):
        lines += ["Next: open the Control Center and apply when you have 20 minutes."]
    lines += ["=" * 60]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cloud", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore the 12-hour guard")
    ap.add_argument("--auto", action="store_true", help="scheduled run: exit quietly if it ran in the last 12 hours")
    ap.add_argument("--retailor", action="store_true", help="rebuild resumes/cover letters for every Packet ready job")
    ap.add_argument("--sync", action="store_true", help="only sync the Google Sheet")
    ap.add_argument("--step", choices=["discover", "score", "tailor", "resolve", "outreach"], help="run a single stage")
    ap.add_argument("--job", help="with --apply: open only this job id")
    args = ap.parse_args()
    load_env()
    if not args.cloud:   # the cloud scan starts from an empty database on GitHub and needs no lock
        mode = ("auto scan" if args.auto else "apply" if args.apply else "status" if args.status else
                "sheet sync" if args.sync else f"step: {args.step}" if args.step else "rebuild packets" if args.retailor else "daily scan")
        require_lock(mode, quiet=args.auto)   # a scheduled run that finds the lock taken just exits quietly
    conn = db()

    if args.cloud:
        # GitHub starts with an empty database every day. Pull the Sheet FIRST so jobs already there are
        # not scored again and rows the laptop has updated (Packet ready, Applied...) are never overwritten.
        sync_to_sheet(conn)
        stage("Discovery", discover.run, cloud=True)
        stage("Scoring", triage.run)
        sync_to_sheet(conn)
        return

    # Laptop-only parts are loaded here so the cloud scan needs neither your profile nor the document tools.
    import claude_discover
    import fill
    import outreach
    import resolver   # phase 3: job-board links -> the company's own application page
    import tailor

    if args.sync:
        sync_to_sheet(conn)
        stamp_applied(conn)
        return

    if args.step:
        sync_to_sheet(conn)
        if args.step == "discover":
            stage("Discovery", discover.run, cloud=False)
            meta_set(conn, "last_discovery", time.time())
        elif args.step == "score":
            stage("Scoring", triage.run)
        elif args.step == "tailor":
            stage("Tailoring", tailor.run)
        elif args.step == "resolve":
            stage("Direct links", resolver.run)
        else:
            stamp_applied(conn)
            stage("Outreach drafts", outreach.run)
        sync_to_sheet(conn)
        print(summary(conn))
        return

    if args.status:
        sync_to_sheet(conn)
        stamp_applied(conn)
        print(summary(conn))
        return

    if args.retailor:
        # Rebuild packets that are ready but not yet opened or applied (e.g. after a tailoring fix).
        sync_to_sheet(conn)
        n = conn.execute("UPDATE jobs SET status='QUALIFIED' WHERE status IN ('PACKET_READY','FILLED')").rowcount
        conn.commit()
        log(f"Re-tailoring {n} packets.")
        stage("Tailoring", tailor.run, limit=max(n, 10))
        sync_to_sheet(conn)
        print(summary(conn))
        return

    if args.apply:
        sync_to_sheet(conn)
        stage("Applications", fill.run, limit=CONFIG.get("max_applications_per_session", 6), job_id=args.job)
        stamp_applied(conn)
        stage("Outreach drafts", outreach.run)
        sync_to_sheet(conn)
        return

    now = time.time()
    plan = scan_plan(now, float(meta_get(conn, "last_discovery", 0) or 0),
                     float(meta_get(conn, "last_full_discovery", 0) or 0), args.auto, args.force)
    if plan == "quit":
        return  # the hourly check found nothing due: do nothing, show nothing
    sync_to_sheet(conn)  # pull cloud finds, rows you added by hand, and your Status edits first
    if plan in ("light", "full"):
        # full = job boards + company sites; light = company sites only (cloud=True skips the boards on purpose)
        stage("Discovery", discover.run, cloud=(plan == "light"))
        if plan == "full":
            stage("Claude discovery", claude_discover.discover)   # off unless claude_discover_per_day > 0
            meta_set(conn, "last_full_discovery", now)
        meta_set(conn, "last_discovery", now)
        log("Scan: " + ("full (job boards + company sites)." if plan == "full" else "company career sites only (job boards run once a day)."))
    else:
        log("Skipped discovery (ran less than 12 hours ago).")
    stage("Scoring", triage.run)          # always: also scores rows that arrived from the Sheet
    stage("Tailoring", tailor.run)
    stage("Direct links", resolver.run)   # phase 3: company links for job-board postings (new ones only)
    stamp_applied(conn)
    stage("Outreach drafts", outreach.run)  # for anything you marked Applied in the Sheet
    sync_to_sheet(conn)
    stage("Contacts", outreach.export_contacts)  # keeps the Control Center's Network tab current
    print(summary(conn))
    ready = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='PACKET_READY'").fetchone()[0]
    if args.auto and ready and CONFIG.get("auto_open_apply"):
        print("APPLY_READY")  # RUN_PIPELINE_AUTO.bat sees this and offers to open the application session


if __name__ == "__main__":
    main()
