"""source_sweep.py — the board sweep's pattern for the scheduler's other searches: code finds, the scheduler vets.

Sources (each with --only <name>; tags use the existing lead `search` values):
    watchlist    Coverage report, fetches nothing: watchlist employers whose careers link is on a platform
                 board_sweep.py reads (Workday, Greenhouse, Lever, Ashby, ...) are swept there, with
                 config.json extra_workday_boards; the rest are listed as "manual". This script is the
                 catch-all for employers with their own backend, not for boards board_sweep can read.
    jobspy       Indeed via python-jobspy: the target-title queries x (remote US, home-area 50 mi).
                 Tag `jobspy`. Indeed-only hits still need an employer/LinkedIn link before they become
                 leads (config.json search.indeed_queries).
    government   NOT run by default: the candidate's USAJobs key lives only in the scheduler's
                 the scheduler's secret store and can't reach a Python process, so the scheduler runs USAJobs
                 itself through its vault connector and vets those hits alongside source_candidates.json.
                 Opt-in local path for a machine that has the key in env vars: --only government with
                 USAJOBS_API_KEY + USAJOBS_EMAIL (User-Agent must be the exact registration email).
LinkedIn: scripts/linkedin_sweep.py (public no-login search, paced; the scheduler vets data/li_candidates.json).

Same filters as board_sweep.py (imported, not copied): target titles, dropped title ladders, off-list catch
(lane word + stated pay top >= the screen minimum), remote US / 50 mi of home, excluded companies/aggregators out, dedupe
against seen.txt + leads.json.

Usage (from the repo root):
    python scripts/source_sweep.py                    all sources, postings from the last 24h
    python scripts/source_sweep.py --hours 72         Monday AM (weekend)
    python scripts/source_sweep.py --since-last      PM top-off: window = time since the last run
    python scripts/source_sweep.py --only watchlist   one source (watchlist|jobspy|government)
    python scripts/source_sweep.py --limit 3          test: first 3 queries/boards per source
Writes data/source_candidates.json (same candidate shape as sweep_candidates.json, plus `search`).
"""
import json, os, re, sys, time, random, urllib.request, urllib.error, urllib.parse
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import SEARCH  # noqa: E402
from common import drop_row, write_drops  # noqa: E402
from board_sweep import offlist_needs_pay  # noqa: E402
from board_sweep import (TITLE_RE, DROP_TITLE, LANE_RE, OFFLIST_PAY_MIN, pay_top, loc_ok, known, is_known,  # noqa: E402
                         EXCLUDE_CO, AGGREGATOR, get, UA, DATA, ROOT)

OUT = DATA / "source_candidates.json"
SOURCES = ("watchlist", "jobspy", "government")
DEFAULT_SOURCES = ("watchlist", "jobspy")  # government: the scheduler via vault

# Search terms: config.json search.indeed_queries + core lane titles.
QUERIES = SEARCH["indeed_queries"]
SRC_DROPS = []  # drop log


def keep(c, since, K, tally, force=False):
    """Apply board_sweep's filters to one candidate dict. Returns True if it's a keeper (adds offlist flag)."""
    t = (c["role"] or "").lower()
    tally["postings"] += 1
    inwin = not c.get("_posted") or c["_posted"] >= since

    def no(rule):  # drop log: in-window postings only
        if inwin:
            SRC_DROPS.append(drop_row(f"source:{c.get('search')}", rule, c.get("company"), c.get("role"),
                                      c.get("location"), c.get("url"), c.get("pay", ""),
                                      c["_posted"].isoformat() if c.get("_posted") else ""))
        return False
    if not force:
        if DROP_TITLE.search(t):
            return no("title dropped (DROP_TITLE)")
        on_list = bool(TITLE_RE.search(t))
        if not on_list and not LANE_RE.search(t):
            return no("title off list, no lane word")
        c["offlist"] = not on_list
    if EXCLUDE_CO.search((c["company"] or "").lower()) or AGGREGATOR.search(f"{c['url']} {c['company']}".lower()):
        return no("excluded company/aggregator")
    if c.get("_posted") and c["_posted"] < since:
        return False
    tally["window"] += 1
    ok, why = loc_ok(c["location"], c.get("board_remote"), c.get("workplace", ""))
    if not ok and not force:
        return no(f"location ({why})")
    c["where"] = why if ok else f"{why} (kept anyway)"
    if is_known(dict(url=c["url"], id=c["posting_id"], company=c["company"], title=c["role"]), K):
        tally["known"] += 1
        return no("already logged")
    top = pay_top(c.get("pay"))
    if c.get("offlist") and (top and top < OFFLIST_PAY_MIN or (not top and offlist_needs_pay(t))):
        return no(f"off-list: stated pay top under ${OFFLIST_PAY_MIN // 1000}K (or PM title with no pay)")
    if top and top < OFFLIST_PAY_MIN and not force:  # search-time pay screen: stated top under the screen minimum = drop
        tally["low_pay"] = tally.get("low_pay", 0) + 1
        return no(f"stated pay top under ${OFFLIST_PAY_MIN // 1000}K")
    return True


def cand(search, company, role, location, url, pid, posted=None, pay="", workplace="", remote=None, **kw):
    d = dict(company=company, role=role, location=location, where="", pay=pay or "",
             posted=posted.isoformat(timespec="minutes") if posted else "", url=url, platform=search,
             posting_id=str(pid), change="new", board_remote=remote, workplace=workplace, offlist=False,
             search=search, _posted=posted)
    d.update(kw)
    return d


# ---------- watchlist (coverage report only) ----------
def watchlist(o, since, K, tally):
    """board_sweep.py sweeps every watchlist company whose careers link is on a platform it reads (it learns
    them from data/watchlist.json). This source only reports which ones it covers and which need a hand check
    or their own lane."""
    from board_sweep import BOARD_URL, DEFAULT_OFF
    covered, manual, off = [], [], []
    try:
        w = json.loads((DATA / "watchlist.json").read_text(encoding="utf-8"))
        rows = w.get("watching", []) + w.get("reported", [])
    except Exception:
        rows = []
    for r in rows:
        url = r.get("careers") or ""
        plat = next((pl for pl, rx in BOARD_URL if rx.search(url)), None)
        if plat in DEFAULT_OFF:  # board_sweep reads it, but only with --with: say so, never count it as covered
            off.append(f"{r.get('company')} ({plat}: board_sweep.py --with {plat})")
        elif plat:
            covered.append(r.get("company"))
        else:
            manual.append(f"{r.get('company')} ({url or 'no careers link'})")
    return [], {}, dict(manual=manual, covered_by_board_sweep=covered, board_sweep_off_by_default=off)


def jobspy(o, since, K, tally):
    try:
        from jobspy import scrape_jobs
    except ImportError:
        return [], {"jobspy": "python-jobspy not installed: pip install python-jobspy"}, {}
    qs = QUERIES[:o["limit"]] if o["limit"] else QUERIES
    out, fails = [], {}
    for q in qs:
        for loc, remote in (("United States", True), (SEARCH["indeed_location"], False)):
            try:
                df = scrape_jobs(site_name=["indeed"], search_term=q, location=loc, is_remote=remote,
                                 distance=50, hours_old=o["hours"], results_wanted=50, country_indeed="USA",
                                 verbose=0)
            except Exception as e:
                fails[f"{q} @ {loc}"] = type(e).__name__
                continue
            for r in df.to_dict("records") if df is not None else []:
                r = {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}  # NaN -> None
                pay = ""
                if r.get("min_amount") and r.get("max_amount") and str(r.get("interval")) == "yearly":
                    pay = f"${int(r['min_amount']):,}-${int(r['max_amount']):,}"
                loc_s = r.get("location") or ""
                if r.get("is_remote") and "remote" not in str(loc_s).lower():
                    loc_s = f"Remote / {loc_s}"
                posted = None
                if r.get("date_posted"):
                    try:
                        posted = datetime.fromisoformat(str(r["date_posted"])[:10]).replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass  # not a parseable value: leave the default
                c = cand("jobspy", str(r.get("company") or ""), str(r.get("title") or ""), str(loc_s),
                         str(r.get("job_url_direct") or r.get("job_url") or ""), r.get("id") or "", posted, pay,
                         indeed_url=str(r.get("job_url") or ""))
                if keep(c, since - timedelta(days=1), K, tally) and c["url"] not in {x["url"] for x in out}:
                    out.append(c)
            time.sleep(o["pace"] * 4)
    return out, fails, {}


# ---------- run log ----------

# Auditability: a heartbeat every 30s, and every progress line also appended to
# data/logs/sweep_<date>.log, so a long run can be watched live and checked afterwards.
import threading  # noqa: E402
STATE = dict(source="", step="starting", t0=time.time())
LOG = None


def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    if LOG:
        try:
            with open(LOG, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        except OSError:
            pass  # best effort: a missing or locked file isn't fatal here


def _heartbeat(stop, every=30):
    while not stop.wait(every):
        s, m = STATE, int(time.time() - STATE["t0"])
        log(f"♥ {m // 60}m{m % 60:02d}s · {s['source']} · {s['step']}")

# ---------- USAJobs ----------
def government(o, since, K, tally):
    key, email = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not key or not email:
        return [], {"government": "skipped: no USAJOBS_API_KEY/USAJOBS_EMAIL here. Normal path: the scheduler runs "
                                  "USAJobs via its own connector"}, {}
    out, fails = [], {}
    days = max(1, o["hours"] // 24)
    for q in (QUERIES[:o["limit"]] if o["limit"] else QUERIES):
        for extra in ("&RemoteIndicator=True", f"&LocationName={urllib.parse.quote(SEARCH['usajobs_location'])}&Radius={SEARCH['commute_radius_mi']}"):
            url = (f"https://data.usajobs.gov/api/search?Keyword={urllib.parse.quote(q)}&DatePosted={days}"
                   f"&ResultsPerPage=100{extra}")
            try:
                time.sleep(o["pace"] + random.random() * o["pace"])
                req = urllib.request.Request(url, headers={"Host": "data.usajobs.gov", "User-Agent": email,
                                                           "Authorization-Key": key})
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = json.loads(r.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                fails[q] = f"HTTP {e.code}" + (" (key/email not accepted: USAJOBS_EMAIL must be the email the key "
                                               "was registered with; new keys can take a while to activate)"
                                               if e.code == 401 else "")
                if e.code == 401:
                    return out, fails, {}
                continue
            except Exception as e:
                fails[q] = type(e).__name__
                continue
            for it in (d.get("SearchResult") or {}).get("SearchResultItems") or []:
                m = it.get("MatchedObjectDescriptor") or {}
                pr = (m.get("PositionRemuneration") or [{}])[0]
                pay = f"${float(pr.get('MinimumRange', 0)):,.0f}-${float(pr.get('MaximumRange', 0)):,.0f}" \
                    if pr.get("RateIntervalCode") == "PA" else ""
                posted = None
                try:
                    posted = datetime.fromisoformat(m.get("PublicationStartDate", "")[:19]).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass  # not a parseable value: leave the default
                loc = m.get("PositionLocationDisplay", "")
                if "Remote" in extra and "remote" not in loc.lower():
                    loc = f"Remote / {loc}"
                c = cand("government", m.get("OrganizationName", ""), m.get("PositionTitle", ""), loc,
                         m.get("PositionURI", ""), m.get("PositionID", ""), posted, pay)
                if keep(c, since - timedelta(days=1), K, tally) and c["url"] not in {x["url"] for x in out}:
                    out.append(c)
    return out, fails, {}


RUNNERS = {"watchlist": watchlist, "jobspy": jobspy, "government": government}


def main(argv):
    o = dict(hours=24, limit=0, only="", pace=0.5, vet=True, all=False)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--since-last":
            from common import hours_since_last
            o["hours"] = hours_since_last(OUT)
            print(f"--since-last: window {o['hours']}h (top-off from the previous run)")
            i += 1
            continue
        if a == "--no-vet":
            o["vet"] = False
            i += 1
            continue
        if a in ("--hours", "--limit"):
            o[a[2:]] = int(argv[i + 1]); i += 1
        elif a == "--pace":
            o["pace"] = float(argv[i + 1]); i += 1
        elif a == "--only":
            o["only"] = argv[i + 1]; i += 1
            if o["only"] not in SOURCES:
                raise SystemExit(f"--only takes one of {', '.join(SOURCES)}")
        elif a in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        else:
            raise SystemExit(f"Unknown option {a}\n{__doc__}")
        i += 1
    since = datetime.now(timezone.utc) - timedelta(hours=o["hours"])
    global LOG
    (DATA / "logs").mkdir(exist_ok=True)
    LOG = DATA / "logs" / f"sweep_{datetime.now().strftime('%Y-%m-%d')}.log"
    log(f"=== source_sweep start: {' '.join(argv) or '(defaults)'} · log {LOG.relative_to(ROOT)}")
    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(stop,), daemon=True).start()
    K = known()
    report, allc = {}, []
    if not o["only"]:
        report["government"] = dict(candidates=0, note="not run here: the scheduler runs USAJobs via its vault connector "
                                    "and vets those hits alongside this file")
        print("government   not run here (the scheduler runs USAJobs via its vault connector)", flush=True)
    for name in ([o["only"]] if o["only"] else DEFAULT_SOURCES):
        tally = dict(postings=0, window=0, known=0)
        t0 = time.time()
        STATE.update(source=name, step="running")
        try:
            got, fails, extra = RUNNERS[name](o, since, K, tally)
        except Exception as e:  # one source failing never stops the others
            got, fails, extra = [], {name: f"crashed: {type(e).__name__}: {e}"}, {}
        report[name] = dict(candidates=len(got), offlist=sum(1 for c in got if c.get("offlist")),
                            seconds=int(time.time() - t0), failures=fails, **tally, **extra)
        allc += got
        log(f"=== {name} done: {len(got)} candidates · read {tally['postings']} · {report[name]['seconds']}s"
            f" · failures {len(fails)}")
        print(f"{name:<10} {len(got):>3} candidates ({report[name]['offlist']} off-list)"
              f" · read {tally['postings']} · in window {tally['window']} · already logged {tally['known']}"
              f" · {report[name]['seconds']}s" + (f" · failures: {len(fails)}" if fails else ""), flush=True)
        for k, v in list(fails.items())[:4]:
            print(f"           ! {k}: {v}")
        if extra.get("manual"):
            print(f"           manual (no feed): {'; '.join(extra['manual'])}")
        if extra.get("board_sweep_off_by_default"):
            print(f"           NOT swept (platform off by default): {'; '.join(extra['board_sweep_off_by_default'])}")
        if extra.get("covered_by_board_sweep"):
            print(f"           swept by board_sweep.py: {len(extra['covered_by_board_sweep'])} watchlist employer(s)")
    for c in allc:
        c.pop("_posted", None)
    screened_out = []
    if o.get("vet", True) and allc:  # read + screen every candidate, same pass as board_sweep
        from board_sweep import vet
        allc, screened_out = vet(allc)
    for c in screened_out:
        SRC_DROPS.append(drop_row("source:vet", "screen FAIL: " + "; ".join(c.get("fails", []))[:200], c.get("company"),
                                  c.get("role"), c.get("location"), c.get("url"), c.get("pay", ""), c.get("posted", ""),
                                  jd_file=c.get("jd_file")))
    write_drops("source", SRC_DROPS)
    run_at = datetime.now().astimezone().isoformat(timespec="minutes")
    OUT.write_text(json.dumps(dict(run_at=run_at, hours=o["hours"], sources=report, candidates=allc, screened_out=screened_out),
                              indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8", newline="\n")
    for c in allc:
        tag = "OFF " if c.get("offlist") else ""
        print(f"  {tag}{c['search']:<10} {c['company']} | {c['role']} | {c['location'][:50]} | {c['pay'] or 'pay ?'} | {c['url']}")
    print(f"→ {OUT.relative_to(ROOT)}  ({len(allc)} candidates; the scheduler vets these; never a lead until screened)")


if __name__ == "__main__":
    main(sys.argv[1:])
