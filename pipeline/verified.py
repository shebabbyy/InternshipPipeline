"""Which ready applications the auto-apply is verified on (Nov 24, 2026, his ask: "only applications that certainly
work and you have verified are the ones I can apply to, the other ones on a different tab").

A job is VERIFIED when either
  1. its application system (by the link Open application will use) is in config `verified_ats` = live-tested end to
     end (every answerable question filled, resume + cover letter in their own boxes, stopped before Submit), or
  2. this very job already had a run that ended that way (recorded in output/_apply_results.json by fill.py).
Everything else goes to the Control Center's "Not verified" tab with the reason. Read-only for the Control Center;
only fill.py writes the results file. No Playwright, no network.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlparse

from common import CONFIG, ROOT

RESULTS = ROOT / "output" / "_apply_results.json"
GOOD = ("ready", "review")

REASONS = {
    "workday": "Workday: auto-fill is live-tested only through My Information so far; later steps not verified yet",
    "lever": "Lever: not live-tested yet",
    "greenhouse": "Greenhouse: not marked verified in config.yaml (verified_ats)",
    "other_ats": "Other application system: Simplify Autofill by hand (the pipeline never drives Simplify)",
    "careers": "Company careers site: find the role there, then Simplify by hand",
    "job_board": "Only a job-board link (no company page found): apply there by hand",
    "manual": "Unknown site: apply by hand",
}


def ats_key(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    path = (urlparse(url or "").path or "").lower()
    if "myworkdayjobs.com" in host or ("workday.com" in host and "/recruiting/" in path):
        return "workday"
    if "greenhouse.io" in host or "greenhouse.io" in path:
        return "greenhouse"
    if "lever.co" in host or "lever.co" in path:
        return "lever"
    import apply_window as aw   # no Playwright at import time
    r = aw.route(url)
    return {"greenhouse_lever": "greenhouse"}.get(r, r)


def load() -> dict:
    try:
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(job: dict, ats: str, outcome: str, files_ok: bool) -> None:
    """fill.py: remember how this job's last run ended."""
    data = load()
    data[job["id"]] = {"company": job.get("company"), "title": job.get("title"), "ats": ats, "outcome": str(outcome)[:120],
                       "files_ok": bool(files_ok), "at": time.strftime("%Y-%m-%d %H:%M")}
    try:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        tmp = RESULTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(RESULTS)
    except OSError:
        pass


def status(job: dict, results: dict | None = None) -> tuple[bool, str, str]:
    """(verified, ats key, reason shown in the Control Center)."""
    import resolver
    try:
        url, banner = resolver.target(job)
    except Exception:  # noqa: BLE001
        url, banner = job.get("url") or "", None
    key = "careers" if banner else ats_key(url)
    last = (results if results is not None else load()).get(job.get("id") or "") or {}
    if last.get("outcome") in GOOD and last.get("files_ok"):
        return True, key, f"Verified on this job ({last.get('at')}): every answerable question filled, both files attached"
    if key in (CONFIG.get("verified_ats") or []):
        return True, key, f"{key.title()}: auto-fill verified live (questions + both files, stops before Submit)"
    why = REASONS.get(key, REASONS["manual"])
    if last:
        why += f". Last test run {last.get('at')}: {last.get('outcome')}"
    return False, key, why


def split(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """(verified, not verified), each job given 'ats' and 'why'."""
    res = load()
    ok, other = [], []
    for j in jobs:
        v, key, why = status(j, res)
        j = dict(j, ats=key, why=why)
        (ok if v else other).append(j)
    return ok, other
