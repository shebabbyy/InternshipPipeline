"""Phase 3: job-board link resolver.

Most postings the job boards find (LinkedIn especially) carry only a job-board link. This step finds where to
apply on the employer's own site, so the apply window can use the right tool (Workday filler, Greenhouse/Lever
autofill, or Simplify on the company's ATS) instead of a LinkedIn page.

It never opens a job-board page (LinkedIn, Indeed, ...): that would be scraping them. It only reads public
employer feeds and careers pages. Order, per job:
  1. Company directory (ats_directory.yaml, researched by hand + ats_directory_found.yaml, learned) plus the
     watchlist feeds. Feed systems (Workday, Greenhouse, Lever, SmartRecruiters) are searched for the same title
     in the Houston area: one clear match = DIRECT link. Two close matches = no guess.
  2. No directory entry: try the company name as a Greenhouse / Lever / SmartRecruiters board (a few requests);
     kept only when the same title is found there.
  3. A careers page in the directory: read it once for links to an ATS (Workday, Greenhouse, iCIMS ...).
  4. Company uses a system without a public feed (iCIMS, SuccessFactors, Oracle, Dayforce ...): CAREERS link
     (its search page with the title filled in where the directory has a search address). He finds the posting
     there and clicks Simplify Autofill.
  5. Nothing found: stays on the job-board link ("apply manually").
Results: output/_resolved_links.json (job id -> url, kind, how). The database and the Sheet are not changed.
Two jobs that resolve to the same direct link are reported as duplicates (nothing is skipped automatically).

  python pipeline/resolver.py                 resolve every ready job that only has a job-board link
  python pipeline/resolver.py --job ID        one job
  python pipeline/resolver.py --force         re-check jobs already resolved
  python pipeline/resolver.py --dry-run       show what it would find, write nothing
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import yaml

from common import CONFIG, ROOT, db, log

DIRECTORY_FILE = ROOT / "ats_directory.yaml"          # researched by hand (in the repo)
FOUND_FILE = ROOT / "ats_directory_found.yaml"        # learned by this step (guessed boards, careers-page links)
RESOLVED_FILE = ROOT / "output" / "_resolved_links.json"

FEED_ATS = ("workday", "greenhouse", "lever", "smartrecruiters")
STATUSES = ("QUALIFIED", "PACKET_READY", "FILLED")    # jobs worth resolving
MATCH_MIN = 0.82        # title similarity needed for a direct link
MATCH_GAP = 0.06        # the best match must beat the second best by this much, else no guess
RECHECK_DAYS = 7        # "careers" and "none" results are looked at again after this many days
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) InternshipPipeline/1.0",
      "Accept": "application/json, text/html;q=0.9"}


# ---------------------------------------------------------------- names and titles
_CO_DROP = {"inc", "incorporated", "llc", "llp", "lp", "ltd", "plc", "corp", "corporation", "company", "co",
            "holdings", "the", "us", "usa", "na", "group", "international", "intl"}


def norm_company(name: str) -> str:
    """'RSM US LLP' -> 'rsm', 'ENGIE North America Inc.' -> 'engie', 'Quanta Services, Inc.' -> 'quanta services'."""
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"\bnorth america\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [w for w in s.split() if w not in _CO_DROP]
    return " ".join(words)


_TITLE_DROP = {"the", "a", "an", "of", "and", "for", "to", "in", "at", "with", "program", "opportunities", "opportunity"}
_LOC_TAIL = re.compile(r"\s[-|,]\s*(houston|the woodlands|sugar land|webster|katy|spring|texas|tx|usa|united states|remote)\b.*$", re.I)


def norm_title(title: str) -> str:
    """Lower case, no brackets, no location tail, no filler words: 'Audit Intern - Houston, TX' -> 'audit intern'."""
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", title or "")
    t = _LOC_TAIL.sub("", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    t = re.sub(r"\binternships?\b", "intern", t)
    return " ".join(w for w in t.split() if w not in _TITLE_DROP)


_SEASON = re.compile(r"\b(20\d\d|summer|fall|spring|winter)\b")


def _core(title: str) -> str:
    """Title without season/year words (both sides of a match often differ only there)."""
    return " ".join(_SEASON.sub(" ", norm_title(title)).split()) or norm_title(title)


def title_score(a: str, b: str) -> float:
    na, nb = _core(a), _core(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    sa, sb = set(na.split()), set(nb.split())
    jac = len(sa & sb) / len(sa | sb)
    return max(SequenceMatcher(None, na, nb).ratio(), jac)


def best_match(title: str, candidates: list[dict]) -> tuple[dict | None, float, str]:
    """candidates: {title, url, location}. Only Houston-area (or unknown) locations count.
    Returns (match or None, score, reason)."""
    import discover
    scored = []
    for c in candidates:
        loc = (c.get("location") or "").strip()
        if loc and not discover.near_houston(loc) and not re.search(r"\b(multiple|various|\d+ locations?)\b", loc, re.I):
            continue
        scored.append((title_score(title, c.get("title") or ""), c))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < MATCH_MIN:
        return None, (scored[0][0] if scored else 0.0), "no posting with that title in the Houston area"
    top = scored[0]
    same = [s for s in scored[1:] if s[1].get("url") != top[1].get("url")]
    if same and top[0] - same[0][0] < MATCH_GAP:
        return None, top[0], f"two similar postings ({top[1].get('title')} / {same[0][1].get('title')}); not guessing"
    return top[1], top[0], "matched"


def search_words(title: str, n: int = 5) -> str:
    return " ".join(_core(title).split()[:n])


# ---------------------------------------------------------------- network (job boards are never fetched)
class Http:
    """requests wrapper with a refusal for job-board hosts and a polite delay. Tests pass fake get/post."""

    def __init__(self, get=None, post=None, delay: float = 0.6):
        import requests
        self._get, self._post, self.delay, self.count = get or requests.get, post or requests.post, delay, 0

    @staticmethod
    def allowed(url: str) -> bool:
        import apply_window as aw
        host = (urlparse(url or "").hostname or "").lower()
        return bool(host) and not aw._on(host, aw.JOB_BOARD_HOSTS)

    def _wait(self):
        if self.count and self.delay:
            time.sleep(self.delay)
        self.count += 1

    def get(self, url, **kw):
        if not self.allowed(url):
            raise PermissionError(f"refusing to fetch a job-board page: {url}")
        self._wait()
        return self._get(url, headers=UA, timeout=25, **kw)

    def post(self, url, **kw):
        if not self.allowed(url):
            raise PermissionError(f"refusing to fetch a job-board page: {url}")
        self._wait()
        return self._post(url, headers=UA, timeout=25, **kw)


def _json(resp):
    if resp.status_code >= 400:
        raise ValueError(f"HTTP {resp.status_code}")
    if "json" not in (resp.headers.get("content-type") or "").lower():
        raise ValueError("not JSON")
    return resp.json()


# ---------------------------------------------------------------- feed searches (public, read-only)
def feed_candidates(entry: dict, title: str, http: Http) -> list[dict]:
    ats, board = entry.get("ats"), entry.get("board") or ""
    if ats == "greenhouse":
        data = _json(http.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"))
        return [{"title": j.get("title"), "url": j.get("absolute_url"), "location": (j.get("location") or {}).get("name", "")}
                for j in data.get("jobs", [])]
    if ats == "lever":
        data = _json(http.get(f"https://api.lever.co/v0/postings/{board}?mode=json"))
        return [{"title": j.get("text"), "url": j.get("hostedUrl"), "location": (j.get("categories") or {}).get("location", "")}
                for j in data]
    if ats == "smartrecruiters":
        data = _json(http.get(f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
                              f"?q={quote_plus(search_words(title))}&limit=100"))
        out = []
        for j in data.get("content", []):
            loc = j.get("location") or {}
            out.append({"title": j.get("name"), "url": f"https://jobs.smartrecruiters.com/{board}/{j.get('id')}",
                        "location": ", ".join(x for x in (loc.get("city"), loc.get("region")) if x)})
        return out
    if ats == "workday":
        import discover
        api, public, _ = discover.workday_parts(entry["url"])
        out, seen = [], set()
        for text in dict.fromkeys((search_words(title, 8), search_words(title, 3))):
            data = _json(http.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": text}))
            for p in data.get("jobPostings", []):
                if p.get("externalPath") and p["externalPath"] not in seen:
                    seen.add(p["externalPath"])
                    out.append({"title": p.get("title"), "url": f"{public}{p['externalPath']}", "location": p.get("locationsText", "")})
            if any(title_score(title, c["title"] or "") >= MATCH_MIN for c in out):
                break
        return out
    return []


# ---------------------------------------------------------------- careers-page ATS detection
_DETECT = [
    ("workday", re.compile(r"https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?!wday\b)[A-Za-z0-9_-]+")),
    ("workday", re.compile(r"https?://wd\d+\.myworkdaysite\.com/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+")),
    ("greenhouse", re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"https?://jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("smartrecruiters", re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
    ("other", re.compile(r"https?://[a-z0-9.-]*\b(?:icims\.com|taleo\.net|successfactors\.(?:com|eu)|oraclecloud\.com|"
                         r"dayforcehcm\.com|paylocity\.com|ultipro\.com|ukg\.com|workforcenow\.adp\.com|jobvite\.com|"
                         r"ashbyhq\.com|ats\.rippling\.com|paycomonline\.net|eightfold\.ai|avature\.net|brassring\.com|"
                         r"csod\.com|workable\.com|bamboohr\.com|teamtailor\.com|applytojob\.com|recruitee\.com)"
                         r"[^\s\"'<>\\]*")),
]
_GH_NOT_BOARDS = {"embed", "v1", "jobs", "js"}


def detect_ats(html: str) -> list[dict]:
    """Directory entries for every ATS linked from a careers page, feed systems first."""
    found, seen = [], set()
    for ats, rx in _DETECT:
        for m in rx.finditer(html or ""):
            if ats == "workday":
                e = {"ats": "workday", "url": m.group(0).rstrip("/")}
            elif ats == "other":
                e = {"ats": "other", "url": m.group(0).replace("&amp;", "&")}
            else:
                board = m.group(1)
                if board.lower() in _GH_NOT_BOARDS:
                    continue
                e = {"ats": ats, "board": board}
            key = (e["ats"], (e.get("url") or e.get("board")).lower())
            if key not in seen:
                seen.add(key)
                found.append(e)
    return found


# ---------------------------------------------------------------- directory
def _load(path: Path, key: str = "companies") -> list[dict]:
    try:
        return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(key) or []
    except Exception:  # noqa: BLE001
        return []


def directory() -> list[dict]:
    """Researched + learned entries + the watchlist feeds (same shape: name, aliases, ats, url/board, careers, search)."""
    import discover
    entries = _load(DIRECTORY_FILE) + _load(FOUND_FILE)
    for w in discover.watchlist_entries():
        entries.append({"name": w["name"], "ats": w["ats"], "url": w.get("url"), "board": w.get("board"), "via": "watchlist"})
    return entries


def _names(entry: dict) -> list[str]:
    return [n for n in (norm_company(x) for x in [entry.get("name")] + list(entry.get("aliases") or [])) if n]


def same_company(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = a.split(), b.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return long_[:len(short)] == short and (len(short) >= 2 or len(short[0]) >= 5)


def entries_for(company: str, entries: list[dict]) -> list[dict]:
    me = norm_company(company)
    hits = [e for e in entries if any(same_company(me, n) for n in _names(e))]
    hits.sort(key=lambda e: (e.get("ats") not in FEED_ATS, e.get("via") == "watchlist"))
    return hits


def save_found(company: str, entry: dict, how: str) -> None:
    """Remember a feed or ATS link learned for a company, so the next run (and discovery) can use it."""
    rows = _load(FOUND_FILE)
    key = (entry["ats"], (entry.get("url") or entry.get("board") or "").lower())
    if any((r.get("ats"), (r.get("url") or r.get("board") or "").lower()) == key for r in rows):
        return
    row = {"name": company, "ats": entry["ats"]}
    row.update({k: entry[k] for k in ("url", "board") if entry.get(k)})
    row.update({"watch": entry["ats"] in ("workday", "greenhouse", "lever", "smartrecruiters"),
                "found": date.today().isoformat(), "how": how})
    rows.append(row)
    FOUND_FILE.write_text(
        "# Written by the link resolver (phase 3): careers systems it learned. Safe to delete; it relearns them.\n"
        + yaml.safe_dump({"companies": rows}, sort_keys=False, allow_unicode=True), encoding="utf-8")


def search_url(entry: dict, title: str) -> str:
    url = entry.get("search") or entry.get("url") or entry.get("careers") or ""
    return url.replace("{q}", quote_plus(search_words(title))) if url else ""


# ---------------------------------------------------------------- the resolver
def _guesses(company: str) -> list[dict]:
    words = norm_company(company).split()
    if not words:
        return []
    joined, dashed = "".join(words), "-".join(words)
    out = [{"ats": "greenhouse", "board": joined}, {"ats": "lever", "board": dashed}]
    if dashed != joined:
        out.append({"ats": "lever", "board": joined})
    out.append({"ats": "smartrecruiters", "board": "".join(w.capitalize() for w in words)})
    return out


def resolve(job: dict, entries: list[dict] | None = None, http: Http | None = None, learn: bool = True) -> dict:
    """Where to apply for this job. Returns {kind: direct|careers|none|own, url, ats, how}."""
    import apply_window as aw
    http = http or Http()
    entries = directory() if entries is None else entries
    url, title, company = job.get("url") or "", job.get("title") or "", job.get("company") or ""
    base = {"job": job.get("id"), "company": company, "title": title, "from": url, "checked": date.today().isoformat()}
    if aw.route(url) != "job_board":
        return {**base, "kind": "own", "url": url, "ats": aw.route(url), "how": "already an employer link"}
    mine = entries_for(company, entries)
    tried, careers, notes = set(), None, []

    def try_feed(entry, how):
        key = (entry["ats"], (entry.get("url") or entry.get("board") or "").lower())
        if key in tried:
            return None
        tried.add(key)
        try:
            cands = feed_candidates(entry, title, http)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{entry['ats']} {entry.get('board') or entry.get('url')}: {str(exc)[:50]}")
            return None
        hit, score, why = best_match(title, cands)
        if hit:
            return {**base, "kind": "direct", "url": hit["url"], "ats": entry["ats"], "score": round(score, 2),
                    "matched_title": hit["title"], "how": f"same title on the company's {entry['ats'].title()} feed ({how})"}
        if cands:
            notes.append(f"{entry['ats']}: {why}")
        return None

    # 1. known feeds for this company
    for e in mine:
        if e.get("ats") in FEED_ATS and (e.get("url") or e.get("board")):
            res = try_feed(e, "directory" if e.get("via") != "watchlist" else "watchlist")
            if res:
                return res
    # 2. no entry at all: try the name as a board (kept only on a title match)
    if not mine:
        for g in _guesses(company):
            res = try_feed(g, "guessed board name")
            if res:
                if learn:
                    save_found(company, g, "board name guessed, same title found")
                return res
    # 3. careers pages: look for ATS links
    for e in mine:
        page = e.get("careers")
        if not page or e.get("ats") in FEED_ATS and (e.get("url") or e.get("board")):
            continue
        try:
            resp = http.get(page)
            html = resp.text if resp.status_code < 400 else ""
        except Exception as exc:  # noqa: BLE001
            notes.append(f"careers page: {str(exc)[:50]}")
            html = ""
        for d in detect_ats(html):
            if d["ats"] in FEED_ATS:
                res = try_feed(d, "linked from the careers page")
                if res:
                    if learn:
                        save_found(company, d, "linked from the careers page")
                    return res
            elif not careers:
                careers = {**base, "kind": "careers", "url": d["url"], "ats": "other",
                           "how": "careers system linked from the company's careers page; find the role by its title"}
                if learn:
                    save_found(company, d, "linked from the careers page")
    # 4. company uses a system without a public feed: its careers search page
    for e in mine:
        if e.get("ats") not in FEED_ATS:
            target = search_url(e, title)
            if target:
                kind_of = e.get("ats") or "careers site"
                return {**base, "kind": "careers", "url": target, "ats": kind_of,
                        "how": f"company careers site ({kind_of}); search for the role by its title"}
    if careers:
        return careers
    # feed company but no title match: its job list is still better than LinkedIn
    for e in mine:
        if e.get("ats") in FEED_ATS:
            board = e.get("board") or ""
            listing = {"greenhouse": f"https://job-boards.greenhouse.io/{board}", "lever": f"https://jobs.lever.co/{board}",
                       "smartrecruiters": f"https://jobs.smartrecruiters.com/{board}"}.get(e["ats"], e.get("url") or "")
            if listing:
                return {**base, "kind": "careers", "url": listing, "ats": e["ats"],
                        "how": f"company's {e['ats'].title()} job list (no exact title match: " + "; ".join(notes)[:120] + ")"}
    return {**base, "kind": "none", "url": url, "ats": "job_board",
            "how": "no company link found" + (f" ({'; '.join(notes)[:140]})" if notes else "") + "; apply from the job board"}


# ---------------------------------------------------------------- stored results
def load_results() -> dict:
    try:
        return json.loads(RESOLVED_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_results(results: dict) -> None:
    RESOLVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESOLVED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(RESOLVED_FILE)


def mark_duplicates(results: dict, scores: dict) -> list[tuple[str, str]]:
    by_url: dict[str, list[str]] = {}
    for jid, r in results.items():
        r.pop("duplicate_of", None)
        if r.get("kind") == "direct":
            by_url.setdefault(r["url"].rstrip("/").lower(), []).append(jid)
    pairs = []
    for ids in by_url.values():
        if len(ids) > 1:
            ids.sort(key=lambda j: -(scores.get(j) or 0))
            for other in ids[1:]:
                results[other]["duplicate_of"] = ids[0]
                pairs.append((ids[0], other))
    return pairs


def target(job: dict) -> tuple[str, str | None]:
    """(url to open, banner text or None) for the apply window. Job-board links use the resolved link if there is one."""
    import apply_window as aw
    url = job.get("url") or ""
    if aw.route(url) != "job_board":
        return url, None
    r = load_results().get(job.get("id") or "", {})
    if r.get("kind") == "direct" and r.get("url"):
        return r["url"], None
    if r.get("kind") == "careers" and r.get("url"):
        return r["url"], (f"Company careers site (the job board had no direct link). Find this role: "
                          f"\"{job.get('title')}\". Open it, then click Simplify Autofill. Only you click Submit.")
    return url, None


def _stale(r: dict, force: bool) -> bool:
    if force or not r:
        return True
    if r.get("kind") == "direct":
        return False
    try:
        return (date.today() - date.fromisoformat(r.get("checked", "2000-01-01"))).days >= RECHECK_DAYS
    except ValueError:
        return True


def run(limit: int = 40, job_id: str | None = None, force: bool = False, dry_run: bool = False, http: Http | None = None) -> dict:
    import apply_window as aw
    conn = db()
    marks = ",".join("?" * len(STATUSES))
    where, args = (" AND id=?", (job_id,)) if job_id else ("", ())
    jobs = [dict(r) for r in conn.execute(f"SELECT * FROM jobs WHERE status IN ({marks}){where} ORDER BY score DESC",
                                          (*STATUSES, *args))]
    jobs = [j for j in jobs if aw.route(j.get("url") or "") == "job_board"]
    results = load_results()
    todo = [j for j in jobs if job_id or _stale(results.get(j["id"]), force)][:limit]
    if not todo:
        log(f"Direct links: nothing new to look up ({len(jobs)} job-board jobs, all checked recently).")
        return results
    http = http or Http()
    entries = directory()
    counts: dict[str, int] = {}
    for j in todo:
        r = resolve(j, entries, http, learn=not dry_run)
        results[j["id"]] = r
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        label = {"direct": f"DIRECT ({r['ats']})", "careers": "careers site", "none": "not found"}.get(r["kind"], r["kind"])
        log(f"  {j['company']} | {j['title']}: {label}. {r['how']}")
        if r["kind"] != "none" and not dry_run:
            entries = directory()   # pick up anything learned for the next job of the same company
    scores = {j["id"]: j.get("score") for j in jobs}
    for a, b in mark_duplicates(results, scores):
        ja, jb = next((x for x in jobs if x["id"] == a), {}), next((x for x in jobs if x["id"] == b), {})
        log(f"  Same posting: {ja.get('company')} | {ja.get('title')} and {jb.get('company')} | {jb.get('title')}. "
            "Skip one of them on the Apply tab.")
    if not dry_run:
        save_results(results)
    log(f"Direct links: {counts.get('direct', 0)} direct, {counts.get('careers', 0)} careers site, "
        f"{counts.get('none', 0)} not found (of {len(todo)} looked up; {http.count} requests)."
        + (" Dry run: nothing saved." if dry_run else ""))
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--job")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    if not a.dry_run:
        from common import require_lock
        require_lock("find direct links")
    run(limit=a.limit, job_id=a.job, force=a.force, dry_run=a.dry_run)
