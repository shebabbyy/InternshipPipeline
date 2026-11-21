"""Stage 1a: find new internship postings.

Sources:
  * Company watchlist: public Greenhouse / Lever / Workday career-site feeds (the core source).
  * JobSpy (LinkedIn/Indeed/Google/ZipRecruiter/Glassdoor), laptop only and optional.
Bauer Gateway / Handshake / LinkedIn alerts arrive by email; add good ones to the Sheet by hand
(Company + Role + Link) and the pipeline picks them up.

  python pipeline/discover.py --check    test every watchlist link (config.yaml + watchlist_candidates.yaml)
                                         and save the working ones to watchlist_found.yaml
"""
from __future__ import annotations

import argparse
import re
import time
from urllib.parse import urlparse

import requests
import yaml

from common import CONFIG, ROOT, clean_text, db, log, upsert_job

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) InternshipPipeline/1.0", "Accept": "application/json"}
FOUND_FILE = ROOT / "watchlist_found.yaml"            # written by --check; read on every run
CANDIDATES_FILE = ROOT / "watchlist_candidates.yaml"  # unverified links to test with --check

INTERN_RE = re.compile(r"\b(intern|interns|internship|internships|co-?op|summer analyst|summer associate)\b", re.I)
# Houston area only (Nov 18 decision): no "Remote", no bare "TX", no other Texas cities.
NEAR_RE = re.compile(r"\b(houston|the woodlands|sugar land|baytown|pearland|deer park|tomball|missouri city|league city|"
                     r"friendswood|clear lake|la porte|channelview|galena park|fulshear)\b", re.I)
# Names that also exist in other states only count with a Texas marker next to them.
NEAR_TX_RE = re.compile(r"\b(katy|pasadena|spring|humble|cypress|stafford|webster|richmond|conroe|rosenberg|alvin)\b.*\b(tx|texas)\b", re.I)


def is_intern(title: str) -> bool:
    """True for 'Finance Intern'; False for 'Internal Auditor' or 'International Tax Senior'."""
    title = title or ""
    return bool(INTERN_RE.search(title)) or any(
        re.search(r"\b" + re.escape(k) + r"\b", title, re.I) for k in CONFIG.get("title_keywords") or [])


def relevant(title: str) -> bool:
    """False for internships clearly outside finance/accounting/business (engineering, IT, lab...), so they are
    never scored or put in the Sheet. The word list is skip_title_words in config.yaml."""
    words = CONFIG.get("skip_title_words") or []
    return not any(re.search(r"\b" + re.escape(w), title or "", re.I) for w in words)


def near_houston(loc: str) -> bool:
    """Houston-area locations, or Workday's unresolved '3 Locations' (the real list is looked up afterwards).
    A blank or remote-only location no longer passes."""
    loc = (loc or "").strip()
    return bool(NEAR_RE.search(loc)) or bool(NEAR_TX_RE.search(loc)) or bool(re.fullmatch(r"\d+ locations?", loc, re.I))


def _s(value) -> str:
    """JobSpy returns NaN/None for missing cells; turn those into '' instead of the text 'nan'."""
    if value is None or value != value:  # NaN != NaN
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


# ---------------------------------------------------------------- JobSpy
def from_jobspy(cloud: bool) -> list[dict]:
    boards = (CONFIG.get("boards_cloud") if cloud else CONFIG.get("boards_laptop")) or []
    if not boards:
        return []
    try:
        from jobspy import scrape_jobs
    except Exception as exc:  # noqa: BLE001
        log(f"  Job boards skipped: python-jobspy is not available ({exc.__class__.__name__}). The watchlist still runs.")
        return []
    found = []
    for term in CONFIG["searches"]:
        for board in boards:  # one board at a time so a block on one doesn't kill the rest
            try:
                df = scrape_jobs(
                    site_name=[board], search_term=term,
                    google_search_term=f"{term} internship near {CONFIG['location']} since yesterday",
                    location=CONFIG["location"], distance=CONFIG["distance_miles"],
                    results_wanted=CONFIG["results_per_search"],
                    hours_old=CONFIG["hours_old"], country_indeed="USA",
                    linkedin_fetch_description=(board == "linkedin"),
                    verbose=0,
                )
            except Exception as exc:  # noqa: BLE001
                log(f"  {board}: skipped ({exc.__class__.__name__})")
                continue
            for _, r in df.iterrows():
                found.append({
                    "company": _s(r.get("company")), "title": _s(r.get("title")),
                    "location": _s(r.get("location")), "url": _s(r.get("job_url_direct")) or _s(r.get("job_url")),
                    "description": _s(r.get("description")), "source": board,
                })
            time.sleep(3)
    return found


# ---------------------------------------------------------------- watchlist
def get_json(resp):
    """Workday sometimes serves an HTML maintenance page with HTTP 200; don't crash on it."""
    if resp.status_code >= 400:
        raise ValueError(f"HTTP {resp.status_code}")
    if "json" not in resp.headers.get("content-type", "").lower():
        raise ValueError(f"not JSON (status {resp.status_code}): site may be down for maintenance")
    return resp.json()


def greenhouse(entry):
    data = get_json(requests.get(f"https://boards-api.greenhouse.io/v1/boards/{entry['board']}/jobs?content=true",
                                 headers=UA, timeout=30))
    for j in data.get("jobs", []):
        yield {"company": entry["name"], "title": j["title"], "location": (j.get("location") or {}).get("name", ""),
               "url": j["absolute_url"], "description": clean_text(j.get("content", "")), "source": "greenhouse"}


def lever(entry):
    data = get_json(requests.get(f"https://api.lever.co/v0/postings/{entry['board']}?mode=json", headers=UA, timeout=30))
    for j in data:
        yield {"company": entry["name"], "title": j["text"], "location": (j.get("categories") or {}).get("location", ""),
               "url": j["hostedUrl"], "description": clean_text(j.get("descriptionPlain", "")), "source": "lever"}


def workday_parts(url: str) -> tuple[str, str, str]:
    """Return (api base, public base, host) for both Workday URL styles:
       https://TENANT.wdN.myworkdayjobs.com/[en-US/]SITE
       https://wdN.myworkdaysite.com/[en-US/]recruiting/TENANT/SITE"""
    u = urlparse(url)
    host = u.netloc
    parts = [p for p in u.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
    if "myworkdaysite" in host:
        tenant, site = parts[1], parts[2]
        return f"https://{host}/wday/cxs/{tenant}/{site}", f"https://{host}/recruiting/{tenant}/{site}", host
    tenant, site = host.split(".")[0], parts[0]
    return f"https://{host}/wday/cxs/{tenant}/{site}", f"https://{host}/{site}", host


WORKDAY_SEARCHES = ("intern", "summer analyst")   # banks title their internships "Summer Analyst"
WORKDAY_MAX = 600          # most postings looked at per search
WORKDAY_EMPTY_PAGES = 3    # stop after this many pages in a row with no internship titles


def workday(entry):
    """Workday's search is fuzzy ('intern' also matches 'internal' in any description), so big employers return
    hundreds of results with the real internships first. Keep paging while pages still contain internship
    titles; stop after a few pages without any."""
    api, public, _ = workday_parts(entry["url"])
    seen = set()
    for term in WORKDAY_SEARCHES:
        offset = empty = 0
        while offset < WORKDAY_MAX and empty < WORKDAY_EMPTY_PAGES:
            resp = requests.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term},
                                 headers=UA, timeout=30)
            posts = get_json(resp).get("jobPostings", [])
            if not posts:
                break
            hits = 0
            for p in posts:
                if not p.get("title") or not p.get("externalPath") or not is_intern(p["title"]):
                    continue
                hits += 1
                if p["externalPath"] in seen:
                    continue
                seen.add(p["externalPath"])
                loc = p.get("locationsText") or ""
                desc = ""
                if near_houston(loc) and relevant(p["title"]):  # only fetch details for postings we might keep
                    try:
                        d = get_json(requests.get(f"{api}{p['externalPath']}", headers=UA, timeout=30))
                        info = d.get("jobPostingInfo", {})
                        desc = clean_text(info.get("jobDescription", ""))
                        if re.fullmatch(r"\d+ locations?", loc, re.I):  # "3 Locations": replace with the real list
                            places = [info.get("location")] + list(info.get("additionalLocations") or [])
                            loc = "; ".join(str(x) for x in places if x) or loc
                    except Exception:  # noqa: BLE001
                        pass
                yield {"company": entry["name"], "title": p["title"], "location": loc,
                       "url": f"{public}{p['externalPath']}", "description": desc, "source": "workday"}
            empty = 0 if hits else empty + 1
            offset += 20
            time.sleep(0.4)


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "workday": workday}


def _load(path) -> list[dict]:
    try:
        return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("watchlist") or []
    except Exception:  # noqa: BLE001
        return []


def watchlist_entries(include_candidates: bool = False) -> list[dict]:
    """config.yaml watchlist + links verified by --check (+ untested candidates when asked). One per link."""
    entries = list(CONFIG.get("watchlist") or []) + _load(FOUND_FILE)
    if include_candidates:
        entries += _load(CANDIDATES_FILE)
    seen, out = set(), []
    for e in entries:
        key = (e.get("ats"), (e.get("url") or e.get("board") or "").lower().rstrip("/"))
        if e.get("ats") in FETCHERS and key[1] and key not in seen:
            seen.add(key)
            out.append(e)
    return out


def from_watchlist() -> list[dict]:
    found = []
    for entry in watchlist_entries():
        try:
            jobs = [j for j in FETCHERS[entry["ats"]](entry) if is_intern(j["title"])]
            log(f"  watchlist {entry['name']}: {len(jobs)} intern postings")
            found += jobs
        except Exception as exc:  # noqa: BLE001
            log(f"  watchlist {entry['name']}: skipped today ({str(exc)[:80]}); if this repeats for days, run Check watchlist (tools folder)")
    return found


def check() -> None:
    """Test every link. Working ones are saved to watchlist_found.yaml and used from the next run on."""
    good, bad = [], []
    for entry in watchlist_entries(include_candidates=True):
        try:
            jobs = list(FETCHERS[entry["ats"]](entry))
            near = [j for j in jobs if near_houston(j["location"]) and is_intern(j["title"]) and relevant(j["title"])]
            print(f"  OK    {entry['name']}: feed works, {len(near)} Houston-area business-side intern postings right now")
            good.append(entry)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  {entry['name']}: {str(exc)[:70]}")
            bad.append(entry)
    in_config = {(e.get("ats"), (e.get("url") or e.get("board") or "").lower().rstrip("/")) for e in CONFIG.get("watchlist") or []}
    keep = [e for e in good if (e.get("ats"), (e.get("url") or e.get("board") or "").lower().rstrip("/")) not in in_config]
    FOUND_FILE.write_text(
        "# Written by CHECK_WATCHLIST.bat: career-site links that were tested and work. Safe to delete; re-run to rebuild.\n"
        + yaml.safe_dump({"watchlist": keep}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"\n{len(good)} working ({len(keep)} saved to {FOUND_FILE.name}), {len(bad)} failed.")
    if bad:
        print("Failed ones are simply not used. Those companies likely use a different careers system: "
              "rely on their job alerts, or fix the link in watchlist_candidates.yaml and run this again.")


def run(cloud: bool = False) -> int:
    conn = db()
    jobs = from_watchlist() + from_jobspy(cloud)
    new = skipped = 0
    for j in jobs:
        if not j["company"] or not j["title"] or not is_intern(j["title"]) or not near_houston(j["location"]):
            continue
        if not relevant(j["title"]):
            skipped += 1
            continue
        if len(clean_text(j["description"])) < 80:
            j["description"] = (j["description"] or "") + " (short description: open the link)"
        new += upsert_job(conn, j)
    log(f"Discovery: {len(jobs)} postings seen, {new} new" + (f", {skipped} off-target titles ignored." if skipped else "."))
    return new


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cloud", action="store_true", help="watchlist feeds only")
    ap.add_argument("--check", action="store_true", help="test every watchlist link and save the working ones")
    args = ap.parse_args()
    check() if args.check else run(args.cloud)
