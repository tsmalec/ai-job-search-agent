"""linkedin_sweep.py — LinkedIn search from the public (no-login) job pages. Code finds, the scheduler judges.

Takes LinkedIn crawling off the scheduler's limits. Same public pages JobSpy uses, built around our own
429 findings (agent/adjustments.md): search first, read postings after, never both at once;
paced calls; stop and checkpoint on repeated 429s instead of hammering. No login, no proxies, no IP rotation.

Usage (from the repo root):
    python scripts/linkedin_sweep.py                      full run: every term x (remote US, home-area 50 mi), last 24h
    python scripts/linkedin_sweep.py --hours 48           wider window (LinkedIn offers 24h / week / month; rounded up)
    python scripts/linkedin_sweep.py --terms "chief of staff,revenue operations"   only these terms
    python scripts/linkedin_sweep.py --max-terms 4        first 4 terms (quick test)
    python scripts/linkedin_sweep.py --pages 3            result pages per search (10 cards each; default 3)
    python scripts/linkedin_sweep.py --no-postings        search phase only (skip reading postings)
    python scripts/linkedin_sweep.py --max-calls 1200 hard cap on LinkedIn calls per run (default 1200)
    python scripts/linkedin_sweep.py --fresh              ignore the checkpoint and start from term 1
    python scripts/linkedin_sweep.py --reread             read again only the postings a FINISHED run couldn't read
                                                          ("didn't load" / "not read") from data/li_candidates.json
    python scripts/linkedin_sweep.py --resume             pick up a cut run (crash, restart, block) where it stopped:
                                                          search groups done and postings already read are kept
    python scripts/linkedin_sweep.py --max-postings 80    read at most 80 postings, save, exit cleanly (rerun --resume for the
                                                          next 80); long runs can get cut off
    python scripts/linkedin_sweep.py --drain --max-postings 80 --terms "a,b"
                                                          read every chunk in one go (reruns itself with --resume until no
                                                          postings are left, a block stops it, or --max-chunks, default 10)
    python scripts/linkedin_sweep.py --trickle 25         one call every ~25s (a slow steady run instead of a burst)
    python scripts/linkedin_sweep.py --single-terms       the old way: one search per term, 3 pages each
    python scripts/linkedin_sweep.py --no-covered-skip    also read postings from companies the board sweep covers

run it AFTER the board sweep. Searches use a few OR groups (LinkedIn honors OR), newest first, paging
until results run out; cards from companies with a live board in the board sweep are skipped (their postings come
in through the lanes), so postings are read only for companies on other systems. Two blocks in a row = cool down
5-10 min and continue (up to --max-waits, default 5), then stop with a checkpoint.

Phases:
  1. Search: keyword pages only (jobs-guest seeMoreJobPostings), 1.5-2.5s between calls, 6-10s between terms.
     Cards are filtered in code with board_sweep's title / location / company / aggregator / dedupe rules.
  2. Postings: only the survivors are read (jobs-guest jobPosting), 6-9s apart (2.5-4s drew blocks on the scheduler's IP), then screen_leads.screen()
     gives PASS / FLAG / FAIL (remote integrity, pay floor, OTE, caveats) from the posting text itself.

429 handling: honor Retry-After (capped at 120s) and retry once. Two blocked calls in a row (429/999) = stop,
save the checkpoint (data/li_state.json) and exit cleanly; the next run resumes at the term that was cut.

Read failures: a cut-short read (IncompleteRead, connection reset, timeout, HTTP 5xx) is retried up to
MAX_READ_ERRORS times; if it still won't load, that search page is logged to `failed_groups` in the output
(and printed in the summary) instead of being treated as "results ran out" — a page that never loaded is
not the same as a page that loaded and said there was nothing there. After the search phase each failed page
gets one more try (from that page on) after a pause; the ones that still fail stay in `failed_groups`, the
checkpoint and data/logs/li_runs.jsonl.

Custom --terms runs (gap fills) keep their own checkpoint and state files, keyed on the term list, so they
resume with --resume (same --terms) and never overwrite or delete a full run's checkpoint. They also keep
the candidates an earlier run in the same window left in data/li_candidates.json (merged by posting ID);
every other run saves the file it replaces as data/li_candidates.prev.json first.

Repost check: a job ID far below this run's newest IDs is likely an old posting re-timestamped. It is
flagged "low ID", not dropped: check the employer's own posted date.

Writes data/li_candidates.json:
    candidates   passed every code filter; each has screen verdict, fails, flags, pay, posted date
    screened_out code filters passed but screen() FAILed (kept for audit)
    stats        calls, 429s, cards, kept, stopped_early
the scheduler vets `candidates` and writes the good ones to data/leads.json per agent/README.md.
"""
import hashlib
import html
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SEARCH as CFG_SEARCH, HOME  # noqa: E402
from board_sweep import (TITLE_RE, DROP_TITLE, AGGREGATOR, EXCLUDE_CO, US_WORDS, NON_US, loc_ok,  # noqa: E402
                         known, is_known, norm, key_co, load_boards, pretty, REGION_TAG)
from common import drop_row, write_drops  # noqa: E402
from screen_leads import screen  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "li_candidates.json"
STATE = DATA / "li_state.json"
CKPT = DATA / "li_checkpoint.json"  # full progress (search + postings) for --resume; local only, gitignored
CKPT_MAX_AGE_H = 6  # an older checkpoint is stale for a 24h window: start fresh
PREV = DATA / "li_candidates.prev.json"  # the candidates file a non-resume run replaced (local only, gitignored)
STATUS = DATA / "li_status.json"  # live progress for the scheduler's watcher (local only, gitignored)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
POSTING = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

# Search terms: agent/adjustments.md "Target-title search list" + config.json title list +
# the missed set. LinkedIn matches loosely, so families are folded to one phrase each.
TERMS = CFG_SEARCH["linkedin_terms"]

# OR groups: LinkedIn honors OR inside one search (tested: one query returned business ops, program
# manager and chief of staff titles together), so the 36 terms fold into a few searches. Quoted 2-3 word phrases keep
# recall wide; TITLE_RE (the real filter) decides afterwards. --single-terms brings back the old one-term-per-search.
GROUPS = CFG_SEARCH["linkedin_or_groups"]

WHERE = [  # (label, params). home-area: location + distance, then loc_ok() re-checks every card.
    ("remote US", {"location": "United States", "f_WT": "2"}),
    (f"{HOME} {CFG_SEARCH['commute_radius_mi']} mi", {"location": CFG_SEARCH["linkedin_location"], "distance": str(CFG_SEARCH["commute_radius_mi"])}),
]

BLOCK_CODES = (429, 999)
# LinkedIn-side reposters/staffing feeds beyond board_sweep's list (no aggregators:
# job-board resellers and chapters reposting a req, "A Global Staffing Service")
REPOSTER = re.compile(r"swooped|talenthop|remotehunter|ladders|\balpfa\b|staffing|recruitment agency")


class Blocked(Exception):
    pass


class ReadFailed(Exception):
    """A page never loaded after bounded retries (IncompleteRead, connection reset, timeout). Distinct from
    a real empty response (0 job cards on a page that DID load), so callers can mark that page/group as failed
    instead of silently treating a cut-short read the same as end-of-results."""
    pass


RETRY_PAUSE_S = 60  # wait before the second-chance pass over failed search pages
MAX_READ_ERRORS = 3  # IncompleteRead/timeout/reset: this many tries total before giving up and raising ReadFailed


class Client:
    def __init__(self, max_calls, max_waits=5):
        self.calls = self.blocks = self.streak = self.waits = 0
        self.max_calls, self.max_waits = max_calls, max_waits

    def get(self, url, pace):
        """GET text, paced. Returns text or "" (404 / other HTTP error). Blocks (429/999) never use up a URL's
        tries: one block = wait (Retry-After) and retry; two in a row = cool down 5-10 min and retry the same URL;
        after max_waits cool-downs, raise Blocked. Fix: a block on the retry used to end the loop and
        return "", so a whole batch of postings was marked "didn't load" when they were really blocked.
        A transient read failure (IncompleteRead, connection reset, timeout) is retried up to MAX_READ_ERRORS
        times, then raises ReadFailed instead of returning "" — "" must mean the page loaded and said
        there was nothing there, never that the read itself broke."""
        errors = 0

        def read_error(kind):
            nonlocal errors
            errors += 1
            if errors >= MAX_READ_ERRORS:
                raise ReadFailed(f"{kind} after {errors} tries: {url[:80]}")
            print(f"   ⚠ {kind} on {url[:80]} (retry {errors}/{MAX_READ_ERRORS - 1})", flush=True)
            time.sleep(5 * errors + random.random() * 5)

        while True:
            if self.calls >= self.max_calls:
                raise Blocked(f"call cap reached ({self.max_calls})")
            time.sleep(pace[0] + random.random() * (pace[1] - pace[0]))
            self.calls += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    self.streak = 0
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code in BLOCK_CODES:
                    self.blocks += 1
                    self.streak += 1
                    if self.streak >= 2:
                        # blocks are cool-downs (usually lifted within the hour), so wait it out a few times first
                        if self.waits >= self.max_waits:
                            raise Blocked(f"HTTP {e.code} twice in a row, after {self.waits} cool-down waits")
                        self.waits += 1
                        cool = 300 + random.random() * 300
                        print(f"   🧊 HTTP {e.code} twice: cooling down {cool / 60:.0f} min "
                              f"(wait {self.waits}/{self.max_waits}), then retrying", flush=True)
                        time.sleep(cool)
                        self.streak = 0
                        continue
                    wait = e.headers.get("Retry-After", "")
                    wait = min(int(wait), 120) if wait.isdigit() else 30 + random.random() * 15
                    print(f"   ⏳ HTTP {e.code}, waiting {wait:.0f}s, then retrying", flush=True)
                    time.sleep(wait)
                    continue
                if e.code >= 500:  # a server error is a page that never loaded, not an empty page
                    read_error(f"HTTP {e.code}")
                    continue
                self.streak = 0
                return ""
            except Exception as e:  # timeout, reset, IncompleteRead: bounded retries, then ReadFailed
                read_error(type(e).__name__)


def txt(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def cards(page):
    out = []
    for li in re.split(r"<li[\s>]", page)[1:]:
        m = re.search(r"jobPosting:(\d+)", li)
        if not m:
            continue
        grab = lambda pat: txt((re.search(pat, li, re.S) or [None, ""])[1])
        out.append({
            "id": m.group(1),
            "title": grab(r'base-search-card__title[^>]*>(.*?)</h3>'),
            "company": grab(r'base-search-card__subtitle[^>]*>(.*?)</h4>'),
            "location": grab(r'job-search-card__location[^>]*>(.*?)</span>'),
            "posted": (re.search(r'datetime="([^"]+)"', li) or [None, ""])[1],
            "card_pay": grab(r'job-search-card__salary-info[^>]*>(.*?)</span>'),
        })
    return out


def posting(page):
    grab = lambda pat: txt((re.search(pat, page, re.S) or [None, ""])[1])
    body = (re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S) or [None, ""])[1]
    body = re.sub(r"<br\s*/?>|</p>|</li>", "\n", body)
    text = "\n".join(" ".join(html.unescape(re.sub(r"<[^>]+>", " ", l)).split()) for l in body.split("\n"))
    crit = dict(zip([txt(x) for x in re.findall(r'description__job-criteria-subheader[^>]*>(.*?)</h3>', page, re.S)],
                    [txt(x) for x in re.findall(r'description__job-criteria-text[^>]*>(.*?)</span>', page, re.S)]))
    closed = bool(re.search(r"no longer accepting applications", page, re.I))
    return {"pay": grab(r'compensation__salary[^>]*>(.*?)</div>'), "text": text.strip(),
            "seniority": crit.get("Seniority level", ""), "type": crit.get("Employment type", ""), "closed": closed}


def code_filter(c, K):
    t = norm(c["title"])
    if not TITLE_RE.search(t):
        return "title off list"
    if DROP_TITLE.search(t):
        return "title dropped (specialist/engineer/etc.)"
    if REGION_TAG.search(t) and not re.search(r"\b(?:us|usa|united states|north america|americas)\b", t):
        return "region-only title (e.g. Europe Only)"
    if EXCLUDE_CO.search(c["company"].lower()):
        return "excluded company"
    if AGGREGATOR.search(c["company"].lower()) or REPOSTER.search(c["company"].lower()):
        return "aggregator"
    ok, why = loc_ok(c["location"], is_remote=c.get("where") == "remote US")
    if not ok:
        # Remote-filter cards often show a city (HQ) even for real remote jobs: keep US ones, let the
        # posting text decide in phase 2 (screen() fails office/hybrid roles from the body).
        l = c["location"].lower()
        if c.get("where") == "remote US" and US_WORDS.search(l) and not NON_US.search(l):
            c["city_card"] = True
        else:
            return why
    k = is_known({"url": c["url"], "id": c["id"], "company": c["company"], "title": c["title"]}, K)
    if k:
        return f"already logged ({k})"
    if COVERED and key_co(c["company"]) in COVERED:
        return "covered by board sweep (company has a live board)"
    return ""


COVERED = set()
LI_DROPS = []  # drop log


def covered_companies():
    """Company keys with a live board in the board sweep: their postings come in through the lanes,
    so LinkedIn only reads postings from companies on other systems (iCIMS, Taleo, SuccessFactors, own sites)."""
    try:
        b = load_boards()
    except SystemExit:
        return set()
    try:
        wdn = json.loads((DATA / "workday_names.json").read_text(encoding="utf-8")).get("names", {})
    except Exception:
        wdn = {}
    wdn = {**b.get("wd_learned", {}), **wdn}
    out = set()
    for p, slugs in b.get("slugs", {}).items():
        for s_ in slugs:
            if f"{p}:{s_}" in b["dead"]:
                continue
            nm = b["names"].get(f"{p}:{s_}")
            if p == "workday":
                nm = nm or wdn.get(s_.split("|")[0])  # Workday codes alone ("wf") would never match a real name
                if not nm:
                    continue
            k = key_co(nm or pretty(s_))
            if len(k) >= 3:
                out.add(k)
    return out


def keyed(path, terms, custom):
    """A custom --terms run gets its own copy of a state file (name + a short hash of the term list), so it
    can't overwrite or delete the full run's. The full run keeps the plain name."""
    if not custom:
        return path
    h = hashlib.sha1("\n".join(terms).encode("utf-8")).hexdigest()[:8]
    return path.with_name(f"{path.stem}.{h}{path.suffix}")


def load_state(fresh, terms, path=None):
    path = path or STATE
    if fresh or not path.exists():
        return 0
    s = json.loads(path.read_text(encoding="utf-8"))
    return s.get("next_term", 0) if s.get("terms") == terms else 0


def num(v, kind, name):
    """A bad number on the command line stops with a clear message, not a traceback."""
    try:
        n = kind(v)
    except (TypeError, ValueError):
        raise SystemExit(f"{name} needs a number, got {v!r}")
    if n < 0:
        raise SystemExit(f"{name} can't be negative")
    return n


def main(argv):
    def opt(name, default):
        return argv[argv.index(name) + 1] if name in argv else default

    global COVERED
    single = "--single-terms" in argv
    custom_terms = [t.strip() for t in opt("--terms", "").split(",") if t.strip()]
    custom = bool(custom_terms)
    terms = custom_terms or (TERMS if single else GROUPS)
    terms = terms[:num(opt("--max-terms", len(terms)), int, "--max-terms")]
    hours = num(opt("--hours", 24), int, "--hours")
    tpr = "r86400" if hours <= 24 else ("r604800" if hours <= 168 else "r2592000")
    pages = num(opt("--pages", 3 if single else 40), int, "--pages")  # OR groups page until results run out (date-sorted)
    trickle = num(opt("--trickle", 0), float, "--trickle")  # seconds between calls (e.g. 25): a slow steady run instead of a burst
    s_pace = (trickle, trickle * 1.3) if trickle else (1.5, 2.5)
    # posting reads 2.5-4s apart drew frequent blocks; slower pacing avoids most of them.
    # 6-9s is slower but steady.
    p_pace = (trickle, trickle * 1.3) if trickle else (6.0, 9.0)
    client = Client(num(opt("--max-calls", 1200), int, "--max-calls"), num(opt("--max-waits", 5), int, "--max-waits"))
    max_posts = num(opt("--max-postings", 0), int, "--max-postings")  # chunked runs; 0 = no limit
    RUNLOG = DATA / "logs" / "li_runs.jsonl"  # one line per run, committed (why runs end, empty reads)
    COVERED = set() if "--no-covered-skip" in argv else covered_companies()
    print(f"Covered by board sweep: {len(COVERED):,} companies (their LinkedIn cards are skipped)", flush=True)
    t0 = time.time()

    def status(phase, **kw):
        d = dict(phase=phase, updated=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                 elapsed_s=int(time.time() - t0), calls=client.calls, blocked=client.blocks, cool_waits=client.waits,
                 kept=len(found), dropped=len(why_drop), **kw)
        try:
            tmp = STATUS.with_suffix(".tmp"); tmp.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8", newline="\n"); tmp.replace(STATUS)
        except Exception:
            pass  # best effort: this step is optional, carry on without it
    ckpt_file, state_file = keyed(CKPT, terms, custom), keyed(STATE, terms, custom)
    start = load_state("--fresh" in argv, terms, state_file)
    K = known()
    found, why_drop, seen_ct = {}, {}, set()
    failed_groups = []  # term+place+page a read never recovered on; those are NOT covered, unlike a clean end-of-results
    stopped = ""
    sig = dict(terms=terms, hours=hours, where=[w[0] for w in WHERE], pages=pages)
    phase = "search"
    reread = "--reread" in argv
    prev, n_un = {}, 0
    if reread:
        # read again only the postings a finished run couldn't read ("didn't load" / "not read"), from
        # data/li_candidates.json. No new searching; the read ones and screened_out are kept as they were.
        if not OUT.exists():
            raise SystemExit("Nothing to reread: data/li_candidates.json is missing.")
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        rows = prev.get("candidates", []) + prev.get("screened_out", [])
        found = {str(c["id"]): c for c in rows}
        n_un = 0
        for c in found.values():
            fl = c.get("flags") or []
            if c.pop("unread", False) or (fl and fl[0].startswith(("Posting didn't load", "Posting not read"))):
                for k in ("verdict", "fails", "flags"):
                    c.pop(k, None)
                n_un += 1
        why_drop = {}
        phase, start = "postings", len(terms)
        print(f"Reread: {n_un} unread posting(s) of {len(found)} from {OUT.relative_to(ROOT)} "
              f"(run {prev.get('run', '?')}); no new searching", flush=True)
    elif "--resume" in argv:
        # a restart mid-run used to lose every result and every result was lost. The checkpoint
        # (written after each group and every 10 postings) brings back the cards, the drops and the postings read.
        if not ckpt_file.exists():
            raise SystemExit(f"No checkpoint to resume (data/{ckpt_file.name}). Run without --resume"
                             + (" (a --terms run resumes only with the same --terms)." if custom else "."))
        ck = json.loads(ckpt_file.read_text(encoding="utf-8"))
        age_h = (time.time() - ck.get("saved_ts", 0)) / 3600
        if ck.get("sig") != sig:
            raise SystemExit("Checkpoint is for a different run (terms/window/places/pages). Run without --resume.")
        if age_h > CKPT_MAX_AGE_H:
            raise SystemExit(f"Checkpoint is {age_h:.1f}h old (max {CKPT_MAX_AGE_H}h for a 24h window). Run without --resume.")
        found, why_drop = ck["found"], ck["why_drop"]
        for c in found.values():
            if c.pop("unread", False):  # marked FLAG only because it wasn't read: read it this time
                if c.get("empty_tries", 0) >= 2:  # came back empty twice: stop re-queuing it
                    c["flags"] = ["Posting came back empty twice: open it in the browser"]
                    continue
                for k in ("verdict", "fails", "flags"):
                    c.pop(k, None)
        seen_ct = {tuple(x) for x in ck["seen_ct"]}
        failed_groups[:] = ck.get("failed_groups", [])
        start, phase = ck["done_term"], ck["phase"]
        n_read = sum(1 for c in found.values() if "verdict" in c)
        print(f"Resuming a cut run ({age_h * 60:.0f} min old): {start}/{len(terms)} groups searched, {len(found)} kept, "
              f"{n_read} postings already read", flush=True)
    done_term = start

    def save_ckpt(ph):
        try:
            tmp = ckpt_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(sig=sig, saved_ts=time.time(), phase=ph, done_term=done_term, found=found,
                                           why_drop=why_drop, seen_ct=sorted(seen_ct),
                                           failed_groups=failed_groups), ensure_ascii=False),
                           encoding="utf-8", newline="\n")
            tmp.replace(ckpt_file)  # atomic
        except Exception as e:
            print(f"   (checkpoint not saved: {e})", flush=True)

    print(f"LinkedIn sweep: {len(terms) - start} {'terms' if single else 'OR groups'} x {len(WHERE)} places, up to {pages} pages, window {hours}h"
          + (f", trickle {trickle:g}s/call" if trickle else "")
          + (f" (resuming at term {start + 1})" if start else ""), flush=True)
    def sweep_group(i, term, label, params, first_page=0):
        """Search one group at one place, paging until results run out. Returns None when it finished, or a
        failed_groups row when a page never loaded (that page and the rest of the group are NOT searched)."""
        ids_here = set()  # cards this group+place has shown (stop rule)
        dry = 0  # pages in a row with nothing new to the run
        for p in range(first_page, pages):
            q = dict(params, keywords=term, f_TPR=tpr, start=str(p * 10))
            if not single:
                q["sortBy"] = "DD"  # newest first
            try:
                page = client.get(SEARCH + urllib.parse.urlencode(q), s_pace)
            except ReadFailed as e:
                # a read that never recovered is not "results ran out": report it as failed instead of
                # silently treating this group+place as fully (and falsely) swept.
                print(f"   ✗ {label} · page {p + 1} ({term[:40]}): {e}", flush=True)
                return {"term": term, "where": label, "page": p + 1, "reason": str(e)}
            got = cards(page)
            new_here = 0
            fresh = {c["id"] for c in got} - ids_here
            ids_here |= fresh
            for c in got:
                c["where"], c["term"] = label, term
                c["url"] = f"https://www.linkedin.com/jobs/view/{c['id']}/"
                if c["id"] in found or c["id"] in why_drop:
                    continue
                new_here += 1
                ct = (norm(c["company"]), norm(c["title"]))  # same req posted to several cities
                if ct in seen_ct:
                    why_drop[c["id"]] = "duplicate (same company + title this run)"
                    continue
                r = code_filter(c, K)
                if r:
                    why_drop[c["id"]] = r
                    LI_DROPS.append(drop_row("linkedin", r, c.get("company"), c.get("title"), c.get("location"),
                                             c.get("url"), c.get("card_pay", ""), c.get("posted", ""),
                                             term=c.get("term"), where=c.get("where")))
                else:
                    found[c["id"]] = c
                    seen_ct.add(ct)
            if (p + 1) % 10 == 0:
                print(f"[{int(time.time() - t0)}s]   group {i + 1} · {label} · page {p + 1} · "
                      f"{new_here} new on this page · kept {len(found)} · calls {client.calls}", flush=True)
            # "no new" counted cards earlier groups already saw, so overlapping groups stopped on
            # page 1 (group 5 read 0 cards). Now stop only when LinkedIn repeats this search's own cards.
            dry = dry + 1 if new_here == 0 else 0
            # Local-area pages ran all 40 deep at ~1 new card a page (~40 calls a group, ~0 kept).
            if len(got) < 10 or (not single and (not fresh or dry >= 3)):
                break  # results ran out, the search is repeating itself, or 3 pages in a row added nothing
        return None

    try:
        for i in range(start if phase == "search" else len(terms), len(terms)):
            term = terms[i]
            for label, params in WHERE:
                bad = sweep_group(i, term, label, params)
                if bad:
                    failed_groups.append(bad)
            done_term = i + 1
            cov = sum(1 for r in why_drop.values() if r.startswith("covered"))
            print(f"[{int(time.time() - t0)}s] {'term' if single else 'group'} {i + 1}/{len(terms)}: "
                  f"calls {client.calls} · cards {len(found) + len(why_drop):,} · kept {len(found)} · "
                  f"covered by lanes {cov} · blocked {client.blocks}  ({term[:60]})", flush=True)
            status("search", group=i + 1, groups=len(terms), covered_skipped=cov)
            save_ckpt("search")
            time.sleep(6 + random.random() * 4 if not trickle else trickle)
        if failed_groups and phase == "search":
            # One more try per failed page after a pause: a dropped connection is usually gone within minutes.
            where_by = dict(WHERE)
            print(f"Retrying {len(failed_groups)} failed search page(s) after {RETRY_PAUSE_S}s…", flush=True)
            time.sleep(RETRY_PAUSE_S)
            still = []
            for f in failed_groups:
                bad = sweep_group(terms.index(f["term"]) if f["term"] in terms else 0, f["term"], f["where"],
                                  where_by[f["where"]], first_page=f["page"] - 1)
                if bad:
                    still.append(dict(bad, retried=True))
            print(f"Retry pass: {len(failed_groups) - len(still)} recovered, {len(still)} still failing", flush=True)
            failed_groups[:] = still
            save_ckpt("search")
    except Blocked as e:
        stopped = str(e)
        print(f"🛑 Stopped: {stopped}. Checkpoint saved; next run resumes at term {done_term + 1}.", flush=True)

    # repost check against this run's newest IDs (all cards seen, kept or not)
    all_ids = sorted(int(x) for x in list(found) + list(why_drop))
    if reread:
        all_ids = []  # keep each row's low_id from the original run (its full card list isn't here)
    top = all_ids[int(len(all_ids) * 0.95)] if all_ids else 0
    for c in found.values():
        if not reread:
            c["low_id"] = bool(top and int(c["id"]) < top - 50_000_000)

    paused = 0  # postings left for the next --resume chunk
    n_ok = n_empty = 0

    backed_up = []

    def write_out(partial=False, held=0, held_ids=()):
        """Written after every 20 reads too, so a killed run still leaves what it read."""
        k_ = [c for c in found.values() if c.get("verdict") and c["verdict"] != "FAIL"]
        f_ = [c for c in found.values() if c.get("verdict") == "FAIL"]
        left = held + sum(1 for c in found.values() if "verdict" not in c or c.get("unread"))
        order_ = {"PASS": 0, "FLAG": 1}
        k_.sort(key=lambda c: (order_.get(c["verdict"], 2), c.get("low_id", False), -int(c["id"])))
        d_ = {}
        for r in why_drop.values():
            r = re.sub(r" \(.*\)$", "", r)
            d_[r] = d_.get(r, 0) + 1
        carry = []  # rows an earlier run in the same window left in the file (gap-fill runs add to it)
        if OUT.exists():
            try:
                old = json.loads(OUT.read_text(encoding="utf-8"))
            except Exception:
                old = {}
            if not reread and not backed_up:
                backed_up.append(1)
                try:
                    PREV.write_text(OUT.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
                except Exception as e:
                    print(f"   (previous candidates not backed up: {e})", flush=True)
            if custom and not reread and old.get("window_hours") == hours:
                carry = [c for c in old.get("candidates", []) + old.get("screened_out", [])
                         if str(c.get("id")) not in found and str(c.get("id")) not in held_ids]
                for c in carry:
                    (f_ if c.get("verdict") == "FAIL" else k_).append(c)
                left += sum(1 for c in carry if c.get("unread"))
                k_.sort(key=lambda c: (order_.get(c["verdict"], 2), c.get("low_id", False), -int(c["id"])))
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "run": datetime.now(timezone.utc).astimezone().isoformat(timespec="minutes"), "window_hours": hours,
            "partial": partial, "unread_left": left,
            "stats": {"terms_done": done_term - start, "terms_total": len(terms), "calls": client.calls,
                      "blocked_calls": client.blocks, "cards": len(found) + len(why_drop), "kept": len(k_),
                      "screened_out": len(f_), "read_ok": n_ok, "read_empty": n_empty,
                      "dropped_by_filter": d_, "stopped_early": stopped,
                      "paused_left": paused, "failed_groups": failed_groups},
            "candidates": k_, "screened_out": f_,
            **({"reread_of": {"run": prev.get("run"), "stats": prev.get("stats")},
                "stats_reread": {"reread": n_un, "still_unread": sum(1 for c in k_ if c.get("unread"))}} if reread else {}),
        }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(OUT)

    if "--no-postings" not in argv and not stopped:
        todo = [c for c in found.values() if "verdict" not in c]
        if max_posts and len(todo) > max_posts:
            paused = len(todo) - max_posts
            todo = todo[:max_posts]
        print(f"Reading {len(todo)} postings" + (f" ({len(found) - len(todo)} already read)" if len(todo) < len(found) else "") + "…", flush=True)
        save_ckpt("postings")
        try:
            for n_read, c in enumerate(todo, 1):
                if n_read % 5 == 0:
                    save_ckpt("postings")
                if n_read % 20 == 0:
                    write_out(partial=True)
                if n_read % 10 == 0:
                    print(f"[{int(time.time() - t0)}s] postings {n_read}/{len(todo)} · calls {client.calls} · "
                          f"blocked {client.blocks}", flush=True)
                    status("postings", read=n_read, total=len(todo))
                try:
                    page = client.get(POSTING.format(c["id"]), p_pace)
                except ReadFailed:
                    page = ""  # same handling as an empty read: flagged unread below, never silently dropped
                if not page:
                    n_empty += 1
                    c["empty_tries"] = c.get("empty_tries", 0) + 1
                    c.update(verdict="FLAG", fails=[], flags=["Posting didn't load: open it in the browser"], unread=True)
                    continue
                n_ok += 1
                d = posting(page)
                c.update(pay=d["pay"] or c["card_pay"], seniority=d["seniority"], type=d["type"])
                v, fails, flags = screen(d["text"], location=c["location"], title=c["title"], card_pay=c["pay"], company=c.get("company", ""))
                if c.get("city_card") and any(f.startswith("No remote language") for f in flags):
                    # these are often plain on-site jobs leaking through LinkedIn's remote filter
                    v, fails = "FAIL", [f"Remote-filter leak: card shows {c['location']}, no remote wording in the posting"] + fails
                if d["closed"]:
                    v, fails = "FAIL", ["Posting closed (no longer accepting applications)"] + fails
                if c["low_id"]:
                    flags.append("Low job ID: likely a repost, check the employer's posted date")
                if c.get("city_card") and v != "FAIL":
                    flags.append(f"Remote-filter card showed a city ({c['location']}): confirm the workplace tag on the job page")
                c.update(verdict=v, fails=fails, flags=flags)
        except Blocked as e:
            stopped = f"posting phase: {e}"
            print(f"🛑 Stopped in posting phase: {e}. Unread postings kept as FLAG.", flush=True)
            for c in found.values():
                if "verdict" not in c:
                    c.update(verdict="FLAG", fails=[], flags=["Posting not read (rate limit): open it in the browser"],
                             unread=True)
            save_ckpt("postings")  # --resume reads the unread ones later
        if paused:
            print(f"⏸ Chunk done: {max_posts} postings read, {paused} left. Rerun with --resume for the next chunk.", flush=True)
            save_ckpt("postings")
    else:
        for c in found.values():
            if "verdict" not in c:
                c.update(verdict="FLAG", fails=[], flags=["Posting not read yet"]
                         + (["Low job ID: likely a repost"] if c["low_id"] else []), unread=True)
    left_unread = paused and [c for c in found.values() if "verdict" not in c]
    for c in left_unread or []:
        c["_pending"] = True  # not read yet in this chunk: kept out of the output, still in the checkpoint
    hold = {k: found.pop(k) for k in [k for k, c in found.items() if c.pop("_pending", False)]}
    write_out(partial=bool(paused or stopped), held=len(hold), held_ids=set(hold))
    found.update(hold)
    kept = [c for c in found.values() if c.get("verdict") and c["verdict"] != "FAIL"]
    out_fail = [c for c in found.values() if c.get("verdict") == "FAIL"]
    drops = {}
    for r in why_drop.values():
        r = re.sub(r" \(.*\)$", "", r)
        drops[r] = drops.get(r, 0) + 1
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="minutes")
    for c in out_fail:
        LI_DROPS.append(drop_row("linkedin:vet", "screen FAIL: " + "; ".join(c.get("fails", []))[:200], c.get("company"),
                                 c.get("title"), c.get("location"), c.get("url"), c.get("pay", ""), c.get("posted", "")))
    write_drops("linkedin", LI_DROPS)
    try:
        RUNLOG.parent.mkdir(parents=True, exist_ok=True)
        with open(RUNLOG, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(dict(run=now, args=" ".join(argv), secs=int(time.time() - t0), calls=client.calls,
                                    blocked=client.blocks, waits=client.waits, read_ok=n_ok, read_empty=n_empty,
                                    kept=len(kept), screened_out=len(out_fail), paused_left=paused,
                                    stopped=stopped or "",
                                    failed_groups=[{k: f[k] for k in ("term", "where", "page")}
                                                   for f in failed_groups])) + "\n")
    except Exception as e:
        print(f"   (run log not written: {e})", flush=True)
    state_file.write_text(json.dumps({"terms": terms, "next_term": done_term if done_term < len(terms) else 0,
                                 "saved": now}, indent=1) + "\n", encoding="utf-8", newline="\n")

    status("done", candidates=len(kept), screened_out=len(out_fail))
    if not stopped and not paused and "--no-postings" not in argv and ckpt_file.exists():
        ckpt_file.unlink()  # finished: nothing to resume
    elif paused:
        print(f"Checkpoint kept: {paused} postings left. Rerun with --resume.", flush=True)
    elif stopped:
        print("Checkpoint kept: rerun with --resume to finish (same day).", flush=True)
    print(f"\nRead {n_ok} · came back empty {n_empty} · Calls {client.calls} · blocked {client.blocks} (cool-down waits {client.waits}) · "
          f"cards {len(found) + len(why_drop)} · kept {len(kept)} · screened out {len(out_fail)} · "
          f"{int(time.time() - t0)}s")
    print("Dropped by filter: " + ", ".join(f"{k} {v}" for k, v in sorted(drops.items(), key=lambda x: -x[1])))
    if failed_groups:
        print(f"⚠ {len(failed_groups)} search page(s) failed to load and were NOT searched (see failed_groups "
              f"in {OUT.relative_to(ROOT)}):")
        for f in failed_groups[:10]:
            print(f"     · {f['where']} · page {f['page']} ({f['term'][:60]}): {f['reason']}")
    for c in kept:
        icon = "✅" if c["verdict"] == "PASS" else "🟡"
        print(f"{icon} {c['company']} - {c['title']} · {c['location']} · {c.get('pay') or 'no pay shown'} · {c['url']}")
        for f in c["flags"]:
            print(f"     · {f}")
    print(f"→ {OUT.relative_to(ROOT)}")
    return {"paused": paused, "stopped": stopped, "failed_groups": len(failed_groups)}


def drain(argv, run=None):
    """--drain: run a chunk, then rerun with --resume until no postings are left, a block or call cap stops the
    run, or --max-chunks chunks have run. The first chunk takes the arguments as given."""
    run = run or main
    cap = num(argv[argv.index("--max-chunks") + 1] if "--max-chunks" in argv else 10, int, "--max-chunks")
    args = [a for a in argv if a != "--drain"]
    res = run(args)
    chunks = 1
    later = [a for a in args if a != "--fresh"] + ([] if "--resume" in args else ["--resume"])
    while res and res.get("paused") and not res.get("stopped") and chunks < cap:
        chunks += 1
        print(f"\n=== Drain: chunk {chunks} ({res['paused']} postings left) ===", flush=True)
        res = run(later)
    if res and res.get("paused"):
        print(f"Drain ended with {res['paused']} postings left"
              + (f" ({res['stopped']})" if res.get("stopped") else f" (hit --max-chunks {cap})")
              + ". Rerun with --resume to continue.", flush=True)
    return res


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    (drain if "--drain" in sys.argv[1:] else main)(sys.argv[1:])
