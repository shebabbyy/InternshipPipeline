"""Shehab's Internship Control Center (Today; Scan & tools + live log; Jobs; Documents; Apply; Network).

A small local web page that runs the existing pipeline scripts as separate processes and shows their output.
  * Opens in your normal browser at http://127.0.0.1:8765 (this computer only).
  * Started with "Start Control Center" on the desktop (no console window). Quit button stops it.
  * Never clicks Submit and never sends an email: it only starts the same scripts the .bat files used to start.
  * Never shows .env or the Google key file: only "present" or "missing".
  * One job at a time, shared with the 6:40 AM scheduled run through pipeline.lock.
Run by hand for debugging:  .venv\\Scripts\\python.exe control_center.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from functools import partial
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parent
PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
APP_LOG = ROOT / "control_center.log"

# pythonw.exe (no console window) has no stdout/stderr; the web server needs them. Keep a log file instead.
if sys.stdout is None or sys.stderr is None:
    _fh = open(APP_LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _fh
    sys.stderr = sys.stderr or _fh

sys.path.insert(0, str(ROOT / "pipeline"))
from common import CONFIG, DB_PATH, LOCK_PATH, STATUSES, lock_info, pid_alive  # noqa: E402  (no Playwright here, on purpose)
import prospects  # noqa: E402  (read-only ranking from prospects.yaml + pipeline.db)
import answers  # noqa: E402  (phase 2: Workday questions waiting for you; answers go to an inbox file, never the DB)
import resolver  # noqa: E402  (phase 3: reads output/_resolved_links.json only; lookups run as a subprocess)
import verified  # noqa: E402  (Nov 24: which ready jobs the auto-apply is verified on; reads output/_apply_results.json)

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW (Windows): child scripts never open a console


# ---------------------------------------------------------------- running jobs
def child_python() -> str:
    """python.exe of the project's environment. (pythonw.exe can't run scripts that print.)"""
    if os.name == "nt":
        venv = ROOT / ".venv" / "Scripts" / "python.exe"
        if venv.exists():
            return str(venv)
        exe = Path(sys.executable)
        return str(exe.with_name("python.exe")) if exe.name.lower() == "pythonw.exe" else sys.executable
    return sys.executable


# key -> (label shown in the page, arguments after python)
JOBS: dict[str, tuple[str, list[str]]] = {
    "scan": ("Daily scan", ["pipeline/run_all.py", "--force"]),
    "status": ("Status summary", ["pipeline/run_all.py", "--status"]),
    "sync": ("Sync Sheet", ["pipeline/run_all.py", "--sync"]),
    "test": ("Test setup", ["pipeline/selftest.py"]),
    "unit": ("Code self-test", ["pipeline/tests/offline_test.py"]),
    "resumes": ("Make track resumes", ["pipeline/make_track_resumes.py"]),
    "rebuild": ("Rebuild packets", ["pipeline/run_all.py", "--retailor"]),
    "simplify": ("Simplify setup", ["pipeline/setup_simplify.py"]),
    "recheck": ("Re-check old jobs", ["pipeline/jobctl.py", "--recheck"]),
    "resolve": ("Find company links", ["pipeline/resolver.py"]),
}


class Runner:
    """Starts one script at a time, collects its output, can stop it."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.name = ""
        self.started = 0.0
        self.lines: deque[str] = deque(maxlen=4000)
        self.total = 0                       # lines ever added (pages remember how many they have shown)
        self.last_code: int | None = None
        self.finished_at = 0.0
        self.lock = threading.Lock()

    def _add(self, text: str) -> None:
        with self.lock:
            self.lines.append(text)
            self.total += 1

    def new_lines(self, seen: int) -> tuple[list[str], int]:
        with self.lock:
            fresh = self.total - seen
            return (list(self.lines)[-fresh:] if fresh > 0 else []), self.total

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def busy_reason(self) -> str:
        """Why nothing new can start (empty string = free to start)."""
        if self.running:
            return f"{self.name} is running"
        held = lock_info()
        if held:
            return f"{held['job'] or 'another job'} is running (started outside this page, e.g. the scheduled run)"
        return ""

    def start(self, name: str, args: list[str], extra_env: dict | None = None) -> str:
        """Returns '' on success, otherwise a plain-English reason."""
        why = self.busy_reason()
        if why:
            return why
        env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1", **(extra_env or {}))
        kw: dict = {}
        if os.name == "nt":
            # BELOW_NORMAL_PRIORITY_CLASS: scans and tests give way to games and everything else you are doing
            kw["creationflags"] = NO_WINDOW | 0x00000200 | 0x00004000   # + CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(
                [child_python(), "-u", *args], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **kw)
        except OSError as exc:
            return f"could not start ({exc.__class__.__name__}). Run INSTALL.bat once, then try again."
        self.name, self.started, self.last_code = name, time.time(), None
        self._add(f"--- {datetime.now():%H:%M:%S}  {name} started ---")
        threading.Thread(target=self._read, args=(self.proc, name), daemon=True).start()
        return ""

    def _read(self, proc: subprocess.Popen, name: str) -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                self._add(line.rstrip("\r\n"))
        finally:
            code = proc.wait()
            self.last_code, self.finished_at = code, time.time()
            note = "finished" if code == 0 else ("stopped" if code in (-15, -9, 1, 3221225786) and self._stopped else f"ended with exit code {code}")
            if code == 3:
                note = "did not start: another job holds the lock"
            self._add(f"--- {datetime.now():%H:%M:%S}  {name} {note} ---")
            if code not in (0, 3) and not self._stopped:
                self._add("What to do: the lines just above say what went wrong. Nothing was submitted or sent. "
                          "If it isn't obvious, paste them into the Claude project with /pipeline.")
            self._stopped = False

    _stopped = False

    def stop(self) -> None:
        if self.running and self.proc:
            self._stopped = True
            kill_tree(self.proc.pid)


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass


def is_python_process(pid: int) -> bool:
    """Before stopping a job we did not start (the scheduled run), make sure the lock's pid really is Python."""
    if os.name != "nt":
        return pid_alive(pid)
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout.lower()
    return "python" in out


RUNNER = Runner()


# ---------------------------------------------------------------- read-only database + health
def counters() -> dict | None:
    """Counts for the header. Reads pipeline.db read-only; None if it can't be read right now."""
    if not DB_PATH.exists():
        return {"new_today": 0, "review": 0, "ready": 0, "followups": 0, "applied": 0, "last_scan": 0.0}
    try:
        con = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True, timeout=1.5)
    except sqlite3.Error:
        return None
    try:
        one = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731
        today = date.today().isoformat()
        lo, hi = (date.today() - timedelta(days=21)).isoformat(), (date.today() - timedelta(days=7)).isoformat()
        followups = one("SELECT COUNT(*) FROM jobs j JOIN meta m ON m.k = 'applied_' || j.id "
                        "WHERE j.status='APPLIED' AND m.v BETWEEN ? AND ?", lo, hi)
        last = con.execute("SELECT v FROM meta WHERE k='last_discovery'").fetchone()
        return {
            "new_today": one("SELECT COUNT(*) FROM jobs WHERE found >= ?", today),
            "review": one("SELECT COUNT(*) FROM jobs WHERE status IN ('QUALIFIED','BLOCKED')"),
            "ready": one("SELECT COUNT(*) FROM jobs WHERE status IN ('PACKET_READY','FILLED')"),
            "followups": followups,
            "applied": one("SELECT COUNT(*) FROM jobs WHERE status='APPLIED'"),
            "last_scan": float(last[0]) if last and last[0] else 0.0,
        }
    except sqlite3.Error:
        return None
    finally:
        con.close()


def health() -> dict:
    """Presence only. The contents of .env and the Google key file are never read into the page."""
    key = False
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.upper().startswith("GOOGLE_API_KEY="):
                key = bool(line.split("=", 1)[1].strip().strip("\"'"))
            elif line and not line.startswith("#") and "=" not in line and " " not in line and len(line) >= 30:
                key = True   # key pasted on a line by itself (the pipeline accepts that too)
    except OSError:
        pass
    return {"gemini key": key, "Sheet key file": (ROOT / "google_service_account.json").exists(),
            "profile": (ROOT / "master_profile.json").exists()}


def log_tail(n: int = 25) -> list[str]:
    try:
        with open(ROOT / "pipeline.log", "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 12000))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-n:]


GROUPS = {
    "Active": ("NEW", "QUALIFIED", "PACKET_READY", "FILLED", "BLOCKED"),
    "Ready to apply": ("PACKET_READY", "FILLED"),
    "Applied": ("APPLIED",),
    "Skipped or low fit": ("SKIPPED", "LOW"),
    "All": tuple(STATUSES),
}


def _ro():
    return sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True, timeout=1.5)


def job_rows(group: str = "Active") -> list[dict]:
    """Jobs for the tables, read-only. Empty list if the database is busy for a moment."""
    statuses = GROUPS.get(group, GROUPS["Active"])
    if not DB_PATH.exists():
        return []
    try:
        con = _ro()
        con.row_factory = sqlite3.Row
        marks = ",".join("?" * len(statuses))
        rows = [dict(r) for r in con.execute(
            "SELECT id, company, title, location, url, score, status, found, notes, resume, cover, description "
            f"FROM jobs WHERE status IN ({marks}) "
            "ORDER BY CASE status WHEN 'PACKET_READY' THEN 0 WHEN 'FILLED' THEN 1 ELSE 2 END, score DESC", statuses)]
        con.close()
    except sqlite3.Error:
        return []
    for r in rows:
        r["status_label"] = STATUSES.get(r["status"], r["status"])
        r["description"] = (r["description"] or "")[:4000]
    return rows


def one_job(jid: str) -> dict | None:
    if not jid or not DB_PATH.exists():
        return None
    try:
        con = _ro()
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        con.close()
        return dict(row) if row else None
    except sqlite3.Error:
        return None


def packet_file(job: dict | None, kind: str) -> Path | None:
    """The job's resume or cover letter, only if it is inside the output folder."""
    raw = (job or {}).get(kind) or ""
    if not raw:
        return None
    try:
        path = Path(raw).resolve()
        path.relative_to((ROOT / "output").resolve())
    except (OSError, ValueError):
        return None
    return path


def side_text(job: dict | None, name: str) -> str:
    """FILL_SHEET.txt or job_posting.txt next to the packet ('' if not there yet)."""
    resume = packet_file(job, "resume")
    try:
        return (resume.parent / name).read_text(encoding="utf-8", errors="replace") if resume else ""
    except OSError:
        return ""


def simplify_ready() -> bool:
    prof = CONFIG.get("apply_profile_dir", "apply_profile") if CONFIG.get("apply_browser", "cdp") == "cdp" else CONFIG.get("browser_profile_dir", "browser_profile")
    ext = ROOT / prof / "Default" / "Extensions" / CONFIG.get("simplify_extension_id", "")
    try:
        return ext.is_dir() and any(ext.iterdir())
    except OSError:
        return False


def link_line(job: dict) -> str:
    """Apply tab: where Open application will go for a job-board posting (phase 3)."""
    try:
        if resolver.Http.allowed(job.get("url") or "") or not job.get("url"):
            return ""
        r = resolver.load_results().get(job["id"]) or {}
    except Exception:  # noqa: BLE001
        return ""
    if not r:
        return "\nLink: job board only. Press Find company links (Scan & tools) to look for the company's page."
    dup = f" Same posting as job {r['duplicate_of']}: skip one." if r.get("duplicate_of") else ""
    return {"direct": f"\nLink: company {str(r.get('ats', '')).title()} page found ({r.get('how')}).{dup}",
            "careers": f"\nLink: company careers site ({r.get('ats')}); you find the role there.{dup}",
            }.get(r.get("kind"), f"\nLink: no company page found ({r.get('checked')}); apply from the job board.")


def open_in_chrome(url: str) -> bool:
    """Your normal Chrome (where Simplify is installed), never the pipeline's apply window."""
    if not str(url or "").startswith(("http://", "https://")):
        return False
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), os.environ.get("LOCALAPPDATA", "")):
            chrome = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if base and chrome.exists():
                subprocess.Popen([str(chrome), url], creationflags=NO_WINDOW)
                return True
    webbrowser.open(url)
    return True


QUICK_LINKS = [
    ("Career Gateway (via AccessUH)", "https://accessuh.uh.edu/"),
    ("Simplify", "https://simplify.jobs/"),
    ("Claude project", "https://claude.ai/projects"),
    ("UH alumni on LinkedIn", "https://www.linkedin.com/school/university-of-houston/people/"),
]
CONTACTS_CACHE = ROOT / "output" / "_networking" / "contacts.json"


def upcoming_events(limit: int = 6) -> list[tuple[int, str, str]]:
    """(days left, date text, what) for dates in config.yaml events that haven't passed."""
    out = []
    for e in CONFIG.get("events") or []:
        try:
            d = e["date"] if isinstance(e["date"], date) else date.fromisoformat(str(e["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        left = (d - date.today()).days
        if left >= 0:
            out.append((left, d.strftime("%a %b %d, %Y"), str(e.get("what", ""))))
    return sorted(out)[:limit]


def new_matches(days: int = 3) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    return [r for r in job_rows("Active") if r["status"] in ("QUALIFIED", "PACKET_READY") and (r["found"] or "") >= since][:10]


def applied_followups() -> list[dict]:
    """Applications 7 to 21 days old, with their follow-up draft if one was written."""
    if not DB_PATH.exists():
        return []
    lo, hi = (date.today() - timedelta(days=21)).isoformat(), (date.today() - timedelta(days=7)).isoformat()
    try:
        con = _ro()
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT j.id, j.company, j.title, j.outreach, m.v AS applied FROM jobs j JOIN meta m ON m.k = 'applied_' || j.id "
            "WHERE j.status='APPLIED' AND m.v BETWEEN ? AND ? ORDER BY m.v", (lo, hi))]
        con.close()
        return rows
    except sqlite3.Error:
        return []


def followup_text(job: dict) -> str:
    """The 3_FOLLOW_UP.txt drafted after applying, or a short honest default."""
    m = re.search(r"Drafts: (.+?)\. Find", job.get("outreach") or "")
    if m:
        try:
            path = (Path(m.group(1)) / "3_FOLLOW_UP.txt").resolve()
            path.relative_to((ROOT / "output").resolve())
            return path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            pass
    return (f"Subject: Following up: {job.get('title', '')} application\n\nHi [name],\n\n"
            f"I applied for the {job.get('title', '')} role at {job.get('company', '')} on {job.get('applied', '')} and wanted to "
            "follow up. I'm still very interested and would be glad to share anything else that helps. "
            "Thank you for your time.\n\nThanks,\nShehab Moqbel")


def contacts() -> dict:
    """Outreach contacts: the cache the Refresh button writes, else the local log."""
    try:
        return json.loads(CONTACTS_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    path = ROOT / "output" / "outreach_log.csv"
    keys = ["drafted", "company", "contact", "type", "status", "follow_up", "alumni", "folder", "notes"]
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))[1:]
    except OSError:
        rows = []
    return {"updated": "", "source": "outreach_log.csv (press Refresh for the Sheet's statuses)", "sheet_url": "",
            "contacts": [dict(zip(keys, (r + [""] * 9)[:9])) for r in rows if any(r)]}


def contact_rows() -> list[dict]:
    today = date.today().isoformat()
    rows = contacts().get("contacts", [])
    for r in rows:
        due = r.get("status", "").strip().lower() == "sent" and r.get("follow_up", "") and r["follow_up"] <= today
        r["due"] = "Follow up now" if due else ""
    return list(reversed(rows))


_SCHED: dict = {"at": 0.0, "text": ""}


def scheduled_task() -> str:
    """Windows Task Scheduler status of the 6:40 AM run (cached a minute)."""
    if os.name != "nt":
        return "only shown on Windows"
    if time.time() - _SCHED["at"] < 60:
        return _SCHED["text"]
    parts = []
    for name, label in (("Shehab Internship Pipeline", "daily"),):
        try:
            out = subprocess.run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"],
                                 capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            parts.append(f"{label}: could not check")
            continue
        if out.returncode != 0:
            parts.append(f"{label}: not set up (run tools\\SCHEDULE_AT_LOGIN.bat)")
            continue
        info = {}
        for line in out.stdout.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                info.setdefault(k.strip(), v.strip())
        result = info.get("Last Result", "")
        parts.append(f"{label}: {info.get('Status', '?')}, next {info.get('Next Run Time', '?')}"
                     + ("" if result in ("0", "", "267011") else f" (last result {result})"))
    text = " | ".join(parts)
    _SCHED.update(at=time.time(), text=text)
    return text


def last_sheet_sync() -> str:
    for line in reversed(log_tail(400)):
        if "Sheet synced" in line:
            return line[:19].replace("T", " ")
        if "Sheet sync failed" in line or "Google Sheet skipped" in line or "Sheet not available" in line:
            return "failed at " + line[:19].replace("T", " ") + " (see log)"
    return "not in the recent log"


def last_auto_run() -> tuple[str, str]:
    path = ROOT / "last_auto_run.txt"
    try:
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%a %b %d, %I:%M %p")
        text = path.read_text(encoding="utf-8", errors="replace")[-3000:]
        return when, text.strip() or "(it ran and had nothing to say: skipped because a run happened in the last 12 hours)"
    except OSError:
        return "never", ""


def send_info(folder: str) -> dict:
    """to / subject / body for a drafts folder (send.json, else 2_EMAIL_DRAFT.txt). Only inside output/."""
    try:
        path = Path(folder).resolve()
        path.relative_to((ROOT / "output").resolve())
    except (OSError, ValueError):
        return {}
    try:
        return json.loads((path / "send.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        text = (path / "2_EMAIL_DRAFT.txt").read_text(encoding="utf-8")
    except OSError:
        return {}
    first, _, body = text.partition("\n\n")
    return {"to": "", "subject": first.replace("Subject:", "", 1).strip(), "body": body}


def compose_url(info: dict, service: str) -> str:
    """Opens a pre-filled, UNSENT message. You read it and click Send yourself."""
    to, subject, body = info.get("to", ""), info.get("subject", ""), info.get("body", "")
    if service == "gmail":
        return "https://mail.google.com/mail/?" + urlencode({"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}, quote_via=quote)
    return "https://outlook.office.com/mail/deeplink/compose?" + urlencode({"to": to, "subject": subject, "body": body}, quote_via=quote)


def sent_this_week(rows: list[dict]) -> int:
    since = (date.today() - timedelta(days=7)).isoformat()
    return sum(1 for r in rows if r.get("status", "").strip() in ("Sent", "Followed up", "Replied") and r.get("drafted", "") >= since)


QUEUE_PATH = ROOT / "output" / "_outreach_queue.json"
EVENTS_DIR = ROOT / "output" / "_outreach_events"
SENDER_LABEL = {"school": "School Outlook", "gmail": "Gmail"}
STATE_ORDER = {"queued": 0, "sent": 1, "replied": 2, "closed": 3, "bounced": 3, "skipped": 4}
SOURCE_CHOICES = ["UH alum", "Bauer / UH", "Career fair", "Other"]


def queue_rows() -> list[dict]:
    """Phase 4 outreach queue (read-only here; only contacts.py / outreach.py write it)."""
    try:
        items = json.loads(QUEUE_PATH.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError, AttributeError):
        return []
    rows = [{**i, "sender_label": SENDER_LABEL.get(i.get("sender"), i.get("sender", "")), "person": i.get("name") or "(name needed)"}
            for i in items if isinstance(i, dict) and i.get("id")]
    return sorted(rows, key=lambda r: (STATE_ORDER.get(r.get("state"), 9), r.get("added", "")), reverse=False)


def queue_week() -> tuple[int, int]:
    since = (date.today() - timedelta(days=7)).isoformat()
    n = sum(1 for r in queue_rows() if r.get("kind") != "follow_up" and r.get("state") != "skipped" and r.get("added", "") >= since)
    try:
        cap = int(CONFIG.get("outreach_weekly_cap", 8))
    except (TypeError, ValueError):
        cap = 8
    return n, cap


def pending_events() -> int:
    try:
        return len(list(EVENTS_DIR.glob("*.json")))
    except OSError:
        return 0


def prospect_rows() -> list[dict]:
    try:
        return prospects.ranked()
    except Exception:  # noqa: BLE001  (a typo in prospects.yaml must not break the page)
        return []


def data_signature() -> tuple:
    """Changes whenever the pipeline, the contacts or the prospect list change (the page then refreshes itself)."""
    sig = []
    for path in (DB_PATH, CONTACTS_CACHE, ROOT / "prospects.yaml", ROOT / "output" / "outreach_log.csv", QUEUE_PATH, EVENTS_DIR):
        try:
            sig.append(path.stat().st_mtime)
        except OSError:
            sig.append(0)
    return tuple(sig)


def auto_problem() -> str:
    """The last automatic run's error, if it failed after the last successful scan ('' if all is well)."""
    err = ROOT / "last_auto_error.txt"
    try:
        when = err.stat().st_mtime
    except OSError:
        return ""
    c = counters() or {}
    if c.get("last_scan", 0) and c["last_scan"] > when:
        return ""
    try:
        tail = err.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
    except OSError:
        tail = []
    return f"The automatic run on {datetime.fromtimestamp(when):%a %b %d, %I:%M %p} hit a problem. Nothing was submitted or sent. " \
           "Press Run Daily Scan to try again; if it repeats, paste this into the Claude project with /pipeline:\n" + "\n".join(tail)


def fmt_age(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


# ---------------------------------------------------------------- the page
try:
    from nicegui import app, ui  # noqa: E402
except ImportError:   # INSTALL.bat has not been run since the Control Center was added
    _msg = "The Control Center needs one more package. Double-click INSTALL.bat in the InternshipPipeline folder, then open it again."
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, _msg, "Control Center", 0x30)
    print(_msg)
    sys.exit(1)


class LocalOnly:
    """Refuse requests whose Host or Origin is not this computer's own address (stops a web page you happen to
    have open from sending commands to this app)."""

    ALLOWED = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            origin = headers.get("origin")
            bad = headers.get("host", "") not in self.ALLOWED or (
                origin is not None and origin.replace("http://", "", 1) not in self.ALLOWED)
            if bad:
                if scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body", "body": b"Control Center only answers on this computer."})
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.inner(scope, receive, send)


app.add_middleware(LocalOnly)


@app.get("/doc/{jid}/{kind}")
def serve_doc(jid: str, kind: str):
    """Resume / cover letter preview: the PDF if Word made one, otherwise the .docx shown as HTML."""
    from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
    if kind not in ("resume", "cover"):
        return PlainTextResponse("Not found", status_code=404)
    path = packet_file(one_job(jid), kind)
    if path is None:
        return PlainTextResponse("No document for this job yet.", status_code=404)
    pdf = path.with_suffix(".pdf")
    if pdf.exists():
        return FileResponse(pdf, media_type="application/pdf")
    if path.exists():
        try:
            import mammoth
            with open(path, "rb") as fh:
                body = mammoth.convert_to_html(fh).value
        except Exception as exc:  # noqa: BLE001
            return PlainTextResponse(f"Could not show the .docx ({exc.__class__.__name__}). Use Open folder.", status_code=500)
        return HTMLResponse("<html><body style='font-family:Times New Roman;font-size:11pt;max-width:8in;margin:24px'>"
                            "<p style='color:#a00;font-family:sans-serif'>No PDF yet: Word preview approximation.</p>" + body + "</body></html>")
    return PlainTextResponse("This document has not been built yet.", status_code=404)

COUNTER_HELP = {
    "new_today": "Jobs found today",
    "review": "Qualified jobs waiting for a packet, plus blocked ones that need a fix (missing link)",
    "ready": "Packets ready to apply, plus applications opened but not yet submitted",
    "followups": "Applications 7 to 21 days old: time for a short follow-up",
}


def index() -> None:
    ui.colors(primary="#1f4e79", positive="#2e7d32", negative="#b3261e")
    ui.page_title("Internship Control Center")
    seen = {"n": 0}
    busy_buttons: list = []

    # ---- header: scan status, Run Daily Scan, Stop, Quit
    with ui.header().classes("items-center justify-between bg-primary text-white px-4 py-2"):
        ui.label("Internship Control Center").classes("text-xl font-bold")
        status_chip = ui.label("").classes("text-sm bg-white/20 rounded-full px-3 py-1")
        with ui.row().classes("items-center gap-2"):
            scan_btn = ui.button("Run Daily Scan", icon="play_arrow", on_click=lambda: launch("scan")).props("color=white text-color=primary")
            scan_btn.tooltip("Finds new postings, scores them and builds packets. Never submits anything.")
            stop_btn = ui.button("Stop", icon="stop", on_click=lambda: stop_dialog.open()).props("outline color=white")
            ui.button("Quit", icon="power_settings_new", on_click=lambda: quit_dialog.open()).props("flat color=white")
    busy_buttons.append(scan_btn)

    with ui.tabs().classes("w-full max-w-6xl mx-auto") as tabs:
        t_today = ui.tab("Today", icon="today")
        t_home = ui.tab("Scan & tools", icon="build")
        t_jobs = ui.tab("Jobs", icon="work")
        t_docs = ui.tab("Documents", icon="description")
        t_apply = ui.tab("Apply", icon="send")
        t_unver = ui.tab("Not verified", icon="help_outline")
        t_net = ui.tab("Network", icon="groups")
    selected: dict = {}
    contact_sel: dict = {}
    with ui.tab_panels(tabs, value=t_today).classes("w-full max-w-6xl mx-auto") as panels:
        # ---------------- Today
        with ui.tab_panel(t_today):
            today_problem = ui.label("").classes("w-full whitespace-pre-wrap text-sm text-red-800 bg-red-50 border border-red-300 rounded p-2")
            today_problem.set_visibility(False)
            with ui.row().classes("w-full gap-4 items-stretch"):
                with ui.card().classes("flex-1 min-w-80"):
                    ui.label("New matches to review").classes("text-lg font-semibold")
                    today_new = ui.column().classes("w-full gap-1")
                with ui.card().classes("flex-1 min-w-80"):
                    ui.label("Ready to apply").classes("text-lg font-semibold")
                    today_ready = ui.label("").classes("text-4xl font-bold text-primary")
                    ui.button("Go to Apply", icon="send", on_click=lambda: panels.set_value(t_apply)).props("outline")
                    ui.separator()
                    ui.label("Follow-ups due").classes("text-lg font-semibold")
                    today_follow = ui.column().classes("w-full gap-1")
            with ui.row().classes("w-full gap-4 items-stretch"):
                with ui.card().classes("flex-1 min-w-80"):
                    ui.label("Coming up").classes("text-lg font-semibold")
                    today_events = ui.column().classes("w-full gap-1")
                    ui.label("Edit these under events: in config.yaml.").classes("text-xs text-gray-500")
                with ui.card().classes("flex-1 min-w-80"):
                    ui.label("Quick links").classes("text-lg font-semibold")
                    today_links = ui.column().classes("w-full gap-1")

        with ui.tab_panel(t_home):
            with ui.column().classes("w-full max-w-6xl mx-auto gap-4 p-4"):
                # ---- counters
                with ui.row().classes("w-full gap-4"):
                    labels: dict[str, ui.label] = {}
                    for key, title in (("new_today", "New today"), ("review", "To review"), ("ready", "Ready to apply"), ("followups", "Follow-ups due")):
                        with ui.card().classes("flex-1 min-w-40 items-center py-2").tooltip(COUNTER_HELP[key]):
                            labels[key] = ui.label("-").classes("text-4xl font-bold text-primary")
                            ui.label(title).classes("text-sm text-gray-600")
                ui.label("Nothing here ever clicks Submit or sends an email. You apply and send yourself.").classes("text-sm text-gray-600")
                health_row = ui.row().classes("gap-2")
                with ui.expansion("Health", icon="monitor_heart").classes("w-full"):
                    health_box = ui.column().classes("w-full gap-1 text-sm")
                    with ui.expansion("Last automatic run (6:40 AM)", icon="schedule").classes("w-full"):
                        auto_log = ui.label("").classes("whitespace-pre-wrap font-mono text-xs")

                with ui.row().classes("w-full gap-4 no-wrap items-start"):
                    # ---- tools
                    with ui.card().classes("w-72 shrink-0 gap-2"):
                        ui.label("Tools").classes("text-lg font-semibold")

                        def tool(text: str, icon: str, key: str, tip: str):
                            b = ui.button(text, icon=icon, on_click=lambda: launch(key)).props("outline align=left").classes("w-full")
                            b.tooltip(tip)
                            busy_buttons.append(b)

                        tool("Test setup", "fact_check", "test", "Checks Python packages, keys, profile, Chrome and Simplify. Changes nothing.")
                        tool("Sync Sheet now", "sync", "sync", "Pulls your edits from the Google Sheet and pushes local changes.")
                        tool("Make track resumes", "description", "resumes", "Builds the 3 track resumes (Gmail + Gateway copies) from master_profile.json.")
                        tool("Rebuild packets", "construction", "rebuild", "Rebuilds resume and cover letter for every Packet ready job. Never submits.")
                        tool("Find company links", "link", "resolve", "For LinkedIn/Indeed postings: finds the company's own "
                             "application page (Workday, Greenhouse, its careers site). Reads public pages only; never LinkedIn.")
                        tool("Simplify setup", "extension", "simplify", "One time: opens the apply window so you can add Simplify and sign in. Close that window when done.")
                        nb = ui.button("Network draft", icon="group", on_click=lambda: net_dialog.open()).props("outline align=left").classes("w-full")
                        nb.tooltip("Writes LinkedIn note, email, follow-up and thank-you drafts for one person. Nothing is sent.")
                        busy_buttons.append(nb)
                        ui.separator()
                        tool("Status summary", "summarize", "status", "Prints where everything stands.")
                        tool("Code self-test", "bug_report", "unit", "The offline test suite (same as TEST.bat). Run after any code change.")
                        tool("Re-check old jobs", "rule", "recheck", "Applies the current filters (skip words, Houston area, score 80) to jobs not yet opened or applied, and marks the ones that fail as Skipped with the reason. Never touches Filled or Applied jobs.")
                        if os.name == "nt":
                            ui.button("Open output folder", icon="folder_open", on_click=lambda: os.startfile(ROOT / "output")).props("flat align=left").classes("w-full")  # noqa: S606

                    # ---- live log
                    with ui.card().classes("flex-1 min-w-0"):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label("Live log").classes("text-lg font-semibold")
                            ui.button("Clear", icon="delete_sweep", on_click=lambda: log.clear()).props("flat dense")
                        log = ui.log(max_lines=3000).classes("w-full h-[28rem] font-mono text-xs")
                        for line in log_tail():
                            log.push(line)
                        if log_tail():
                            log.push("--- earlier activity above (pipeline.log) ---")

        # ---------------- Jobs
        with ui.tab_panel(t_jobs):
            with ui.row().classes("w-full items-center gap-4"):
                group = ui.select(list(GROUPS), value="Active", label="Show", on_change=lambda: refresh_tables()).classes("w-48")
                sel_label = ui.label("Click a row to pick a job.").classes("text-sm text-gray-600")
            grid = ui.aggrid({
                "columnDefs": [
                    {"field": "score", "headerName": "Score", "width": 90},
                    {"field": "company", "headerName": "Company", "filter": True},
                    {"field": "title", "headerName": "Role", "flex": 2, "filter": True},
                    {"field": "status_label", "headerName": "Status", "width": 190},
                    {"field": "location", "headerName": "Location"},
                    {"field": "found", "headerName": "Found", "width": 115},
                    {"field": "notes", "headerName": "Notes", "flex": 2, "tooltipField": "notes"},
                ],
                "rowData": [],
                "defaultColDef": {"resizable": True, "sortable": True},
            }).classes("w-full h-[30rem]")
            grid.on("cellClicked", lambda e: pick((e.args or {}).get("data") or {}))
            with ui.row().classes("gap-2"):
                ui.button("Open posting", icon="open_in_new", on_click=lambda: open_posting()).props("outline")
                ui.button("Preview documents", icon="visibility", on_click=lambda: show_docs(selected.get("id"), switch=True)).props("outline")
                for text, icon, flag, tip in (
                        ("Build packet", "construction", "--build", "Builds (or rebuilds) the resume and cover letter for this job."),
                        ("Skip", "block", "--skip", "Marks this job Skipped. Change it back in the Sheet if you change your mind."),
                        ("I submitted it", "task_alt", "--applied", "Marks Applied with today's date, sets a follow-up in 7 days, writes outreach drafts.")):
                    b = ui.button(text, icon=icon, on_click=(lambda t, f: (lambda: job_action(t, f)))(text, flag)).props("outline")
                    b.tooltip(tip)
                    busy_buttons.append(b)
            ui.label("Status changes made here go to the Google Sheet on the same run. Your My Notes column is never touched.").classes("text-sm text-gray-600")

        # ---------------- Documents
        with ui.tab_panel(t_docs):
            with ui.row().classes("w-full items-center gap-2"):
                doc_pick = ui.select({}, label="Job", with_input=True, on_change=lambda e: show_docs(e.value)).classes("w-[40rem]")
                ui.button("Open folder", icon="folder_open", on_click=lambda: open_folder(doc_pick.value)).props("flat")
            doc_note = ui.label("Pick a job to see its resume and cover letter.").classes("text-sm text-gray-700")
            doc_area = ui.row().classes("w-full no-wrap gap-2")
            with ui.expansion("Posting text", icon="article").classes("w-full"):
                posting = ui.label("").classes("whitespace-pre-wrap text-sm")

        # ---------------- Apply
        with ui.tab_panel(t_apply):
            with ui.card().classes("w-full bg-amber-50 border border-amber-400"):
                ui.label("You click Submit yourself. Nothing on this page submits or sends anything.").classes("text-lg font-bold text-amber-900")
                ui.label("Open the application, review every field, click Submit in that window, close the window, then press "
                         "I submitted it (or Not yet / Skip).").classes("text-sm")
            ui.label("Only applications the auto-apply is verified on are listed here: every question it can answer gets "
                     "filled and your resume + cover letter go into their own boxes. The rest are on the Not verified tab."
                     ).classes("text-sm text-gray-700")
            simplify_note = ui.label("").classes("text-sm")
            apply_pick = ui.select({}, label="Application", on_change=lambda e: show_apply(e.value)).classes("w-[40rem]")
            apply_info = ui.label("").classes("text-sm text-gray-700 whitespace-pre-wrap")
            with ui.row().classes("gap-2"):
                b_open = ui.button("Open application", icon="launch", on_click=lambda: open_application())
                b_open.tooltip("Opens this one application in the apply window and fills what it safely can. Only one at a time.")
                b_simp = ui.button("Use Simplify instead", icon="extension", on_click=lambda: simplify_instead()).props("outline")
                b_simp.tooltip("Opens the posting in your normal Chrome, where Simplify is installed.")
                b_done = ui.button("I submitted it", icon="task_alt", color="positive", on_click=lambda: apply_action("I submitted it", "--applied"))
                b_not = ui.button("Not yet", on_click=lambda: ui.notify("Left as it is. It stays in the queue.")).props("flat")
                b_skip = ui.button("Skip this job", icon="block", color="negative", on_click=lambda: apply_action("Skip", "--skip")).props("outline")
                busy_buttons.extend([b_open, b_done, b_skip])
            pend_card = ui.card().classes("w-full bg-yellow-50 border-2 border-yellow-500")
            pend_card.set_visibility(False)
            with pend_card:
                pend_head = ui.label("").classes("text-lg font-bold text-yellow-900")
                pend_area = ui.column().classes("w-full gap-1")
                with ui.row().classes("gap-2"):
                    ui.button("Continue", icon="play_arrow", on_click=lambda: pend_continue()).tooltip(
                        "Tell the paused Workday fill to go on (e.g. after you clicked the email verification link).")
            ui.label("Fill sheet (answers ready to paste)").classes("text-lg font-semibold mt-2")
            fill_view = ui.label("").classes("whitespace-pre-wrap font-mono text-xs bg-gray-50 p-2 rounded w-full")

        # ---------------- Not verified (Nov 24): ready jobs whose application system isn't verified yet
        with ui.tab_panel(t_unver):
            with ui.card().classes("w-full bg-gray-50 border border-gray-300"):
                ui.label("Not verified yet: the auto-apply hasn't been proven on these application systems.").classes("text-lg font-bold")
                ui.label("Apply by hand or with Simplify (Use Simplify instead), or press Open application as a test run: it never "
                         "submits, and a clean run moves the job to the Apply tab. If a test run pauses, answer on the Apply tab."
                         ).classes("text-sm")
            unver_label = ui.label("Click a row to pick a job.").classes("text-sm text-gray-600")
            unver_grid = ui.aggrid({
                "columnDefs": [
                    {"field": "score", "headerName": "Score", "width": 90},
                    {"field": "company", "headerName": "Company", "filter": True},
                    {"field": "title", "headerName": "Role", "flex": 2, "filter": True},
                    {"field": "ats", "headerName": "System", "width": 120, "filter": True},
                    {"field": "why", "headerName": "Why it's here", "flex": 3, "tooltipField": "why"},
                ],
                "rowData": [],
                "defaultColDef": {"resizable": True, "sortable": True},
            }).classes("w-full h-[26rem]")
            unver_sel: dict = {}
            unver_grid.on("cellClicked", lambda e: pick_unver((e.args or {}).get("data") or {}))
            with ui.row().classes("gap-2"):
                u_open = ui.button("Open application (test run)", icon="science", on_click=lambda: unver_open()).props("outline")
                u_open.tooltip("Fills what it safely can, never submits. A clean run (questions + both files) verifies this job.")
                ui.button("Use Simplify instead", icon="extension", on_click=lambda: unver_simplify()).props("outline")
                u_done = ui.button("I submitted it", icon="task_alt", color="positive",
                                   on_click=lambda: unver_action("I submitted it", "--applied"))
                u_skip = ui.button("Skip this job", icon="block", color="negative", on_click=lambda: unver_action("Skip", "--skip")).props("outline")
                busy_buttons.extend([u_open, u_done, u_skip])

        # ---------------- Network
        with ui.tab_panel(t_net):
            with ui.row().classes("w-full items-center gap-4"):
                ui.label("Prospects: companies that fit your background").classes("text-lg font-semibold")
                week_label = ui.label("").classes("text-sm text-gray-600")
                pros_sel = ui.label("Click a company to pick it.").classes("text-sm text-gray-600")
            pros_grid = ui.aggrid({
                "columnDefs": [
                    {"field": "fit", "headerName": "Fit", "width": 80},
                    {"field": "company", "headerName": "Company", "filter": True},
                    {"field": "group_label", "headerName": "Track", "filter": True, "width": 230},
                    {"field": "why", "headerName": "Why", "flex": 2, "tooltipField": "why"},
                    {"field": "last_contact", "headerName": "Last contact", "width": 125},
                ],
                "rowData": [],
                "defaultColDef": {"resizable": True, "sortable": True},
            }).classes("w-full h-[22rem]")
            pros_grid.on("cellClicked", lambda e: pick_prospect((e.args or {}).get("data") or {}))
            with ui.row().classes("gap-2"):
                b = ui.button("Draft cold email", icon="edit_note", on_click=lambda: open_cold())
                b.tooltip("An internship inquiry to one person there, built only from your verified profile. Nothing is sent.")
                busy_buttons.append(b)
                ui.button("Find UH alumni there", icon="person_search", on_click=lambda: open_in_chrome(
                    "https://www.linkedin.com/school/university-of-houston/people/?keywords=" + quote(pros_pick.get("company", "")))).props("outline")
                ui.button("Find recruiting contact", icon="travel_explore", on_click=lambda: open_in_chrome(
                    "https://www.google.com/search?q=" + quote(f'"{pros_pick.get("company", "")}" campus recruiting internship Houston contact'))).props("flat")
            ui.label("Best targets: a UH alum on the team you want (LinkedIn), or the campus recruiter. Edit the list in prospects.yaml. "
                     "Aim for about 5 a week.").classes("text-sm text-gray-600")
            ui.separator()
            with ui.row().classes("w-full items-center gap-4"):
                ui.label("Outreach queue: Claude drafts these, you approve").classes("text-lg font-semibold")
                queue_label = ui.label("").classes("text-sm text-gray-600")
                queue_sel = ui.label("").classes("text-sm text-gray-600")
            queue_grid = ui.aggrid({
                "columnDefs": [
                    {"field": "state", "headerName": "State", "width": 100},
                    {"field": "kind", "headerName": "Kind", "width": 110},
                    {"field": "company", "headerName": "Company", "filter": True},
                    {"field": "person", "headerName": "Person"},
                    {"field": "title", "headerName": "Title", "flex": 1},
                    {"field": "email", "headerName": "Email", "flex": 1},
                    {"field": "email_status", "headerName": "Email check", "width": 120},
                    {"field": "sender_label", "headerName": "Sends from", "width": 130},
                ],
                "rowData": [],
                "defaultColDef": {"resizable": True, "sortable": True},
            }).classes("w-full h-[16rem]")
            queue_grid.on("cellClicked", lambda e: pick_queue((e.args or {}).get("data") or {}))
            with ui.row().classes("gap-2"):
                b = ui.button("Find contacts", icon="person_search", on_click=lambda: open_find()).props("color=primary")
                b.tooltip("Hunter.io finds a recruiter or finance contact (or the email of a name you found on LinkedIn) and queues them. Nothing is sent.")
                busy_buttons.append(b)
                b = ui.button("Queue top 3 prospects", icon="playlist_add", on_click=lambda: run_job(
                    "Queue prospects", ["pipeline/contacts.py", "--auto", "3"])).props("outline")
                b.tooltip("Takes the best-ranked companies you haven't contacted and looks up one person each (3 Hunter lookups).")
                busy_buttons.append(b)
                b = ui.button("Add follow-ups", icon="replay", on_click=lambda: run_job(
                    "Queue follow-ups", ["pipeline/contacts.py", "--followups"])).props("outline")
                b.tooltip("Puts people you emailed 7+ days ago (no reply) in the queue: one follow-up each.")
                busy_buttons.append(b)
                b = ui.button("Sync sent emails", icon="sync_alt", on_click=lambda: run_job(
                    "Sync sent emails", ["pipeline/outreach.py", "--fold"])).props("outline")
                b.tooltip("Records emails sent through Claude or logged here in the Outreach tab. The daily scan does this too.")
                busy_buttons.append(b)
                ui.button("Log an email", icon="mark_email_read", on_click=lambda: logmail_dialog.open()).props("outline").tooltip(
                    "You emailed someone yourself (outside Claude)? Log it so follow-ups are tracked.")
                ui.button("Log an application", icon="assignment_turned_in", on_click=lambda: logapp_dialog.open()).props("outline").tooltip(
                    "You applied somewhere outside the pipeline (Handshake, Gateway, a company site)? Log it.")
                ui.button("Remove from queue", icon="delete_outline", on_click=lambda: queue_remove()).props("flat")
            ui.label("In Claude (this project): type /outreach to draft every queued email, read them, approve the ones you like, and Claude sends "
                     "them. Type /inbox to check for replies. Nothing is ever sent from this page.").classes("text-sm text-gray-600")
            ui.separator()
            ui.label("Your outreach").classes("text-lg font-semibold")
            with ui.row().classes("w-full items-center gap-4"):
                net_source = ui.label("").classes("text-sm text-gray-600")
                net_sel = ui.label("Click a row to pick a contact.").classes("text-sm text-gray-600")
            net_grid = ui.aggrid({
                "columnDefs": [
                    {"field": "drafted", "headerName": "Last touch", "width": 120},
                    {"field": "company", "headerName": "Company", "filter": True},
                    {"field": "contact", "headerName": "Contact"},
                    {"field": "type", "headerName": "Type", "width": 130},
                    {"field": "status", "headerName": "Status", "width": 110},
                    {"field": "follow_up", "headerName": "Next touch", "width": 120},
                    {"field": "due", "headerName": "", "width": 130},
                    {"field": "notes", "headerName": "My Notes", "flex": 1},
                ],
                "rowData": [],
                "defaultColDef": {"resizable": True, "sortable": True},
            }).classes("w-full h-[26rem]")
            net_grid.on("cellClicked", lambda e: pick_contact((e.args or {}).get("data") or {}))
            with ui.row().classes("gap-2"):
                b = ui.button("Refresh from Sheet", icon="sync", on_click=lambda: run_job("Refresh contacts", ["pipeline/outreach.py", "--export"]))
                busy_buttons.append(b)
                b = ui.button("Draft message", icon="edit", on_click=lambda: net_dialog.open()).props("outline")
                b.tooltip("LinkedIn note, email, follow-up and thank-you drafts for one person. Nothing is sent.")
                busy_buttons.append(b)
                ui.button("Open in Outlook", icon="outgoing_mail", on_click=lambda: contact_compose("outlook")).tooltip(
                    "Opens the draft, unsent, in your school Outlook. Read it, add the [bracket] line if any, click Send yourself.")
                ui.button("Open in Gmail", icon="mail", on_click=lambda: contact_compose("gmail")).props("outline")
                b = ui.button("I sent it", icon="mark_email_read", on_click=lambda: contact_status("Sent")).props("outline")
                b.tooltip("Sets Status to Sent in the Outreach tab and the next touch to 7 days from today.")
                busy_buttons.append(b)
                b = ui.button("I followed up", icon="replay", on_click=lambda: contact_status("Followed up")).props("outline")
                busy_buttons.append(b)
                b = ui.button("They replied", icon="forum", color="positive", on_click=lambda: contact_status("Replied")).props("outline")
                b.tooltip("Stops follow-up reminders. Book the call, then send the thank-you draft after it.")
                busy_buttons.append(b)
                ui.button("Show follow-up text", icon="reply", on_click=lambda: contact_followup()).props("outline")
                ui.button("Open drafts folder", icon="folder_open", on_click=lambda: contact_folder()).props("flat")
                ui.button("Find UH alumni", icon="person_search", on_click=lambda: open_in_chrome(contact_sel.get("alumni") or QUICK_LINKS[3][1])).props("flat")
            ui.label("Statuses live in the Sheet's Outreach tab. Change Contact or My Notes there; Refresh pulls them in.").classes("text-sm text-gray-600")


    # ---- dialogs
    with ui.dialog() as stop_dialog, ui.card():
        stop_text = ui.label("")
        with ui.row():
            ui.button("Keep running", on_click=stop_dialog.close).props("flat")
            ui.button("Stop it", color="negative", on_click=lambda: do_stop(stop_dialog))

    with ui.dialog() as quit_dialog, ui.card():
        quit_text = ui.label("Close the Control Center? Scheduled runs are not affected.")
        with ui.row():
            ui.button("Cancel", on_click=quit_dialog.close).props("flat")
            ui.button("Quit", color="negative", on_click=lambda: do_quit())

    with ui.dialog() as net_dialog, ui.card().classes("w-96"):
        ui.label("Network draft").classes("text-lg font-semibold")
        ui.label("Find a real person first (LinkedIn > University of Houston > Alumni). Nothing is sent.").classes("text-sm text-gray-600")
        company = ui.input("Company").classes("w-full")
        first = ui.input("Their first name (optional)").classes("w-full")
        last = ui.input("Their last name (optional)").classes("w-full")
        email = ui.input("Their email (optional)").classes("w-full")
        with ui.row():
            ui.button("Cancel", on_click=net_dialog.close).props("flat")
            ui.button("Make drafts", on_click=lambda: do_network()).props("color=primary")

    # ---- actions
    def launch(key: str) -> None:
        label, args = JOBS[key]
        why = RUNNER.start(label, args)
        if why:
            ui.notify(f"Not started: {why}.", type="warning")
        else:
            ui.notify(f"{label} started.", type="positive")

    def do_network() -> None:
        name = re.sub(r"[\x00-\x1f]", "", company.value or "").strip()
        if not name:
            ui.notify("Type a company first.", type="warning")
            return
        clean = lambda v, n: re.sub(r"[\x00-\x1f]", "", v or "").strip()[:n]  # noqa: E731
        why = RUNNER.start("Network draft", ["pipeline/outreach.py", "--network", f"--company={clean(name, 80)}",
                                              f"--first={clean(first.value, 40)}", f"--last={clean(last.value, 40)}",
                                              f"--email={clean(email.value, 120)}"])
        if why:
            ui.notify(f"Not started: {why}.", type="warning")
            return
        net_dialog.close()
        for field in (company, first, last, email):
            field.value = ""

    def do_stop(dialog) -> None:
        if RUNNER.running:
            RUNNER.stop()
            ui.notify("Stopping...", type="warning")
        else:
            held = lock_info()
            if held and is_python_process(held["pid"]):
                kill_tree(held["pid"])
                ui.notify("Stopping the running job...", type="warning")
        dialog.close()

    def do_quit() -> None:
        if RUNNER.running:
            RUNNER.stop()
        ui.notify("Control Center closed. You can close this tab.", type="info")
        app.shutdown()

    with ui.dialog() as text_dialog, ui.card().classes("w-[44rem]"):
        text_title = ui.label("").classes("text-lg font-semibold")
        text_body = ui.label("").classes("whitespace-pre-wrap text-sm")
        with ui.row():
            ui.button("Copy", icon="content_copy", on_click=lambda: (ui.clipboard.write(text_body.text), ui.notify("Copied.")))
            ui.button("Close", on_click=text_dialog.close).props("flat")

    pros_pick: dict = {}
    with ui.dialog() as cold_dialog, ui.card().classes("w-[30rem]"):
        cold_title = ui.label("Cold email").classes("text-lg font-semibold")
        ui.label("Pick a real person first (UH alum or campus recruiter). Nothing is sent: you open it in Outlook and click Send.").classes("text-sm text-gray-600")
        cold_first = ui.input("Their first name").classes("w-full")
        cold_last = ui.input("Their last name (optional)").classes("w-full")
        cold_email = ui.input("Their email (optional; you can add it in Outlook)").classes("w-full")
        with ui.row():
            ui.button("Cancel", on_click=cold_dialog.close).props("flat")
            ui.button("Make draft", on_click=lambda: do_cold()).props("color=primary")

    queue_pick: dict = {}
    with ui.dialog() as find_dialog, ui.card().classes("w-[30rem]"):
        ui.label("Find contacts").classes("text-lg font-semibold")
        ui.label("Company only: Hunter finds a recruiter or finance contact. Add their first and last name (from LinkedIn) to find that "
                 "person's email. Nothing is sent.").classes("text-sm text-gray-600")
        find_company = ui.input("Company").classes("w-full")
        find_first = ui.input("First name (optional)").classes("w-full")
        find_last = ui.input("Last name (optional)").classes("w-full")
        find_source = ui.select(SOURCE_CHOICES, value="Other", label="How you know them (UH alum, Bauer and career-fair contacts send from your school email)").classes("w-full")
        find_email = ui.input("Their email, if you already have it (skips the lookup)").classes("w-full")
        with ui.row():
            ui.button("Cancel", on_click=find_dialog.close).props("flat")
            ui.button("Find and queue", on_click=lambda: do_find()).props("color=primary")

    with ui.dialog() as logmail_dialog, ui.card().classes("w-[30rem]"):
        ui.label("Log an email you sent").classes("text-lg font-semibold")
        lm_company = ui.input("Company").classes("w-full")
        lm_contact = ui.input("Person").classes("w-full")
        lm_email = ui.input("Their email (optional)").classes("w-full")
        lm_sender = ui.select({"school": "School Outlook", "gmail": "Gmail"}, value="gmail", label="Sent from").classes("w-full")
        lm_kind = ui.select({"cold": "Cold email", "network": "Networking", "follow_up": "Follow-up", "after_apply": "After applying"},
                            value="cold", label="Kind").classes("w-full")
        lm_event = ui.select({"sent": "I sent it", "followed_up": "I followed up", "replied": "They replied", "closed": "Closed"},
                             value="sent", label="What happened").classes("w-full")
        lm_subject = ui.input("Subject (optional)").classes("w-full")
        lm_date = ui.input("Date (YYYY-MM-DD, blank = today)").classes("w-full")
        with ui.row():
            ui.button("Cancel", on_click=logmail_dialog.close).props("flat")
            ui.button("Log it", on_click=lambda: do_logmail()).props("color=primary")

    with ui.dialog() as logapp_dialog, ui.card().classes("w-[30rem]"):
        ui.label("Log an application").classes("text-lg font-semibold")
        ui.label("For applications made outside the pipeline. It becomes an Applied job (follow-up in 7 days).").classes("text-sm text-gray-600")
        la_company = ui.input("Company").classes("w-full")
        la_title = ui.input("Role title").classes("w-full")
        la_link = ui.input("Link (optional)").classes("w-full")
        la_date = ui.input("Date applied (YYYY-MM-DD, blank = today)").classes("w-full")
        with ui.row():
            ui.button("Cancel", on_click=logapp_dialog.close).props("flat")
            ui.button("Log it", on_click=lambda: do_logapp()).props("color=primary")

    def pick_prospect(row: dict) -> None:
        pros_pick.clear()
        pros_pick.update(row)
        pros_sel.text = f"Selected: {row.get('company')} (fit {row.get('fit')})" if row else ""

    def open_cold() -> None:
        if not pros_pick.get("company"):
            ui.notify("Click a company in the Prospects table first.", type="warning")
            return
        cold_title.text = f"Cold email: {pros_pick['company']}"
        cold_dialog.open()

    def do_cold() -> None:
        clean = lambda v, n: re.sub(r"[\x00-\x1f]", "", v or "").strip()[:n]  # noqa: E731
        why = RUNNER.start(f"Cold email: {pros_pick.get('company', '')}",
                           ["pipeline/outreach.py", "--network", "--cold", f"--company={clean(pros_pick.get('company'), 80)}",
                            f"--first={clean(cold_first.value, 40)}", f"--last={clean(cold_last.value, 40)}",
                            f"--email={clean(cold_email.value, 120)}"])
        if why:
            ui.notify(f"Not started: {why}.", type="warning")
            return
        cold_dialog.close()
        for f in (cold_first, cold_last, cold_email):
            f.value = ""
        ui.notify("Drafting. It appears under Your outreach in a moment; then use Open in Outlook.")

    def _c(v, n: int) -> str:
        return re.sub(r"[\x00-\x1f]", "", v or "").strip()[:n]

    def pick_queue(row: dict) -> None:
        queue_pick.clear()
        queue_pick.update(row)
        queue_sel.text = f"Selected: {row.get('company')} | {row.get('person')} ({row.get('state')})" if row else ""

    def open_find() -> None:
        find_company.value = pros_pick.get("company", "") or find_company.value
        find_dialog.open()

    def do_find() -> None:
        company = _c(find_company.value, 80)
        if not company:
            ui.notify("Type a company first.", type="warning")
            return
        first, last, mail = _c(find_first.value, 40), _c(find_last.value, 40), _c(find_email.value, 120)
        source = find_source.value if find_source.value in SOURCE_CHOICES else "Other"
        if mail:   # you already have the address: no lookup, no Hunter credit
            args = ["pipeline/contacts.py", "--add", company, f"--name={first} {last}".strip(), f"--email={mail}", f"--source={source}"]
        else:
            args = ["pipeline/contacts.py", f"--find={company}", f"--source={source}"] + ([f"--first={first}", f"--last={last}"] if first and last else [])
        why = RUNNER.start(f"Find contacts: {company}", args)
        if why:
            ui.notify(f"Not started: {why}.", type="warning")
            return
        find_dialog.close()
        for f in (find_company, find_first, find_last, find_email):
            f.value = ""

    def queue_remove() -> None:
        if not queue_pick.get("id"):
            ui.notify("Click a row in the queue first.", type="warning")
            return
        run_job(f"Remove from queue: {queue_pick.get('company', '')}", ["pipeline/contacts.py", f"--remove={queue_pick['id']}"])

    def do_logmail() -> None:
        company = _c(lm_company.value, 80)
        if not company:
            ui.notify("Type a company first.", type="warning")
            return
        why = RUNNER.start(f"Log email: {company}", ["pipeline/outreach.py", "--log-email", f"--company={company}", f"--contact={_c(lm_contact.value, 80)}",
                                                     f"--email={_c(lm_email.value, 120)}", f"--sender={lm_sender.value}", f"--kind={lm_kind.value}",
                                                     f"--event={lm_event.value}", f"--subject={_c(lm_subject.value, 150)}", f"--date={_c(lm_date.value, 10)}"])
        if why:
            ui.notify(f"Not started: {why}.", type="warning")
            return
        logmail_dialog.close()
        for f in (lm_company, lm_contact, lm_email, lm_subject, lm_date):
            f.value = ""

    def do_logapp() -> None:
        company, title = _c(la_company.value, 80), _c(la_title.value, 120)
        if not company or not title:
            ui.notify("Company and role title are both needed.", type="warning")
            return
        why = RUNNER.start(f"Log application: {company}", ["pipeline/jobctl.py", "--log-app", f"--company={company}", f"--title={title}",
                                                           f"--link={_c(la_link.value, 300)}", f"--date={_c(la_date.value, 10)}"])
        if why:
            ui.notify(f"Not started: {why}.", type="warning")
            return
        logapp_dialog.close()
        for f in (la_company, la_title, la_link, la_date):
            f.value = ""

    def contact_compose(service: str) -> None:
        if not contact_needed():
            return
        info = send_info(contact_sel["folder"])
        if not info:
            ui.notify("No draft found for this contact.", type="warning")
            return
        open_in_chrome(compose_url(info, service))
        ui.notify("Opened unsent. Read it, click Send yourself, then press I sent it here.")

    def contact_status(status: str) -> None:
        if contact_needed():
            run_job(f"{status}: {contact_sel.get('company', '')}", ["pipeline/outreach.py", f"--sent={contact_sel['folder']}", f"--status={status}"])

    def show_text(title: str, body: str) -> None:
        text_title.text, text_body.text = title, body
        text_dialog.open()

    def pick_contact(row: dict) -> None:
        contact_sel.clear()
        contact_sel.update(row)
        net_sel.text = f"Selected: {row.get('company')} | {row.get('contact')} ({row.get('status')})" if row else ""

    def contact_needed() -> bool:
        if not contact_sel.get("folder"):
            ui.notify("Click a contact in the table first.", type="warning")
            return False
        return True

    def contact_followup() -> None:
        if not contact_needed():
            return
        try:
            path = (Path(contact_sel["folder"]) / "3_FOLLOW_UP.txt").resolve()
            path.relative_to((ROOT / "output").resolve())
            show_text(f"Follow-up for {contact_sel.get('company')}", path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ui.notify("No follow-up draft found for this contact.", type="warning")

    def contact_folder() -> None:
        if not contact_needed():
            return
        try:
            path = Path(contact_sel["folder"]).resolve()
            path.relative_to((ROOT / "output").resolve())
        except (OSError, ValueError):
            path = None
        if path and path.exists() and os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            ui.notify("That drafts folder isn't on this computer.", type="warning")

    def refresh_today() -> None:
        problem = auto_problem()
        today_problem.text = problem
        today_problem.set_visibility(bool(problem))
        today_new.clear()
        with today_new:
            matches = new_matches()
            if not matches:
                ui.label("Nothing new in the last 3 days.").classes("text-sm text-gray-600")
            for r in matches:
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.label(f"{r['score']}  {r['company']} | {r['title']}").classes("text-sm flex-1 truncate")
                    ui.button("Review", on_click=partial(show_docs, r["id"], True)).props("flat dense")
        today_ready.text = str(len(job_rows("Ready to apply")))
        today_follow.clear()
        with today_follow:
            items = 0
            for j in applied_followups():
                items += 1
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.label(f"Applied {j['applied']}: {j['company']} | {j['title']}").classes("text-sm flex-1 truncate")
                    ui.button("Draft follow-up", on_click=partial(lambda jj: show_text(f"Follow-up for {jj['company']}", followup_text(jj)), j)).props("flat dense")
            for c in contact_rows():
                if c["due"]:
                    items += 1
                    with ui.row().classes("w-full items-center no-wrap"):
                        ui.label(f"Networking: {c['company']} | {c['contact']} (due {c['follow_up']})").classes("text-sm flex-1 truncate")
                        ui.button("Open", on_click=lambda: panels.set_value(t_net)).props("flat dense")
            if not items:
                ui.label("None due.").classes("text-sm text-gray-600")
        today_events.clear()
        with today_events:
            for left, when, what in upcoming_events():
                ui.label(f"{'Today' if left == 0 else f'In {left} days'} ({when}): {what}").classes("text-sm")
        today_links.clear()
        with today_links:
            sheet_url = contacts().get("sheet_url") or ""
            for name, url in QUICK_LINKS + ([("Google Sheet tracker", sheet_url)] if sheet_url else []):
                ui.button(name, icon="open_in_new", on_click=partial(open_in_chrome, url)).props("flat dense align=left no-caps")
            if not sheet_url:
                ui.label("The Sheet link appears after Refresh from Sheet on the Network tab.").classes("text-xs text-gray-500")

    def refresh_network() -> None:
        info = contacts()
        rows = contact_rows()
        net_grid.options["rowData"] = rows
        net_grid.update()
        pros_grid.options["rowData"] = prospect_rows()
        pros_grid.update()
        qrows = queue_rows()
        queue_grid.options["rowData"] = qrows
        queue_grid.update()
        n, cap = queue_week()
        waiting = sum(1 for r in qrows if r.get("state") == "queued")
        queue_label.text = (f"{waiting} waiting for /outreach in Claude. New people this week: {n} of {cap}."
                            + (f" {pending_events()} sent-email record(s) waiting to sync." if pending_events() else ""))
        week_label.text = f"Sent in the last 7 days: {sent_this_week(rows)} (aim for about 5)"
        net_source.text = f"From the {info.get('source', '?')}" + (f", {info['updated'].replace('T', ' ')}" if info.get("updated") else "")

    # ---- tab actions
    def run_job(label: str, args: list[str], extra_env: dict | None = None) -> None:
        why = RUNNER.start(label, args, extra_env)
        ui.notify(f"Not started: {why}." if why else f"{label} started. Watch the log on Scan & tools.", type="warning" if why else "positive")

    def pick(row: dict) -> None:
        selected.clear()
        selected.update(row)
        sel_label.text = f"Selected: {row.get('company')} | {row.get('title')} ({row.get('status_label', '')})" if row else ""

    def need_pick() -> str:
        jid = selected.get("id") or ""
        if not jid:
            ui.notify("Click a job in the table first.", type="warning")
        return jid

    def job_action(label: str, flag: str) -> None:
        jid = need_pick()
        if jid:
            run_job(f"{label}: {selected.get('company', '')}", ["pipeline/jobctl.py", flag, jid])

    def open_posting() -> None:
        url = selected.get("url") if selected else ""
        if not need_pick():
            return
        if not open_in_chrome(url):
            ui.notify("This job has no link. Add it in the Sheet.", type="warning")

    def open_folder(jid) -> None:
        path = packet_file(one_job(jid), "resume")
        if path and path.parent.exists() and os.name == "nt":
            os.startfile(path.parent)  # noqa: S606
        else:
            ui.notify("No packet folder for this job yet.", type="warning")

    def show_docs(jid, switch: bool = False) -> None:
        if not jid:
            if switch:
                ui.notify("Click a job in the table first.", type="warning")
            return
        job = one_job(jid)
        if not job:
            return
        if switch:
            panels.set_value(t_docs)
        if doc_pick.value != jid and jid in (doc_pick.options or {}):
            doc_pick.value = jid      # fires show_docs again with switch=False
            return
        doc_area.clear()
        if not packet_file(job, "resume"):
            doc_note.text = f"{job['company']} | {job['title']}: no packet built yet. Use Build packet on the Jobs tab."
            posting.text = job.get("description") or ""
            return
        doc_note.text = f"{job['company']} | {job['title']} | {STATUSES.get(job['status'], job['status'])}. Check: {job.get('notes') or 'no notes'}"
        stamp = int(time.time())
        with doc_area:
            for kind in ("resume", "cover"):
                ui.element("iframe").props(f'src="/doc/{jid}/{kind}?t={stamp}"').classes("flex-1 h-[56rem] border rounded bg-white")
        posting.text = side_text(job, "job_posting.txt") or job.get("description") or ""

    def show_apply(jid) -> None:
        job = one_job(jid)
        if not job:
            apply_info.text, fill_view.text = ("Nothing verified to apply right now. Other ready jobs are on the Not verified tab "
                                               "(apply by hand / Simplify, or run a test)."), ""
            return
        apply_info.text = (f"{job['company']} | {job['title']} | score {job['score']} | {STATUSES.get(job['status'], job['status'])}"
                           + (f" | {job['notes']}" if job.get("notes") else "") + link_line(job))
        fill_view.text = side_text(job, "FILL_SHEET.txt") or "The fill sheet is written when you open the application."

    def open_application() -> None:
        jid = apply_pick.value
        if not jid:
            ui.notify("Nothing to open.", type="warning")
            return
        run_job(f"Open application: {(one_job(jid) or {}).get('company', '')}",
                ["pipeline/fill.py", "--job", jid, "--limit", "1"], {"PIPELINE_GUI": "1"})

    pend_state = {"sig": None}

    def pend_save(label: str, field) -> None:
        val = str(field.value or "").strip()
        if not val:
            ui.notify("Type an answer first.", type="warning")
            return
        answers.queue_answer(label, val)
        ui.notify(f"Sent to the application and saved for next time: {label}")

    def pend_continue() -> None:
        answers.queue_answer("", cont=True)
        ui.notify("Continuing the Workday fill.")

    def refresh_pending() -> None:
        data = answers.read_pending()
        sig = (data.get("at"), data.get("waiting")) if data else None
        if sig == pend_state["sig"]:
            return
        pend_state["sig"] = sig
        pend_card.set_visibility(bool(data))
        pend_area.clear()
        if not data:
            return
        pend_head.text = f"Paused: {data.get('company')} | {data.get('waiting')}"
        with pend_area:
            for it in data.get("items") or []:
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(it["label"]).classes("text-sm font-semibold w-[28rem]")
                    if it["category"] in ("never", "eeo"):
                        ui.label("Yours: do this one on the page. " + (it.get("note") or "")).classes("text-sm text-gray-700")
                        continue
                    if it.get("options") and it["kind"] in ("dropdown", "radio", "checkgroup"):
                        fld = ui.select(it["options"], label="Answer").classes("w-72")
                    else:
                        fld = ui.input("Answer").classes("w-96") if it["kind"] != "textarea" else ui.textarea("Answer").classes("w-96")
                    ui.button("Save + fill", on_click=(lambda lab, f: (lambda: pend_save(lab, f)))(it["label"], fld)).props("outline")
                    if it.get("note"):
                        ui.label(it["note"]).classes("text-xs text-gray-600")

    def simplify_instead() -> None:
        job = one_job(apply_pick.value)
        if not job or not open_in_chrome(resolver.target(job)[0] if job else None):   # company link when found
            ui.notify("This job has no link.", type="warning")
        else:
            ui.notify("Opened in your normal Chrome. Come back and press I submitted it when you're done.")

    def pick_unver(row: dict) -> None:
        unver_sel.clear()
        unver_sel.update(row or {})
        unver_label.text = (f"Picked: {row.get('company')} | {row.get('title')}" + link_line(row)) if row else "Click a row to pick a job."

    def unver_open() -> None:
        jid = unver_sel.get("id")
        if not jid:
            ui.notify("Pick a job first.", type="warning")
            return
        run_job(f"Open application (test run): {unver_sel.get('company', '')}",
                ["pipeline/fill.py", "--job", jid, "--limit", "1"], {"PIPELINE_GUI": "1"})

    def unver_simplify() -> None:
        job = one_job(unver_sel.get("id"))
        if not job or not open_in_chrome(resolver.target(job)[0]):
            ui.notify("Pick a job with a link first.", type="warning")
        else:
            ui.notify("Opened in your normal Chrome. Come back and press I submitted it when you're done.")

    def unver_action(label: str, flag: str) -> None:
        jid = unver_sel.get("id")
        if jid:
            run_job(f"{label}: {unver_sel.get('company', '')}", ["pipeline/jobctl.py", flag, jid])
        else:
            ui.notify("Pick a job first.", type="warning")

    def apply_action(label: str, flag: str) -> None:
        jid = apply_pick.value
        if jid:
            run_job(f"{label}: {(one_job(jid) or {}).get('company', '')}", ["pipeline/jobctl.py", flag, jid])

    def refresh_tables() -> None:
        rows = job_rows(group.value)
        grid.options["rowData"] = rows
        grid.update()
        if selected.get("id"):
            fresh = next((r for r in rows if r["id"] == selected["id"]), None)
            pick(fresh or {})
        with_docs = {r["id"]: f"{r['company']} | {r['title']}" for r in job_rows("All") if r.get("resume")}
        doc_pick.set_options(with_docs, value=doc_pick.value if doc_pick.value in with_docs else None)
        ok_rows, other_rows = verified.split(job_rows("Ready to apply"))
        ready = {r["id"]: f"{r['company']} | {r['title']} ({r['score']})" for r in ok_rows}
        unver_grid.options["rowData"] = [{k: r.get(k) for k in ("id", "company", "title", "score", "ats", "why", "url")}
                                         for r in other_rows]
        unver_grid.update()
        keep = apply_pick.value if apply_pick.value in ready else next(iter(ready), None)
        apply_pick.set_options(ready, value=keep)
        show_apply(keep)
        refresh_today()
        refresh_network()
        simplify_note.text = ("Simplify is installed in the apply window." if simplify_ready() else
                              "Simplify isn't in the apply window. Use Simplify instead opens the posting in your normal Chrome, where Simplify already is.")

    # ---- refresh
    state = {"count_at": 0.0, "was_busy": False, "seen_finish": time.time()}

    def refresh_counters() -> None:
        c = counters()
        if c is None:
            return   # database busy for a moment; keep the old numbers
        for key, lab in labels.items():
            lab.text = str(c[key])
        state["last_scan"] = c["last_scan"]
        state["count_at"] = time.time()

    def tick() -> None:
        fresh, seen["n"] = RUNNER.new_lines(seen["n"])
        for line in fresh:
            log.push(line)
        held = lock_info()
        busy = RUNNER.running or held is not None
        if RUNNER.running:
            status_chip.text = f"Running: {RUNNER.name} ({fmt_age(time.time() - RUNNER.started)})"
            stop_text.text = f"Stop {RUNNER.name}? Work done so far stays; the next run picks up the rest."
        elif held:
            status_chip.text = f"Running: {held['job'] or 'a job'} (started outside this page, {fmt_age(time.time() - held['started'])})"
            stop_text.text = f"Stop {held['job'] or 'the running job'}? It was started by the scheduled run or another window."
        else:
            last = state.get("last_scan") or 0
            status_chip.text = "Idle. Last scan: " + (datetime.fromtimestamp(last).strftime("%a %b %d, %I:%M %p") if last else "never")
        stop_btn.set_enabled(busy)
        for b in busy_buttons:
            b.set_enabled(not busy)
        if RUNNER.finished_at > state.get("seen_finish", 0):
            state["seen_finish"] = RUNNER.finished_at
            if RUNNER.last_code not in (0, None, 3) and not busy:
                ui.notify(f"{RUNNER.name} hit a problem. The log on Scan & tools says why; nothing was submitted or sent.",
                          type="negative", timeout=10000)
        if state["was_busy"] and not busy:
            refresh_tables()
            state["sig"] = data_signature()
        elif not busy and time.time() - state.get("sig_at", 0) > 15:
            state["sig_at"] = time.time()
            sig = data_signature()
            if sig != state.get("sig"):          # the scheduled run (or the Sheet) changed something: show it
                state["sig"] = sig
                refresh_tables()
        if state["was_busy"] and not busy or time.time() - state["count_at"] > 10:
            refresh_counters()
        state["was_busy"] = busy

    def paint_health() -> None:
        health_box.clear()
        with health_box:
            last = state.get("last_scan") or 0
            for name, value in (
                    ("Last scan", datetime.fromtimestamp(last).strftime("%a %b %d, %I:%M %p") if last else "never"),
                    ("Last Sheet sync", last_sheet_sync()),
                    ("Scheduled 6:40 AM run", scheduled_task()),
                    ("Simplify in the apply window", "installed" if simplify_ready() else "not installed (use Use Simplify instead on the Apply tab)"),
                    ("Submit mode", f"{CONFIG.get('submit_mode', 'manual')} (this page never submits either way)")):
                ui.label(f"{name}: {value}")
        when, text = last_auto_run()
        auto_log.text = f"{when}\n\n{text}" if text else when
        health_row.clear()
        with health_row:
            for name, ok in health().items():
                ui.chip(f"{name}: {'present' if ok else 'missing'}", icon="check_circle" if ok else "error",
                        color="positive" if ok else "negative").props("outline dense")

    refresh_counters()
    refresh_tables()
    paint_health()
    tick()
    ui.timer(1.0, tick)
    ui.timer(1.5, refresh_pending)   # phase 2: questions a paused Workday fill is waiting on
    ui.timer(30.0, paint_health)
    ui.timer(300.0, refresh_today)


def already_running() -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


if __name__ in {"__main__", "__mp_main__"}:
    if already_running():
        webbrowser.open(URL)   # a second click on the shortcut just brings the page up again
        sys.exit(0)
    # reload=False on purpose: auto-reload breaks subprocesses on Windows. 127.0.0.1 = this computer only.
    ui.run(index, host="127.0.0.1", port=PORT, reload=False, show=True, title="Internship Control Center", favicon="🧭")
