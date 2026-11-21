"""Companies to cold-email, ranked by how well they fit Shehab's background. No AI and no personal data leave the
laptop: the ranking is a simple, explainable score from prospects.yaml, the pipeline's open jobs and the
Outreach log. Used by the Control Center's Network tab and by outreach.py --cold.

  fit = group base (healthcare 72, corporate/energy 70, banking 65, accounting 65)
        + 15 if the pipeline has an open matching job there
        + 5 for a local mid-size firm (they answer students more often)
        - 25 if you already contacted someone there in the last 30 days
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import date, timedelta

import yaml

from common import CONFIG, DB_PATH, ROOT

PROSPECTS_FILE = ROOT / "prospects.yaml"
FOUND_FILE = ROOT / "prospects_found.yaml"   # added by the weekly research task; never edits prospects.yaml
TRACK_TO_GROUP = {"corporate_finance_fpa": "corporate_energy", "banking": "banking", "public_accounting": "accounting_advisory"}


def norm(name: str) -> str:
    n = re.sub(r"\b(llp|llc|inc|corp|corporation|co|company|usa|us|na|north america|the|group|holdings)\b", " ", (name or "").lower())
    return re.sub(r"[^a-z0-9]", "", n)


def same_company(a: str, b: str) -> bool:
    x, y = norm(a), norm(b)
    return bool(x and y) and (x == y or (len(min(x, y, key=len)) >= 3 and (x.startswith(y) or y.startswith(x))))


def load_groups() -> dict:
    try:
        return (yaml.safe_load(PROSPECTS_FILE.read_text(encoding="utf-8")) or {}).get("groups") or {}
    except (OSError, yaml.YAMLError):
        return {}


def load_found() -> list[dict]:
    """[{company, group, note, source, added}] from the weekly research (empty if none yet)."""
    try:
        items = (yaml.safe_load(FOUND_FILE.read_text(encoding="utf-8")) or {}).get("found") or []
    except (OSError, yaml.YAMLError):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("company")]


def open_jobs() -> list[dict]:
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True, timeout=1.5)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT company, title, location, source, notes, track, score, status FROM jobs "
            "WHERE status IN ('QUALIFIED','PACKET_READY','FILLED','APPLIED')")]
        con.close()
    except sqlite3.Error:
        return []
    import jobctl   # only jobs that pass today's filters count (same rules as Re-check old jobs)
    return [r for r in rows if r["status"] in ("FILLED", "APPLIED") or not jobctl.recheck_reason(r)]


def contacted() -> dict[str, str]:
    """company -> most recent outreach date, from the cached Outreach tab or the local log."""
    out: dict[str, str] = {}
    rows: list[dict] = []
    cache = ROOT / CONFIG["output_dir"] / "_networking" / "contacts.json"
    try:
        rows = json.loads(cache.read_text(encoding="utf-8")).get("contacts", [])
    except (OSError, ValueError):
        path = ROOT / CONFIG["output_dir"] / "outreach_log.csv"
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                rows = [{"drafted": r[0], "company": r[1], "status": r[4]} for r in list(csv.reader(fh))[1:] if len(r) > 4]
        except OSError:
            rows = []
    for r in rows:
        if r.get("company"):
            out[r["company"]] = max(out.get(r["company"], ""), r.get("drafted", ""))
    return out


def ranked() -> list[dict]:
    groups = load_groups()
    jobs = open_jobs()
    touched = contacted()
    recent = (date.today() - timedelta(days=30)).isoformat()
    seen: dict[str, dict] = {}

    def add(company: str, gkey: str, small: bool = False) -> None:
        key = norm(company)
        if not key or key in seen:
            return
        g = groups.get(gkey, {})
        seen[key] = {"company": company, "group": gkey, "group_label": g.get("label", gkey), "fit_line": g.get("fit", ""),
                     "base": int(g.get("base", 60)), "small": small, "note": (g.get("notes") or {}).get(company, "")}

    for gkey, g in groups.items():
        for c in g.get("companies") or []:
            add(c, gkey)
        for c in g.get("small") or []:
            add(c, gkey, small=True)
    for f in load_found():   # weekly research finds (verified with a source link)
        gkey = f.get("group") if f.get("group") in groups else "corporate_energy"
        add(str(f["company"])[:80], gkey)
        p = seen.get(norm(str(f["company"])))
        if p is not None and not p["note"]:
            p["note"] = (f"New {f.get('added', '')}: " if f.get("added") else "") + str(f.get("note", ""))[:160] \
                        + (f" ({f['source']})" if f.get("source") else "")
    for j in jobs:   # companies with open matching jobs that aren't listed yet
        add(j["company"], TRACK_TO_GROUP.get(j.get("track") or "", "corporate_energy"))

    out = []
    for p in seen.values():
        mine = [j for j in jobs if same_company(j["company"], p["company"])]
        last = next((d for c, d in touched.items() if same_company(c, p["company"])), "")
        score = p["base"] + (15 if mine else 0) + (5 if p["small"] else 0) - (25 if last and last >= recent else 0)
        why = [p["group_label"]]
        if mine:
            why.append(f"{len(mine)} open role{'s' if len(mine) > 1 else ''} in your pipeline")
        if p["small"]:
            why.append("local mid-size firm")
        if p["note"]:
            why.append(p["note"])
        out.append({**p, "fit": max(0, min(100, score)), "open_roles": len(mine), "last_contact": last,
                    "roles": "; ".join(f"{j['title']} ({j['score']})" for j in mine[:3]), "why": " | ".join(why)})
    return sorted(out, key=lambda r: (-r["fit"], r["company"]))


def fit_line(company: str) -> str:
    """The one sentence about Shehab's experience that fits this company's group ('' if unknown)."""
    for p in ranked():
        if same_company(p["company"], company):
            return p["fit_line"]
    return ""
