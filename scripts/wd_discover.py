"""wd_discover.py — find Workday job boards in bulk from the Wayback Machine's archive of *.myworkdayjobs.com links,
then confirm each one is live (method from the CareerScout repo, Ramcharan747/careerscout, MIT; our own
stdlib code).

Why: board_sweep.py only sweeps Workday boards it already knows. Workday has no public directory, but the web
archive has seen millions of links like acme.wd5.myworkdayjobs.com/en-US/External/job/..., and each one names a
tenant (acme), an environment (wd5) and a site (External). That's exactly the slug board_sweep needs.

Steps: 1) ask the archive's CDX index for every archived URL on each wdN host (paged, de-duplicated by URL key);
2) pull tenant|wdN|site out of each; 3) ask each new board's job API once (POST /wday/cxs/...); 4) save every live
board with open postings to data/sweep_boards.json "added", so the next sweep reads it.

NOT RUNNABLE FROM THE DEVELOPMENT ENVIRONMENT: web.archive.org answers 403 there. Run it on a different network or the scheduler's machine.

Usage (from the repo root):
    python scripts/wd_discover.py                   wd1, wd3, wd5, wd12 (the four CareerScout found in use)
    python scripts/wd_discover.py --envs wd5        one environment
    python scripts/wd_discover.py --pages 5         CDX pages per environment (default 20; each page is large)
    python scripts/wd_discover.py --dry             report finds, don't save them

Other platforms: JazzHR (applytojob.com), Oracle Cloud Recruiting, iCIMS. Added later (build
order, step 2-3): Paylocity (recruiting.paylocity.com) and ADP (myjobs.adp.com) - same shape, but the identifier
sits in a URL *path* on one shared host, not in subdomains, so these two use matchType=prefix + a wb_filter/
cc_filter to narrow the path instead of matchType=domain (see OTHER's wb_match). SuccessFactors (the 4th,
"most brittle, build last") does NOT fit this script: it's self-hosted per company on arbitrary career domains
with no shared host or path prefix to scan - that needs a full-text search Wayback/CC's URL-only CDX
index can't do, so it's not implemented here.
board_sweep has no lanes for any of these yet, so the finds go to data/discovered_boards.json (and a plain list,
data/discovered_<platform>.txt), NOT to sweep_boards.json. You run this and hand the list over; the lanes get
built from it.
    python scripts/wd_discover.py --platform jazzhr             Wayback + Common Crawl, then a light live check
    python scripts/wd_discover.py --platform oracle --source cc one source only (wayback | cc | both, default both)
    python scripts/wd_discover.py --platform icims --crawls 3   newest N Common Crawl crawls (default 2)
    python scripts/wd_discover.py --platform paylocity
    python scripts/wd_discover.py --platform adp
    python scripts/wd_discover.py --platform jazzhr --from-file urls.txt
                                  skip the archives: read board URLs (one per line) you found yourself, e.g. from a
                                  Google `site:applytojob.com/apply "remote"` search, extract the slugs, check them
Live check per platform: JazzHR = the /apply page lists job links; iCIMS = the search page lists jobs; Paylocity =
the board page's window.pageData is present and non-empty; ADP = the career-site API returns an orgoid. Status:
live (jobs), empty (board answers, no jobs), dead (gone/inactive), unknown (blocked or errored: kept apart, retry
later). Only live + empty reach the .txt list.

Oracle Recruiting Cloud is its own path (discover_oracle, not discover_other) because the slug-guessing approach
above doesn't apply and the obvious verification call is actively misleading:
- Oracle has no public directory of tenants either, but unlike Workday there's also no small set of environment
  hosts to enumerate (oraclecloud.com hosts every kind of Oracle Cloud app, and tenants split between a bare
  `tenant.fa.oraclecloud.com` and a handful of `tenant.fa.<region>.oraclecloud.com` forms). Candidate HOSTS come
  from Wayback/Common Crawl URLs whose path contains hcmUI/CandidateExperience (that path is the signal that
  separates a recruiting tenant from Oracle Cloud's other apps on the same domain - everything else is noise) -
  or from --from-file: a GitHub code search for "hcmUI/CandidateExperience" turns up hard-coded tenant URLs in
  other repos (needs a GitHub API token this repo doesn't have configured), and a company's careers-page redirect
  landing on such a URL is a host hit too.
- Once a host is found, recruitingCESites lists that tenant's real sites - the closest thing to a directory,
  but per-host only. DON'T verify a Wayback-found siteNumber by calling recruitingCEJobRequisitions with it:
  confirmed live against two real hosts , an unknown/bogus siteNumber doesn't error - Oracle
  answers HTTP 200 and silently returns some default site's job count either way, so a typo-checked "live" board
  can be reporting the wrong site. recruitingCESites has no such trap; discover_oracle enumerates every
  ORA_ACTIVE site per host from it instead of trusting the URL's own siteNumber.
- The public posting-search link for a site uses its SiteURLName when the tenant set one, SiteNumber otherwise
  (oracle_posting_url) - build it from the site record recruitingCESites returned, not from whatever number was
  searched with.
Output: data/discovered_oracle_sites.json, one row per active site (host, site_number, site_url_name, site_name,
posting_search_url, via, found_at_url, verified). Never written into data/oracle_sites.json - that file stays
hand-curated; hand the discovery file to the owner for review and manual copy-in.
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
                raise SystemExit("web.archive.org refused the request (403). This runs on a different network or the scheduler's machine, "
                                 "not in the development environment.")
        except Exception as e:
            last = type(e).__name__
        time.sleep(5 * (k + 1))
    raise SystemExit(f"web.archive.org didn't answer after 3 tries ({locals().get('last', 'error')}). "
                     "If this is the development environment, run it on a different network or the scheduler's machine instead.")


# ---- other platforms: JazzHR, Oracle Cloud Recruiting, iCIMS --------------------------------------------------
CC_INDEX = "https://index.commoncrawl.org"
OTHER = {
    # archive host patterns, the archive-side filter that keeps only career-site URLs, the URL -> slug regex
    "jazzhr": dict(hosts=["*.applytojob.com", "*.theresumator.com"], wb_filter=None, cc_filter=None,
                   rx=re.compile(r"^(?:https?://)?([a-z0-9-]+)\.(?:applytojob|theresumator)\.com", re.I),
                   skip={"www", "app", "api", "careers", "help", "support", "blog", "demo", "jazzhr", "resumator",
                         "static", "assets", "cdn", "mail", "login", "signup"}),
    # one query over all of *.oraclecloud.com timed out (Wayback) / 504'd (Common Crawl), so go region by region.
    # Some tenants (e.g. a bare acme.fa.oraclecloud.com) carry no region segment at all, so "*.fa.oraclecloud.com"
    # (bare) is its own host alongside the per-region ones.
    "oracle": dict(hosts=["*.fa.oraclecloud.com"] + [f"*.fa.{r}.oraclecloud.com" for r in
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
    # One shared host, not subdomains - the identifier (a guid) lives in the URL path, so this
    # scans recruiting.paylocity.com with matchType=prefix (wb_match) and narrows to the board-page path via
    # wb_filter/cc_filter, same CDX filter syntax Oracle already uses for hcmUI/CandidateExperience.
    "paylocity": dict(hosts=["recruiting.paylocity.com"], wb_match="prefix", check_workers=3,
                       wb_filter=r"original:.*/recruiting/jobs/All/.*", cc_filter="~url:recruiting/jobs/All",
                       rx=re.compile(r"^(?:https?://)?recruiting\.paylocity\.com/recruiting/jobs/All/"
                                     r"([0-9a-f-]{36})/([A-Za-z0-9_.~%-]+)", re.I), skip=set()),
    # Same shape as Paylocity - myjobs.adp.com/{domain}/cx/... is the public job-search front
    # end; {domain} is the identifier the career-site API (checked in check_board) takes directly.
    "adp": dict(hosts=["myjobs.adp.com"], wb_match="prefix", wb_filter=r"original:.*/cx/.*", cc_filter="~url:/cx/",
                rx=re.compile(r"^(?:https?://)?myjobs\.adp\.com/([A-Za-z0-9_-]+)/cx/", re.I),
                skip={"public", "static", "assets", "api"}),
}


def slug_from_url(platform, u):
    """URL -> board slug, or None. JazzHR: the subdomain. Oracle: host|site. iCIMS: the full host. Paylocity:
    guid|company (the trailing company-name path segment is cosmetic, but check_board needs it to
    rebuild the exact URL that was crawled). ADP: the {domain} identifier alone."""
    cfg = OTHER[platform]
    m = cfg["rx"].match(u.strip())
    if not m:
        return None
    if platform == "jazzhr":
        s = m.group(1).lower()
        return None if s in cfg["skip"] else s
    if platform in ("oracle", "paylocity"):
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


def extract_js_object(body, var_name):
    """Pull a `window.<var_name> = {...}` (or `[...]`) assignment out of an HTML page and JSON-parse it. Brace
    counting, not a regex, because the object is large and nested - a non-greedy regex would stop at the first
    inner `}` and truncate it. Returns None if the variable isn't there or doesn't parse."""
    m = re.search(rf"{re.escape(var_name)}\s*=\s*([\[{{])", body)
    if not m:
        return None
    open_ch, close_ch = m.group(1), ("]" if m.group(1) == "[" else "}")
    start, depth, in_str, esc = m.start(1), 0, False, False
    for i in range(start, len(body)):
        c = body[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(body[start:i + 1])
                except Exception:
                    return None
    return None


def oracle_sites(host, timeout=20):
    """A host's real Recruiting CE sites, from recruitingCESites - the only reliable per-host directory.

    Gotcha: recruitingCEJobRequisitions can't tell a real siteNumber from a typo - confirmed live against
    two real hosts, a bogus siteNumber echoes back in the response but TotalJobsCount comes back identical to
    the real site's count either way (Oracle silently serves some default site's data, HTTP 200).
    recruitingCESites has no such trap: it lists what's actually configured, each with its own StatusCode.

    Returns (sites, err): sites is a list of dict(number=SiteNumber, url_name=SiteURLName, name=SiteName,
    active=<StatusCode == "ORA_ACTIVE">), or None on failure. err is "" on success, "dead" (404/410), or
    f"HTTP {n}" / the exception name otherwise. Never raises.

    Retries a connection-level failure (status 0 - "couldn't connect", never a real HTTP error) up to 3
    times with a short backoff before giving up. Seen live: a 414-host run's whole check phase, kicked off right
    after a ~25-min Wayback crawl in the same process, came back "no response" for every single host including
    ones confirmed live seconds later in a fresh process - a transient connection blip, not a dead or unknown
    host, so one failed attempt alone shouldn't be read as "retry later."
    """
    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCESites?onlyData=true&limit=100"
    st, body = 0, ""
    for k in range(3):
        st, _, body = http_get(url, timeout)
        if st != 0:
            break
        time.sleep(2 * (k + 1))
    if st in (404, 410):
        return None, "dead"
    if st != 200:
        return None, f"HTTP {st}" if st else "no response"
    try:
        items = json.loads(body)["items"]
        return [dict(number=i.get("SiteNumber"), url_name=i.get("SiteURLName"), name=i.get("SiteName"),
                     active=i.get("StatusCode") == "ORA_ACTIVE") for i in items], ""
    except Exception as e:
        return None, type(e).__name__


def oracle_posting_url(host, site):
    """A site's public posting-search URL. Gotcha: use SiteURLName when the tenant set one, else
    SiteNumber - the ad links Oracle itself generates follow the same rule, and a SiteNumber-only link can land
    wrong (or 404) once a tenant has set a URL name for that site."""
    return f"https://{host}/hcmUI/CandidateExperience/en/sites/{site['url_name'] or site['number']}"


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
        host, _, _ = slug.partition("|")  # the site half of a Wayback slug is a hint only - see oracle_sites()
        sites, err = oracle_sites(host)
        if err == "dead":
            return "dead", 0
        if err:
            return "unknown", 0
        active = [s for s in sites if s["active"]]
        return ("live", len(active)) if active else ("empty", 0)
    if platform == "paylocity":
        # Confirmed by hand - recruiting.paylocity.com 429s quickly under this script's 8-way concurrent
        # live-check (full pilot: 7,482/7,734 came back "unknown" from exactly this). A plain single GET isn't
        # enough here like it is for the other platforms, so retry 429 specifically, with backoff.
        guid, _, company = slug.partition("|")
        url = f"https://recruiting.paylocity.com/recruiting/jobs/All/{guid}/{company}"
        st, _, body = http_get(url)
        for wait in (2, 5, 10):
            if st != 429:
                break
            time.sleep(wait)
            st, _, body = http_get(url)
        if st in (404, 410):
            return "dead", 0
        if st != 200:
            return "unknown", 0
        data = extract_js_object(body, "window.pageData")
        if data is None:
            return "dead", 0   # 200 with no pageData = retired board
        jobs = (data.get("Jobs") or data.get("jobs") or []) if isinstance(data, dict) else data
        return ("live", len(jobs)) if jobs else ("empty", 0)
    if platform == "adp":
        st, _, body = http_get(f"https://myjobs.adp.com/public/staffing/v1/career-site/{slug}")
        if st in (400, 404, 410):   # 400 confirmed live: the API's answer for an unknown domain
            return "dead", 0
        if st != 200:
            return "unknown", 0
        try:
            orgoid = json.loads(body).get("orgoid")
        except Exception:
            return "unknown", 0
        return ("live", 1) if orgoid else ("dead", 0)
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

    Paylocity, ADP: these live on one shared host with the identifier in the path, not in subdomains, so
    OTHER sets wb_match="prefix" for them instead of the default "domain" - matchType=prefix walks every archived
    path under the host, narrowed by wb_filter to just the board-page path.
    """
    cfg = OTHER[platform]
    batch = cfg.get("wb_batch", WB_BATCH)
    pages = pages * (WB_BATCH // batch) if batch < WB_BATCH else pages  # smaller batch -> more of them, same coverage
    hosts = cfg["hosts"]
    match = cfg.get("wb_match", "domain")
    t0 = time.time()
    for hi, host in enumerate(hosts, 1):
        bare = host[2:] if host.startswith("*.") else host
        resume = None
        for batch_n in range(pages):
            print(f"  wayback {host} ({hi}/{len(hosts)}) batch {batch_n + 1}: requesting... "
                  f"[{time.time() - t0:.0f}s elapsed]", flush=True)
            q = {"url": bare, "matchType": match, "output": "json", "fl": "original", "collapse": "urlkey",
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
                        raise SystemExit("web.archive.org refused the request (403). Run this from a different network or the scheduler's machine.")
                    if k < 2:
                        print(f"    retry {k + 1}/3 after {why}, waiting {5 * (k + 1)}s...", flush=True)
                except Exception as e:
                    why = f"{type(e).__name__}: {e}"
                    if k < 2:
                        print(f"    retry {k + 1}/3 after {why}, waiting {5 * (k + 1)}s...", flush=True)
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
            if cfg.get("wb_match") == "prefix":   # Paylocity/ADP - one host, path-based, not a *.domain hit
                base["matchType"] = "prefix"
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


def discover_oracle(source, crawls, pages, dry, from_file):
    """Oracle Recruiting Cloud discovery: find candidate HOSTS (Wayback / Common Crawl / --from-file), then ask
    each host once, via oracle_sites() (recruitingCESites), for its real site list - never trust a siteNumber
    lifted from an archived URL path on its own (see oracle_sites' docstring for the gotcha). Dedupes to
    one check per host even if Wayback turned up several site numbers for it.

    Writes data/discovered_oracle_sites.json, one row per *active* site, for a human to review and hand-copy
    into data/oracle_sites.json - this never writes that file itself. Also worth feeding --from-file: a GitHub
    code search for "hcmUI/CandidateExperience" turns up hard-coded tenant URLs in other repos (needs a GitHub
    API token this repo doesn't have configured), and any company's careers-page redirect landing on an
    oraclecloud.com /hcmUI/CandidateExperience/ URL is a host hit too - paste either kind of URL list in and run
    with --from-file instead of a fresh crawl.
    """
    if from_file:
        srcs = [("file", (l.strip() for l in Path(from_file).read_text(encoding="utf-8").splitlines() if l.strip()))]
    else:
        srcs = []
        if source in ("wayback", "both"):
            srcs.append(("wayback", wayback_urls("oracle", pages)))
        if source in ("cc", "both"):
            srcs.append(("cc", cc_urls("oracle", crawls, pages)))

    hosts = {}  # host -> (url it was first seen at, how it was found)
    for name, it in srcs:
        n = 0
        for u in it:
            s = slug_from_url("oracle", u)
            if s:
                host = s.partition("|")[0]
                hosts.setdefault(host, (u, name))
            n += 1
        print(f"{name}: read {n:,} URLs, {len(hosts):,} candidate oracle host(s) so far", flush=True)
    print(f"{len(hosts):,} candidate host(s). Asking each once for its real site list...", flush=True)

    today = datetime.now().date().isoformat()
    rows = []
    tally = {"live": 0, "empty": 0, "dead": 0, "unknown": 0}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(oracle_sites, h): h for h in hosts}
        for i, f in enumerate(as_completed(futs), 1):
            host = futs[f]
            try:
                sites, err = f.result()
            except Exception:
                sites, err = None, "error"
            if err == "dead":
                tally["dead"] += 1
                print(f"  ... {i}/{len(hosts)} checked: {host} -> dead", flush=True)
            elif err:
                tally["unknown"] += 1
                print(f"  ... {i}/{len(hosts)} checked: {host} -> unknown ({err})", flush=True)
            else:
                active = [s for s in sites if s["active"]]
                tally["live" if active else "empty"] += 1
                print(f"  ... {i}/{len(hosts)} checked: {host} -> "
                      f"{'live, ' + str(len(active)) + ' active site(s)' if active else 'empty'}", flush=True)
                for s in active:
                    rows.append(dict(host=host, site_number=s["number"], site_url_name=s["url_name"],
                                      site_name=s["name"], posting_search_url=oracle_posting_url(host, s),
                                      via=hosts[host][1], found_at_url=hosts[host][0], verified=today))
                    print(f"      ✅ oracle:{host}|{s['number']}  ({s['name']})", flush=True)

    print(f"\noracle: live {tally['live']} host(s) · empty {tally['empty']} · dead {tally['dead']} · "
          f"unknown {tally['unknown']} (retry later) · {len(rows)} active site(s) for human review")
    if not dry:
        path = bs.DATA / "discovered_oracle_sites.json"
        path.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("saved data/discovered_oracle_sites.json (hand it to the owner for review - never auto-copied "
              "into data/oracle_sites.json)")
    return rows


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
    with ThreadPoolExecutor(max_workers=OTHER[platform].get("check_workers", 8)) as ex:
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
        print(f"saved data/discovered_boards.json and data/discovered_{platform}.txt (hand the .txt to the owner)")


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
        if platform == "oracle":
            discover_oracle(source, crawls, pages, dry, from_file)
        else:
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
