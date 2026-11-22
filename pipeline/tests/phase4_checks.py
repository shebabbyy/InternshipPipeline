# Phase 4 checks (outreach queue, Hunter lookup, sender tags, sent-email events). Run from offline_test.py via exec:
# uses its check(), IP, common, outreach, SHEET, R (fake response), conn. No network: every request is faked.
import json as _json, re as _re
from datetime import date as _date, timedelta as _td
import contacts as _ct
import jobctl as _jc

_today = _date.today().isoformat()
_ct.QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
if _ct.QUEUE_PATH.exists():
    _ct.QUEUE_PATH.unlink()
_saved_cfg = {k: common.CONFIG.get(k) for k in ("outreach_weekly_cap", "outreach_per_company", "outreach_company_gap_days",
                                                "outreach_skip", "hunter_monthly_cap", "company_domains")}

# ---------- sender tags: UH-linked -> school Outlook, everyone else -> Gmail
check("phase4 sender: UH alum, Bauer, career fair, UH address -> school",
      all(_ct.tag_sender(s) == "school" for s in ("UH alum", "Bauer / UH", "Career fair", "Beta Alpha Psi"))
      and _ct.tag_sender("Other", "x@cougarnet.uh.edu") == "school" and _ct.tag_sender("", "y@uh.edu") == "school")
check("phase4 sender: everyone else -> Gmail",
      all(_ct.tag_sender(s, e) == "gmail" for s, e in (("Other", "a@b.com"), ("", ""), ("LinkedIn", "r@acme.com"))))
check("phase4 sender: addresses come from the profile",
      _ct.sender_address("school") == common.PROFILE["candidate"]["email_campus"] and _ct.sender_address("gmail") == common.PROFILE["candidate"]["email"])

# ---------- queue basics and limits
common.CONFIG.update({"outreach_weekly_cap": 3, "outreach_per_company": 2, "outreach_company_gap_days": 21, "outreach_skip": ["Skip Corp"]})
_i1, _w = _ct.add_item("Memorial Hermann", "Ana Ruiz", "ana@mh.example.org", "Campus Recruiter", "UH alum", "cold", "verified")
check("phase4 queue: item has sender tag, fit line, history list, state queued",
      _i1 and _i1["sender"] == "school" and _i1["sender_address"] == _ct.sender_address("school") and _i1["state"] == "queued"
      and "CVS" in _i1["fit_line"] and isinstance(_i1["history"], list) and _i1["email_status"] == "verified", str(_i1))
_dup, _w = _ct.add_item("Memorial Hermann", "Ana Ruiz", "ana@mh.example.org", "", "UH alum", "cold")
check("phase4 queue: the same person twice is refused", _dup is None and "already" in _w, _w)
_bad, _w = _ct.add_item("Acme", "Bo", "not-an-email", "", "Other")
check("phase4 queue: a bad email address is refused", _bad is None and "email" in _w, _w)
_sk, _w = _ct.add_item("Skip Corp", "Cy Dee", "cy@skip.example.org", "", "Other")
check("phase4 queue: outreach_skip companies are refused", _sk is None and "outreach_skip" in _w, _w)
_i2, _ = _ct.add_item("Memorial Hermann", "Bo Chen", "bo@mh.example.org", "Finance Manager", "Other", "cold", "likely")
_i3, _w = _ct.add_item("Memorial Hermann", "Cal Fox", "cal@mh.example.org", "", "Other")
check("phase4 queue: at most outreach_per_company people at one company", _i2 and _i2["sender"] == "gmail" and _i3 is None and "outreach_per_company" in _w, _w)
_i4, _ = _ct.add_item("Houston Methodist", "Di Lee", "di@hm.example.org", "", "Other")
_i5, _w = _ct.add_item("Phillips 66", "Ed Moe", "ed@p66.example.org", "", "Other")
check("phase4 queue: weekly cap stops new people (3 per 7 days here)", _i4 and _i5 is None and "weekly cap" in _w, _w)
_i5, _w = _ct.add_item("Phillips 66", "Ed Moe", "ed@p66.example.org", "", "Other", force=True)
check("phase4 queue: force overrides the cap for one person", _i5 is not None)
_ct.set_state(_i1["id"], "sent", sent=_today)
common.CONFIG["outreach_weekly_cap"] = 50
_g, _w = _ct.add_item("Memorial Hermann", "Zed Ray", "zed@mh.example.org", "", "Other")
_fu, _ = _ct.add_item("Memorial Hermann", "Ana Ruiz", "ana@mh.example.org", "", "UH alum", "follow_up")
check("phase4 queue: no second person within the gap after an email; the one follow-up is still allowed",
      _g is None and _fu is not None, _w)
_again, _w = _ct.add_item("Memorial Hermann", "Ana Ruiz", "ana@mh.example.org", "", "UH alum", "follow_up")
check("phase4 queue: only one follow-up per person", _again is None)
check("phase4 queue: file is valid JSON, listed by --list text",
      len(_json.loads(_ct.QUEUE_PATH.read_text(encoding="utf-8"))["items"]) == 5 and "Ana Ruiz" in _ct.render_list())

# ---------- Hunter lookup (fake)
calls = []
HUNTER = {"data": {"domain": "acme-health.example.org", "emails": [
    {"value": "info@acme-health.example.org", "type": "generic", "position": "", "confidence": 99},
    {"value": "dev@acme-health.example.org", "type": "personal", "first_name": "Dev", "last_name": "Ops", "position": "Software Engineer",
     "department": "it", "confidence": 95, "verification": {"status": "valid"}},
    {"value": "fin@acme-health.example.org", "type": "personal", "first_name": "Fin", "last_name": "Ance", "position": "Finance Manager",
     "department": "finance", "confidence": 80, "verification": {"status": None}},
    {"value": "rec@acme-health.example.org", "type": "personal", "first_name": "Rae", "last_name": "Cruiter", "position": "University Recruiter",
     "department": "hr", "confidence": 97, "verification": {"status": "valid"}},
    {"value": "rec2@acme-health.example.org", "type": "personal", "first_name": "Tal", "last_name": "Ent", "position": "Talent Acquisition",
     "department": "hr", "confidence": 60, "verification": {"status": None}}]}}
class FakeHttp:
    def get(self, url, **k):
        calls.append((url, dict(k.get("params") or {})))
        if "email-finder" in url:
            return R({"data": {"email": "ann@acme-health.example.org", "score": 95, "position": "Senior Accountant", "verification": {"status": "valid"}}})
        return R(HUNTER)
    def post(self, url, **k):
        calls.append((url, {}))
        return types.SimpleNamespace(status_code=403, json=lambda: {})
_ct.HTTP = FakeHttp()
_conn = common.db()
_ct.meta_set(_conn, _ct._month_key(), 0)
common.CONFIG["hunter_monthly_cap"] = 3
os.environ.pop("HUNTER_API_KEY", None)
_p, _w = _ct.find_people(_conn, "Acme Health")
check("phase4 hunter: no key -> a plain reason, no request", _p == [] and "HUNTER_API_KEY" in _w and not calls, _w)
os.environ["HUNTER_API_KEY"] = "test-key"
_p, _w = _ct.find_people(_conn, "Acme Health")
check("phase4 hunter: recruiters first, then finance; generic and engineering addresses dropped",
      [x["email"] for x in _p] == ["rec@acme-health.example.org", "rec2@acme-health.example.org", "fin@acme-health.example.org"]
      and _p[0]["email_status"] == "verified" and _p[1]["email_status"] == "unverified", str(_p))
check("phase4 hunter: one call, to api.hunter.io, by company name, counted against the monthly cap",
      len(calls) == 1 and calls[0][0].startswith("https://api.hunter.io/v2/domain-search") and calls[0][1].get("company") == "Acme Health"
      and _ct.hunter_budget(_conn) == 2, str(calls))
check("phase4 hunter: the domain is learned for next time", _ct.domain_for(_conn, "Acme Health") == "acme-health.example.org")
_p2, _ = _ct.find_people(_conn, "Acme Health")
check("phase4 hunter: a repeat lookup is served from the 90-day cache (no credit)", len(calls) == 1 and _p2 == _p)
common.CONFIG["company_domains"] = {"Texas Capital Bank": "texascapitalbank.com"}
_ct.find_people(_conn, "Texas Capital Bank")
check("phase4 hunter: a known domain is sent as domain=", calls[-1][1].get("domain") == "texascapitalbank.com", str(calls[-1]))
common.CONFIG["hunter_monthly_cap"] = 2   # two lookups already used this month
_p3, _w = _ct.find_people(_conn, "Another Co")
check("phase4 hunter: monthly cap stops further lookups", _p3 == [] and "cap" in _w and len(calls) == 2, _w)
common.CONFIG["hunter_monthly_cap"] = 20
_added = _ct.find("Acme Health", source="Other")
check("phase4 find: queues the best person, sender Gmail", len(_added) == 1 and _added[0]["email"] == "rec@acme-health.example.org"
      and _added[0]["sender"] == "gmail" and _added[0]["email_status"] == "verified", str(_added))
_ef = _ct.find("Texas Capital Bank", "Ann", "Ledger", "UH alum", limit=1)
check("phase4 find: a name from LinkedIn goes to the email finder; UH alum -> school Outlook",
      len(_ef) == 1 and _ef[0]["email"] == "ann@acme-health.example.org" and _ef[0]["sender"] == "school"
      and "email-finder" in calls[-1][0] and calls[-1][1].get("first_name") == "Ann", str(_ef))
os.environ.pop("APOLLO_API_KEY", None)
check("phase4 apollo: optional, no key -> reason", _ct.apollo_people("Acme")[1].startswith("no APOLLO_API_KEY"))
os.environ["APOLLO_API_KEY"] = "k"
_ap, _w = _ct.apollo_people("Acme")
check("phase4 apollo: a free-plan refusal is reported, never raised", _ap == [] and "paid plan" in _w, _w)
os.environ.pop("APOLLO_API_KEY", None)
check("phase4 network: only Hunter and Apollo are ever contacted", all(u.startswith(("https://api.hunter.io/", "https://api.apollo.io/")) for u, _ in calls))
os.environ.pop("HUNTER_API_KEY", None)

# ---------- follow-ups: Sent and due -> one follow-up item; Followed up / Replied -> none
_t = SHEET.tabs["Outreach"]
_old = (_date.today() - _td(days=8)).isoformat()
for key, status, follow in (("queue:fu1", "Sent", _today), ("queue:fu2", "Followed up", ""), ("queue:fu3", "Replied", ""), ("queue:fu4", "Sent", (_date.today() + _td(days=3)).isoformat())):
    _t.rows.append([_old, f"FU Co {key[-1]}", f"Pat {key[-1]}", "Cold email", status, follow, "link", key, ""])
outreach.export_contacts()
_n = _ct.refresh_followups()
_fus = [i for i in _ct.load_queue()["items"] if i["kind"] == "follow_up" and i.get("followup_of", "").startswith("queue:fu")]
_all_fu = [i["company"] for i in _ct.load_queue()["items"] if i["kind"] == "follow_up"]
check("phase4 follow-ups: only due, un-followed Sent rows are queued (not Followed up, Replied or not yet due)",
      _n >= 1 and len(_fus) == 1 and _fus[0]["company"] == "FU Co 1" and not any(c in _all_fu for c in ("FU Co 2", "FU Co 3", "FU Co 4")), str(_all_fu))
check("phase4 follow-ups: running again adds nothing", _ct.refresh_followups() == 0)

# ---------- sent-email events -> Outreach tab + queue
_q = _ct.load_queue()["items"]
_a = next(i for i in _q if i["company"] == "Acme Health")
rows_before = len(_t.rows)
outreach.add_event("sent", "Acme Health", "Rae Cruiter", "rec@acme-health.example.org", _a["id"], "gmail", "UH Bauer junior: Summer 2027", "cold")
check("phase4 events: Claude's record is a small file, nothing else changes yet", len(list(_ct.EVENTS_DIR.glob("*.json"))) == 1 and len(_t.rows) == rows_before)
check("phase4 events: fold records Sent with a 7-day next touch, keyed by the queue item",
      outreach.fold_events() == 1 and _t.rows[-1][1] == "Acme Health" and _t.rows[-1][4] == "Sent"
      and _t.rows[-1][5] == (_date.today() + _td(days=7)).isoformat() and _t.rows[-1][7] == f"queue:{_a['id']}", str(_t.rows[-1]))
_a2 = next(i for i in _ct.load_queue()["items"] if i["id"] == _a["id"])
check("phase4 events: the queue item becomes sent; the file moves to done/",
      _a2["state"] == "sent" and _a2["sent"] == _today and not list(_ct.EVENTS_DIR.glob("*.json")) and len(list((_ct.EVENTS_DIR / "done").glob("*.json"))) == 1)
outreach.add_event("followed_up", "Acme Health", "Rae Cruiter", "rec@acme-health.example.org", _a["id"], "gmail", "Re: UH Bauer junior")
outreach.fold_events()
check("phase4 events: Followed up ends the reminders (one follow-up only)", _t.rows[-1][4] == "Followed up" and _t.rows[-1][5] == "" and len(_t.rows) == rows_before + 1, str(_t.rows[-1]))
_fu1 = _fus[0]
outreach.add_event("replied", "FU Co 1", "Pat 1", "", _fu1["id"], "", "", "follow_up")
outreach.fold_events()
check("phase4 events: a reply marks the row Replied and cancels the queued follow-up",
      next(r for r in _t.rows if r[7] == "queue:fu1")[4] == "Replied"
      and next(i for i in _ct.load_queue()["items"] if i["id"] == _fu1["id"])["state"] == "replied")
outreach.add_event("sent", "Hand Co", "Sam Poe", "sam@hand.example.org", "", "school", "Hello", "network", "logged by hand", "2026-11-01")
outreach.fold_events()
check("phase4 events: an email logged by hand (no queue item) gets its own row with its date",
      _t.rows[-1][1] == "Hand Co" and _t.rows[-1][0] == "2026-11-01" and _t.rows[-1][3] == "Networking" and _t.rows[-1][4] == "Sent"
      and _t.rows[-1][5] == "2026-11-08", str(_t.rows[-1]))
(_ct.EVENTS_DIR / "junk.json").write_text("{not json", encoding="utf-8")
outreach.fold_events()
check("phase4 events: a broken event file is set aside, not fatal", (_ct.EVENTS_DIR / "junk.bad").exists())
check("phase4 events: Followed up no longer schedules another touch (mark_sent)",
      outreach.mark_sent("queue:fu1", "Followed up") and next(r for r in _t.rows if r[7] == "queue:fu1")[5] == "")

# ---------- Log an application
_conn.execute("DELETE FROM jobs WHERE company='Logged Co'"); _conn.commit()
_row = _jc.log_app(_conn, "Logged Co", "Finance Intern", "https://example.org/job", "2026-11-10")
_r = _conn.execute("SELECT status, source FROM jobs WHERE id=?", (_row["id"],)).fetchone()
check("phase4 log-app: new job added as Applied with the date you gave",
      _r["status"] == "APPLIED" and _r["source"] == "added by hand" and common.meta_get(_conn, f"applied_{_row['id']}") == "2026-11-10")
_row2 = _jc.log_app(_conn, "Logged Co", "Finance Intern", "", "bad-date")
check("phase4 log-app: the same job again is not duplicated; a bad date becomes today",
      _row2["id"] == _row["id"] and common.meta_get(_conn, f"applied_{_row['id']}") == _today
      and _conn.execute("SELECT COUNT(*) FROM jobs WHERE company='Logged Co'").fetchone()[0] == 1)

# ---------- safety and wiring
_src = {n: (IP / "pipeline" / n).read_text(encoding="utf-8") for n in ("contacts.py", "outreach.py", "jobctl.py")}
check("phase4 safety: nothing in the pipeline can send an email",
      not any(w in s for s in _src.values() for w in ("smtplib", "sendmail", "send_message", "SMTP(", "messages.send", "users().messages")))
check("phase4 safety: contacts.py talks only to Hunter and Apollo",
      set(_re.findall(r"https://([\w.-]+)/", _src["contacts.py"])) <= {"api.hunter.io", "api.apollo.io"}, str(set(_re.findall(r"https://([\w.-]+)/", _src["contacts.py"]))))
check("phase4 safety: no raw browser clicks, no LinkedIn fetching", ".click(" not in _src["contacts.py"] and "requests.get(\"https://www.linkedin" not in _src["contacts.py"])
_ra = (IP / "pipeline" / "run_all.py").read_text(encoding="utf-8")
check("phase4 wiring: the daily scan folds sent emails and queues due follow-ups, with no lookups",
      "outreach.fold_events" in _ra and "contacts.refresh_followups" in _ra and "contacts.find" not in _ra and "contacts.auto" not in _ra)
_cc4 = (IP / "control_center.py").read_text(encoding="utf-8")
compile(_cc4, "control_center.py", "exec")
check("phase4 control center: reads the queue only; every change goes through a subprocess",
      "QUEUE_PATH.write" not in _cc4 and "QUEUE_PATH.open" not in _cc4 and '"pipeline/contacts.py"' in _cc4 and "--log-email" in _cc4 and "--log-app" in _cc4)

# restore settings so later suites are unaffected
for k, v in _saved_cfg.items():
    if v is None:
        common.CONFIG.pop(k, None)
    else:
        common.CONFIG[k] = v
