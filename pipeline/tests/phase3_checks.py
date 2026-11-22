# Phase 3 checks (job-board link resolver). Run from offline_test.py via exec: uses its check(), IP, common, fill,
# conn, base (local test web server), other (a job with packet files). No network: every request is faked.
import json as _json, types as _types
import resolver
import discover as _disc
import apply_window as _aw

# ---------- names and titles
check("phase3 company names normalized",
      resolver.norm_company("RSM US LLP") == "rsm" and resolver.norm_company("ENGIE North America Inc.") == "engie"
      and resolver.norm_company("Quanta Services, Inc.") == "quanta services"
      and resolver.norm_company("Mauldin & Jenkins") == "mauldin and jenkins",
      str([resolver.norm_company(x) for x in ("RSM US LLP", "ENGIE North America Inc.", "Quanta Services, Inc.")]))
check("phase3 same company: aliases, prefixes, no loose one-word matches",
      resolver.same_company("pilot", "pilot flying j") and resolver.same_company("american national", "american national insurance")
      and not resolver.same_company("ryan", "ryan specialty") and not resolver.same_company("bny", "bnym"))
check("phase3 titles: location tails, brackets and filler words ignored",
      resolver.title_score("Audit Accounting Intern - Houston, TX", "Audit Accounting Intern") == 1.0
      and resolver.title_score("Program Intern,  Energy Accounting", "Energy Accounting Intern") == 1.0
      and resolver.title_score("Summer 2027 Intern - Americas Division [UG/Masters]", "Summer 2027 Intern - Americas Division") == 1.0)
check("phase3 titles: different roles don't match",
      resolver.title_score("Tax Intern", "Audit Intern") < resolver.MATCH_MIN
      and resolver.title_score("Accounting Intern", "Accounting Manager") < resolver.MATCH_MIN)
_c = [{"title": "Finance Intern", "url": "u1", "location": "Houston, TX"},
      {"title": "Finance Intern", "url": "u2", "location": "Chicago, IL"}]
check("phase3 match: other cities ignored", resolver.best_match("Finance Intern - Summer 2027", _c)[0]["url"] == "u1")
_c2 = _c + [{"title": "Finance Interns", "url": "u3", "location": "Houston, TX"}]
check("phase3 match: two close Houston postings -> no guess", resolver.best_match("Finance Intern", _c2)[0] is None)

# ---------- careers-page detection
_html = """<a href="https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/job/x">jobs</a>
<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeco"></script>
<a href='https://jobs.lever.co/acme-inc/123'>x</a> <a href="https://careers-acme.icims.com/jobs/search?ss=1&amp;a=b">icims</a>"""
_d = resolver.detect_ats(_html)
check("phase3 detect: Workday, Greenhouse embed, Lever, iCIMS links found",
      {"ats": "workday", "url": "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers"} in _d
      and {"ats": "greenhouse", "board": "acmeco"} in _d and {"ats": "lever", "board": "acme-inc"} in _d
      and any(e["ats"] == "other" and "icims.com" in e["url"] and "&amp;" not in e["url"] for e in _d), str(_d))
check("phase3 router: new ATS hosts", _aw.route("https://ats.rippling.com/desri-careers/jobs") == "other_ats"
      and _aw.route("https://jobs.smartrecruiters.com/PilotCompany/1") == "other_ats")

# ---------- fake network: records every URL, refuses nothing itself (Http does that)
class _R:
    def __init__(self, data=None, text="", ct="application/json", status=200):
        self.d, self.text, self.status_code, self.headers = data, text, status, {"content-type": ct}
    def json(self): return self.d
_seen = []
def _get(url, **k):
    _seen.append(url)
    if "boards/americannational/jobs" in url:
        return _R({"jobs": [{"title": "Strategic Analyst Intern", "absolute_url": "https://job-boards.greenhouse.io/americannational/jobs/77",
                             "location": {"name": "League City, TX; Houston, TX"}},
                            {"title": "Internship - Competitive Intelligence", "absolute_url": "https://x/2", "location": {"name": "Glenmont, NY"}}]})
    if "companies/PilotCompany/postings" in url:
        return _R({"content": [{"name": "Program Intern, Energy Accounting", "id": "744000", "location": {"city": "Houston", "region": "TX"}},
                               {"name": "Program Intern, Income Tax", "id": "744001", "location": {"city": "Knoxville", "region": "TN"}}]})
    if "boards/acmewidgets/jobs" in url:
        return _R({"jobs": [{"title": "Treasury Intern", "absolute_url": "https://job-boards.greenhouse.io/acmewidgets/jobs/5",
                             "location": {"name": "Houston, Texas"}}]})
    if "careers.example-energy.com" in url:
        return _R(text='<a href="https://exen.wd1.myworkdayjobs.com/ExEn_Careers">Search jobs</a>', ct="text/html")
    return _R(status=404, ct="text/html")
def _post(url, **k):
    _seen.append(url)
    if "/wday/cxs/exen/ExEn_Careers/jobs" in url or "/wday/cxs/wdco/Careers/jobs" in url:
        return _R({"jobPostings": [{"title": "Finance Intern - Summer 2027", "externalPath": "/job/Houston/Finance-Intern_R1",
                                    "locationsText": "Houston, TX"}]})
    return _R({"jobPostings": []})
_http = resolver.Http(_get, _post, delay=0)
resolver.FOUND_FILE = IP / "_test_ats_found.yaml"
resolver.RESOLVED_FILE = IP / "output" / "_resolved_links_test.json"
_entries = [e for e in resolver._load(resolver.DIRECTORY_FILE) if e.get("name") != "American National"] + [
    {"name": "American National", "aliases": ["American National Insurance Company"], "ats": "greenhouse", "board": "americannational"},
    {"name": "WD Co", "ats": "workday", "url": "https://wdco.wd1.myworkdayjobs.com/en-US/Careers", "via": "watchlist"},
    {"name": "Example Energy", "ats": "other", "careers": "https://careers.example-energy.com/jobs"}]
LI = "https://www.linkedin.com/jobs/view/"
def _job(i, co, title): return {"id": f"p3_{i}", "company": co, "title": title, "url": LI + str(i), "score": 85}

_r = resolver.resolve(_job(1, "American National Insurance Company", "Strategic Analyst Intern"), _entries, _http)
check("phase3 resolve: Greenhouse feed match -> direct", _r["kind"] == "direct" and _r["url"].endswith("/jobs/77") and _r["ats"] == "greenhouse", str(_r))
_r = resolver.resolve(_job(2, "Pilot Flying J", "Program Intern,  Energy Accounting"), _entries, _http)
check("phase3 resolve: SmartRecruiters (alias name) -> direct, Houston one",
      _r["kind"] == "direct" and _r["url"] == "https://jobs.smartrecruiters.com/PilotCompany/744000", str(_r))
_r = resolver.resolve(_job(3, "WD Co", "Finance Intern (Summer 2027)"), _entries, _http)
check("phase3 resolve: Workday watchlist feed -> direct, routes to the Workday filler",
      _r["kind"] == "direct" and _aw.route(_r["url"]) == "workday", str(_r))
_r = resolver.resolve(_job(4, "Acme Widgets Inc.", "Treasury Intern"), _entries, _http)
check("phase3 resolve: unknown company, guessed Greenhouse board with the same title -> direct + learned",
      _r["kind"] == "direct" and "acmewidgets" in _r["url"]
      and "acmewidgets" in resolver.FOUND_FILE.read_text(encoding="utf-8"), str(_r))
_r = resolver.resolve(_job(5, "Example Energy", "Finance Intern - Summer 2027"), _entries, _http)
check("phase3 resolve: careers page links to Workday -> searched -> direct + learned",
      _r["kind"] == "direct" and "exen.wd1.myworkdayjobs.com" in _r["url"]
      and "exen.wd1" in resolver.FOUND_FILE.read_text(encoding="utf-8"), str(_r))
_r = resolver.resolve(_job(6, "Quanta Services, Inc.", "Quanta Services Internship(s) - All Degrees | Summer 2027"), _entries, _http)
check("phase3 resolve: iCIMS company -> careers search with the title words",
      _r["kind"] == "careers" and "careers-quanta.icims.com" in _r["url"] and "searchKeyword=quanta" in _r["url"], str(_r))
_r = resolver.resolve(_job(7, "Nobody Corp", "Finance Intern"), _entries, _http)
check("phase3 resolve: nothing found -> stays on the job board", _r["kind"] == "none" and _r["url"] == LI + "7", str(_r))
_r = resolver.resolve(_job(8, "American National", "Actuarial Intern"), _entries, _http)
check("phase3 resolve: feed company without that title -> its job list, never a wrong posting",
      _r["kind"] == "careers" and _r["url"] == "https://job-boards.greenhouse.io/americannational", str(_r))
_r = resolver.resolve({"id": "p3_9", "company": "X", "title": "Y", "url": "https://x.wd1.myworkdayjobs.com/S/job/1"}, _entries, _http)
check("phase3 resolve: employer links are left alone", _r["kind"] == "own")
check("phase3 never fetches a job-board page", not any("linkedin" in u or "indeed" in u for u in _seen), str(_seen[:5]))
try:
    _http.get("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1"); _refused = False
except PermissionError:
    _refused = True
check("phase3 Http refuses job-board hosts", _refused and not resolver.Http.allowed("https://uk.indeed.com/x"))

# ---------- run(): stored results, duplicates, apply target
_ins = [_job(21, "Pilot Company", "Program Intern, Energy Accounting"), _job(22, "Pilot Flying J", "Program Intern,  Energy Accounting"),
        _job(23, "Quanta Services, Inc.", "Quanta Services Internship(s) - All Degrees | Summer 2027"), _job(24, "Nobody Corp", "Finance Intern")]
for j in _ins:
    conn.execute("INSERT OR REPLACE INTO jobs (id, company, title, url, status, score) VALUES (?,?,?,?,?,?)",
                 (j["id"], j["company"], j["title"], j["url"], "PACKET_READY", 85 if j["id"] != "p3_22" else 80))
conn.commit()
resolver.directory = lambda: _entries
_res = resolver.run(http=_http)
_saved = _json.loads(resolver.RESOLVED_FILE.read_text(encoding="utf-8"))
check("phase3 run: results saved per job", all(j["id"] in _saved for j in _ins))
check("phase3 run: same posting under two names flagged as duplicate (lower score)",
      _saved["p3_22"].get("duplicate_of") == "p3_21" and "duplicate_of" not in _saved["p3_21"], str({k: _saved[k].get("duplicate_of") for k in ("p3_21", "p3_22")}))
_n = len(_seen)
resolver.run(http=_http)
check("phase3 run: direct results aren't looked up again", len(_seen) - _n <= 2, str(_seen[_n:]))
check("phase3 target: direct link opens instead of LinkedIn", resolver.target(_ins[0]) == (_saved["p3_21"]["url"], None))
_t = resolver.target(_ins[2])
check("phase3 target: careers site with a find-this-role banner", "icims" in _t[0] and _t[1] and "Simplify Autofill" in _t[1] and _ins[2]["title"] in _t[1])
check("phase3 target: not found -> the job-board link", resolver.target(_ins[3]) == (_ins[3]["url"], None))
check("phase3 run: database untouched (only the json file)",
      {r["status"] for r in conn.execute("SELECT status FROM jobs WHERE id LIKE 'p3_%'")} == {"PACKET_READY"})
conn.execute("DELETE FROM jobs WHERE id LIKE 'p3_%'"); conn.commit()

# ---------- discovery: SmartRecruiters feed + directory feeds marked watch: true
def _sr_get(url, **k):
    if url.endswith("/postings/744000"):
        return _R({"jobAd": {"sections": {"jobDescription": {"text": "Energy accounting support. " * 10}}}})
    return _R({"content": [{"name": "Program Intern, Energy Accounting", "id": "744000", "location": {"city": "Houston", "region": "TX"}},
                           {"name": "Program Intern, Income Tax", "id": "744001", "location": {"city": "Knoxville", "region": "TN"}},
                           {"name": "Store Manager", "id": "744002", "location": {"city": "Houston", "region": "TX"}}]})
_old_req = _disc.requests
_disc.requests = _types.SimpleNamespace(get=_sr_get, post=_old_req.post)
_sr = list(_disc.smartrecruiters({"name": "Pilot Company", "board": "PilotCompany"}))
_disc.requests = _old_req
check("phase3 discovery: SmartRecruiters feed (Houston intern gets a description)",
      any(j["title"] == "Program Intern, Energy Accounting" and len(j["description"]) > 80 and j["url"].endswith("/744000") for j in _sr), str(_sr)[:200])
_old_files = _disc.DIRECTORY_FILES
_disc.DIRECTORY_FILES = (IP / "ats_directory.yaml",)
_we = _disc.watchlist_entries()
_disc.DIRECTORY_FILES = _old_files
check("phase3 discovery: directory feeds with watch: true are scanned daily",
      any(e.get("board") == "PilotCompany" for e in _we)
      and not any(e.get("name") == "Quanta Services" for e in _we))

# ---------- wiring (static)
_fill_src = (IP / "pipeline" / "fill.py").read_text(encoding="utf-8")
_cc_src = (IP / "control_center.py").read_text(encoding="utf-8")
_ra_src = (IP / "pipeline" / "run_all.py").read_text(encoding="utf-8")
_rs_src = (IP / "pipeline" / "resolver.py").read_text(encoding="utf-8")
check("phase3 fill: opens resolver.target(), never the raw job-board link",
      "go, hint = resolver.target(job)" in _fill_src and 'page.goto(go' in _fill_src and 'page.goto(job["url"]' not in _fill_src)
check("phase3 control center: Find company links button + link line; Simplify instead uses the company link",
      '"resolve": ("Find company links", ["pipeline/resolver.py"])' in _cc_src and "link_line(job)" in _cc_src
      and "open_in_chrome(resolver.target(job)[0]" in _cc_src)
check("phase3 daily run + single step", 'stage("Direct links", resolver.run)' in _ra_src and '"resolve"' in _ra_src)
check("phase3 resolver: no clicks, no browser, no LinkedIn fetching",
      ".click(" not in _rs_src and "playwright" not in _rs_src.lower().replace("never", "") and "require_lock" in _rs_src)

# ---------- browser: a LinkedIn job resolved to a (local) Greenhouse form opens and fills there
try:
    _jid = "p3_browser"
    conn.execute("INSERT OR REPLACE INTO jobs (id, company, title, url, status, score, resume, cover) VALUES (?,?,?,?,?,?,?,?)",
                 (_jid, "GH Resolved Co", "Finance Intern", LI + "999", "PACKET_READY", 90, other["resume"], other["cover"]))
    conn.commit()
    resolver.save_results({_jid: {"kind": "direct", "url": base + "/greenhouse.io/form.html", "ats": "greenhouse", "how": "test"}})
    _urls = {}
    def _spy3(conn_, opened):
        for job, page in opened:
            _urls[job["id"]] = (page.url, page.input_value("#first_name"))
    fill.confirm_manual = _spy3
    fill.run(job_id=_jid, limit=1)
    _u = _urls.get(_jid, ("", ""))
    check("browser: phase3 job-board job opens the company form, not LinkedIn, and fills it",
          _u[0].startswith(base) and "linkedin" not in _u[0] and _u[1] == "Shehab", str(_u))
    _fs = Path(other["resume"]).parent / "FILL_SHEET.txt"
    check("browser: phase3 fill sheet lists the company link", "Company link: " + base in _fs.read_text(encoding="utf-8"))
    conn.execute("DELETE FROM jobs WHERE id=?", (_jid,)); conn.commit()
except Exception as exc:
    print("SKIP phase3 browser check (Playwright or Chrome not available here):", repr(exc)[:120])
