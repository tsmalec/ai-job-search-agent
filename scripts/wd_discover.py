"""wd_discover.py — find Workday job boards in bulk from the Wayback Machine's archive of *.myworkdayjobs.com links,
then confirm each one is live (method from the CareerScout repo, Ramcharan747/careerscout, MIT; our own
stdlib code).

Why: board_sweep.py only sweeps Workday boards it already knows. Workday has no public directory, but the web
archive has seen millions of links like acme.wd5.myworkdayjobs.com/en-US/External/job/..., and each one names a
tenant (acme), an environment (wd5) and a site (External). That's exactly the slug board_sweep needs.

Steps: 1) ask the archive's CDX index for every archived URL on each wdN host (paged, de-duplicated by URL key);
2) pull tenant|wdN|site out of each; 3) ask each new board's job API once (POST /wday/cxs/...); 4) save every live
board with open postings to data/sweep_boards.json "added", so the next sweep reads it.

NOT RUNNABLE FROM CLAUDE'S WORKSPACE: web.archive.org answers 403 there. Run it on the candidate's PC or the scheduler's box.

Usage (from the repo root):
    python scripts/wd_discover.py                   wd1, wd3, wd5, wd12 (the four CareerScout found in use)
    python scripts/wd_discover.py --envs wd5        one environment
    python scripts/wd_discover.py --pages 5         CDX pages per environment (default 20; each page is large)
    python scripts/wd_discover.py --dry             report finds, don't save them

Other platforms: JazzHR (applytojob.com), Oracle Cloud Recruiting, iCIMS. board_sweep has no lanes for
these yet, so the finds go to data/discovered_boards.json (and a plain list, data/discovered_<platform>.txt), NOT to
sweep_boards.json. the candidate runs this and hands the list over; the lanes get built from it.
    python scripts/wd_discover.py --platform jazzhr             Wayback + Common Crawl, then a light live check
    python scripts/wd_discover.py --platform oracle --source cc one source only (wayback | cc | both, default both)
    python scripts/wd_discover.py --platform icims --crawls 3   newest N Common Crawl crawls (default 2)
    python scripts/wd_discover.py --platform jazzhr --from-file urls.txt
                                  skip the archives: read board URLs (one per line) you found yourself, e.g. from a
                                  Google `site:applytojob.com/apply "remote"` search, extract the slugs, check them
Live check per platform: JazzHR = the /apply page lists job links; Oracle = the public requisitions API reports a job
count; iCIMS = the search page lists jobs. Status: live (jobs), empty (board answers, no jobs), dead (gone/inactive),
unknown (blocked or errored: kept apart, retry later). Only live + empty reach the .txt list.
"""
import json, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_sweep as bs  # noqa: E402

CDX = "https://web.archive.org/cdx/search/cdx"
ENVS = ("wd1", "wd3", "wd5", "wd12")
URL_RE = re.compile(r"^(?:https?://)?([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)",
                    re.I)
SKIP_SITE = {"wday", "job", "jobs", "login", "apply", "robots.txt", "favicon.ico", "en-us", "userhome"}


def cdx_page(env, page):
    q = urllib.parse.urlencode({"url": f"*.{env}.myworkdayjobs.com", "output": "json", "fl": "original",
                                "collapse": "urlkey", "filter": "statuscode:200", "page": page})
    req = urllib.request.Request(f"{CDX}?{q}", headers={"User-Agent": bs.UA})
    for k in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                rows = json.loads(r.read().decode("utf-8", "replace") or "[]")
            return [x[0] for x in rows[1:]]  # first row is the header
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None  # past the last page
            if e.code == 403:
                raise SystemExit("web.archive.org refused the request (403). This runs on the candidate's PC or the scheduler's box, "
                                 "not in Claude's workspace.")
        except Exception as e:
            last = type(e).__name__
        time.sleep(5 * (k + 1))
    raise SystemExit(f"web.archive.org didn't answer after 3 tries ({locals().get('last', 'error')}). "
                     "If this is Claude's workspace, run it on the candidate's PC or the scheduler's box instead.")


# ---- other platforms: JazzHR, Oracle Cloud Recruiting, iCIMS --------------------------------------------------
CC_INDEX = "https://index.commoncrawl.org"
OTHER = {
    # archive host patterns, the archive-side filter that keeps only career-site URLs, the URL -> slug regex
    "jazzhr": dict(hosts=["*.applytojob.com", "*.theresumator.com"], wb_filter=None, cc_filter=None,
                   rx=re.compile(r"^(?:https?://)?([a-z0-9-]+)\.(?:applytojob|theresumator)\.com", re.I),
                   skip={"www", "app", "api", "careers", "help", "support", "blog", "demo", "jazzhr", "resumator",
                         "static", "assets", "cdn", "mail", "login", "signup"}),
    # one query over all of *.oraclecloud.com timed out (Wayback) / 504'd (Common Crawl), so go region by region
    "oracle": dict(hosts=[f"*.fa.{r}.oraclecloud.com" for r in
                          ("us2", "us6", "us7", "em2", "em3", "em4", "em5", "em8", "ap1", "ap2", "ap3", "ca2", "ca3",
                           "sa1", "ocs")], wb_filter=r"original:.*hcmUI/CandidateExperience.*",
                   cc_filter="~url:hcmUI/CandidateExperience",
                   rx=re.compile(r"^(?:https?://)?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.oraclecloud\.com)/hcmUI/"
                                 r"CandidateExperience/[A-Za-z_-]+/sites/([A-Za-z0-9_-]+)", re.I), skip=set()),
    # icims.com's own CDX index is far bigger than applytojob.com's, and limit=10000 503'd/504'd twice running
    # the full crawl (though an identical one-batch pilot had just succeeded) - smaller batches, less to time out on.
    "icims": dict(hosts=["*.icims.com"], wb_filter=None, cc_filter=None, wb_batch=3_000,
                  rx=re.compile(r"^(?:https?://)?((?:[a-z0-9-]+)\.icims\.com)", re.I),
                  skip={"www", "app", "api", "login", "help", "support", "blog", "static", "assets", "cdn", "mail",
                        "icims", "developer", "community", "partners"}),
}


def slug_from_url(platform, u):
    """URL -> board slug, or None. JazzHR: the subdomain. Oracle: host|site. iCIMS: the full host."""
    cfg = OTHER[platform]
    m = cfg["rx"].match(u.strip())
    if not m:
        return None
    if platform == "jazzhr":
        s = m.group(1).lower()
        return None if s in cfg["skip"] else s
    if platform == "oracle":
        return f"{m.group(1).lower()}|{m.group(2)}"
    host = m.group(1).lower()
    return None if host.split(".")[0] in cfg["skip"] else host


def http_get(url, timeout=20):
    """(status, final_url, body) · status 0 = couldn't connect. Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": bs.UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.read(1_500_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, ""
    except Exception:
        return 0, url, ""


def check_board(platform, slug):
    """(status, jobs) · live | empty | dead | unknown. One request per board."""
    if platform == "jazzhr":
        st, final, body = http_get(f"https://{slug}.applytojob.com/apply")
        if st in (404, 410) or (st == 200 and final and f"{slug}.applytojob.com" not in final.lower()):
            return "dead", 0
        if st != 200:
            return "unknown", 0
        if "Inactive Career Page" in body[:4000]:
            return "dead", 0
        n = len(set(re.findall(r"/apply/([A-Za-z0-9]{8,12})/", body)))
        return ("live", n) if n else ("empty", 0)
    if platform == "oracle":
        host, _, site = slug.partition("|")
        st, _, body = http_get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                               f"&finder=findReqs;siteNumber={site},limit=1")
        if st in (404, 410):
            return "dead", 0
        if st != 200:
            return "unknown", 0
        try:
            n = int(json.loads(body)["items"][0].get("TotalJobsCount") or 0)
        except Exception:
            return "unknown", 0
        return ("live", n) if n else ("empty", 0)
    st, _, body = http_get(f"https://{slug}/jobs/search?ss=1&in_iframe=1")   # iCIMS
    if st in (404, 410):
        return "dead", 0
    if st != 200:
        return "unknown", 0
    n = len(set(re.findall(r"/jobs/(\d+)/", body)))
    return ("live", n) if n else ("empty", 0)


WB_BATCH = 10_000


def wayback_urls(platform, pages):
    """Yield archived URLs from the Wayback CDX index (reuses cdx_page's retry and 403 message).

    url=*.domain.com misses subdomains when domain.com is a bare 2-label host (applytojob.com, icims.com;
    confirmed by hand - it returned just the apex URL). matchType=domain is the syntax that actually walks
    subdomains - but it doesn't support page=N (showNumPages comes back null, page=0 quietly returns just the
    apex row), so this batches with limit + resumeKey instead. `pages` now means "how many batches", not literal
    CDX pages; deeper hosts (fa.us2.oraclecloud.com) work the same way either way, so this applies to all of them.
    """
    cfg = OTHER[platform]
    batch = cfg.get("wb_batch", WB_BATCH)
    pages = pages * (WB_BATCH // batch) if batch < WB_BATCH else pages  # smaller batch -> more of them, same coverage
    for host in cfg["hosts"]:
        bare = host[2:] if host.startswith("*.") else host
        resume = None
        for batch_n in range(pages):
            q = {"url": bare, "matchType": "domain", "output": "json", "fl": "original", "collapse": "urlkey",
                 "filter": ["statuscode:200"] + ([cfg["wb_filter"]] if cfg["wb_filter"] else []),
                 "limit": batch, "showResumeKey": "true"}
            if resume:
                q["resumeKey"] = resume
            req = urllib.request.Request(f"{CDX}?{urllib.parse.urlencode(q, doseq=True)}", headers={"User-Agent": bs.UA})
            rows, why = None, "no response"
            for k in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=120) as r:
                        rows = json.loads(r.read().decode("utf-8", "replace") or "[]")
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (400, 404):
                        rows = []
                        break
                    why = f"HTTP {e.code}"
                    if e.code == 429:   # rate-limited: retrying in seconds only extends the lockout, so stop this source
                        print("  wayback: HTTP 429 (rate limit). Stopping Wayback for this run; wait 30-60 minutes "
                              "before trying it again, or use --source cc.", flush=True)
                        return
                    if e.code == 403:
                        raise SystemExit("web.archive.org refused the request (403). Run this on the candidate's PC or the scheduler's box.")
                except Exception as e:
                    why = f"{type(e).__name__}: {e}"
                time.sleep(5 * (k + 1))
            if rows is None:
                print(f"  wayback {host} batch {batch_n + 1}: no answer ({why}), moving on", flush=True)
                break
            data = rows[1:] if rows else []   # drop the header row
            resume = None
            if len(data) >= 2 and data[-2] == []:   # trailing [[], [resumeKey]] marks more data to fetch
                resume = data[-1][0] if data[-1] else None
                data = data[:-2]
            yield from (x[0] for x in data)
            print(f"  wayback {host} batch {batch_n + 1} done ({len(data):,} rows)", flush=True)
            if not resume:
                break
            time.sleep(8)   # slowed from 6s further (reduce 429 risk)
        time.sleep(10)   # between hosts too (15 Oracle regions back to back hit Wayback's rate limit)


def cc_urls(platform, crawls, pages):
    """Yield URLs from the newest `crawls` Common Crawl indexes. Each index page is a few MB of JSON lines."""
    cfg = OTHER[platform]
    st, _, body = http_get(f"{CC_INDEX}/collinfo.json", timeout=60)
    if st != 200:
        print(f"  commoncrawl: couldn't read the crawl list (HTTP {st}); skipping this source", flush=True)
        return
    ids = [c["id"] for c in json.loads(body)][:crawls]
    for cid in ids:
        for host in cfg["hosts"]:
            base = {"url": host, "output": "json", "fl": "url"}
            if cfg["cc_filter"]:
                base["filter"] = cfg["cc_filter"]
            st, _, nb = http_get(f"{CC_INDEX}/{cid}-index?{urllib.parse.urlencode({**base, 'showNumPages': 'true'})}", 120)
            try:
                npages = min(int(json.loads(nb)["pages"]), pages)
            except Exception:
                print(f"  commoncrawl {cid} {host}: no page count (HTTP {st}), skipping", flush=True)
                continue
            for pg in range(npages):
                st, _, txt = http_get(f"{CC_INDEX}/{cid}-index?{urllib.parse.urlencode({**base, 'page': pg})}", 180)
                if st != 200:
                    print(f"  commoncrawl {cid} {host} page {pg + 1}: HTTP {st}, moving on", flush=True)
                    continue
                for line in txt.splitlines():
                    try:
                        yield json.loads(line)["url"]
                    except Exception:
                        pass  # best effort: this step is optional, carry on without it
                print(f"  commoncrawl {cid} {host} page {pg + 1}/{npages} done", flush=True)
                time.sleep(1)


def discover_other(platform, source, crawls, pages, dry, from_file):
    path = bs.DATA / "discovered_boards.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    book = store.setdefault(platform, {})
    seen_urls = {}
    if from_file:
        srcs = [("file", (l.strip() for l in Path(from_file).read_text(encoding="utf-8").splitlines() if l.strip()))]
    else:
        srcs = []
        if source in ("wayback", "both"):
            srcs.append(("wayback", wayback_urls(platform, pages)))
        if source in ("cc", "both"):
            srcs.append(("cc", cc_urls(platform, crawls, pages)))
    for name, it in srcs:
        n = 0
        for u in it:
            s = slug_from_url(platform, u)
            if s and s.lower() not in {k.lower() for k in book} and s not in seen_urls:
                seen_urls[s] = (u, name)
            n += 1
        print(f"{name}: read {n:,} URLs, {len(seen_urls):,} new {platform} boards so far", flush=True)
    print(f"{len(seen_urls):,} new {platform} boards. Checking each once...", flush=True)

    today = datetime.now().date().isoformat()
    tally = {"live": 0, "empty": 0, "dead": 0, "unknown": 0}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(check_board, platform, s): s for s in seen_urls}
        for i, f in enumerate(as_completed(futs), 1):
            s = futs[f]
            try:
                status, jobs = f.result()
            except Exception:
                status, jobs = "unknown", 0
            tally[status] += 1
            book[s] = dict(status=status, jobs=jobs, first_seen=today, via=seen_urls[s][1], url=seen_urls[s][0])
            if status == "live":
                print(f"  ✅ {platform}:{s}  ({jobs} jobs)", flush=True)
            if i % 100 == 0:
                print(f"  ... {i:,}/{len(seen_urls):,} checked, {tally['live']} live", flush=True)

    keep = sorted(s for s, v in book.items() if v["status"] in ("live", "empty"))
    print(f"\n{platform}: live {tally['live']} · empty {tally['empty']} · dead {tally['dead']} · "
          f"unknown {tally['unknown']} (retry later) · list now {len(keep)} boards")
    if not dry:
        path.write_text(json.dumps(store, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        (bs.DATA / f"discovered_{platform}.txt").write_text("\n".join(keep) + "\n", encoding="utf-8", newline="\n")
        print(f"saved data/discovered_boards.json and data/discovered_{platform}.txt (hand the .txt to Claude)")


def main(argv):
    envs, pages, dry = list(ENVS), 20, False
    platform, source, crawls, from_file = "workday", "both", 2, None
    it = iter(argv)
    for a in it:
        if a == "--envs": envs = next(it).split(",")
        elif a == "--pages": pages = int(next(it))
        elif a == "--dry": dry = True
        elif a == "--platform": platform = next(it).lower()
        elif a == "--source": source = next(it).lower()
        elif a == "--crawls": crawls = int(next(it))
        elif a == "--from-file": from_file = next(it)
        else: raise SystemExit(__doc__)
    if platform != "workday":
        if platform not in OTHER or source not in ("wayback", "cc", "both"):
            raise SystemExit(__doc__)
        discover_other(platform, source, crawls, pages, dry, from_file)
        return None

    b = bs.load_boards()
    have = {s.lower() for s in b["slugs"].get("workday", [])}
    have |= {k.partition(":")[2].lower() for k in b.get("added", {}) if k.startswith("workday:")}
    dead = {k.partition(":")[2].lower() for k in b.get("dead", {}) if k.startswith("workday:")}

    cands = {}
    for env in envs:
        for pg in range(pages):
            urls = cdx_page(env, pg)
            if urls is None:
                break
            for u in urls:
                m = URL_RE.match(u)
                if not m:
                    continue
                tenant, wd, site = m.group(1).lower(), m.group(2).lower(), m.group(3)
                if site.lower() in SKIP_SITE:
                    continue
                slug = f"{tenant}|{wd}|{site}"
                if slug.lower() not in have and slug.lower() not in dead:
                    cands.setdefault(slug, u)
            print(f"  {env} page {pg + 1}: {len(cands):,} new boards so far", flush=True)
            time.sleep(6)   # slowed from 2s to match the other-platforms pacing (reduce 429 risk)
        time.sleep(6)   # between environments too
    print(f"{len(cands):,} boards the sweep doesn't know yet. Asking each one once...", flush=True)

    bs.SWEEP_HOURS[0] = 24 * 30  # we only need "does it answer with postings", not today's postings
    found, gone = [], 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(bs.read_workday, s, 0.3): s for s in cands}
        for i, f in enumerate(as_completed(futs), 1):
            s = futs[f]
            try:
                rows, err = f.result()
            except Exception:
                rows, err = None, "error"
            if rows:
                found.append((s, len(rows)))
                print(f"  ✅ workday:{s}  ({len(rows)} postings in 30 days)", flush=True)
            elif err == "404":
                gone += 1
            if i % 200 == 0:
                print(f"  ... {i:,}/{len(cands):,} asked, {len(found)} live", flush=True)

    today = datetime.now().date().isoformat()
    if not dry:
        for s, _ in found:
            b.setdefault("added", {})[f"workday:{s}"] = dict(source="wd_discover: Wayback CDX", added=today)
        bs.save_boards(b)
    print(f"\nLive Workday boards found: {len(found)} · dead/closed: {gone}"
          + ("" if dry else " · saved to data/sweep_boards.json \"added\""))
    return None


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    main(sys.argv[1:])
