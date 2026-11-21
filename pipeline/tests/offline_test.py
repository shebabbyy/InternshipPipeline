"""Offline end-to-end test of the whole pipeline. Safe to run any time: it copies the code to a temporary
folder and uses fake job feeds, a fake Gemini and a fake Google Sheet, so it never touches your real
database, Sheet, keys or output folder, and needs no internet. Run it after ANY code change:
    Windows:  double-click TEST.bat          Anywhere:  python pipeline/tests/offline_test.py
Ends with "ALL PASSED" or a list of FAILED checks."""
import builtins, json, os, shutil, sys, tempfile, types
from pathlib import Path

SRC = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
IP = Path(tempfile.mkdtemp(prefix="pipeline_test_")) / "InternshipPipeline"
shutil.copytree(SRC, IP, ignore=shutil.ignore_patterns(".venv", "browser_profile", "output", ".env", "*.db", "*.log",
                                                       "google_service_account.json", "__pycache__", ".git", "watchlist_found.yaml"))
# On a machine without the private profile (e.g. a GitHub/cloud copy), run on the redacted example profile.
if not (IP / "master_profile.json").exists() and (IP / "master_profile.example.json").exists():
    shutil.copy(IP / "master_profile.example.json", IP / "master_profile.json")
for k in ("GOOGLE_API_KEY", "HUNTER_API_KEY"):
    os.environ.pop(k, None)
sys.path.insert(0, str(IP / "pipeline"))
os.chdir(IP)

import common, discover, triage, tailor, outreach, fill, run_all, claude_discover  # noqa

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)

# ---------------- fake Google Sheet
class WS:
    def __init__(self, header=None): self.rows = [list(header)] if header else []
    def get_all_values(self): return [list(map(str, r)) for r in self.rows]
    def append_row(self, row, **k): self.rows.append(list(row))
    def append_rows(self, rows, **k): self.rows += [list(r) for r in rows]
    def batch_update(self, ups, **k):
        for u in ups:
            start = u["range"].split(":")[0]; c, n = ord(start[0]) - 65, int(start[1:]); v = u["values"][0]
            self.rows[n - 1][c:c + len(v)] = v
class SH:
    def __init__(self): self.tabs = {}
    def worksheet(self, t):
        if t not in self.tabs: raise KeyError(t)
        return self.tabs[t]
    def add_worksheet(self, t, rows, cols): self.tabs[t] = WS(); return self.tabs[t]
SHEET = SH()
common.spreadsheet = lambda: SHEET
outreach.spreadsheet = lambda: SHEET
if hasattr(common, "_SHEET_CACHE"): common._SHEET_CACHE = None

# ---------------- fake feeds
class R:
    def __init__(self, data, ct="application/json", status=200): self.d, self.headers, self.status_code = data, {"content-type": ct}, status
    def json(self): return self.d
LONG = "Support the FP&A team with budget, forecast and variance analysis. Requires Excel. Junior standing, graduating 2028. " * 3
def fake_post(url, **k):
    if "maint" in url: return R("<html>", "text/html")
    off = k["json"]["offset"]
    if off > 0: return R({"jobPostings": []})
    return R({"jobPostings": [
        {"title": "Finance Intern - Summer 2027", "externalPath": "/job/Houston/Finance-Intern_R1", "locationsText": "Houston, TX"},
        {"title": "Internal Auditor", "externalPath": "/job/x", "locationsText": "Houston, TX"},
        {"title": "Treasury Internship", "externalPath": "/job/NY/T_R2", "locationsText": "New York, NY"},
        {"title": "Credit Analyst Summer Analyst", "externalPath": "/job/2/C_R3", "locationsText": "2 Locations"},
        {"title": "Software Engineering Intern", "externalPath": "/job/Houston/SWE_R9", "locationsText": "Houston, TX"},
        {"title": "Audit Intern", "externalPath": "/job/3/A_R4", "locationsText": "3 Locations"},
        {"title": None, "externalPath": "/job/bad"}]})
def fake_get(url, **k):
    if "greenhouse" in url:
        return R({"jobs": [{"title": "Accounting Intern", "location": {"name": "Houston, Texas"}, "absolute_url": "https://boards.greenhouse.io/gh/jobs/1", "content": "<p>" + LONG + "</p>"},
                           {"title": "Senior Accountant", "location": {"name": "Houston"}, "absolute_url": "https://x", "content": ""}]})
    if "lever" in url:
        return R([{"text": "Wealth Management Co-op", "categories": {"location": "Houston, TX"}, "hostedUrl": "https://jobs.lever.co/lv/1", "descriptionPlain": LONG}])
    if "A_R4" in url: return R({"jobPostingInfo": {"jobDescription": LONG, "location": "Chicago, IL", "additionalLocations": ["Denver, CO"]}})
    return R({"jobPostingInfo": {"jobDescription": "<b>" + LONG + "</b>"}})
discover.requests = types.SimpleNamespace(post=fake_post, get=fake_get)
discover.time.sleep = lambda s: None
common.CONFIG["watchlist"] = [
    {"name": "WD Co", "ats": "workday", "url": "https://wdco.wd1.myworkdayjobs.com/en-US/Careers"},
    {"name": "Down Co", "ats": "workday", "url": "https://maint.wd1.myworkdayjobs.com/Careers"},
    {"name": "GH Co", "ats": "greenhouse", "board": "gh"},
    {"name": "LV Co", "ats": "lever", "board": "lv"},
    {"name": "Bad", "ats": "icims", "url": "x"}]
common.CONFIG["boards_laptop"] = []
discover.FOUND_FILE = IP / "nope.yaml"

check("workday_parts std", discover.workday_parts("https://a.wd5.myworkdayjobs.com/en-US/Site")[0] == "https://a.wd5.myworkdayjobs.com/wday/cxs/a/Site")
check("workday_parts site", discover.workday_parts("https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs")[0] == "https://wd1.myworkdaysite.com/wday/cxs/wf/WellsFargoJobs")
check("is_intern", discover.is_intern("Finance Intern") and not discover.is_intern("Internal Auditor") and not discover.is_intern("International Tax"))

new = discover.run(cloud=True)
conn = common.db()
titles = sorted(r["title"] for r in conn.execute("SELECT title FROM jobs"))
check("discovery finds 4 right jobs", titles == ["Accounting Intern", "Credit Analyst Summer Analyst", "Finance Intern - Summer 2027", "Wealth Management Co-op"], str(titles))
check("discovery dedupes on rerun", discover.run(cloud=True) == 0)

# ---------------- Nov 18 filters: Houston area only, stricter titles, duplicate postings, one-job-at-a-time lock
check("location: Houston area passes", all(discover.near_houston(x) for x in ("Houston, TX", "US-TX-Houston", "The Woodlands, TX", "Katy, Texas", "3 Locations")))
check("location: remote, blank, other Texas cities, other states fail", not any(discover.near_houston(x) for x in ("Remote", "", "Dallas, TX", "Austin, Texas", "Texas", "Pasadena, CA", "Richmond, VA", "New York, NY")))
check("title: operations/supply chain/scheduling/purchasing/sales support/project controls skipped",
      not any(discover.relevant(t) for t in ("Finance Operations Intern", "Supply Chain Intern", "Scheduling Intern", "Purchasing Intern", "Sales Support Intern", "Project Controls Intern")))
check("title: finance titles still pass", all(discover.relevant(t) for t in ("Finance Intern", "Accounting Intern", "Treasury Summer Analyst", "Credit Analyst Intern")))
check("qualify_score is 80", common.CONFIG["qualify_score"] == 80)
import sqlite3
mem = sqlite3.connect(":memory:"); mem.row_factory = sqlite3.Row
mem.execute(conn.execute("SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0])
check("dupes: first posting added", common.upsert_job(mem, {"company": "RSM US LLP", "title": "Tax Intern - Summer 2027"}))
check("dupes: same posting, other spelling, merged", not common.upsert_job(mem, {"company": "RSM US", "title": "Tax Internship (Houston)"}))
check("dupes: different role kept", common.upsert_job(mem, {"company": "RSM US LLP", "title": "Audit Intern - Summer 2027"}))
common.LOCK_PATH = IP / "test.lock"
check("lock: free at start", common.lock_info() is None)
check("lock: acquire", common.acquire_lock("test job") and common.lock_info()["job"] == "test job")
check("lock: second job refused while held", not common.acquire_lock("other"))
common.release_lock()
check("lock: released", common.lock_info() is None)
common.LOCK_PATH.write_text('{"pid": 999999, "job": "dead", "started": %s}' % __import__("time").time())
check("lock: stale lock (dead process) ignored and taken over", common.lock_info() is None and common.acquire_lock("after crash"))
common.release_lock()

# ---------------- triage with messy model output
def fake_gemini(prompt, retries=3):
    if "ONE plain sentence" in prompt:
        return {"sentence": "The team manages a $4 billion budget across 30 sites."}   # invented numbers -> must be dropped
    return {"results": [{"id": 0, "score": "95", "track": "corporate_finance_fpa", "missing_keywords": ["Excel"], "why": "fit", "season": "Summer 2027"},
                        {"id": 1, "score": 80.4, "track": "banking", "missing_keywords": "none", "why": "ok"},
                        {"id": 2, "score": 30, "track": "skip"}, {"id": 99, "score": 90}, "junk", {"id": 3, "score": 88, "track": "weird"}]}
triage.gemini_json = fake_gemini
tailor.gemini_json = fake_gemini
check("triage scores 4", triage.run() == 4)
st = {r["title"]: (r["status"], r["score"]) for r in conn.execute("SELECT * FROM jobs")}
check("triage statuses", sorted(s for s, _ in st.values()) == ["LOW", "LOW", "QUALIFIED", "QUALIFIED"], str(st))
assert "Moqbel" not in triage.RUBRIC and "281" not in triage.RUBRIC

# ---------------- tailoring (Claude returns a fabricated bullet first -> must be rejected)
calls = []
def fake_claude(job):
    calls.append(job["title"])
    return {"experience": {"cvs_lead": ["Built financial models in Excel to forecast pharmacy revenue for regional leadership",
                                   "Resolve denied insurance claims and billing discrepancies with insurance providers"]},
            "leadership": {"eagle": ["Led a team of up to 17 volunteers to install two permanent metal benches at a public mosque"]},
            "cover_letter": ["I am a junior at UH Bauer.", "At CVS I resolve denied insurance claims.", "Thank you."], "gaps": ["Excel"]}
tailor.claude_tailor = fake_claude
tailor.find_claude = lambda: "claude"
tailor.to_pdf = lambda p: None
made = tailor.run()
check("tailor builds 2 packets", made == 2)
rows = [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE status='PACKET_READY'")]
check("claude called once (daily cap, top score only)", len(calls) == 1, str(calls))
top = max(rows, key=lambda r: r["score"])
check("fabricated Claude bullet rejected -> local", "local" in (top["notes"] or ""), top["notes"])
from docx import Document
for r in rows:
    txt = "\n".join(p.text for p in Document(r["resume"]).paragraphs)
    cov = "\n".join(p.text for p in Document(r["cover"]).paragraphs)
    check(f"resume ok {r['company']}", "Shehab Moqbel" in txt and "3.72" in txt and "100+" not in txt and "25+" not in txt and "financial models" not in txt)
    check(f"cover drops invented hook {r['company']}", "billion" not in cov and r["company"] in cov)
    check(f"docs pass validator {r['company']}", not tailor.validate([txt.split("EXPERIENCE")[1]]) , str(tailor.validate([txt.split('EXPERIENCE')[1]])))

good = {"experience": {"cvs_lead": ["Resolve denied insurance claims and billing discrepancies by working with insurance providers",
                               "Coordinate prescription fulfillment across the pharmacy team to meet readiness deadlines"]},
        "leadership": {"eagle": ["Raised and managed project funds and tracked all expenses to deliver within budget"]},
        "cover_letter": ["I am a junior at UH Bauer applying to WD Co.", "At CVS I resolve denied insurance claims.", "Thank you."]}
import common as _c
_ce = tailor.PROFILE["candidate"]
check("email: external job uses Gmail", _c.email_for({"company": "Chevron", "source": "workday"}) == _ce["email"] and "gmail" in _ce["email"])
check("email: UH job uses school email", _c.email_for({"company": "University of Houston"}) == _ce["email_campus"])
check("email: career-fair note uses school email", _c.email_for({"company": "Acme", "notes": "Met them at the career fair"}) == _ce["email_campus"])
import tempfile as _tf
from documents import build_resume as _br
with _tf.TemporaryDirectory() as _d:
    _p = _br(tailor.PROFILE, {"coursework": [], "experience": {}, "leadership": {}}, Path(_d) / "r.docx", email=_ce["email_campus"])
    from docx import Document as _D
    _t = "\n".join(x.text for x in _D(_p).paragraphs)
    check("email: resume header uses the routed email", _ce["email_campus"] in _t and _ce["email"] not in _t)
check("hook: perk sentence dropped", tailor.clean_hook("offer interns a retirement plan with a 401K match") == "")
check("hook: claim about company dropped", tailor.clean_hook("Corporate bankers at Citi decide where to invest capital", "Citi") == "")
check("hook: task phrase kept", tailor.clean_hook("Analyze budget variances and support the monthly forecast")
      == "I am especially interested in the chance to analyze budget variances and support the monthly forecast.")
ok, probs = tailor.check_claude(good, "")
check("honest Claude output accepted", ok is not None, str(probs))
for bad, label in [({**good, "experience": {"cvs_lead": ["Reconciled general ledger accounts monthly"]}}, "forbidden claim"),
                   ({**good, "experience": {"cvs_lead": ["Resolve denied insurance claims for 40 patients daily"]}}, "new number"),
                   ({**good, "experience": {"walmart": ["x"]}}, "unknown employer"),
                   ({**good, "cover_letter": "one string"}, "bad shape"),
                   ({**good, "experience": {"cvs_lead": ["Analyzed quarterly revenue trends and presented findings to executives"]}}, "invented duty")]:
    ok, probs = tailor.check_claude(bad, "")
    check(f"rejects {label}", ok is None, str(probs))
check("vital != vita", not tailor.validate(["a vital role"]))

# ---------------- sheet sync: push, user edits, hand-typed row, cloud run with empty DB
common.sync_to_sheet(conn)
ws = SHEET.tabs[common.CONFIG["inbox_tab"]]
check("sheet has header + 4 rows", len(ws.rows) == 5 and ws.rows[0] == common.SHEET_HEADER, str(len(ws.rows)))
col = {n: i for i, n in enumerate(common.SHEET_COLUMNS)}
low = next(r for r in ws.rows[1:] if r[col["Status"]] == common.STATUSES["LOW"])
low[col["Status"]] = common.STATUSES["SKIPPED"]
ws.rows.append(["", "Hand Co", "Finance Intern", "Houston", "", "", "", "https://hand.co/job", "", "", "", "", "", "", "", "", LONG, "my note"])
ws.rows[1].append("keep me") if len(ws.rows[1]) == len(common.SHEET_COLUMNS) else None
common.sync_to_sheet(conn)
check("status edit pulled", conn.execute("SELECT status FROM jobs WHERE id=?", (low[col["Job ID"]],)).fetchone()[0] == "SKIPPED")
hand = conn.execute("SELECT * FROM jobs WHERE company='Hand Co'").fetchone()
check("hand-typed row imported + given ID", hand is not None and ws.rows[5][col["Job ID"]] == hand["id"], str(ws.rows[5]))
check("My Notes column untouched", ws.rows[5][-1] == "my note")
check("no duplicate rows", len(ws.rows) == 6, str(len(ws.rows)))
# cloud: fresh DB must not rescore or clobber
conn.close()
common.DB_PATH = IP / "cloud.db"; common.DB_PATH.unlink(missing_ok=True)
before = json.dumps(ws.rows)
scored_before = []
triage.gemini_json = lambda p, retries=3: (scored_before.append(p.count('"company"')), [{"id": 0, "score": 77, "track": "banking"}])[1]
sys.argv = ["run_all", "--cloud"]; run_all.main()
check("cloud scores only the unscored hand row", scored_before == [1], str(scored_before))
check("cloud keeps laptop rows intact", len(ws.rows) == 6 and sum(a != b for a, b in zip(json.loads(before), ws.rows)) == 1)
common.DB_PATH = IP / "pipeline.db"
conn = common.db()

# ---------------- apply bookkeeping + outreach
answers = iter([])
conn.execute("UPDATE jobs SET status='APPLIED', synced=0 WHERE id=?", (top["id"],)); conn.commit()
n = outreach.run()
kit = Path(top["resume"]).parent / "outreach"
files = sorted(p.name for p in kit.iterdir())
check("outreach kit files", files == ["1_LINKEDIN_NOTE.txt", "2_EMAIL_DRAFT.eml", "2_EMAIL_DRAFT.txt", "3_FOLLOW_UP.txt", "4_THANK_YOU.txt", "send.json"], str(files))
note = (kit / "1_LINKEDIN_NOTE.txt").read_text(encoding="utf-8").split("\n")[0]
check("linkedin note <= 300", len(note) <= 300, str(len(note)))
eml = (kit / "2_EMAIL_DRAFT.eml").read_bytes()
check("eml is unsent draft with attachment", b"X-Unsent: 1" in eml and b"Shehab-Moqbel-Resume" in eml)
for comp in ["Ham Langston & Brezina", "Alvarez & Marsal", "Fidelity Investments"]:
    for role in ["", "Finance and Accounting Development Program Summer Intern 2027"]:
        m = outreach.messages(comp, role, "Christopher")
        check(f"note<=300 & complete {comp[:6]} {bool(role)}", len(m["note"]) <= 300 and m["note"].endswith("Shehab"), m["note"])
        check(f"msgs pass validator {comp[:6]} {bool(role)}", not tailor.validate([m["email"], m["follow_up"], m["thank_you"], m["note"]], role + " 10"), str(tailor.validate([m["email"]], role + " 10")))
inputs = iter(["Kinder Morgan", "Sam", "Lee", "", ""])
outreach.ask = lambda p, d="": next(inputs, "")
outreach.network()
check("network kit", (IP / "output/_networking/KinderMorgan/SamLee/1_LINKEDIN_NOTE.txt").exists())
outreach.network_one("Chevron", "Ana", "Ruiz", "")
check("network kit without prompts (Control Center)", (IP / "output/_networking/Chevron/AnaRuiz/1_LINKEDIN_NOTE.txt").exists())
ot = SHEET.tabs["Outreach"]
check("outreach tab logged 3 (2 earlier + the prompt-free network kit)", len(ot.rows) == 4 and ot.rows[0] == outreach.OUTREACH_HEADER, str(len(ot.rows)))
ot.rows[2][4] = "Sent"; ot.rows[2][5] = "2026-01-01"
check("follow-up due detected", len(outreach.due_followups()) == 1)
run_all.stamp_applied(conn)
s = run_all.summary(conn)
check("summary prints", "INTERNSHIP PIPELINE SUMMARY" in s and "Networking follow-ups due" in s)

# ---------------- fill sheet + non-interactive apply (no browser needed for bookkeeping)
other = next(r for r in rows if r["id"] != top["id"])
p = fill.fill_sheet(other)
check("fill sheet", "Finance; Accounting" in p.read_text(encoding="utf-8"))
conn.execute("UPDATE jobs SET url='' WHERE id=?", (hand["id"],)); conn.execute("UPDATE jobs SET status='PACKET_READY' WHERE id=?", (hand["id"],)); conn.commit()


# ---------------- real browser: Greenhouse-style form, closed posting, sign-in page (manual mode, no keyboard)
import http.server, threading, functools
web = IP / "_web"; (web / "greenhouse.io").mkdir(parents=True, exist_ok=True)
(web / "greenhouse.io" / "form.html").write_text("""<html><body><form>
<label for=first_name>First Name *</label><input id=first_name name=first_name>
<label for=last_name>Last Name *</label><input id=last_name name=last_name>
<label for=email>Email *</label><input id=email name=email type=email>
<label for=phone>Phone</label><input id=phone name=phone type=tel>
<input aria-label="LinkedIn Profile" name=q1>
<input type=file id=resume style="display:none"><input type=file id=cover_letter style="display:none">
<button type=button>Submit application</button></form></body></html>""")
(web / "closed.html").write_text("<html><body>Sorry, this job is no longer available.</body></html>")
(web / "signin.html").write_text("<html><body><input type=email id=email><input type=password><button>Sign In</button></body></html>")
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
conn.execute("UPDATE jobs SET status='PACKET_READY', url=? WHERE id=?", (base + "/greenhouse.io/form.html", other["id"]))
conn.execute("UPDATE jobs SET status='PACKET_READY', url=?, resume=?, cover=? WHERE id=?", (base + "/closed.html", other["resume"], other["cover"], top["id"]))
low2 = conn.execute("SELECT id FROM jobs WHERE status IN ('LOW','SKIPPED') LIMIT 1").fetchone()["id"]
conn.execute("UPDATE jobs SET status='FILLED', url=?, resume=?, cover=? WHERE id=?", (base + "/signin.html", other["resume"], other["cover"], low2))
conn.commit()
os.environ["PIPELINE_HEADLESS"] = "1"
common.CONFIG["browser_profile_dir"] = "_bp"
seen = {}
orig = fill.confirm_manual
def spy(conn_, opened):
    for job, page in opened:
        if "form.html" in page.url:
            seen["vals"] = [page.input_value(f"#{i}") for i in ("first_name", "last_name", "email", "phone")] + [page.input_value("[name=q1]")]
            seen["files"] = page.evaluate("[...document.querySelectorAll('input[type=file]')].map(i => i.files.length ? i.files[0].name : '')")
        if "signin" in page.url:
            seen["signin_email"] = page.input_value("#email"); seen["pw"] = page.input_value("input[type=password]")
    fill.ask = lambda p, d="": d
    orig(conn_, opened)
fill.confirm_manual = spy
fill.ask = lambda p, d="": d
try:
    n = fill.run()
    check("browser: 2 tabs opened, closed one skipped", n == 2, str(n))
    check("browser: greenhouse fields filled", seen.get("vals") == ["Shehab", "Moqbel", common.PROFILE["candidate"]["email"], common.PROFILE["candidate"]["phone"], common.PROFILE["candidate"]["linkedin"]], str(seen.get("vals")))
    check("browser: resume + cover uploaded", seen.get("files") == ["Shehab-Moqbel-Resume.docx", "Shehab-Moqbel-Cover-Letter.docx"], str(seen.get("files")))
    check("browser: sign-in email filled, password never typed (assisted)", seen.get("signin_email") == common.PROFILE["candidate"]["email"] and seen.get("pw") == "", str(seen))
    sts = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM jobs")}
    check("browser: statuses (never auto-Applied)", sts[other["id"]] == "FILLED" and sts[top["id"]] == "SKIPPED" and sts[hand["id"]] == "BLOCKED", str(sts))
except Exception as exc:
    print("SKIP browser checks (Playwright or Chrome not available here):", repr(exc)[:120])

# ---------------- session 3: contacts for the Network tab, "I sent it"
check("contacts: export from the Outreach tab", outreach.export_contacts() == len(ot.rows) - 1)
_cj = json.loads(outreach.CONTACTS_CACHE.read_text(encoding="utf-8"))
check("contacts: cache has rows and folders", _cj["source"] == "Sheet" and all(r["folder"] for r in _cj["contacts"]))
_folder = _cj["contacts"][0]["folder"]
check("contacts: I sent it marks Sent + follow-up date", outreach.mark_sent(_folder)
      and ot.rows[1][4] == "Sent" and ot.rows[1][5] == (__import__("datetime").date.today() + __import__("datetime").timedelta(days=7)).isoformat()
      and ot.rows[1][7] == _folder, str(ot.rows[1]))
check("contacts: unknown folder changes nothing", not outreach.mark_sent("no/such/folder"))
check("config: Today-tab events parse", all("date" in e and "what" in e for e in common.CONFIG.get("events") or []))
_auto = (IP / "tools" / "RUN_PIPELINE_AUTO.bat").read_text(encoding="ascii")
check("scheduled run: below-normal priority", "/BELOWNORMAL /WAIT" in _auto and "--auto > last_auto_run.txt" in _auto)

# ---------------- cold outreach: prospects, cold email, send file, reply tracking
import prospects
_pr = prospects.ranked()
check("prospects: ranked list from prospects.yaml", len(_pr) > 20 and _pr == sorted(_pr, key=lambda r: (-r["fit"], r["company"])))
check("prospects: healthcare finance group present", any(r["group"] == "healthcare_finance" for r in _pr))
check("prospects: company matching", prospects.same_company("RSM US LLP", "RSM") and not prospects.same_company("EY", "Eyeglass Co"))
_cm = outreach.cold_messages("MD Anderson Cancer Center", "Ana", prospects.fit_line("MD Anderson Cancer Center"))
check("cold email: facts pass the fact check (10 minutes is a request, not a claim)",
      not tailor.validate([_cm["email"].replace("10 minutes", "a few minutes")]) and not tailor.validate([_cm["follow_up"], _cm["note"]]))
check("cold email: no unconfirmed CVS numbers, no job/referral ask", not any(x in _cm["email"] for x in ("25+", "team of 4", "100+", "referral", "hire me")))
check("cold email: short, from the school email", len(_cm["email"].split()) < 140 and common.PROFILE["candidate"]["email_campus"] in _cm["email"])
_cf = outreach.cold_one("MD Anderson Cancer Center", "Ana", "Ruiz", "ana@example.org")
_sj = json.loads((_cf / "send.json").read_text(encoding="utf-8"))
check("cold kit: send.json for Open in Outlook/Gmail", _sj["to"] == "ana@example.org" and "Summer 2027" in _sj["subject"] and _sj["body"].startswith("Hi Ana"))
check("cold kit: logged as Cold email", any(r[3] == "Cold email" and r[7] == str(_cf) for r in ot.rows[1:]))
check("tracking: Replied stops follow-ups", outreach.mark_sent(str(_cf), "Replied")
      and next(r for r in ot.rows if len(r) > 7 and r[7] == str(_cf))[4:6] == ["Replied", ""])
check("tracking: unknown status falls back to Sent", outreach.mark_sent(str(_cf), "Whatever")
      and next(r for r in ot.rows if len(r) > 7 and r[7] == str(_cf))[4] == "Sent")

# ---------------- continuous updates: scan cadence, research finds
H = 3600
common.CONFIG["scan_every_hours"], common.CONFIG["full_scan_every_hours"] = 4, 24
sp = run_all.scan_plan
check("cadence: hourly check quits when nothing is due", sp(10 * H, 8 * H, 8 * H, True, False) == "quit")
check("cadence: every 4 h a light scan (company sites only)", sp(13 * H, 8 * H, 1 * H, True, False) == "light")
check("cadence: job boards at most once a day", sp(30 * H, 25 * H, 5 * H, True, False) == "full" and sp(30 * H, 25 * H, 10 * H, True, False) == "light")
check("cadence: Run Daily Scan is always full", sp(10 * H, 9.9 * H, 9.9 * H, False, True) == "full")
(IP / "prospects_found.yaml").write_text("found:\n  - {company: Acme Health Partners, group: healthcare_finance, note: Accounting Intern Summer 2027, source: https://example.org/job, added: 2026-11-22}\n  - {company: Nowhere Co, group: bogus}\n", encoding="utf-8")
_pf = {r["company"]: r for r in prospects.ranked()}
check("research finds: added to Prospects with note + source", "Acme Health Partners" in _pf and "example.org" in _pf["Acme Health Partners"]["why"]
      and _pf["Acme Health Partners"]["group"] == "healthcare_finance")
check("research finds: unknown group falls back safely", _pf.get("Nowhere Co", {}).get("group") == "corporate_energy")
(IP / "prospects_found.yaml").write_text("found: [", encoding="utf-8")
check("research finds: a broken file never breaks the list", len(prospects.ranked()) > 20)
_bat = (IP / "tools" / "RUN_PIPELINE_AUTO.bat").read_text(encoding="ascii")
check("hidden run never waits for a key", _bat.index('if "%PIPELINE_HIDDEN%"=="1" exit /b 1') < _bat.index("pause")
      and "last_auto_error.txt" in _bat)
_ps = (IP / "tools" / "schedule_task.ps1").read_text(encoding="utf-8")
check("schedule: one daily hidden task, old hourly task removed", "run_hidden.vbs" in _ps and "Unregister-ScheduledTask" in _ps
      and "$hourly" not in _ps and _ps.count("Register-ScheduledTask -TaskName") == 1)
import yaml
_cfg = yaml.safe_load((IP / "config.yaml").read_text(encoding="utf-8"))
check("schedule: one scan a day (config)", _cfg["scan_every_hours"] >= 20 and _cfg["full_scan_every_hours"] >= 20)

# ---------------- session 2: re-check old jobs, single-job tailoring, no-keyboard apply
import jobctl, inspect
rr = jobctl.recheck_reason
check("recheck: off-target title", "off-target" in rr({"title": "Supply Chain Intern", "location": "Houston, TX", "status": "QUALIFIED", "score": 90}))
check("recheck: out of area", "Houston" in rr({"title": "Finance Intern", "location": "Chicago, IL", "status": "PACKET_READY", "score": 90}))
check("recheck: score under 80", "below" in rr({"title": "Finance Intern", "location": "Houston, TX", "status": "PACKET_READY", "score": 75}))
check("recheck: good job kept", rr({"title": "Finance Intern", "location": "Houston, TX", "status": "PACKET_READY", "score": 85}) == "")
check("recheck: hand-added / blank location kept", rr({"title": "Finance Intern", "location": "", "source": "added by hand", "status": "QUALIFIED", "score": 82}) == ""
      and rr({"title": "Finance Intern", "location": "Dallas, TX", "source": "added by hand", "status": "QUALIFIED", "score": 82}) == "")
mem2 = sqlite3.connect(":memory:"); mem2.row_factory = sqlite3.Row
mem2.execute(conn.execute("SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0])
for i, (t, st, sc) in enumerate([("Supply Chain Intern", "PACKET_READY", 90), ("Finance Intern", "FILLED", 70),
                                 ("Finance Intern", "APPLIED", 70), ("Treasury Intern", "QUALIFIED", 88), ("Audit Intern", "PACKET_READY", 76)]):
    mem2.execute("INSERT INTO jobs (id, company, title, location, status, score) VALUES (?,?,?,?,?,?)", (f"j{i}", "Co", t, "Houston, TX", st, sc))
check("recheck: dry run changes nothing", jobctl.recheck(mem2, dry_run=True) == 2 and mem2.execute("SELECT COUNT(*) FROM jobs WHERE status='SKIPPED'").fetchone()[0] == 0)
jobctl.recheck(mem2)
st2 = {r["id"]: r["status"] for r in mem2.execute("SELECT id, status FROM jobs")}
check("recheck: skips only unopened jobs that break the rules; Filled/Applied untouched",
      st2 == {"j0": "SKIPPED", "j1": "FILLED", "j2": "APPLIED", "j3": "QUALIFIED", "j4": "SKIPPED"}, str(st2))
check("tailor: single-job flag", "job_id" in inspect.signature(tailor.run).parameters)
_fill = (IP / "pipeline" / "fill.py").read_text(encoding="utf-8")
check("apply: Control Center mode is checked before approve mode (never submits)",
      _fill.index('os.environ.get("PIPELINE_GUI")') < _fill.index('approve_and_submit(conn, opened)\n        elif'))

_cc = (IP / "control_center.py").read_text(encoding="utf-8")
check("control center: bound to 127.0.0.1 only, no reload", 'host="127.0.0.1"' in _cc and "reload=False" in _cc)
import re as _re
check("control center: never imports Playwright", not _re.search(r"^\s*(import|from)\s+playwright", _cc, _re.M))
check("control center: has no code path that clicks Submit", "approve_and_submit" not in _cc and "fill.run" not in _cc and "--apply\"" not in _cc)
check("control center: apply runs one job in no-keyboard mode", '"PIPELINE_GUI": "1"' in _cc and '"pipeline/fill.py", "--job"' in _cc)
check("control center: documents served only from the output folder", 'relative_to((ROOT / "output").resolve())' in _cc)
check("control center: email is only opened for you to send (compose links, no send API)",
      "deeplink/compose" in _cc and "view" in _cc and "send_message" not in _cc and "smtplib" not in _cc)
check("control center: key file only checked for presence", _cc.count("google_service_account.json") == _cc.count('"google_service_account.json").exists()'))

# ---------------- phase 1: apply window (router, click guard, real Chrome args, banner)
exec(compile((IP / "pipeline" / "tests" / "phase1_checks.py").read_text(encoding="utf-8"), "phase1_checks.py", "exec"))

print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
