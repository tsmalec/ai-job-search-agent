"""board_sweep.py — reverse sweep of public job boards (Greenhouse, Lever, Ashby). Code finds, AI judges.

Idea from the public career-ops repo (career-ops-hq/career-ops, MIT): scan *every* company on the big
board platforms, not just a hand-kept list. Our own code, stdlib only. Board slug lists come from the
public MIT dataset Feashliaa/job-board-aggregator (refreshed with --refresh-boards).

Usage (from the repo root):
    python scripts/board_sweep.py                    full sweep, postings from the last 24h
    python scripts/board_sweep.py --hours 48         wider window
    python scripts/board_sweep.py --since-last      PM top-off: window = time since the last run
    python scripts/board_sweep.py --limit 300        first 300 boards per platform (quick test)
    python scripts/board_sweep.py --only greenhouse  one platform (greenhouse|lever|ashby|workday|bamboohr)
    python scripts/board_sweep.py --no-vet           skip the vet pass (full posting read + screen of each candidate)
    python scripts/board_sweep.py --with bamboohr    add a platform that's off by default (BambooHR)
    python scripts/board_sweep.py --skip workday     every platform except these (comma list), e.g. to run Workday or
                                                     BambooHR on their own schedule
    python scripts/board_sweep.py --dead-only        re-check EVERY dead board on all lanes, no sweep
    python scripts/board_sweep.py --refilter         re-apply the filters to the saved candidates (no network)
    python scripts/board_sweep.py --refresh-boards   re-download the slug lists, keep learned names
    python scripts/board_sweep.py --workers 18        workers per lane (default 18; Lever can grow to 24 via handoff)
    python scripts/board_sweep.py --pace 0.4          fixed pause for every platform (default: adaptive per platform,
                                                     starting GH/Ashby 0.2s, Lever 0.4s; fast answers shrink it,
                                                     settled once per batch: >10% slow/timeouts x1.5, >3% x1.2,
                                                     all fast x0.8; a 429/5xx doubles it at once; 0.1s-2s)
    python scripts/board_sweep.py --batch 250        boards per batch (default 100)
    python scripts/board_sweep.py --resume           continue a cut-off sweep from data/sweep_checkpoint.json

Parallel lanes: Greenhouse, Lever and Ashby each run in their own lane at the same time (different
sites, so each sees only its own lane's load): 18 workers per lane, 100-board batches. A lane that finishes
hands its workers, the moment its main pass ends, to lanes still in their main pass (Lever first, up to 24).
When only one lane is left in its main pass and it
is at its cap, up to 4 spare workers start its rolling slow lane, retrying its timed-out boards during its main
pass instead of after it.
Total time ~ the slowest lane (Lever).
Pause: adaptive for Greenhouse/Ashby (settled once per batch, 0.1-2s); fixed 0.4s for Lever (a longer pause
never cut its timeouts in 3 test runs). Main-pass timeout adapts per platform to 3x its 90th-percentile answer
time (GH/Ashby 5-10s, Lever 10-25s); one try per board. Anything slower goes to that lane's own slow lane right
after its main pass: adaptive timeouts (95th-pct answer time x3/x6/x10, never below 10/20/30s, max 30s), 3 tries (Lever 2; 
needed no third try). The slow lane is per run: nothing is remembered. A board that still fails gets a last-chance try (60s,
8 at a time, no new tries after 3 min) after all lanes finish; a platform with more than 20 such boards skips it (site-wide
trouble, not a few giant boards) and they stay failed for next run. Anything still failing is named in the summary.
Lesson from a slow scheduler machine: timeouts now count in the timeout tuning (as "took at
least the timeout", so a slow tail raises it), a batch over 10% slow cuts that lane's workers by 12 (min 12; Lever exempt)
and a clean batch adds 6 back, and the status file shows each lane's slow share. BambooHR is off by default (--with bamboohr).
Board memory: dead boards go to data/sweep_dead/<platform>.json (the one source for them) and are skipped; the oldest ~1/30th of them are
re-checked after each sweep (--no-recheck skips that).
Crash-safe: after every batch the keepers so far go to sweep_candidates.json (partial: true) and a checkpoint is
saved with each lane's progress; --resume continues every lane where it stopped. A crashed lane leaves the
checkpoint and the run exits asking for --resume. The checkpoint is deleted when a full sweep finishes.
    python scripts/board_sweep.py --add-board greenhouse:acme "found on LinkedIn (Acme Ops Mgr)"
                                                     add a board the dataset doesn't list, with its source
    python scripts/board_sweep.py --boards-added     list boards added beyond the dataset, with sources

Off-list catch: a posting whose title is NOT on the target list but has a lane word
(operations, program, strategy, transformation, enablement), states pay whose top is >= the screen minimum, and passes
every other filter is kept as an `offlist: true` candidate. Counted separately each run. Greenhouse lists
carry no pay, so for those few the posting itself is read once for its pay line.
Board learning: each run adds any Greenhouse/Lever/Ashby board found in seen.txt /
leads.json URLs that the dataset lacks, to sweep_boards.json "added" with a source note, and prints them.

Keeps a posting only if ALL hold:
    - posted/first published within --hours
    - title matches the candidate's target-title list (agent/adjustments.md APPLIED "Target-title search list",
      and lane variants, from config.json); Specialist / engineer / sales-quota titles dropped
    - location is remote US ("Remote", "Anywhere in the United States", "US Remote"...) or an
      place within the commute radius of home (config.json near_cities)
    - company isn't on config.json exclude_companies
    - not already in data/seen.txt or data/leads.json (URL, posting ID, or company + title)

Writes:
    data/sweep_candidates.json   this run's keepers, with new/changed flags (the scheduler vets these; see
                                 agent/adjustments.md PENDING "Board sweep")
    data/sweep_snapshot.json     job-ID snapshot of title-matching postings, for new/changed/vanished diffs
    data/sweep_boards.json       real company names, added/learned boards, failed boards (small, committed)
    data/sweep_dead/*.json       dead boards per platform, slug -> date (small, committed; the scheduler syncs these)
    data/sweep_slugs.json        public slug lists, local cache (gitignored; downloaded when missing)
    data/sweep_checkpoint.json   mid-sweep progress (only while a sweep is unfinished)
    data/sweep_status.json       live status: phase, boards done/total, %, keepers, errors, rate, ETA, pauses.
                                 Rewritten every batch and every 30s in the slow lane; local only (gitignored)
Polite: 18 workers, a pause per request, retries with backoff, then per batch one slower retry pass
(1/3 workers, 3x pause) for boards that didn't answer. The summary prints how many boards failed.
"""
import json, re, sys, time, random, threading, urllib.request, urllib.error
import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent))
from common import SEARCH, HOME, STATE_RE, EXCLUDE_COMPANIES  # noqa: E402
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BOARDS = DATA / "sweep_boards.json"   # empirical, committed: names, added boards, learned Workday names, failed
DEAD_DIR = DATA / "sweep_dead"         # empirical, committed: one small file per platform, slug -> date last seen dead
SLUG_CACHE = DATA / "sweep_slugs.json"  # public dataset lists, local cache only (gitignored; re-downloaded when missing)
SNAP = DATA / "sweep_snapshot.json"
OUT = DATA / "sweep_candidates.json"
CKPT = DATA / "sweep_checkpoint.json"
STATUS = DATA / "sweep_status.json"
JD_DIR = DATA / "sweep_jds"  # vet pass: each candidate's full posting text (local only, gitignored)
VET_WORKERS = 8  # live progress, rewritten every batch (local only, gitignored; the scheduler's watcher reads it)
START_PACE = {"greenhouse": 0.2, "ashby": 0.2, "lever": 0.4, "workday": 0.3, "bamboohr": 0.3,
              "smartrecruiters": 0.3, "recruitee": 0.3, "rippling": 0.3, "breezy": 0.3, "workable": 1.0}  # adaptive pause starting points; new lanes kept at 0.3 (no 429/5xx, pause never rose)
SLOW_LANE_PAUSE_CAP = 5.0  # slow lane uses the same adaptive pause, never more than 5s
TIMEOUT = [5]  # per request: 5s in the main pass (slower goes to the slow lane); slow lane escalates per try
LANE_WORKERS = {"greenhouse": 18, "ashby": 18, "lever": 18, "workday": 30, "bamboohr": 18,
                "smartrecruiters": 12, "recruitee": 12, "rippling": 12, "breezy": 12, "workable": 4} # SmartRecruiters/Recruitee/Rippling/Breezy 12: ramp 6/12/18/24 (cloud) = 0% errors, no 429/5xx; speed flat past 12 (lanes only 6-41 boards, so retest once they hold a few hundred) # Workday 30: ramp 18/24/30/36 = 10.6/13.9/18.3/19.6 boards/s, errors <=0.5%, no pushback # parallel lanes, one site each, so 18 per site = the load already tested
LANE_MAX = {"lever": 24, "workday": 36, "bamboohr": 36}  # handoff caps (default 24); Workday/BambooHR 36: Workday was clean at 36 in the ramp, and in the full test 100+ freed workers sat idle while Workday ran its tail
ROLL_WORKERS = 4  # rolling slow lane: spare workers retry a capped lane's timed-out boards during its main pass
SLOW_TRIES = {"lever": 2} # slow-lane tries per platform (default 3); one test run: nearly all answered on try 1, none needed try 3
ADAPTIVE_PAUSE = {"greenhouse": True, "ashby": True, "lever": False, "workday": True, "bamboohr": True}  # Lever: fixed 0.4s; 3 test runs showed a longer pause never cut its timeouts
MAIN_TIMEOUT = {"greenhouse": (5, 5, 10), "ashby": (5, 5, 10), "lever": (15, 10, 25), "workday": (8, 5, 15), "bamboohr": (10, 10, 20),
                "smartrecruiters": (8, 5, 15), "recruitee": (8, 5, 15), "rippling": (8, 5, 15), "breezy": (8, 5, 15),
                "workable": (10, 10, 20)} # new lanes unchanged after the ramp (no timeouts at any setting) # start, floor, ceiling: adapts to 3x the 90th-pct answer time (timeouts count)
# Slow-lane per-try floors and the max (default 10/20/30s, max 30). BambooHR (the scheduler's box): bimodal, a slow
# chunk answers in 10-40s, so 15/30/45.
SLOW_FLOORS = {"bamboohr": ((15, 30, 45), 45)}
# Worker backoff: a batch with >10% slow/timeouts cuts the lane's workers by 12 (never below 12); a clean batch
# (<3%) adds 6 back, up to where it started. Lever keeps its tuned fixed setup.
WORKER_BACKOFF = {"greenhouse", "ashby", "workday", "bamboohr", "smartrecruiters", "recruitee", "rippling", "breezy"} # new lanes (ramp clean)
LC_WORKERS, LC_CAP, LC_BUDGET = 8, 20, 180  # last chance: 8 at once, max 20 boards per platform, no new tries after 180s
SLUG_SRC = "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/{p}_companies.json"
PLATFORMS = ("greenhouse", "lever", "ashby", "workday", "bamboohr",
             "smartrecruiters", "recruitee", "rippling", "breezy", "workable")
# Platforms with a public slug list in the Feashliaa dataset. The rest (from the CareerScout repo,
# Ramcharan747/careerscout, MIT) have no public list: their boards come from ats_probe.py, which confirms them and
# records them under "added" in sweep_boards.json.
DATASET_PLATFORMS = ("greenhouse", "lever", "ashby", "workday", "bamboohr")
# Off by default: on the scheduler's box BambooHR ran 3.6 boards/s and left hundreds of boards to the 60s last-chance pass.
# Back with --with bamboohr (or --only bamboohr) once its timeout handling is fixed.
# New platforms (CareerScout): SmartRecruiters, Recruitee, Rippling and Breezy are on (ramp 6-24 workers clean, the candidate OK'd).
# Workable stays off: rate-limited (429, Cloudflare) from Claude's workspace in the probe and the
# ramp (100% first-try errors at 6 workers). Test it from the candidate's PC or the scheduler's box before turning it on.
DEFAULT_OFF = {"bamboohr", "workable"}
UA = "Mozilla/5.0 (job-search board_sweep; personal use)"

# ---------- title filter ----------
# Each pattern is one target title family (the scheduler's list + variants). Lowercase, matched on a normalized title.
TITLES = SEARCH["title_patterns"]
TITLE_RE = re.compile("|".join(TITLES))
# Off-list catch: lane word in the title + stated pay top >= the screen minimum (data/pay_rules.json)
LANE_RE = re.compile("|".join(SEARCH["lane_words"]))
PM_ONLY_RE = re.compile(r"project manag")
from common import pay_rules  # noqa: E402
OFFLIST_PAY_MIN = pay_rules()["screen_min"]


def offlist_needs_pay(title):
    """Off-list title whose only lane word is "project manager": stated pay required."""
    t = (title or "").lower()
    return bool(PM_ONLY_RE.search(t)) and not re.search(r"operation|\bops\b|program|strategy|strategic|transformation|enablement", t)


# A region tag in the title marks a non-US role even when the location just says "Remote" (e.g. titles
# ending "(LatAm Only)" or "(Europe Only)" listed as "Remote").
REGION_TAG = re.compile(r"[(\[][^)\]]*\b(?:europe|european|emea|latam|latin america|apac|asia|uk|united kingdom|canada|"
                        r"india|mexico|brazil|germany|france|spain|poland|philippines|australia|anz|dach|nordics?)\b[^)\]]*[)\]]",
                        re.I)


def pay_from_text(html):
    """First stated pay in a job description (Workday has no pay field; most US postings state it in the text)."""
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = re.sub(r"&nbsp;|&#160;|\s+", " ", t).replace("\u2013", "-").replace("\u2014", "-")
    m = re.search(r"\$\s?\d[\d,]*(?:\.\d+)?\s?[kK]?\s*(?:-|to)\s*\$?\s?\d[\d,]*(?:\.\d+)?\s?[kK]?[^.;$]{0,40}", t)
    if not m:
        m = re.search(r"\$\s?\d{2,3},\d{3}(?:\.\d+)?[^.;$]{0,40}", t)
    return m.group(0).strip() if m else ""


def pay_top(text):
    """Top of a stated annual USD pay range, or 0. Reads $150,000 / 150,000 USD / $150K; ignores hourly."""
    t = (text or "").replace("\u2013", "-").replace("\u2014", "-")
    if re.search(r"(?:/|per |an )\s*(?:hr|hour)\b|hourly", t, re.I) and not re.search(r"\d{3},\d{3}", t):
        return 0
    if re.search(r"\b(?:CAD|GBP|EUR|AUD|INR|£|€)\b|[£€]", t) and "USD" not in t and "$" not in t:
        return 0
    vals = []
    for m in re.finditer(r"\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?\s?[kK]\b|\d{5,6})(?:\.\d+)?", t):
        raw = m.group(1).replace(",", "").replace(" ", "")
        v = float(raw[:-1]) * 1000 if raw[-1] in "kK" else float(raw)
        if 30_000 <= v <= 1_000_000 and (m.group(0).strip().startswith("$") or "USD" in t or "," in m.group(1) or raw[-1] in "kK"):
            vals.append(v)
    return int(max(vals)) if vals else 0


def greenhouse_pay(r, pace):
    """Greenhouse lists have no pay: read the posting once (only for off-list finalists)."""
    try:
        _, slug, jid = r["id"].split(":", 2)
        d, _ = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}?pay_transparency=true", pace=pace)
        if not d:
            return ""
        rng = [x for x in d.get("pay_input_ranges") or [] if x.get("currency_type", "USD") == "USD"]
        if rng:
            hi = max(int(x.get("max_cents") or 0) for x in rng) // 100
            lo = min(int(x.get("min_cents") or 0) for x in rng) // 100
            return f"${lo:,}-${hi:,}"
        import html as _h
        txt = re.sub(r"<[^>]+>", " ", _h.unescape(_h.unescape(d.get("content") or "")))
        m = re.search(r"[^.]{0,80}\$\s?\d{2,3}(?:,\d{3})+[^.]{0,80}", txt)
        return m.group(0).strip() if m else ""
    except Exception:
        return ""
# Dropped even when a target phrase matches. config.json search.drop_title (a regex) replaces this list:
# an engineering search, for one, must not drop "engineer" / "developer" titles.
DROP_TITLE_DEFAULT = (
    # Drop audit: Lead/Principal Specialist kept; "engineer" = the job, not "engineering";
    # Associate Director / Associate VP are senior, not junior.
    r"(?<!lead )(?<!principal )\bspecialist\b|\bengineers?\b|developer|\bintern\b|internship|account executive|\bsdr\b|\bbdr\b|"
    r"sales representative|recruit|nurse|physician|clinical|counsel|attorney|paralegal|technician|"
    r"\bassociate\b(?!,?\s+(?:director|vice president|vp\b|general counsel))|coordinator|assistant|designer|scientist|architect|teacher|driver|warehouse|"
    r"people operations|leasing|travel|sap |hcm|coding|fraud|security|technical program|\bjunior\b|\bjr\b|entry level|apprentice|retail|store manager|restaurant")
DROP_TITLE = re.compile(SEARCH.get("drop_title") or DROP_TITLE_DEFAULT)

# ---------- location filter ----------
NEAR = SEARCH["near_cities"]  # commute-radius cities; counted only next to a home-state marker
NEAR_RE = re.compile(r"\b(?:%s)\b,?\s*" % "|".join(map(re.escape, NEAR)) + STATE_RE)
HOME_RE = re.compile(r"\b%s\b" % re.escape(NEAR[0])) if NEAR else re.compile(r"(?!x)x")
US_WORDS = re.compile(
    r"united states|\bu\.?s\.?a?\b|\bremote\b|anywhere|nationwide|work from home|\bwfh\b|virtual|"
    r"\bamericas?\b|north america|,\s*(?:a[klrz]|c[aot]|d[ce]|fl|ga|hi|i[adln]|k[sy]|la|m[adeinost]|n[cdehjmvy]|"
    r"o[hkr]|pa|ri|s[cd]|t[nx]|ut|v[at]|w[aivy])\b")
NON_US = re.compile(
    r"canada|toronto|vancouver|montreal|ontario|mexico|brazil|argentina|colombia|chile|peru|costa rica|\buk\b|"
    r"united kingdom|london|england|scotland|ireland|dublin|germany|berlin|munich|france|paris|spain|madrid|"
    r"barcelona|portugal|lisbon|netherlands|amsterdam|belgium|austria|vienna|poland|warsaw|romania|bulgaria|"
    r"india|bangalore|bengaluru|hyderabad|pune|singapore|australia|sydney|melbourne|japan|tokyo|korea|seoul|"
    r"china|hong kong|taiwan|taipei|thailand|bangkok|philippines|manila|israel|tel aviv|emea|apac|latam|"
    r"europe|\beu\b|serbia|ukraine|kyiv|czech|prague|sweden|denmark|norway|finland|switzerland|zurich|italy|"
    r"greece|new zealand|south africa|nigeria|kenya|egypt|saudi|emirates|\buae\b|dubai|qatar|turkey|istanbul|"
    r"vietnam|indonesia|malaysia|pakistan|armenia|georgia \(country\)")
OFFICE = re.compile(r"\b(?:office|hq|headquarters|on-?site|in-?office|hybrid)\b")


def loc_ok(loc, is_remote=None, workplace=""):
    """(ok, why) for a location string. The text decides; a board's remote flag only counts when the
    text itself reads US/remote (Ashby isRemote let "NYC Office" and "Hong Kong" through)."""
    l = (loc or "").lower()
    wp = (workplace or "").lower()
    if NEAR_RE.search(l) or HOME_RE.search(l):
        return True, f"near {HOME}"
    parts = [x.strip() for x in re.split(r"\s*/\s*|;", l) if x.strip()] or [""]
    text_remote = bool(re.search(r"\bremote\b|anywhere|work from home|\bwfh\b|virtual|nationwide|distributed", l))
    if text_remote or bool(is_remote) or wp == "remote":
        # any slash-separated part that is remote and US (or remote with no country) qualifies
        for p in parts:
            if re.search(r"\bhybrid\b|\boffice\b", p):
                continue
            if NON_US.search(p) and not re.search(r"united states|\bus\b|\busa\b|u\.s\.", p):
                continue
            if re.search(r"\bremote\b|anywhere|work from home|virtual|nationwide|distributed", p):
                return True, "remote US"
            if re.fullmatch(r"(?:the )?(?:united states|usa?|u\.s\.a?\.?|us remote|remote us)(?: \(remote\))?", p):
                return True, "remote US"
        if (is_remote or wp == "remote") and not text_remote and not OFFICE.search(l) and \
                any(re.fullmatch(r"(?:united states|usa?|u\.s\.a?\.?)", p) for p in parts):
            return True, "remote US (board flag)"
        return False, "remote, but not US (or office-tied)"
    if re.search(r"^(?:united states|usa|us|u\.s\.?)$|anywhere in (?:the )?united states", l.strip()) and \
            wp not in ("hybrid", "onsite", "on-site"):
        return True, "US-wide (confirm remote)"
    return False, f"not remote, not near {HOME}"


# Aggregators/staffing feeds re-post other companies' jobs: never a direct posting
AGGREGATOR = re.compile(r"jobgether|jooble|talent\.com|lensa|ziprecruiter|dice|hired|bjak|crossover|"
                        r"destinationknot|remotehunter|jobot")
EXCLUDE_CO = re.compile("|".join(map(re.escape, EXCLUDE_COMPANIES)) or r"(?!x)x")


# ---------- helpers ----------
def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def key_co(s):
    s = norm(s)
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology|labs|hq|the)\b", " ", s)
    return re.sub(r"\s+", "", s)


TRIES = [3]  # main pass sets 1 (no inline backoff); slow lane sets 3


_TL = threading.local()  # per worker thread: this request's tries/timeouts, plus answer time and which try worked


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # surface the 3xx as an HTTPError (BambooHR sends a dead board to bamboohr.com)


_NOREDIR = urllib.request.build_opener(_NoRedirect)


def get(url, tries=None, pace=0.4, timeout=None, body=None, no_redirect=False):
    """GET (or POST `body` as JSON) with pacing + exponential backoff. Returns (data, error).
    no_redirect=True treats a redirect as "404" (a dead board)."""
    tries = tries or getattr(_TL, "tries", None) or TRIES[0]
    steps = timeout or getattr(_TL, "timeout", None) or TIMEOUT[0]
    _TL.answer, _TL.try_no = None, 0
    steps = steps if isinstance(steps, (list, tuple)) else [steps]
    err = ""
    for k in range(tries):
        timeout = steps[min(k, len(steps) - 1)]
        time.sleep(pace + random.random() * pace)
        try:
            hdr = {"User-Agent": UA, "Accept": "application/json"}
            data_out = None
            if body is not None:
                data_out = json.dumps(body).encode("utf-8"); hdr["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data_out, headers=hdr, method="POST" if body is not None else "GET")
            ta = time.time()
            with (_NOREDIR.open if no_redirect else urllib.request.urlopen)(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            _TL.answer, _TL.try_no = time.time() - ta, k + 1
            return data, ""
        except urllib.error.HTTPError as e:
            if e.code == 404 or (no_redirect and 300 <= e.code < 400):
                return None, "404"
            err = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504):
                return None, err
        except Exception as e:  # timeouts, resets
            err = type(e).__name__
        if k < tries - 1:
            time.sleep((2 ** k) * 2 + random.random())
    return None, err or "failed"


def parse_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------- per-platform readers -> list of dicts ----------
def read_greenhouse(slug, pace):
    d, err = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", pace=pace)
    if d is None:
        return None, err
    out = []
    for j in d.get("jobs", []):
        out.append(dict(id=f"gh:{slug}:{j.get('id')}", company=j.get("company_name") or "", title=j.get("title", ""),
                        location=(j.get("location") or {}).get("name", ""), remote=None, workplace="",
                        posted=parse_ts(j.get("first_published") or j.get("updated_at")),
                        updated=j.get("updated_at"), url=j.get("absolute_url", ""), pay=""))
    return out, ""


def read_lever(slug, pace):
    d, err = get(f"https://api.lever.co/v0/postings/{slug}?mode=json", pace=pace)
    if d is None:
        return None, err
    out = []
    for j in d if isinstance(d, list) else []:
        c = j.get("categories") or {}
        locs = c.get("allLocations") or [c.get("location", "")]
        pay = ""
        sr = j.get("salaryRange") or {}
        if sr.get("min"):
            pay = f"{sr.get('currency', '')} {sr.get('min'):,}-{sr.get('max', 0):,} {sr.get('interval', '')}".strip()
        out.append(dict(id=f"lv:{slug}:{j.get('id')}", company="", title=j.get("text", ""),
                        location=" / ".join(x for x in locs if x), remote=None, workplace=j.get("workplaceType", ""),
                        posted=parse_ts(j.get("createdAt")), updated=j.get("createdAt"),
                        url=j.get("hostedUrl", ""), pay=pay))
    return out, ""


def read_ashby(slug, pace):
    d, err = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", pace=pace)
    if d is None:
        return None, err
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(dict(id=f"ab:{slug}:{j.get('id')}", company="", title=j.get("title", ""),
                        location=" / ".join(x for x in locs if x), remote=j.get("isRemote"),
                        workplace=j.get("workplaceType", ""), posted=parse_ts(j.get("publishedAt")),
                        updated=j.get("publishedAt"), url=j.get("jobUrl", ""), pay=comp))
    return out, ""


SWEEP_HOURS = [24]  # set by main(); Workday stops paging once its newest-first list is past the window


def wd_posted(s, now):
    """Workday's "Posted Today" / "Posted Yesterday" / "Posted 3 Days Ago" / "Posted 30+ Days Ago" -> datetime."""
    s = (s or "").lower()
    if "today" in s:
        return now
    if "yesterday" in s:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+? days? ago", s)
    return now - timedelta(days=int(m.group(1))) if m else None


def read_workday(slug, pace):
    """Workday CXS: slug = tenant|wdN|site. Newest first, except boards may pin an old posting on top
    (some boards), so paging stops when a page's LAST posting is past the window (max 25 pages = 500 postings).
    A dead tenant answers 422, a dead site 404, a switched-off site 403. Multi-location title matches get one detail read for their places."""
    tenant, wd, site = slug.split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(hours=SWEEP_HOURS[0]) - timedelta(hours=12)  # "Posted 2 Days Ago" is past a 24h window
    out, off = [], 0
    while off < 500:
        d, err = get(api, pace=pace, body={"appliedFacets": {}, "limit": 20, "offset": off, "searchText": ""})
        if d is None:
            if off == 0:
                # 404 = no such site, 422 = no such company, 403 = career site switched off/private ("permission
                # denied", e.g. a university-hiring site). All three are dead boards, not errors.
                # 401 "unable to verify credentials" = the company's public site is gone (e.g. a company that was acquired)
                return None, ("404" if err in ("404", "HTTP 422", "HTTP 403", "HTTP 401") else err)
            break
        jobs = d.get("jobPostings") or []
        last = wd_posted(jobs[-1].get("postedOn"), now) if jobs else None
        past = bool(last and last < oldest)
        for j in jobs:
            posted = wd_posted(j.get("postedOn"), now)
            path = j.get("externalPath", "")
            title, loc = j.get("title", ""), j.get("locationsText", "") or ""
            tl = title.lower()
            if re.search(r"\d+ locations", loc.lower()) and posted and posted >= oldest and \
                    (TITLE_RE.search(tl) or LANE_RE.search(tl)) and not DROP_TITLE.search(tl):
                dd, _ = get(f"{base}/wday/cxs/{tenant}/{site}{path}", pace=pace)
                info = (dd or {}).get("jobPostingInfo", {})
                locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
                loc = " / ".join(x for x in locs if x) or loc
            rid = path.rsplit("_", 1)[-1] if "_" in path else path.rsplit("/", 1)[-1]
            out.append(dict(id=f"wd:{slug}:{rid}", company=pretty(tenant), title=title, location=loc, remote=None,
                            workplace="", posted=posted, updated=j.get("postedOn"), url=f"{base}/{site}{path}", pay=""))
        if past or len(jobs) < 20:
            break
        off += 20
    return out, ""


def read_bamboohr(slug, pace):
    """BambooHR: the list has no posting date, so only title/lane matches get a detail read
    (date posted + pay). A dead board redirects to bamboohr.com instead of answering 404."""
    base = f"https://{slug}.bamboohr.com/careers"
    d, err = get(f"{base}/list", pace=pace, no_redirect=True)
    if d is None:
        return None, err
    out = []
    for j in (d.get("result") or []) if isinstance(d, dict) else []:
        title = j.get("jobOpeningName") or ""
        tl = title.lower()
        loc = j.get("location") or {}
        place = ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)
        remote = str(j.get("locationType")) == "1" or bool(j.get("isRemote"))
        posted, pay = None, ""
        if (TITLE_RE.search(tl) or LANE_RE.search(tl)) and not DROP_TITLE.search(tl):
            dd, _ = get(f"{base}/{j.get('id')}/detail", pace=pace, no_redirect=True)
            jo = (((dd or {}).get("result") or {}).get("jobOpening") or {}) if isinstance(dd, dict) else {}
            posted = parse_ts(jo.get("datePosted"))
            if posted and posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            comp = jo.get("compensation")
            pay = comp if isinstance(comp, str) else ""
        where = (f"Remote / {place}" if place else "Remote") if remote else place
        out.append(dict(id=f"bh:{slug}:{j.get('id')}", company="", title=title, location=where, remote=None,
                        workplace="remote" if remote else "", posted=posted, updated=None,
                        url=f"{base}/{j.get('id')}", pay=pay))
    return out, ""


# ---------- platforms from the CareerScout repo (Ramcharan747/careerscout, MIT; our own stdlib code) ----------
def _where(city, region, country, remote):
    place = ", ".join(x for x in (city, region, country) if x)
    return (f"Remote / {place}" if place else "Remote") if remote else place


def read_smartrecruiters(slug, pace):
    """SmartRecruiters public postings API: newest released first, 100 per page. Stops paging once a page's last
    posting is past the window (max 10 pages). Pay isn't in the list; the vet pass reads it from the posting."""
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(hours=SWEEP_HOURS[0]) - timedelta(hours=12)
    out, off = [], 0
    while off < 1000:
        d, err = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={off}", pace=pace)
        if d is None:
            return (None, err) if off == 0 else (out, "")
        jobs = d.get("content") or []
        if off == 0 and not jobs and not d.get("totalFound"):
            return None, "404"  # SmartRecruiters answers 200 + empty for an unknown company
        last = None
        for j in jobs:
            loc = j.get("location") or {}
            posted = parse_ts(j.get("releasedDate"))
            last = posted or last
            co = (j.get("company") or {}).get("name") or ""
            out.append(dict(id=f"sr:{slug}:{j.get('id')}", company=co, title=j.get("name", ""),
                            location=_where(loc.get("city"), loc.get("region"), (loc.get("country") or "").upper(),
                                            loc.get("remote")),
                            remote=loc.get("remote"), workplace="hybrid" if loc.get("hybrid") else "",
                            posted=posted, updated=j.get("releasedDate"),
                            url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", pay=""))
        if len(jobs) < 100 or (last and last < oldest):
            break
        off += 100
    return out, ""


def read_recruitee(slug, pace):
    """Recruitee careers-site API: every open offer in one call, with created date and (sometimes) pay."""
    d, err = get(f"https://{slug}.recruitee.com/api/offers", pace=pace)
    if d is None:
        return None, err
    out = []
    for j in d.get("offers") or []:
        locs = [l.get("name") or "" for l in j.get("locations") or []] or [j.get("location") or ""]
        sal = j.get("salary") or {}
        pay = ""
        if sal.get("min"):
            pay = f"{sal.get('currency') or ''} {sal.get('min')}-{sal.get('max') or ''} {sal.get('period') or ''}".strip()
        remote = bool(j.get("remote"))
        where = " / ".join(x for x in locs if x)
        out.append(dict(id=f"rc:{slug}:{j.get('id')}", company=j.get("company_name") or "", title=j.get("title", ""),
                        location=(f"Remote / {where}" if where else "Remote") if remote else where, remote=remote,
                        workplace="hybrid" if j.get("hybrid") else ("remote" if remote else ""),
                        posted=parse_ts((j.get("published_at") or j.get("created_at") or "").replace(" UTC", "+00:00")),
                        updated=j.get("updated_at"), url=j.get("careers_url") or j.get("careers_apply_url") or "",
                        pay=pay))
    return out, ""


def read_rippling(slug, pace):
    """Rippling ATS board API. The list has no date, so title/lane matches get one detail read (created date + pay),
    like BambooHR. Slug = the board name in ats.rippling.com/<slug>/jobs."""
    base = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
    d, err = get(base, pace=pace)
    if d is None:
        return None, err
    out = []
    for j in d if isinstance(d, list) else []:
        title = (j.get("name") or "").strip()
        tl = title.lower()
        wl = j.get("workLocation") or {}
        loc = wl.get("label") or ""
        posted, pay, co = None, "", ""
        if (TITLE_RE.search(tl) or LANE_RE.search(tl)) and not DROP_TITLE.search(tl):
            dd, _ = get(f"{base}/{j.get('uuid')}", pace=pace)
            if isinstance(dd, dict):
                posted = parse_ts(dd.get("createdOn"))
                co = dd.get("companyName") or ""
                loc = " / ".join(x for x in (dd.get("workLocations") or []) if isinstance(x, str)) or loc
                pr = dd.get("payRangeDetails") or []
                if pr and isinstance(pr[0], dict):
                    p0 = pr[0]
                    lo, hi = p0.get("rangeStart"), p0.get("rangeEnd")
                    if lo:
                        pay = f"{p0.get('currency') or ''} {lo}-{hi or ''} {p0.get('frequency') or ''}".strip()
        out.append(dict(id=f"rp:{slug}:{j.get('uuid')}", company=co, title=title, location=loc, remote=None,
                        workplace="remote" if "remote" in loc.lower() else "", posted=posted, updated=None,
                        url=j.get("url") or f"https://ats.rippling.com/{slug}/jobs/{j.get('uuid')}", pay=pay))
    return out, ""


def read_breezy(slug, pace):
    """Breezy HR public JSON feed (<slug>.breezy.hr/json; CareerScout's api.breezy.hr URL needs a token).
    A dead board redirects to breezy.hr instead of answering 404."""
    d, err = get(f"https://{slug}.breezy.hr/json", pace=pace, no_redirect=True)
    if d is None:
        return None, err
    out = []
    for j in d if isinstance(d, list) else []:
        locs = j.get("locations") or [j.get("location") or {}]
        remote = any(l.get("is_remote") for l in locs if isinstance(l, dict))
        where = " / ".join(l.get("name", "") for l in locs if isinstance(l, dict) and l.get("name"))
        out.append(dict(id=f"bz:{slug}:{j.get('id')}", company=(j.get("company") or {}).get("name") or "",
                        title=j.get("name", ""), location=(f"Remote / {where}" if where else "Remote") if remote else where,
                        remote=remote, workplace="remote" if remote else "", posted=parse_ts(j.get("published_date")),
                        updated=j.get("published_date"), url=j.get("url", ""), pay=j.get("salary") or ""))
    return out, ""


def read_workable(slug, pace):
    """Workable v3 accounts API (POST, paged by token). UNTESTED: answered 429 (Cloudflare) from Claude's workspace;
    run --only workable --limit 20 from the candidate's PC or the scheduler's box before turning it on."""
    out, token = [], None
    for _ in range(10):
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            body["token"] = token
        d, err = get(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs", pace=pace, body=body)
        if d is None:
            return (None, err) if not out and token is None else (out, "")
        for j in d.get("results") or []:
            loc = j.get("location") or {}
            remote = bool(j.get("remote")) or j.get("workplace") == "remote"
            out.append(dict(id=f"wk:{slug}:{j.get('shortcode')}", company="", title=j.get("title", ""),
                            location=_where(loc.get("city"), loc.get("region"), loc.get("country"), remote),
                            remote=remote, workplace=j.get("workplace") or "", posted=parse_ts(j.get("published")),
                            updated=j.get("published"), url=f"https://apply.workable.com/{slug}/j/{j.get('shortcode')}/",
                            pay=""))
        token = d.get("nextPage")
        if not token:
            break
    return out, ""


PREFIX = {"greenhouse": "gh", "lever": "lv", "ashby": "ab", "workday": "wd", "bamboohr": "bh",
          "smartrecruiters": "sr", "recruitee": "rc", "rippling": "rp", "breezy": "bz", "workable": "wk"}  # must match the readers' id= prefixes
READERS = {"greenhouse": read_greenhouse, "lever": read_lever, "ashby": read_ashby,
           "workday": read_workday, "bamboohr": read_bamboohr, "smartrecruiters": read_smartrecruiters,
           "recruitee": read_recruitee, "rippling": read_rippling, "breezy": read_breezy, "workable": read_workable}


# ---------- dedupe against what's already logged ----------
def known():
    urls, ids, cotitle = set(), set(), set()
    rows = []
    seen = DATA / "seen.txt"
    if seen.exists():
        for line in seen.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "|" not in line:
                continue
            p = [x.strip() for x in line.split("|")]
            rows.append((p[0], p[1], p[2] if len(p) > 2 else ""))
    lj = DATA / "leads.json"
    if lj.exists():
        d = json.loads(lj.read_text(encoding="utf-8"))
        for l in d.get("leads", []) + d.get("dropped_by_agent", []) + d.get("dropped_by_muse", []):
            if isinstance(l, str):  # dropped_by_agent rows are "Company - Role (why)" strings
                co, _, role = l.partition(" - ")
                rows.append((co, re.sub(r"\s*\(.*$", "", role), ""))
            else:
                rows.append((l.get("company", ""), l.get("role", ""), l.get("url", "")))
    for co, role, url in rows:
        if url:
            urls.add(url.split("?")[0].rstrip("/").lower())
            ids.update(posting_ids(url))
        ids.update(posting_ids(role))
        cotitle.add((key_co(co), norm(re.sub(r"\(.*?\)", "", role))))
    return urls, ids, cotitle


def posting_ids(s):
    s = s or ""
    return set(re.findall(r"\b\d{7,}\b", s)) | set(re.findall(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", s.lower()))


def is_known(c, K):
    urls, ids, cotitle = K
    if c["url"].split("?")[0].rstrip("/").lower() in urls:
        return "url"
    if posting_ids(c["url"]) & ids or (c["id"].split(":")[-1] in ids):
        return "posting ID"
    if (key_co(c["company"]), norm(c["title"])) in cotitle:
        return "company + title"
    # same index as lead_gate.py (URL sans tracking params, posting ID (incl. req IDs),
    # normalized company + title, archive included), checked before any posting is read.
    global _LOGGED
    if _LOGGED is None:
        from common import logged_index
        _LOGGED = logged_index()
    hit = _LOGGED.find(c["company"], c["title"], c["url"])
    if hit:
        return f"{hit[0]} of {hit[1]} ({hit[2]})"
    return ""


_LOGGED = None


# ---------- board list ----------
def load_boards(refresh=False):
    """Board state from three places (so the scheduler's connector can sync it): BOARDS (names etc.),
    DEAD_DIR/<platform>.json (dead lists, the one source for them) and SLUG_CACHE (public slug lists).
    An old all-in-one BOARDS file (slugs + dead inside) is read once and split on the next save."""
    b = json.loads(BOARDS.read_text(encoding="utf-8")) if BOARDS.exists() else {}
    old_slugs, old_dead = b.pop("slugs", None), b.pop("dead", None)
    try:
        b["slugs"] = json.loads(SLUG_CACHE.read_text(encoding="utf-8")) if SLUG_CACHE.exists() else {}
    except Exception:
        b["slugs"] = {}
    for p_, v in (old_slugs or {}).items():
        b["slugs"].setdefault(p_, v)
    dead = {}
    for p_ in PLATFORMS:
        f = DEAD_DIR / f"{p_}.json"
        if f.exists():
            dead.update({f"{p_}:{k}": v for k, v in json.loads(f.read_text(encoding="utf-8")).items()})
    for k, v in (old_dead or {}).items():
        dead.setdefault(k, v)
    b["dead"] = dead
    b.setdefault("names", {}); b.setdefault("failed", {})
    if refresh or not all(b["slugs"].get(p) for p in DATASET_PLATFORMS):
        for p in DATASET_PLATFORMS:
            if b["slugs"].get(p) and not refresh:
                continue  # only fetch the lists we don't have yet
            d, err = get(SLUG_SRC.format(p=p), pace=0.1, timeout=60)
            if d:
                b["slugs"][p] = sorted(set(d))
            elif not b["slugs"].get(p):
                raise SystemExit(f"Couldn't download the {p} slug list ({err}) and none saved.")
        b["source"] = "Feashliaa/job-board-aggregator data/*_companies.json (MIT)"
        b["refreshed"] = datetime.now().date().isoformat()
        seed_names(b)
    return b


def save_boards(b):
    """Write board state back to its three places; BOARDS never carries slugs or dead again."""
    DEAD_DIR.mkdir(exist_ok=True)
    per = {p_: {} for p_ in PLATFORMS}
    for k, v in (b.get("dead") or {}).items():
        p_, _, sl = k.partition(":")
        if p_ in per:
            per[p_][sl] = v
    for p_, d in per.items():
        (DEAD_DIR / f"{p_}.json").write_text(json.dumps(dict(sorted(d.items())), indent=0, ensure_ascii=False) + "\n",
                                             encoding="utf-8", newline="\n")
    SLUG_CACHE.write_text(json.dumps(b.get("slugs", {}), ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    rest = {k: v for k, v in b.items() if k not in ("slugs", "dead")}
    BOARDS.write_text(json.dumps(rest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def seed_names(b):
    """Real company names: from logged rows whose name squashes to a slug."""
    cos = set()
    for f in ("seen.txt",):
        p = DATA / f
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if "|" in line and not line.startswith("#"):
                    cos.add(line.split("|")[0].strip())
    bykey = {key_co(c): c for c in cos if c}
    for p in PLATFORMS:
        for s in b["slugs"].get(p, []):
            k = key_co(s)
            if k in bykey and f"{p}:{s}" not in b["names"]:
                b["names"][f"{p}:{s}"] = bykey[k]


BOARD_URL = [("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)")),
             ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
             ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
             ("workday", re.compile(r"([A-Za-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[^/]+/)?(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")),
             ("bamboohr", re.compile(r"([A-Za-z0-9-]+)\.bamboohr\.com/(?:careers|jobs)")),
             ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
             ("recruitee", re.compile(r"([A-Za-z0-9-]+)\.recruitee\.com")),
             ("rippling", re.compile(r"ats\.rippling\.com/([A-Za-z0-9_-]+)")),
             ("breezy", re.compile(r"([A-Za-z0-9-]+)\.breezy\.hr")),
             ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)"))]


# Boards to watch that aren't in watchlist.json links: config.json search.extra_workday_boards ([name, url, note])
WATCH_EXTRA = [(x[0], x[1]) for x in SEARCH.get("extra_workday_boards", [])]


def learn_boards(b):
    """Add boards seen in logged rows/leads/the watchlist that the dataset lacks, each with a source note."""
    b.setdefault("added", {})
    have = {p: set(b["slugs"].get(p, [])) for p in PLATFORMS}
    new = []
    rows = []
    seen = DATA / "seen.txt"
    if seen.exists():
        for line in seen.read_text(encoding="utf-8").splitlines():
            if "|" in line and not line.startswith("#"):
                q = [x.strip() for x in line.split("|")]
                rows.append(("seen.txt", q[0], q[2] if len(q) > 2 else ""))
    wl = DATA / "watchlist.json"  # watchlist employers on a platform read here are swept here, not in source_sweep
    if wl.exists():
        try:
            w = json.loads(wl.read_text(encoding="utf-8"))
            for r in w.get("watching", []) + w.get("reported", []):
                rows.append(("watchlist.json", r.get("company") or "", r.get("careers") or ""))
        except Exception as e:
            print(f"watchlist.json not read ({e}); its boards are not learned this run", flush=True)
    rows += [("watchlist extra", co, url) for co, url in WATCH_EXTRA]
    lj = DATA / "leads.json"
    if lj.exists():
        for l in json.loads(lj.read_text(encoding="utf-8")).get("leads", []):
            if isinstance(l, dict):
                rows.append(("leads.json", l.get("company", ""), l.get("url", "")))
    today = datetime.now().date().isoformat()
    for src, co, url in rows:
        for plat, rx in BOARD_URL:
            m = rx.search(url or "")
            if not m:
                continue
            slug = "|".join(m.groups()) if plat == "workday" else m.group(1)
            if plat == "workday":
                slug = slug.split("|")[0].lower() + "|" + "|".join(slug.split("|")[1:])
                if co and not co.lower().startswith("muse"):
                    b.setdefault("wd_learned", {}).setdefault(slug.split("|")[0], co)  # code -> name from a logged row
            if slug.lower() in ("embed", "jobs", "j", "p", "o") or slug in have[plat] or f"{plat}:{slug}" in b["added"]:
                continue
            b["added"][f"{plat}:{slug}"] = dict(source=f"auto: {src} row ({co})", added=today)
            have[plat].add(slug)
            new.append(f"{plat}:{slug} ({co}, from {src})")
    return new


def add_board(spec, note):
    b = load_boards()
    plat, _, slug = spec.partition(":")
    if plat not in PLATFORMS or not slug:
        raise SystemExit("Use platform:slug, e.g. greenhouse:acme")
    if slug in b["slugs"].get(plat, []):
        raise SystemExit(f"{spec} is already in the dataset list.")
    b.setdefault("added", {})[spec] = dict(source=note or "manual (no note given)", added=datetime.now().date().isoformat())
    save_boards(b)
    print(f"Added {spec}: {b['added'][spec]['source']}")


def pretty(slug):
    return re.sub(r"[-_]+", " ", slug).title()


# ---------- main ----------
def refilter():
    """Re-apply today's title/location/aggregator/known filters to the saved candidates (no network)."""
    d = json.loads(OUT.read_text(encoding="utf-8"))
    K = known()
    keep = []
    for c in d["candidates"]:
        t = c["role"].lower()
        title_ok = (LANE_RE.search(t) and (pay_top(c.get("pay")) >= OFFLIST_PAY_MIN or (not pay_top(c.get("pay")) and not offlist_needs_pay(t)))) if c.get("offlist") else TITLE_RE.search(t)
        if not title_ok or DROP_TITLE.search(t) or AGGREGATOR.search(f"{c['url']} {c['company']}".lower()) \
                or EXCLUDE_CO.search(c["company"].lower()):
            continue
        ok, why = loc_ok(c["location"], c.get("board_remote"), c.get("workplace", ""))
        if not ok or is_known(dict(url=c["url"], id=c["posting_id"], company=c["company"], title=c["role"]), K):
            continue
        c["where"] = why
        keep.append(c)
    print(f"Refiltered {len(d['candidates'])} → {len(keep)} candidates")
    d["candidates"] = keep
    d["refiltered"] = datetime.now().astimezone().isoformat(timespec="minutes")
    OUT.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


VICON = {"PASS": "🟢 ", "FLAG": "🟡 "}


def vet(cands):
    """Vet pass: read every candidate's full posting through the platform's own API (fetch_jd.py: no
    browser, so no rate-limited browser reads and no empty JavaScript-only Workday pages), save it to
    data/sweep_jds/<id>.txt and run the same posting screen as LinkedIn (screen_leads.screen). FAIL moves to
    screened_out with its reasons; a posting that won't load stays FLAG. The scheduler judges from the saved text."""
    from fetch_jd import fetch, write, wrong_page
    from screen_leads import screen
    JD_DIR.mkdir(parents=True, exist_ok=True)
    t_v = time.time()

    def one(c):
        time.sleep(0.2 + random.random() * 0.3)
        try:
            pid = c["posting_id"].split(":")
            # Greenhouse finds often link to the company's own site (?gh_jid=): give fetch the board so it uses the API
            d = fetch(c["url"], board=pid[1] if pid[0] == "gh" and len(pid) > 2 else None)
        except (Exception, SystemExit) as e:  # fetch() raises SystemExit when nothing reads; Ctrl-C still stops the run
            return c, None, str(e).splitlines()[0][:160] if str(e) else type(e).__name__
        return c, d, ""

    kept, out = [], []
    with ThreadPoolExecutor(VET_WORKERS) as ex:
        for n, (c, d, err) in enumerate(ex.map(one, cands), 1):
            if d is None:
                c.update(verdict="FLAG", fails=[], flags=[f"Posting didn't load ({err}): open it in the browser"])
                kept.append(c)
                continue
            f = JD_DIR / (re.sub(r"[^A-Za-z0-9_.-]+", "_", c["posting_id"])[:150] + ".txt")
            write(d, c["url"], f)
            wp = wrong_page(d, c["url"], c["role"])
            if wp:  # don't screen a landing page as if it were the role
                c.update(verdict="FLAG", fails=[], flags=[wp + ": open it in the browser"], jd_file=str(f.relative_to(ROOT)),
                         jd_source=d.get("source", ""), wrong_page=True)
                kept.append(c)
                continue
            if not c.get("pay") and d.get("pay"):
                c["pay"] = d["pay"]
            v, fails, flags = screen(d["text"], location=c["location"], title=c["role"], card_pay=c.get("pay") or "", company=c.get("company", ""))
            if d.get("hidden"):
                flags = flags + ["Hidden text in the posting: read with care (inject_check)"]
            c.update(verdict=v, fails=fails, flags=flags, jd_file=str(f.relative_to(ROOT)), jd_source=d.get("source", ""))
            (out if v == "FAIL" else kept).append(c)
    kept.sort(key=lambda c: c.get("verdict") != "PASS")
    print(f"Vet pass: {len(cands)} postings read in {int(time.time() - t_v)}s · PASS {sum(c['verdict'] == 'PASS' for c in kept)}"
          f" · FLAG {sum(c['verdict'] == 'FLAG' for c in kept)} · screened out {len(out)} "
          f"(didn't load {sum(1 for c in kept if c['flags'] and c['flags'][0].startswith('Posting didn'))}, wrong page {sum(1 for c in kept if c.get('wrong_page'))} → FLAG) → texts in {JD_DIR.relative_to(ROOT)}/", flush=True)
    for c in out:
        print(f"  ✗ {c['company']} | {c['role']} | {'; '.join(c['fails'])[:160]}")
    return kept, out


def main(argv):
    global drop_row, write_drops
    from common import drop_row, write_drops
    o = dict(hours=24, limit=0, only="", workers=0, pace=0.4, refresh=False, batch=100, resume=False, recheck=True, pace_fixed=False, skip="", with_="", vet=True)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--refilter":
            refilter()
            return None
        if a == "--add-board":
            add_board(argv[i + 1], " ".join(argv[i + 2:]))
            return None
        if a == "--boards-added":
            for k, v in sorted(load_boards().get("added", {}).items()):
                print(f"{k:<45} {v.get('added', '')}  {v.get('source', '')}")
            return
        if a == "--refresh-boards":
            o["refresh"] = True
        elif a == "--dead-only":
            o["dead_only"] = True
        elif a == "--no-recheck":
            o["recheck"] = False
        elif a == "--resume":
            o["resume"] = True
        elif a in ("--hours", "--limit", "--workers", "--batch"):
            o[a[2:]] = int(argv[i + 1]); i += 1
        elif a == "--pace":
            o["pace"] = float(argv[i + 1]); o["pace_fixed"] = True; i += 1
        elif a == "--skip":
            o["skip"] = argv[i + 1]; i += 1
        elif a == "--no-vet":
            o["vet"] = False
        elif a == "--since-last":
            from common import hours_since_last
            o["hours"] = hours_since_last(OUT)
            print(f"--since-last: window {o['hours']}h (top-off from the previous run)")
        elif a == "--with":
            o["with_"] = argv[i + 1]; i += 1
        elif a == "--only":
            o["only"] = argv[i + 1]; i += 1
        elif a in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        else:
            raise SystemExit(f"Unknown option {a}\n{__doc__}")
        i += 1
    SWEEP_HOURS[0] = o["hours"]
    b = load_boards(o["refresh"])
    learned = learn_boards(b)
    if learned:
        print(f"Learned {len(learned)} board(s) the dataset lacks: " + "; ".join(learned[:20]), flush=True)
    # Workday names: data/workday_names.json (hand-kept) beats names learned from logged rows
    try:
        wdn = json.loads((DATA / "workday_names.json").read_text(encoding="utf-8")).get("names", {})
    except Exception:
        wdn = {}
    wdn = {**b.get("wd_learned", {}), **wdn}
    for sl in b["slugs"].get("workday", []):
        t_ = sl.split("|")[0]
        k_ = f"workday:{sl}"
        if t_ in wdn:
            b["names"][k_] = wdn[t_]
        elif b["names"].get(k_) == pretty(t_):
            b["names"].pop(k_)  # an old code-as-name from an earlier run
    for k in b.get("added", {}):
        pl, _, sl = k.partition(":")
        if pl in PLATFORMS and sl not in b["slugs"].setdefault(pl, []):
            b["slugs"][pl].append(sl)
    on = set(o["with_"].split(",")) if o["with_"] else set()
    plats = [o["only"]] if o["only"] else [p for p in PLATFORMS if p not in o["skip"].split(",")
                                           and (p not in DEFAULT_OFF or p in on)]
    if not o["only"] and DEFAULT_OFF - on:
        print(f"Off by default: {', '.join(sorted(DEFAULT_OFF - on))} (add with --with)", flush=True)
    dead = b.setdefault("dead", {})     # "platform:slug" -> date last confirmed 404
    b.pop("slow", None)  # no cross-run slow memory; the slow lane is per run
    lanes, n_dead = {}, 0  # platform -> live boards in list order
    if o.get("dead_only"):  # re-check every dead board on every lane, no sweep
        plats = list(PLATFORMS)
    for p in ([] if o.get("dead_only") else plats):
        allp = b["slugs"].get(p, [])
        lst = allp[:o["limit"]] if o["limit"] else allp
        live = [s for s in lst if f"{p}:{s}" not in dead]
        n_dead += len(lst) - len(live)
        if live:
            lanes[p] = live
    jobs = [(p, s) for p in lanes for s in lanes[p]]
    lw = {p: (o["workers"] or LANE_WORKERS[p]) for p in lanes}  # workers per lane (live: handoff raises it, backoff cuts it)
    lw_start = dict(lw)
    dc = {}
    for k in dead:
        dc[k.partition(":")[0]] = dc.get(k.partition(":")[0], 0) + 1
    print("Dead list (data/sweep_dead): " + " · ".join(f"{p_} {dc.get(p_, 0):,}" for p_ in PLATFORMS)
          + f" · total {len(dead):,}", flush=True)
    print(f"Boards: {len(jobs):,} live · {n_dead:,} dead skipped · lanes run in parallel: "
          + " · ".join(f"{p} {len(lanes[p]):,} boards / {lw[p]} workers" for p in lanes), flush=True)
    new_dead, revived = [], []
    raw_state = [0, 0]  # first-try errors, boards tried (for the status file)
    since = datetime.now(timezone.utc) - timedelta(hours=o["hours"])
    K = known()
    snap = json.loads(SNAP.read_text(encoding="utf-8")) if SNAP.exists() else {}
    t0 = time.time()
    found, failed, matched_now = [], {}, {}
    swept_boards = set()
    on_board_now = set()  # every posting ID on a board we read, matching or not
    tally = dict(postings=0, title=0, window=0, location=0, known=0, offlist_lane=0, offlist_location=0, offlist_pay=0)
    offlist_pending = []
    drops_all = []  # drop log

    S = threading.Lock()  # guards everything the lanes share (found, tally, deferred, checkpoint...)

    # Pause per platform: adaptive for Greenhouse/Ashby, fixed for Lever (ADAPTIVE_PAUSE).
    # --pace X fixes it for every platform instead.
    pace = {p: (o["pace"] if o["pace_fixed"] else START_PACE.get(p, 0.4)) for p in PLATFORMS}
    adaptive = {p: (not o["pace_fixed"]) and ADAPTIVE_PAUSE.get(p, True) for p in PLATFORMS}
    pace_lock = threading.Lock()
    answers = {p: [] for p in PLATFORMS}                 # answer times this run (s), for the adaptive timeouts
    main_to = {p: MAIN_TIMEOUT[p][0] for p in PLATFORMS}  # main-pass timeout per platform, retuned each batch
    lane_state = {p: dict(done=0, total=len(lanes[p]), state="main", t0=None, start_done=0, slow=0, slow_ok=0,
                          slow_tries={}, slow_s=0, secs=0, roll=0) for p in lanes}

    def pct(xs, q):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None

    def retune_timeout(p):
        """Main-pass timeout = 3x the platform's 90th-percentile answer time, within its floor/ceiling."""
        _, lo, hi = MAIN_TIMEOUT[p]
        with pace_lock:
            recent = answers[p][-400:]
        p90 = pct(recent, 0.90)
        if p90 is not None and len(recent) >= 30:
            main_to[p] = round(min(hi, max(lo, 3 * p90)), 1)

    def slow_timeouts(p):
        """Slow lane per try = the platform's 95th-percentile answer time x3 / x6 / x10, never below 10 / 20 / 30s
        (a huge board, e.g. a 2.7 MB Greenhouse list, needs room even on a fast platform), max 30s."""
        with pace_lock:
            p95 = pct(answers[p][-400:], 0.95) or 2.0
        floors, top = SLOW_FLOORS.get(p, ((10, 20, 30), 30))
        return [round(min(top, max(floor, m * p95)), 1) for m, floor in zip((3, 6, 10), floors)]

    def lanes_txt():
        parts = []
        for q, x in lane_state.items():
            if x["state"] == "done":
                prog = "✓"
            elif x["state"] == "slow lane":
                prog = "slow lane"
            else:
                prog = f"{x.get('seen', x['done']):,}/{x['total']:,}"
            sh = slow_share.get(q)
            parts.append(f"{q} {prog} ({pace[q]:.2f}s/{main_to[q]:g}s/{lw[q]}w" + (f"/{sh:g}% slow" if sh else "") + ")")
        return " · ".join(parts)

    def status(phase, done_n, total, extra=None):
        el = time.time() - t0
        d = dict(phase=phase, updated=datetime.now().astimezone().isoformat(timespec="seconds"), elapsed_s=int(el),
                 boards_done=done_n, boards_total=total, pct=round(100 * done_n / max(1, total), 1),
                 keepers=len(found), first_try_error_pct=round(100 * raw_state[0] / max(1, raw_state[1]), 2),
                 pauses={k: round(v, 2) for k, v in pace.items()}, main_timeouts=dict(main_to),
                 lanes={q: dict(done=x.get("seen", x["done"]), total=x["total"], state=x["state"], workers=lw[q], slow_lane=x["slow"],
                                slow_share_pct=slow_share.get(q))
                        for q, x in lane_state.items()},
                 pid=__import__("os").getpid())
        d.update(extra or {})
        try:
            tmp = STATUS.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8", newline="\n")
            tmp.replace(STATUS)  # atomic: a reader never sees a half-written file
        except Exception:
            pass  # best effort: this step is optional, carry on without it

    slow_share = {}  # platform -> % slow/timeouts in its last settled batch (status file)
    bstat = {}  # per-platform counts for the batch in progress: [answers, slow-or-timeout, fast]

    def adapt(p, took, err):
        """429/5xx doubles the pause at once (the site says back off). Everything else is counted and
        settled once per batch in settle(p), so one bad batch can't slam the pause to the ceiling."""
        if not adaptive[p]:
            return
        with pace_lock:
            if err.startswith("HTTP 429") or err.startswith("HTTP 5"):
                pace[p] = min(2.0, pace[p] * 2)
                return
            c = bstat.setdefault(p, [0, 0, 0])
            c[0] += 1
            if (err and err != "404") or took > 2:
                c[1] += 1
            elif took < 1:
                c[2] += 1

    def settle(p):
        """Once per batch: >10% slow/timeouts -> x1.5, >3% -> x1.2, none and mostly fast -> x0.8."""
        with pace_lock:
            n, bad_, fast = bstat.pop(p, [0, 0, 0])
            if not n:
                return None
            r = bad_ / n
            slow_share[p] = round(100 * r, 1)
            if not adaptive[p]:
                return r
            if r > 0.10:
                pace[p] = min(2.0, pace[p] * 1.5)
            elif r > 0.03:
                pace[p] = min(2.0, pace[p] * 1.2)
            elif bad_ == 0 and fast / n > 0.8:
                pace[p] = max(0.1, pace[p] * 0.8)
            return r

    def backoff(p, r):  # caller holds S
        """>10% slow in a batch -> 12 fewer workers (min 12); <3% -> 6 back, up to the lane's start."""
        if r is None or p not in WORKER_BACKOFF or lane_state[p]["state"] != "main":
            return
        top = lw_start[p]
        if r > 0.10 and lw[p] > 12:
            lw[p] = max(12, lw[p] - 12)
            print(f"↓ {p}: {100 * r:.0f}% slow in the last batch, workers cut to {lw[p]}", flush=True)
        elif r < 0.03 and lw[p] < top:
            lw[p] = min(top, lw[p] + 6)
            print(f"↑ {p}: clean batch, workers back to {lw[p]}", flush=True)

    def work(ps, mode):
        p, s = ps
        if mode == "slow":
            _TL.tries, _TL.timeout = SLOW_TRIES.get(p, 3), slow_timeouts(p)
        else:
            _TL.tries, _TL.timeout = 1, main_to[p]
        pz = min(SLOW_LANE_PAUSE_CAP, pace[p])
        rows, err = READERS[p](s, pz)
        ans, try_no = getattr(_TL, "answer", None), getattr(_TL, "try_no", 0)
        if ans is None and rows is None and err != "404":
            # a timeout counts as "took at least the timeout". Before, only answers were counted, so a big
            # slow tail was invisible and the timeout drifted DOWN to its floor (a slow BambooHR run: the timeout fell to its floor and hundreds were deferred).
            t_ = _TL.timeout
            ans_c = max(t_) if isinstance(t_, (list, tuple)) else t_
        else:
            ans_c = ans
        if ans_c is not None:
            with pace_lock:
                answers[p].append(ans_c)
        if mode == "main":
            took = ans if ans is not None else (0.5 if err == "404" else 99)
            adapt(p, took, err if rows is None else "")
        return p, s, rows, err, try_no

    def run(batch, workers, mode, label=""):
        bad, tries_ok = [], {}
        last = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, ps, mode) for ps in batch]
            for n, f in enumerate(as_completed(futs), 1):
                p, s, rows, err, try_no = f.result()
                res = filter_board(p, s, rows) if rows is not None else None  # outside the lock
                with S:
                    if rows is None:
                        if err == "404":
                            new_dead.append(f"{p}:{s}")
                        else:
                            bad.append((p, s, err))
                    else:
                        swept_boards.add(f"{PREFIX[p]}:{s}")
                        merge(p, s, res)
                        tries_ok[try_no] = tries_ok.get(try_no, 0) + 1
                if label and time.time() - last >= 30:  # long phases report every 30s
                    last = time.time()
                    status(label, n, len(batch))
                    print(f"  {label}: {n}/{len(batch)} boards done, {len(found)} keepers, {int(time.time() - t0)}s", flush=True)
        return bad, tries_ok

    def filter_board(p, s, rows):
        """Filter one board's postings WITHOUT the shared lock (a profiling run: filtering under the
        lock made the lanes take turns). Only reads shared data; merge() applies the result under the lock."""
        nkey = f"{p}:{s}"
        res = dict(tally={}, ids=[], name=None, matched={}, offlist=[], found=[], drops=[])
        tl_ = res["tally"]

        def bump(k):
            tl_[k] = tl_.get(k, 0) + 1

        def drop(r, rule):  # drop log: only postings inside the window; older ones aren't new misses
            if r.get("posted") and r["posted"] >= since:
                res["drops"].append(drop_row(f"board:{p}", rule, r.get("company"), r.get("title"), r.get("location"),
                                             r.get("url"), r.get("pay", ""), r["posted"].isoformat(),
                                             remote=r.get("remote"), workplace=r.get("workplace")))
        name = b["names"].get(nkey)
        for r in rows:
            bump("postings")
            res["ids"].append(r["id"])
            if r["company"] and not name and not res["name"] and p != "workday":  # Workday "names" are codes
                res["name"] = r["company"]
            r["company"] = name or res["name"] or r["company"] or pretty(s)
            t = r["title"].lower()
            if DROP_TITLE.search(t):
                drop(r, "title dropped (DROP_TITLE)")
                continue
            if REGION_TAG.search(t) and not re.search(r"\b(?:us|usa|united states|north america|americas)\b", t):
                bump("region_tag")
                drop(r, "region-only title")
                continue
            if not TITLE_RE.search(t):
                # Off-list catch: lane word + window + location + not known; pay checked after the sweep
                if not LANE_RE.search(t) or EXCLUDE_CO.search(r["company"].lower()) \
                        or AGGREGATOR.search(f"{s} {r['company']}".lower()) or not r["posted"] or r["posted"] < since:
                    drop(r, "title off list, no lane word" if not LANE_RE.search(t) else "off-list: excluded company/aggregator")
                    continue
                bump("offlist_lane")
                ok, why = loc_ok(r["location"], r["remote"], r["workplace"])
                if not ok:
                    drop(r, f"off-list: location ({why})")
                    continue
                kn = is_known(r, K)
                if kn:
                    drop(r, f"off-list: already logged ({kn})")
                    continue
                bump("offlist_location")
                r["where"], r["platform"], r["offlist"] = why, p, True
                old = snap.get(r["id"])
                r["change"] = "new" if not old else "seen in sweep"
                res["matched"][r["id"]] = dict(title=r["title"], updated=str(r["updated"]), url=r["url"])
                res["offlist"].append(r)
                continue
            bump("title")
            res["matched"][r["id"]] = dict(title=r["title"], updated=str(r["updated"]), url=r["url"])
            if EXCLUDE_CO.search(r["company"].lower()) or AGGREGATOR.search(f"{s} {r['company']}".lower()):
                drop(r, "excluded company/aggregator")
                continue
            if not r["posted"] or r["posted"] < since:
                continue
            bump("window")
            ok, why = loc_ok(r["location"], r["remote"], r["workplace"])
            if not ok:
                drop(r, f"location ({why})")
                continue
            bump("location")
            kn = is_known(r, K)
            if kn:
                bump("known")
                drop(r, f"already logged ({kn})")
                continue
            old = snap.get(r["id"])
            r["change"] = "new" if not old else ("changed" if old.get("title") != r["title"] else "seen in sweep")
            r["where"] = why
            r["platform"] = p
            res["found"].append(r)
        return res

    def merge(p, s, res):  # caller holds S: quick appends only
        nkey = f"{p}:{s}"
        for k, v in res["tally"].items():
            tally[k] = tally.get(k, 0) + v
        on_board_now.update(res["ids"])
        if res["name"] and nkey not in b["names"]:
            b["names"][nkey] = res["name"]
        matched_now.update(res["matched"])
        offlist_pending.extend(res["offlist"])
        found.extend(res["found"])
        drops_all.extend(res.get("drops", []))

    def handle(p, s, rows):  # caller holds S (used where speed doesn't matter: the last-chance pass)
        merge(p, s, filter_board(p, s, rows))

    def ser(r):
        d = dict(r); d["posted"] = r["posted"].isoformat() if r.get("posted") else None; d["updated"] = str(r.get("updated"))
        return d

    def deser(d):
        d = dict(d); d["posted"] = datetime.fromisoformat(d["posted"]) if d.get("posted") else None
        return d

    def cand(r):
        return dict(company=r["company"], role=r["title"], location=r["location"], where=r["where"],
                    pay=r["pay"], posted=r["posted"].isoformat(timespec="minutes"), url=r["url"],
                    platform=r["platform"], posting_id=r["id"], change=r["change"],
                    board_remote=r["remote"], workplace=r["workplace"], offlist=bool(r.get("offlist")))

    raw, deferred = [0, 0], []  # raw = [first-try errors, boards tried]
    ck_elapsed = 0
    sig = dict(only=o["only"], limit=o["limit"], hours=o["hours"], boards=len(jobs))
    if o["resume"]:
        if not CKPT.exists():
            raise SystemExit("No checkpoint to resume (data/sweep_checkpoint.json). Run without --resume.")
        ck = json.loads(CKPT.read_text(encoding="utf-8"))
        if ck.get("sig") != sig or "lanes" not in ck:
            raise SystemExit(f"Checkpoint is for a different run {ck.get('sig')}; this run is {sig}. Run without --resume.")
        for p, x in ck["lanes"].items():
            if p in lane_state:
                lane_state[p]["done"] = x["done"]
                lane_state[p]["state"] = "done" if x["state"] == "done" else "main"
        found.extend(deser(r) for r in ck["found"]); offlist_pending.extend(deser(r) for r in ck["offlist_pending"])
        failed.update(ck["failed"]); matched_now.update(ck["matched_now"]); tally.update(ck["tally"])
        swept_boards.update(ck["swept_boards"]); on_board_now.update(ck["on_board_now"])
        raw[:] = ck.get("raw", [0, 0]); raw_state[:] = raw
        deferred.extend(ck.get("deferred", [])); new_dead.extend(ck.get("new_dead", []))
        ck_elapsed = ck.get("elapsed", 0)
        t0 -= ck_elapsed
        print("Resuming: " + " · ".join(f"{p} {x['done']:,}/{x['total']:,}{' ✓' if x['state'] == 'done' else ''}"
                                       for p, x in lane_state.items()) + f" · {len(found)} keepers so far", flush=True)
    elif CKPT.exists():
        CKPT.unlink()
    session_start = sum(x["done"] for x in lane_state.values())
    session_t0 = time.time()

    ck_last = [0.0]

    def write_checkpoint(force=False):  # caller holds S; at most every 30s unless forced (a lane finishing)
        if all(x["state"] == "done" for x in lane_state.values()):
            return
        if not force and time.time() - ck_last[0] < 30:
            return
        ck_last[0] = time.time()
        CKPT.write_text(json.dumps(dict(sig=sig, elapsed=int(time.time() - t0),
                                        lanes={q: dict(done=x["done"], state=x["state"]) for q, x in lane_state.items()},
                                        found=[ser(r) for r in found], offlist_pending=[ser(r) for r in offlist_pending],
                                        failed=failed, matched_now=matched_now, tally=tally,
                                        swept_boards=sorted(swept_boards), on_board_now=sorted(on_board_now),
                                        raw=raw, deferred=deferred, new_dead=new_dead), ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        ids, part = set(), []
        for r in found:
            if r["id"] not in ids:
                ids.add(r["id"]); part.append(cand(r))
        OUT.write_text(json.dumps(dict(partial=True, boards_done=sum(x["done"] for x in lane_state.values()),
                                       boards_tried=len(jobs), hours=o["hours"], candidates=part),
                                  indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    def progress(p, n_chunk, secs, bn, nbp):  # caller holds S
        tot = sum(x.get("seen", x["done"]) for x in lane_state.values())
        rate = (tot - session_start) / max(1e-6, time.time() - session_t0)
        etas = []
        for x in lane_state.values():
            sn = x.get("seen", x["done"])
            if x["state"] == "main" and sn < x["total"] and x["t0"]:
                lr = (sn - x["start_done"]) / max(1e-6, time.time() - x["t0"])
                etas.append((x["total"] - sn) / lr / 60 if lr else 0)
        left = max(etas) if etas else 0
        status("main pass", tot, len(jobs), dict(rate_per_s=round(rate, 1), eta_min=round(left, 1),
                                                 slow_lane_waiting=len(deferred)))
        return (f"[{int(time.time() - t0)}s · {p}] {tot:,} / {len(jobs):,} ({100 * tot // max(1, len(jobs))}%)"
                f" · {len(found)} keepers · errors {100 * raw[0] / max(1, raw[1]):.1f}% · {rate:.1f}/s · ~{left:.0f} min left"
                f" · lanes (pause/timeout/workers/slow share): {lanes_txt()}  ({p} batch {bn}/{nbp}: {n_chunk} boards in {int(secs)}s)")

    crashed = {}
    claimed = set()   # "platform:slug" already taken by a slow lane (rolling or final), so no board is retried twice
    rollers = {}      # platform -> rolling slow-lane thread

    def slow_batch(p, todo, nw, label=""):
        """Retry boards in slow mode and add the results to the lane's slow-lane counts."""
        bad, tries_ok = run(todo, nw, "slow", label=label)
        st = lane_state[p]
        with S:
            for pp, ss, err in bad:
                failed[f"{pp}:{ss}"] = err
            st["slow"] += len(todo); st["slow_ok"] += len(todo) - len(bad)
            for k, v in tries_ok.items():
                st["slow_tries"][k] = st["slow_tries"].get(k, 0) + v

    def roller(p, nw):
        """Rolling slow lane: spare workers retry p's timed-out boards while its main pass runs."""
        st = lane_state[p]
        while True:
            with S:
                todo = [tuple(x) for x in deferred if x[0] == p and f"{x[0]}:{x[1]}" not in claimed]
                claimed.update(f"{pp}:{ss}" for pp, ss in todo)
                main_done = st["state"] != "main"
            if todo:
                slow_batch(p, todo, nw)
                with S:
                    st["roll"] += len(todo)
            elif main_done:
                return
            else:
                time.sleep(3)

    def roller_safe(p, nw):
        try:
            roller(p, nw)
        except Exception as e:
            import traceback
            crashed[f"{p} rolling slow lane"] = repr(e)
            print(f"Traceback in {p} rolling slow lane:\n{traceback.format_exc()}", flush=True)

    def lane(p):
        st = lane_state[p]
        st["t0"], st["start_done"] = time.time(), st["done"]
        if st["state"] == "done":
            return
        lst, B_ = lanes[p], o["batch"]
        nbp = (len(lst) + B_ - 1) // B_
        # Continuous: every worker stays busy; a slow board no longer holds a whole batch. Progress, pause
        # settling and the timeout retune happen every B_ finished boards. "done" is the contiguous finished prefix
        # (what --resume can trust); "seen" counts every finished board (what the status lines show).
        st["seen"] = st["done"]
        flags = [False] * len(lst)
        cond = threading.Condition()
        inflight = [0]
        chunk = dict(n=0, t=time.time())

        def finish(i, fut):
            try:
                try:
                    _, s_, rows, err, _ = fut.result()
                except Exception as e:  # a reader that blew up on odd data: treat like a failed request
                    s_, rows, err = lst[i], None, type(e).__name__
                report = False
                res = filter_board(p, s_, rows) if rows is not None else None  # outside the lock
                with S:
                    if rows is None:
                        if err == "404":
                            new_dead.append(f"{p}:{s_}")
                        else:
                            deferred.append([p, s_]); raw[0] += 1
                    else:
                        swept_boards.add(f"{PREFIX[p]}:{s_}")
                        merge(p, s_, res)
                    raw[1] += 1; raw_state[:] = raw
                    flags[i] = True
                    while st["done"] < len(lst) and flags[st["done"]]:
                        st["done"] += 1
                    st["seen"] += 1
                    chunk["n"] += 1
                    if chunk["n"] >= B_ or st["seen"] >= len(lst):
                        report, n_chunk, secs = True, chunk["n"], time.time() - chunk["t"]
                        chunk["n"], chunk["t"] = 0, time.time()
                if report:
                    r_ = settle(p)
                    retune_timeout(p)
                    with S:
                        backoff(p, r_)
                        if lane_state[p]["state"] == "main" and lw[p] > lw_start[p]:
                            lw_start[p] = lw[p]  # a handoff raised it: that's the new ceiling for the "back" step
                        print(progress(p, n_chunk, secs, (st["seen"] + B_ - 1) // B_, nbp), flush=True)
                        write_checkpoint()
            except Exception as e:
                import traceback
                crashed[f"{p} lane"] = repr(e)
                print(f"Traceback in {p} lane:\n{traceback.format_exc()}", flush=True)
            finally:
                with cond:
                    inflight[0] -= 1
                    cond.notify_all()

        with ThreadPoolExecutor(max_workers=max(LANE_MAX.get(p, 24), lw[p])) as ex:
            for i in range(st["done"], len(lst)):
                with cond:
                    while inflight[0] >= lw[p]:  # lw[p] can grow mid-lane through a handoff
                        cond.wait(0.5)
                    inflight[0] += 1
                ex.submit(work, (p, lst[i]), "main").add_done_callback(lambda f, i=i: finish(i, f))
        # Main pass over. Hand workers off NOW (not after this lane's slow lane), keeping only what the slow lane needs,
        # so a stuck straggler here can't delay another lane's rolling slow lane (one lane could hold another for over a minute).
        with S:
            mine = [tuple(x) for x in deferred if x[0] == p and f"{x[0]}:{x[1]}" not in claimed]
            claimed.update(f"{pp}:{ss}" for pp, ss in mine)
            st["state"] = "slow lane"   # also tells a rolling slow lane to finish what it has and stop
            sw = min(lw[p], max(2, len(mine))) if mine else 0
            # Handoff: spare workers go to the lanes still in their main pass, Lever first, up to LANE_MAX.
            # When only one lane is left in its main pass and it's at its cap, spare workers start its rolling slow lane.
            free = lw[p] - sw
            for q in sorted((q for q, x in lane_state.items() if x["state"] == "main"), key=lambda q: q != "lever"):
                cap = LANE_MAX.get(q, 24)
                give = max(0, min(free, cap - lw[q]))
                if give:
                    lw[q] += give; free -= give
                    print(f"→ {p} main pass done: {give} workers handed to {q} (now {lw[q]})", flush=True)
                last_one = all(x["state"] != "main" for r_, x in lane_state.items() if r_ != q)
                if free > 0 and lw[q] >= cap and q not in rollers and last_one:  # the candidate: only once q is the last lane running
                    n = min(ROLL_WORKERS, free); free -= n
                    rollers[q] = threading.Thread(target=roller_safe, args=(q, n), name=f"roll-{q}")
                    rollers[q].start()
                    print(f"→ {p} main pass done: {n} workers start {q}'s rolling slow lane (retries its timed-out boards now)", flush=True)
        if mine:
            tl = time.time()
            to = slow_timeouts(p)
            nt = SLOW_TRIES.get(p, 3)
            print(f"  {p} slow lane: {len(mine)} boards ({sw} workers, pause {min(SLOW_LANE_PAUSE_CAP, pace[p]):.2f}s, "
                  f"{nt} tries, timeouts {'/'.join(f'{t:g}' for t in to[:nt])}s)…", flush=True)
            slow_batch(p, mine, sw, label=f"{p} slow lane")
            st["slow_s"] = int(time.time() - tl)
        if p in rollers:
            rollers[p].join()
        with S:
            st["state"], st["secs"] = "done", int(time.time() - st["t0"])
            tries_txt = ", ".join(f"try {k}: {v}" for k, v in sorted(st["slow_tries"].items()))
            roll_txt = f"; {st['roll']} done early in the rolling slow lane" if st["roll"] else ""
            print(f"✓ {p} lane done: {st['total']:,} boards in {st['secs'] / 60:.1f} min · slow lane {st['slow']} "
                  f"({st['slow_ok']} answered{'; ' + tries_txt if tries_txt else ''}{roll_txt}; final {st['slow_s']}s) · "
                  f"main timeout ended at {main_to[p]:g}s", flush=True)
            write_checkpoint(force=True)

    def lane_safe(p):
        try:
            lane(p)
        except Exception as e:  # a crashed lane leaves the checkpoint for --resume; the other lanes finish
            import traceback
            crashed[p] = repr(e)
            print(f"Traceback in {p} lane:\n{traceback.format_exc()}", flush=True)

    threads = [threading.Thread(target=lane_safe, args=(p,), name=f"lane-{p}") for p in lanes]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    if crashed:
        raise SystemExit(f"Lane(s) crashed: {crashed}. Progress is saved; rerun with --resume.")
    # Last chance: a board that failed every slow-lane try gets one more, alone, once the lanes are quiet.
    # Giant boards (~2.7 MB each) answer in <1s alone but can stall while 40+ requests are in flight.
    if failed:
        tl, still, skipped = time.time(), [], []
        retry, per_p = [], {}
        for k in list(failed):
            pp, ss = k.split(":", 1)
            per_p[pp] = per_p.get(pp, 0) + 1
        for k in list(failed):
            pp, ss = k.split(":", 1)
            # a platform with more than LC_CAP left has a site-wide problem, not a few giant boards.
            # Skip it here; those boards stay "failed" and retry next run (the scheduler's box: hundreds of BambooHR boards at 60s each, in a row).
            (skipped if per_p[pp] > LC_CAP else retry).append((pp, ss))
        if skipped:
            print("Last chance skipped for " + ", ".join(f"{pp} ({n} boards)" for pp, n in per_p.items() if n > LC_CAP)
                  + f": over {LC_CAP} per platform; they stay failed and retry next run", flush=True)
            still += [f"{pp}:{ss}" for pp, ss in skipped]
        if retry:
            print(f"Last chance: {len(retry)} board(s) that failed every slow-lane try, {LC_WORKERS} at a time, 60s timeout…", flush=True)

        def lc(ps):
            pp, ss = ps
            if time.time() - tl > LC_BUDGET:
                return pp, ss, None, "budget"
            _TL.tries, _TL.timeout = 1, 60
            rows, err = READERS[pp](ss, 0.5)
            return pp, ss, rows, err

        with ThreadPoolExecutor(max_workers=LC_WORKERS) as ex:
            for fut in as_completed([ex.submit(lc, ps) for ps in retry]):
                try:
                    pp, ss, rows, err = fut.result()
                except Exception:
                    continue
                k = f"{pp}:{ss}"
                with S:
                    if rows is None and err != "404":
                        failed[k] = err if err != "budget" else failed.get(k, "budget"); still.append(k)
                        continue
                    failed.pop(k, None)
                    if rows is None:
                        new_dead.append(k)
                    else:
                        swept_boards.add(f"{PREFIX[pp]}:{ss}")
                        handle(pp, ss, rows)
        shown = ", ".join(still[:20]) + (f" … +{len(still) - 20} more" if len(still) > 20 else "")
        print(f"Last chance: {len(retry) - (len(still) - len(skipped))} recovered · "
              f"still failing: {shown or 'none'} ({int(time.time() - tl)}s)", flush=True)
    today = datetime.now().date().isoformat()
    for k in new_dead:
        dead[k] = today
    # After-pass: re-check the stalest ~1/30th of dead boards (each about monthly); a board that answers comes back
    if o["recheck"]:
        pool = sorted((v, k) for k, v in dead.items() if k.partition(":")[0] in plats and k not in new_dead)
        pick = [k for _, k in (pool if o.get("dead_only") else pool[:max(1, len(pool) // 30)])] if pool else []
        if pick:
            tr = time.time()
            n_new = len(new_dead)
            back, _ = run([tuple(k.split(":", 1)) for k in pick], (48 if o.get("dead_only") else max(2, sum(lw.values()) // 3)), "main", "dead re-check")
            for k in pick:
                if k in new_dead[n_new:]:
                    dead[k] = today  # still 404
                elif any(f"{p}:{s}" == k for p, s, _ in back):
                    pass             # no answer: leave it dead, recheck again next run
                else:
                    dead.pop(k, None); revived.append(k)
            del new_dead[n_new:]
            print(f"Dead re-check: {len(pick)} boards, {len(revived)} back to life"
                  f"{' (' + ', '.join(revived[:10]) + ')' if revived else ''}, {int(time.time() - tr)}s", flush=True)
    # Workday finalists: pay is only in the job description, so read it for the candidates and the off-list
    # hopefuls (not every posting), 12 at a time.
    wd_final = [r for r in found + offlist_pending if r.get("platform") == "workday" and not r.get("pay")]
    if wd_final:
        tp = time.time()

        def wd_pay(r):
            _TL.tries, _TL.timeout = 2, 15
            try:
                slug = r["id"][3:].rsplit(":", 1)[0]
                tenant, wd, site = slug.split("|")
                path = r["url"].split(f"/{site}", 1)[1]
                d, _ = get(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}", pace=0.2)
                return r, pay_from_text(((d or {}).get("jobPostingInfo") or {}).get("jobDescription", ""))
            except Exception:
                return r, ""
        with ThreadPoolExecutor(12) as ex:
            for r, pay in ex.map(wd_pay, wd_final):
                r["pay"] = pay
        print(f"Workday pay: read {len(wd_final)} finalist postings, pay stated on "
              f"{sum(1 for r in wd_final if r['pay'])} ({int(time.time() - tp)}s)", flush=True)
    # Off-list pay gate (stated top >= the screen minimum). Greenhouse finalists get one posting read each.
    for r in offlist_pending:
        if r["platform"] == "greenhouse" and not r["pay"]:
            r["pay"] = greenhouse_pay(r, pace["greenhouse"])
        top = pay_top(r["pay"])
        # No pay listed = hold and keep, like on-list titles. Exception: a title whose
        # only lane word is "project manager" needs a stated base >= the pay floor (construction/field PMs).
        if top >= OFFLIST_PAY_MIN or (not top and not offlist_needs_pay(r["title"])):
            tally["offlist_pay"] += 1
            found.append(r)
        else:
            drops_all.append(drop_row(f"board:{r.get('platform')}", f"off-list: stated pay top under ${OFFLIST_PAY_MIN // 1000}K or no pay",
                                      r.get("company"), r.get("title"), r.get("location"), r.get("url"), r.get("pay", ""),
                                      r["posted"].isoformat() if r.get("posted") else ""))
    # vanished = in the old snapshot for a board we read this time, and no longer on that board at all
    # (a posting that merely stops matching a changed filter is NOT vanished)
    vanished = [dict(id=k, **v) for k, v in snap.items()
                if k not in on_board_now and k.rsplit(":", 1)[0] in swept_boards]
    # Vanished = closed. List written leads whose posting vanished, so the scheduler drops them
    # (log.py promote <lead> --status skipped --live closed --note "Posting vanished from board (sweep)").
    vanished_leads = []
    try:
        gone_urls = {(v.get("url") or "").rstrip("/") for v in vanished}
        gone_ids = {k.rsplit(":", 1)[-1] for k in (v["id"] for v in vanished)}
        for l in json.loads((DATA / "leads.json").read_text(encoding="utf-8")).get("leads", []):
            if l.get("status") != "not_triaged":
                continue
            u = (l.get("url") or "").rstrip("/")
            if u and (u in gone_urls or any(i and re.search(r"\b" + re.escape(i) + r"\b", u) for i in gone_ids)):
                vanished_leads.append(dict(company=l.get("company"), role=l.get("role"), url=l.get("url")))
    except Exception as e:  # never block the sweep on this
        print(f"(vanished-lead check skipped: {e})")
    snap_new = {k: v for k, v in snap.items() if k.rsplit(":", 1)[0] not in swept_boards}
    snap_new.update(matched_now)
    SNAP.write_text(json.dumps(snap_new, indent=0, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    b["failed"] = failed
    save_boards(b)
    ids_ = set()
    found = [r for r in found if not (r["id"] in ids_ or ids_.add(r["id"]))]
    found.sort(key=lambda r: r["posted"], reverse=True)
    cands = [cand(r) for r in found]
    screened_out = []
    if o["vet"] and cands:
        cands, screened_out = vet(cands)
    n_off = sum(1 for c in cands if c["offlist"])
    run_at = datetime.now().astimezone().isoformat(timespec="minutes")
    OUT.write_text(json.dumps(dict(run_at=run_at, hours=o["hours"], boards_tried=len(jobs),
                                   offlist_count=n_off, boards_learned=learned, vetted=o["vet"],
                                   screened_out=screened_out,
                                   boards_failed=len(failed), tally=tally, candidates=cands,
                                   vanished=vanished[:500], vanished_leads=vanished_leads), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for c in screened_out:
        drops_all.append(drop_row("board:vet", "screen FAIL: " + "; ".join(c.get("fails", []))[:200], c.get("company"),
                                  c.get("role"), c.get("location"), c.get("url"), c.get("pay", ""), c.get("posted", ""),
                                  jd_file=c.get("jd_file")))
    write_drops("board", drops_all)
    print(f"\nSwept {len(jobs)} boards in {int(time.time() - t0)}s · failed {len(failed)} "
          f"({100 * len(failed) // max(1, len(jobs))}%) · postings {tally['postings']} → title {tally['title']} "
          f"→ last {o['hours']}h {tally['window']} → location {tally['location']} → already logged {tally['known']} "
          f"→ **{len(cands) - n_off} candidates** · vanished since last sweep {len(vanished)}")
    print(f"Off-list catch (counted separately): lane-word titles in window {tally['offlist_lane']} → location/new "
          f"{tally['offlist_location']} → stated pay top ≥ ${OFFLIST_PAY_MIN // 1000}K **{n_off}**")
    for c in cands:
        print(f"  {VICON.get(c.get('verdict'), '')}{'OFF ' if c['offlist'] else ''}{c['change']:<5} {c['company']} | {c['role']} | {c['location']} | {c['pay'] or 'pay ?'} | {c['url']}")
    for v in vanished_leads:
        print(f"  ⛔ VANISHED LEAD (drop): {v['company']} | {v['role']} | {v['url']}")
    if CKPT.exists():
        CKPT.unlink()
    status("done", len(jobs), len(jobs), dict(candidates=len(cands) - n_off, offlist=n_off, failed=len(failed),
                                              slow_lane=len(deferred), newly_dead=len(new_dead), revived=len(revived)))
    print(f"First-try board errors: {100 * raw[0] / max(1, raw[1]):.1f}% (before the slow lane)")
    print(f"Board memory: {len(new_dead)} newly dead · {len(revived)} revived · dead list {len(dead)} · slow lane this run {len(deferred)}")
    print(f"→ {OUT.relative_to(ROOT)}  (the scheduler vets these; never a lead until screened)")
    return None


if __name__ == "__main__":
    main(sys.argv[1:])
