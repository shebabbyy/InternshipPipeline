"""Shared helpers: config, local database, Google Sheet, Gemini."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
_profile_file = ROOT / "master_profile.json"
# The cloud scan (GitHub) only finds and scores jobs, so it runs without this file: your contact details never need to leave the laptop.
PROFILE = json.loads(_profile_file.read_text(encoding="utf-8")) if _profile_file.exists() else {}
DB_PATH = ROOT / "pipeline.db"

# Windows consoles and redirected log files can't always print emoji or unusual characters in
# company names. Never let that crash a run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# Columns of the "Pipeline Inbox" tab that the pipeline owns (order matters; A to Q).
SHEET_COLUMNS = [
    "Found", "Company", "Role", "Location", "Track", "Score", "Status",
    "Link", "Source", "Missing Keywords", "Why It Fits", "Resume File",
    "Cover Letter", "Outreach Draft", "Notes", "Job ID", "Description",
]
# Column R is yours: the pipeline never writes to it.
SHEET_HEADER = SHEET_COLUMNS + ["My Notes"]
LAST_COL = chr(ord("A") + len(SHEET_COLUMNS) - 1)  # "Q"

STATUSES = {
    "NEW": "🆕 New",
    "QUALIFIED": "⭐ Qualified",
    "LOW": "⬇️ Low fit",
    "PACKET_READY": "📄 Packet ready",
    "FILLED": "✅ Filled — review & Submit",
    "APPLIED": "📨 Applied",
    "BLOCKED": "⚠️ Blocked",
    "SKIPPED": "🚫 Skipped",
}


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)
    try:
        with open(ROOT / "pipeline.log", "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def ask(prompt: str, default: str = "") -> str:
    """input() that never crashes when there is no keyboard (scheduled runs)."""
    try:
        return input(prompt).strip()
    except EOFError:
        return default


def load_env() -> None:
    """Read KEY=VALUE lines from .env into os.environ (no extra package needed)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" not in line and " " not in line and len(line) >= 30:
            os.environ.setdefault("GOOGLE_API_KEY", line)   # key pasted on a line by itself: accept it
            continue
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        if val:
            os.environ.setdefault(key.strip(), val)


def is_campus(job: dict | None) -> bool:
    """UH / Bauer / Gateway / career-fair roles (see campus_markers in config.yaml)."""
    if not job:
        return False
    markers = [m.lower() for m in CONFIG.get("campus_markers", [])]
    blob = " ".join(str(job.get(k) or "") for k in ("company", "source", "notes")).lower()
    return any(m in blob for m in markers)


def email_for(job: dict | None = None) -> str:
    """Gmail for external applications; school email for campus roles."""
    c = PROFILE.get("candidate", {})
    if is_campus(job):
        return c.get("email_campus") or c.get("email", "")
    return c.get("email", "")


# ---------------------------------------------------------------- one-job-at-a-time lock
# Shared by the 6:40 AM scheduled run, the Control Center buttons and every script that changes data.
# pipeline.lock holds {"pid", "job", "started"}. A lock whose process is gone (crash, Stop button) is ignored.
LOCK_PATH = ROOT / "pipeline.lock"
LOCK_MAX_HOURS = 8   # guards against a recycled process id making an old lock look alive


def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == 259             # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_info() -> dict | None:
    """Who holds the lock right now, or None when nothing is running (stale locks count as nothing)."""
    try:
        data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        pid, started = int(data["pid"]), float(data.get("started", 0))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not pid_alive(pid) or time.time() - started > LOCK_MAX_HOURS * 3600:
        return None
    return {"pid": pid, "job": str(data.get("job", "")), "started": started}


def release_lock() -> None:
    try:
        data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        if int(data.get("pid", -1)) == os.getpid():
            LOCK_PATH.unlink()
    except (OSError, ValueError, TypeError):
        pass


def acquire_lock(job: str) -> bool:
    """Take the lock for this process. False means another job is running."""
    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if lock_info() is not None:
                return False
            try:
                LOCK_PATH.unlink()    # stale: its process is gone
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "job": job, "started": time.time()}, fh)
        atexit.register(release_lock)
        return True
    return False


def require_lock(job: str, quiet: bool = False) -> None:
    """Scripts that change data call this first. If something else is running they stop politely."""
    if acquire_lock(job):
        return
    held = lock_info() or {}
    if not quiet:
        print(f"Another job is running right now ({held.get('job') or 'unknown'}). Wait for it to finish "
              "(or press Stop in the Control Center), then try again. Nothing was changed.", flush=True)
    sys.exit(0 if quiet else 3)


def _dupe_key(company: str, title: str) -> str:
    """Looser than job_id: ignores (parentheses), seasons, years and location tails so the same posting listed
    twice ('Tax Intern - Summer 2027' / 'Tax Intern (Houston)') counts once. Does not change job IDs."""
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", (title or "").lower())
    t = re.sub(r"\b(summer|fall|spring|winter|20\d\d|class of|houston|texas|tx|us|usa|program|opportunity|opportunities)\b", " ", t)
    t = re.sub(r"\b(internship|interns)\b", "intern", t)
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s)  # noqa: E731
    return norm(re.sub(r"\b(llp|llc|inc|corp|corporation|co|company|usa|us)\b", " ", (company or "").lower())) + "|" + norm(t)


def job_id(company: str, title: str, url: str = "") -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())  # noqa: E731
    return hashlib.sha256(f"{norm(company)}|{norm(title)}".encode()).hexdigest()[:16]


def clean_text(text: str, limit: int = 2200) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub("[​-‏⁠﻿]", "", text)  # hidden chars (prompt-injection hygiene)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# ---------------------------------------------------------------- database
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, company TEXT, title TEXT, location TEXT, url TEXT,
            source TEXT, description TEXT, found TEXT, score INTEGER, track TEXT,
            missing TEXT, why TEXT, status TEXT DEFAULT 'NEW', resume TEXT,
            cover TEXT, outreach TEXT, notes TEXT, synced INTEGER DEFAULT 0)"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return conn


def meta_get(conn, key, default=None):
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def meta_set(conn, key, val):
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(val)))
    conn.commit()


def upsert_job(conn, job: dict) -> bool:
    """Insert a new job. Returns True if it was new."""
    jid = job_id(job["company"], job["title"])
    if conn.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
        return False
    key = _dupe_key(job["company"], job["title"])
    if any(_dupe_key(r["company"], r["title"]) == key for r in conn.execute("SELECT company, title FROM jobs")):
        return False   # the same posting under a slightly different title or company spelling
    conn.execute(
        "INSERT INTO jobs (id, company, title, location, url, source, description, found)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (jid, job["company"], job["title"], job.get("location", ""), job.get("url", ""),
         job.get("source", ""), clean_text(job.get("description", "")),
         datetime.now(timezone.utc).strftime("%Y-%m-%d")),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------- Google Sheet
_SHEET_CACHE = None


def spreadsheet():
    """Open the tracker spreadsheet (once per run). Returns None (with a plain-English reason) if Sheets isn't set up."""
    global _SHEET_CACHE
    if _SHEET_CACHE is None:
        _SHEET_CACHE = _open_spreadsheet() or False   # False = tried and not available; don't retry or re-log this run
    return _SHEET_CACHE or None


def _open_spreadsheet():
    creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "google_service_account.json"
    try:
        import gspread
    except ImportError:
        log("Google Sheet skipped: gspread is not installed (run INSTALL.bat).")
        return None
    try:
        if creds.lstrip().startswith("{"):  # GitHub Actions passes the JSON itself as a secret
            gc = gspread.service_account_from_dict(json.loads(creds))
        else:
            path = Path(creds) if Path(creds).is_absolute() else ROOT / creds
            if not path.exists():
                log("Google Sheet skipped: google_service_account.json not found (START_HERE.md step 5). Using the local database only.")
                return None
            gc = gspread.service_account(filename=str(path))
        return gc.open(CONFIG["sheet_name"])
    except Exception as exc:  # noqa: BLE001
        name = exc.__class__.__name__
        if name == "SpreadsheetNotFound":
            log(f"Google Sheet skipped: no sheet named '{CONFIG['sheet_name']}' is shared with the service account. "
                "Open the sheet > Share > add the client_email from google_service_account.json as Editor.")
        else:
            log(f"Google Sheet not available ({name}: {str(exc)[:120]}); using the local database only.")
        return None


def worksheet(sh, title: str, header: list[str]):
    try:
        return sh.worksheet(title)
    except Exception:  # noqa: BLE001
        ws = sh.add_worksheet(title, rows=1000, cols=len(header))
        ws.append_row(header, value_input_option="RAW")
        return ws


def sheet():
    """The Pipeline Inbox tab, or None."""
    sh = spreadsheet()
    if sh is None:
        return None
    try:
        return worksheet(sh, CONFIG["inbox_tab"], SHEET_HEADER)
    except Exception as exc:  # noqa: BLE001
        log(f"Google Sheet tab not available ({exc.__class__.__name__}); using the local database only.")
        return None


def sync_to_sheet(conn) -> None:
    """Pull rows this computer hasn't seen (cloud finds, rows you typed in by hand) and your manual
    Status edits, then push local changes. Never raises: the Sheet is a convenience, not a dependency."""
    try:
        _sync(conn, sheet())
    except Exception as exc:  # noqa: BLE001
        log(f"Sheet sync failed this time ({exc.__class__.__name__}: {str(exc)[:120]}); will retry on the next run.")


def _sync(conn, ws) -> None:
    if ws is None:
        return
    rows = ws.get_all_values()
    if not rows:
        ws.append_row(SHEET_HEADER, value_input_option="RAW")
        rows = [SHEET_HEADER]
    header = rows[0]
    if header[:len(SHEET_COLUMNS)] != SHEET_COLUMNS:
        log(f"Sheet sync skipped: row 1 of the '{CONFIG['inbox_tab']}' tab doesn't match the pipeline's columns. "
            "Easiest fix: delete that tab; the pipeline recreates it on the next run.")
        return
    col = {name: i for i, name in enumerate(SHEET_COLUMNS)}
    get = lambda r, name: r[col[name]].strip() if len(r) > col[name] else ""  # noqa: E731
    reverse = {v: k for k, v in STATUSES.items()}

    # Map Job ID -> (row number, row). A row you typed in by hand (Company + Role, no Job ID) gets an ID here.
    existing, manual = {}, set()
    for rownum, r in enumerate(rows[1:], start=2):
        jid = get(r, "Job ID")
        if not jid and get(r, "Company") and get(r, "Role"):
            jid = job_id(get(r, "Company"), get(r, "Role"))
            if jid in existing:
                continue
            manual.add(jid)
        if jid:
            existing[jid] = (rownum, r)

    # 1) pull
    for jid, (_, r) in existing.items():
        if not conn.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
            score = get(r, "Score")
            by_hand = jid in manual
            conn.execute(
                "INSERT INTO jobs (id, company, title, location, url, source, description, found, score, track,"
                " missing, why, status, notes, synced) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (jid, get(r, "Company"), get(r, "Role"), get(r, "Location"), get(r, "Link"),
                 get(r, "Source") or ("added by hand" if by_hand else ""),
                 get(r, "Description") or (get(r, "Notes") if by_hand else ""),
                 get(r, "Found") or datetime.now().strftime("%Y-%m-%d"),
                 int(score) if score.isdigit() else None,
                 get(r, "Track"), get(r, "Missing Keywords"), get(r, "Why It Fits"),
                 reverse.get(get(r, "Status"), "NEW"), get(r, "Notes"), 0 if by_hand else 1))
            continue
        sheet_status = reverse.get(get(r, "Status"))
        if sheet_status:
            conn.execute("UPDATE jobs SET status=? WHERE id=? AND status!=? AND synced=1", (sheet_status, jid, sheet_status))
    conn.commit()

    # 2) push local changes. RAW so posting text that starts with "=" or "+" can never run as a formula.
    new_rows, updates = [], []
    for j in conn.execute("SELECT * FROM jobs WHERE synced=0"):
        row = [j["found"] or "", j["company"] or "", j["title"] or "", j["location"] or "", j["track"] or "",
               j["score"] if j["score"] is not None else "", STATUSES.get(j["status"], j["status"] or ""),
               j["url"] or "", j["source"] or "", j["missing"] or "", j["why"] or "", j["resume"] or "",
               j["cover"] or "", j["outreach"] or "", j["notes"] or "", j["id"], (j["description"] or "")[:2000]]
        if j["id"] in existing:
            rownum = existing[j["id"]][0]
            updates.append({"range": f"A{rownum}:{LAST_COL}{rownum}", "values": [row]})
        else:
            new_rows.append(row)
    if updates:
        ws.batch_update(updates, value_input_option="RAW")
    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW", table_range="A1")
    conn.execute("UPDATE jobs SET synced=1 WHERE synced=0")
    conn.commit()
    log(f"Sheet synced: {len(new_rows)} new, {len(updates)} updated.")


def mark_dirty(conn, jid: str, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields) + ", synced=0"
    conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), jid))
    conn.commit()


# ---------------------------------------------------------------- Gemini
def parse_json(text: str):
    """Parse JSON even when the model wraps it in ```json fences or adds a sentence around it."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    match = re.search(r"(\[.*\]|\{.*\})", text, re.S)
    if not match:
        raise ValueError("no JSON in model reply")
    return json.loads(match.group(1))


def gemini_json(prompt: str, retries: int = 3):
    """Call the Gemini free tier and parse JSON. NEVER pass personal info here:
    the free tier may use prompts for training/human review.
    Raises RuntimeError (and nothing else) when Gemini can't be used."""
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set (paste your free key into .env; START_HERE.md step 3)")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed (run INSTALL.bat)") from exc

    client = genai.Client(api_key=key)
    models = [m for m in (CONFIG.get("gemini_model"), CONFIG.get("gemini_fallback_model")) if m]
    last = None
    # Nov 24: a morning-wide 503 ("high demand") left the day's jobs unscored. When every model is only overloaded,
    # wait and go round again (the daily scan runs hidden, so waiting a few minutes costs nothing).
    rounds = 1 + int(CONFIG.get("gemini_overload_rounds", 2))
    for rnd in range(rounds):
        overloaded = True
        if rnd:
            pause = int(CONFIG.get("gemini_overload_wait_s", 300))
            log(f"Gemini: every model is busy (503); trying again in {pause // 60} min (round {rnd + 1} of {rounds}).")
            time.sleep(pause)
        out = _gemini_round(client, types, models, prompt, retries)
        if out[0]:
            return out[1]
        last, overloaded = out[1], out[2]
        if not overloaded:
            break
    raise RuntimeError(f"Gemini failed: {str(last)[:200]}")


def _gemini_round(client, types, models, prompt, retries):
    """One pass over the models. Returns (True, result) or (False, last_error, only_overloaded)."""
    last, overloaded = None, True
    for model in models:
        extra = {} if model.lower().startswith("gemma") else {"response_mime_type": "application/json"}  # Gemma has no JSON mode
        for attempt in range(retries):
            try:
                resp = client.models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.1, **extra),
                )
                return True, parse_json(resp.text)
            except Exception as exc:  # noqa: BLE001
                last = exc
                msg = str(exc)
                if not any(s in msg for s in ("503", "UNAVAILABLE", "overloaded", "high demand")):
                    overloaded = False
                if any(s in msg for s in ("NOT_FOUND", "404", "API key not valid", "API_KEY_INVALID", "PERMISSION_DENIED", "401", "403")):
                    log(f"Gemini {model}: {msg[:140]} (not retrying; check the model name in config.yaml or the key in .env)")
                    break
                if attempt < retries - 1:
                    wait = 20 * (attempt + 1)
                    log(f"Gemini {model} error ({exc.__class__.__name__}: {msg[:100]}); retry in {wait}s")
                    time.sleep(wait)
    return False, last, overloaded
