"""Shared helpers for the job-search scripts. Standard library only, so it runs on any PC.

Not run directly. Other scripts do `from common import ...`.
"""
import json, os, re, sys
from datetime import date

if sys.version_info < (3, 11):  # older ipaddress.is_global treats some special-purpose ranges as public
    raise SystemExit("Python 3.11+ is required.")
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "data" / "applications.json"
ARCHIVE = ROOT / "data" / "archive.json"   # closed rows (skipped/passed/dropped/closed), moved out by `log.py archive`
SEEN = ROOT / "data" / "seen.txt"          # one line per posting ever logged; the scheduler dedupes against this, not the JSON
LEADS = ROOT / "data" / "leads.json"
DONE = {"skipped", "passed", "dropped", "closed"}
# Overridable so tests (and the public template) can point the scripts at a sample catalog.
B_FILE = Path(os.environ.get("JOBSEARCH_B_FILE") or ROOT / "B_Bullet_Catalog.md")
C_FILE = Path(os.environ.get("JOBSEARCH_C_FILE") or ROOT / "C_Gaps.md")
RECORDS = ROOT / "records"
CONFIG_FILE = ROOT / "config.json"           # your search rules (git-ignored); START_HERE.md fills it in
CONFIG_EXAMPLE = ROOT / "config.example.json"


def load_config():
    p = CONFIG_FILE if CONFIG_FILE.exists() else CONFIG_EXAMPLE
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(f"{p.name} isn't valid JSON ({e}). Fix it, or compare with config.example.json.")
    missing = [k for k in ("home_label", "home_states") if k not in (cfg.get("search") or {})]
    if missing:
        raise SystemExit(f"{p.name}: search.{', search.'.join(missing)} missing (see config.example.json).")
    return cfg


CFG = load_config()
SEARCH = CFG["search"]
HOME = SEARCH["home_label"]
LOCAL_TZ = SEARCH.get("timezone") or "UTC"  # IANA name, e.g. America/Denver (config.json search.timezone)
HOME_CODES = {k.upper() for k in SEARCH["home_states"]}
HOME_NAMES = set(SEARCH["home_states"].values())
EXCLUDE_COMPANIES = [c.lower() for c in SEARCH.get("exclude_companies", [])]
STATE_RE = r"\b(?:%s)\b" % "|".join(list(SEARCH["home_states"]) + list(SEARCH["home_states"].values()))

# Piping output to `head` must not crash with BrokenPipeError (ats_check): exit quietly instead.
try:
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):  # Windows has no SIGPIPE
    pass

# Windows terminals default to a code page that can't print arrows or emoji.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass  # best effort: this step is optional, carry on without it

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


PAY_RULES = ROOT / "data" / "pay_rules.json"
_PAY_EXAMPLE = {"screen_min": 100_000, "vet_floor": 120_000, "ask_no_range": [120_000, 150_000]}  # sample values


def pay_rules():
    """The user's pay thresholds from data/pay_rules.json (the only copy). Sample values if the file is missing."""
    rules = dict(_PAY_EXAMPLE)
    if PAY_RULES.exists():
        rules.update({k: v for k, v in load_json(PAY_RULES).items() if not k.startswith("_")})
    return rules


def k_fmt(v):
    """120000 -> '$120K'."""
    return f"${v / 1000:,.0f}K"


def read_jd(path):
    """Read a jd.txt (header + text). Plain pasted text with no header works too."""
    with open(path, encoding="utf-8") as f:
        raw = f.read().lstrip("\ufeff")
    head, text = {}, raw
    # Keys in any case ("Title:"), and empty values ("PAY:" with nothing after it, which
    # inject_check --strip or an editor can leave) no longer drop the whole header.
    m = re.match(r"((?:[A-Za-z]+:[^\n]*\n)+)[ \t]*\n", raw)  # linear: no backtracking blowup (CodeQL)
    if m and re.match(r"(?i)(title|company|url|location|pay|remote|source)\s*:", raw):
        for line in m.group(1).splitlines():
            k, v = line.split(":", 1)
            head[k.strip().lower()] = v.strip()
        text = raw[m.end():]
    else:  # pasted text whose first line is "Company | Title | ...": take the title from it
        first = raw.strip().splitlines()[0] if raw.strip() else ""
        cells = [c.strip() for c in first.split("|")]
        if len(cells) >= 2 and len(first) < 200:
            head["company"], head["title"] = cells[0], cells[1]
    return head, text


def write_atomic(path, text):
    """Write to a uniquely named temp file beside the target, then swap it in: a crash or a second writer can't
    leave a half-written file, and readers see the old file or the new one. On Windows the swap retries briefly
    when antivirus, OneDrive or an editor holds the target open."""
    import tempfile
    import time as _time
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for n in range(6):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if n == 5:
                    raise
                _time.sleep(0.25 * (n + 1))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def json_style(text):
    """(indent, ensure_ascii, trailing newline) of an existing JSON file's text, so a rewrite can match it."""
    indent = 2
    for line in text.splitlines()[1:]:
        if line.strip():
            indent = len(line) - len(line.lstrip(" ")) or 2
            break
    ascii_only = "\\u" in text and all(ord(c) < 128 for c in text)
    return indent, ascii_only, text.endswith("\n")


def save_json(path, data):
    """Write atomically in the style the file already has (indent, \\u escapes, trailing newline), so a file another
    tool writes in a different layout is not rewritten line by line on every save. A new file gets the house style
    (2-space indent, real unicode, trailing newline)."""
    indent, ascii_only, newline = 2, False, True
    try:
        indent, ascii_only, newline = json_style(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass  # no file yet or unparseable: keep the house-style defaults above
    write_atomic(path, json.dumps(data, indent=indent, ensure_ascii=ascii_only) + ("\n" if newline else ""))


# ---- closed-row archive: size-bounded shards ------------------------------------------------------------------
# The archive holds every closed row (skipped/passed/dropped/closed/rejected) moved out of applications.json by
# `log.py archive`. One file would outgrow what a read tool can open, so rows are split, in order, into shards of
# roughly SHARD_TARGET bytes: data/archive/0001.json, 0002.json, ... Every caller goes through Archive (or
# load_archive()) and never needs to know the layout. SIZE_CEILING is the design limit for any data file an agent
# reads; size_report() (used by rules_audit.py and the sync QA step) warns when something crosses it.
SHARD_TARGET = 300_000     # a full shard is cut here
SHARD_MAX = 400_000        # an existing shard may grow to here before its tail spills into the next one
SIZE_CEILING = 500_000     # design ceiling per data file (the read tool failed at 1 MB+)
SHARD_NOTE = ("Closed rows moved out of applications.json by `log.py archive`, same schema. One shard of the archive: "
              "read and change it only through scripts/common.py Archive and scripts/log.py.")
STUB_NOTE = ("Closed rows now live in size-bounded shards under data/archive/ (one loader: common.load_archive()). "
             "This file is only a pointer and holds no rows.")
# bulk machine output, read by scripts only (never opened whole by an agent): not held to the read ceiling
SIZE_EXEMPT = {"discovered_boards.json", "discovered_paylocity.txt", "sweep_slugs.json"}


def shard_dir():
    return ARCHIVE.with_suffix("")


def _row_size(r):
    return len(json.dumps(r, indent=2, ensure_ascii=False).replace("\n", "\n    ").encode("utf-8")) + 6


def _rows_size(rows):
    return sum(_row_size(r) for r in rows)


def _split(rows, limit):
    """(head, rest): head is the longest prefix within `limit` bytes (at least one row)."""
    total = 0
    for n, r in enumerate(rows):
        total += _row_size(r)
        if total > limit and n:
            return rows[:n], rows[n:]
    return rows, []


def _partition(lists):
    """Re-cut ordered row lists into shards. A shard that still fits SHARD_MAX keeps its rows, so an in-place edit or an
    appended row rewrites one file; a shard past SHARD_MAX spills its tail forward."""
    out, carry = [], []
    for rows in lists:
        rows = carry + rows
        carry = []
        if _rows_size(rows) > SHARD_MAX:
            rows, carry = _split(rows, SHARD_TARGET)
        if rows:
            out.append(rows)
    while carry:
        head, carry = _split(carry, SHARD_TARGET)
        out.append(head)
    return out


def size_alarm():
    """Loud lines for every data file over SIZE_CEILING (empty list = fine). Shown by sync.py's QA step and rules_audit.py."""
    return [f"!! SIZE ALARM: {p} is {n // 1000} KB, over the {SIZE_CEILING // 1000} KB read ceiling. Agents and the read tool "
            "cannot open it whole: shard or trim it now (log.py archive moves closed rows out of applications.json)."
            for p, n, kind in size_report() if kind == "over"]


def row_fingerprint(r):
    import hashlib
    return hashlib.sha1(json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def row_key(r):
    return (r.get("company") or "", r.get("role") or "", r.get("url") or "")


def archive_check(before, after):
    """Problems (list of strings) if the `after` rows are not exactly the `before` rows: counts, whole-row
    fingerprints (catches a lost, duplicated or altered row) and company+role+url keys."""
    from collections import Counter
    problems = []
    if len(before) != len(after):
        problems.append(f"row count {len(before)} -> {len(after)}")
    fb, fa = Counter(map(row_fingerprint, before)), Counter(map(row_fingerprint, after))
    if fb != fa:
        problems.append(f"{sum((fb - fa).values())} row(s) lost or changed, {sum((fa - fb).values())} added or duplicated")
    kb, ka = Counter(map(row_key, before)), Counter(map(row_key, after))
    if kb != ka:
        problems.append(f"company+role+url keys differ ({sum((kb - ka).values())} missing, {sum((ka - kb).values())} extra)")
    return problems


def _read_shards():
    d = shard_dir()
    files = sorted(d.glob("[0-9]*.json")) if d.is_dir() else []
    return [(f.name, load_json(f).get("applications", [])) for f in files]


class Archive:
    """Every archived row across all shards (plus any rows still in the legacy single file, so a half-migrated
    checkout still reads whole). rows() returns the live dicts: change them in place, then save()."""

    def __init__(self):
        self.legacy = []
        if ARCHIVE.exists():
            self.legacy = list(load_json(ARCHIVE).get("applications", []))
        self.shards = [rows for _, rows in _read_shards()]
        self._loaded = len(self.legacy) + sum(map(len, self.shards))

    def rows(self):
        out = list(self.legacy)
        for s in self.shards:
            out += s
        return out

    def append(self, rows):
        """New rows go on the end, so only the last shard (and seen.txt's tail) changes."""
        if not self.shards:
            self.shards.append([])
        self.shards[-1].extend(rows)

    def plan(self):
        return _partition([self.legacy] + self.shards)

    def save(self, allow_shrink=False):
        """Write only the shards whose rows changed, then reload them and prove nothing was lost, duplicated or
        altered. Rows still in the legacy file move into shards first; the legacy file is cut to a stub last."""
        before = self.rows()
        if len(before) < self._loaded and not allow_shrink:
            raise SystemExit(f"Archive would shrink from {self._loaded} to {len(before)} rows; nothing written.")
        parts = self.plan()
        d = shard_dir()
        d.mkdir(parents=True, exist_ok=True)
        old = {f.name: f.read_text(encoding="utf-8") for f in d.glob("[0-9]*.json")}
        names = []
        for n, rows in enumerate(parts, 1):
            name = f"{n:04d}.json"
            names.append(name)
            prior = None
            if name in old:
                try:
                    prior = json.loads(old[name]).get("applications")
                except ValueError:
                    prior = None
            if prior == rows:
                continue
            body = {"note": SHARD_NOTE, "updated": today(), "applications": rows}
            write_atomic(d / name, json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        for name in set(old) - set(names):
            (d / name).unlink()
        flat = [r for p in parts for r in p]
        problems = archive_check(before, flat) + archive_check(flat, [r for _, rows in _read_shards() for r in rows])
        big = [f.name for f in d.glob("[0-9]*.json") if f.stat().st_size > SIZE_CEILING]
        if big:
            problems.append("shard over the size ceiling: " + ", ".join(big))
        if problems:
            raise SystemExit("Archive write failed its row check (" + "; ".join(problems) + "). The legacy file was not "
                             "touched; restore data/archive/ from git before retrying.")
        if self.legacy or (ARCHIVE.exists() and load_json(ARCHIVE).get("applications")):
            write_atomic(ARCHIVE, json.dumps({"note": STUB_NOTE, "shards": "data/archive/", "applications": []},
                                             indent=2, ensure_ascii=False) + "\n")
        self.legacy, self.shards, self._loaded = [], parts, len(flat)


def load_archive():
    """Every archived row, across all shards, in order (read-only use)."""
    return Archive().rows()


def all_apps():
    """Every logged posting: live applications.json rows first, then every archived row."""
    return load_json(APPS)["applications"] + load_archive()


def size_report(ceiling=None, warn_at=0.8):
    """[(path relative to the repo, bytes, 'over' | 'near')] for the data files an agent or the pipeline reads whole:
    data/*.json, *.jsonl, *.txt and every archive shard. Bulk machine dumps (SIZE_EXEMPT) are skipped."""
    ceiling = ceiling or SIZE_CEILING
    base = ARCHIVE.parent
    files = [p for pat in ("*.json", "*.jsonl", "*.txt") for p in base.glob(pat)]
    files += list(shard_dir().glob("*.json")) if shard_dir().is_dir() else []
    out = []
    for p in sorted(set(files)):
        n = p.stat().st_size
        if p.name in SIZE_EXEMPT:
            continue
        rel = p.relative_to(base.parent).as_posix()
        if n > ceiling:
            out.append((rel, n, "over"))
        elif n > ceiling * warn_at:
            out.append((rel, n, "near"))
    return out


def write_seen():
    """Rebuild data/seen.txt: 'company | role | url | status' for every application + archive row."""
    lines = ["# Every posting already logged (applications.json + archive.json). the scheduler: skip any lead that matches.",
             "# Generated by scripts/log.py; don't hand-edit."]
    for r in all_apps():
        lines.append(" | ".join([r.get("company") or "", (r.get("role") or "")[:90],
                                 r.get("url") or "", r.get("status") or ""]))
    write_atomic(SEEN, "\n".join(lines) + "\n")


def today():
    return date.today().isoformat()


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$+&/% ]", " ", (s or "").lower())).strip()


def stem_hit(kw, text):
    """Same matching as shop.py: each word by its first 6 letters; words of 3 letters or fewer exact."""
    words = [w for w in norm(kw).split() if w]
    if not words:
        return False
    pats = [r"\b" + re.escape(w) + r"\b" if len(w) <= 3 else r"\b" + re.escape(w[:6]) for w in words]
    return all(re.search(p, text) for p in pats)


def parse_month(s):
    """'Oct 2024', 'October 2024', 'Jul 2016' -> (year, month); 'Present' -> this month. None if not a date."""
    if re.match(r"\s*present\b", s or "", re.I):
        from datetime import date
        return date.today().year, date.today().month
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", s or "")
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return int(m.group(2)), MONTHS[m.group(1).lower()]


def b_roles(path=B_FILE):
    """Roles from B's header block, most recent first: [{title, company, start, end, raw}]."""
    roles = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"- (.+?) \| (.+?) \| ([A-Za-z]+ \d{4}) - ([A-Za-z]+ \d{4}|Present)", line)
        if m:
            title = re.sub(r"\s*\(.*?\)\s*$", "", m.group(1)).strip()
            roles.append({"title": title, "company": m.group(2).strip(),
                          "start": parse_month(m.group(3)), "end": parse_month(m.group(4)),
                          "raw": line[2:].strip()})
        if line.startswith("## Aisle"):
            break
    return roles


FIELD = re.compile(r"(role|tags|full|short|facts|interview|context)(\d*|-\d+| \([^)]*\))\s*:\s?(.*)", re.I)


def b_items(path=B_FILE):
    """Every catalog item: {id, name, role, tags, full, short, facts, interview, context, extra}.

    Numbered or labeled variants ("facts2:", "full-2:", "full (section):") were dropped;
    they now land in `extra` as (key, text) pairs, and facts variants are also added to `facts`
    so every proof line is visible to guard/truthpack."""
    items, cur = [], None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"### (\S+) · (.+)", line)
        if m:
            cur = {"id": m.group(1), "name": m.group(2).strip(), "role": "", "tags": "", "full": "",
                   "short": "", "facts": "", "interview": "", "context": "", "extra": []}
            items.append(cur)
            continue
        if line.startswith("## "):
            cur = None
            continue
        if cur is None:
            continue
        f = FIELD.match(line)
        if not f:
            continue
        base, suffix, val = f.group(1).lower(), f.group(2), f.group(3).strip()
        if not suffix:
            cur[base] += " " + val
        else:
            cur["extra"].append((base + suffix, val))
            if base == "facts":
                cur["facts"] += " " + val
    return items


def c_never_write(path=C_FILE):
    """Phrases C_Gaps.md says never to write: the quoted terms in the 'Never write' column."""
    terms = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and line.startswith("|") and not set(cells[1]) <= set("-: "):
            terms += re.findall(r'"([^"]{2,60})"', cells[1])
    terms = [t.strip().rstrip(",.;:").strip() for t in terms]
    return sorted(set(t for t in terms if t and t.lower() not in ("never write",)))


def job_ids(text):
    """Posting IDs worth matching on: runs of 6+ digits (LinkedIn, Greenhouse, Workday and other job IDs).
    Tracking query params are stripped first (Indeed's alid is shared by every job in one alert email, so left
    in place it makes unrelated postings look like the same job)."""
    text = re.sub(r"[?&]([^&=#]+)=[^&#]*", lambda m: "" if TRACKING.match(m.group(1)) else m.group(0), text or "")
    return set(re.findall(r"\d{6,}", text))


def months_between(a, b):
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


# Role codes used on B's `role:` lines: job title (lowercase, as in B's roles block) -> code (config.json)
ROLE_CODES = CFG.get("resume", {}).get("role_codes", {})


def role_codes(item):
    """The codes on an item's role line, e.g. 'STL, PM (default)' -> {'STL', 'PM'}."""
    txt = re.sub(r"\(.*?\)", "", item.get("role", ""))
    return {c.strip() for c in txt.split(",") if c.strip()}


# Approved alternate forms of a B roles-block title: alternate (lowercase) -> B title (lowercase) (config.json)
TITLE_ALIASES = CFG.get("resume", {}).get("title_aliases", {})


TRACKING = re.compile(r"^(utm_\w*|source|src|iis|iisn|indeed-apply-token|posteddate|ref|refid|trk|lang|gh_src|domain|alid)$", re.I)


def url_key(url):
    """URL without tracking params. The query is kept otherwise, because Taleo/Oracle/Dayforce-style
    boards put the posting ID there (?job=1111111 vs ?job=2222222 were wrongly merged)."""
    from urllib.parse import urlsplit, parse_qsl, urlencode
    if not url:
        return ""
    p = urlsplit(url.strip())
    q = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if not TRACKING.match(k)))
    return f"{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?{q}" if q else "")


def existing(rows, url):
    """Row already logged for this posting (same URL or posting/req ID in url/role). Parallel chats
    made duplicate rows; add/promote now update the existing row."""
    if not url:
        return None
    ids = job_ids(url)
    u = url_key(url)
    for i, r in enumerate(rows):
        if url_key(r.get("url") or "") == u or (ids and ids & (
                job_ids(r.get("url") or "") | job_ids(r.get("role") or ""))):
            return i
    return None


# ---------- lead dedupe key (dupes can re-enter leads.json through a restore or a stale push) ----------
REQ_ID = re.compile(r"(?<![A-Za-z0-9])(R|JR|RQ|REQ)-?(\d{4,})(?![0-9])", re.I)
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CO_NOISE = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|pbc|plc|technologies|technology|labs|group|holdings|hq|the|us|usa)\b")
TITLE_NOISE = re.compile(r"\s[-–|·]\s.*\b(remote|hybrid|on-?site|us|usa|united states)\b.*$", re.I)


def posting_keys(url="", role=""):
    """Posting IDs for a lead or row: 6+ digit runs and UUIDs in the URL (tracking params stripped), req IDs
    (R0012345, JR123456, RQ234567) in URL or role, 7+ digit runs in the role. Two hosts for one board
    that share the ID digits match each other."""
    u = url_key(url or "")
    ids = set(re.findall(r"\d{6,}", u)) | set(UUID_RE.findall(u.lower()))
    ids |= set(re.findall(r"(?<![\d$,.])\d{7,}(?![\d,])", role or ""))
    for s in (u, role or ""):
        ids |= {f"{a.upper()}{b}" for a, b in REQ_ID.findall(s)}
    return ids


def company_key(co):
    return re.sub(r"\s+", "", CO_NOISE.sub(" ", norm(co)))


def title_key(role):
    """Lowercase title, punctuation out, seniority kept (Sr. -> senior). Drops '(where · pay)' tails (also an
    unclosed one: seen.txt cuts roles at 90 chars), IDs, and ' - Remote US' style location tails."""
    r = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", role or "")
    r = re.sub(r"\(.*$", " ", r)
    r = TITLE_NOISE.sub(" ", r)
    r = REQ_ID.sub(" ", re.sub(r"#?\d{4,}", " ", r))
    r = re.sub(r"\bsr\b", "senior", re.sub(r"\bjr\b", "junior", norm(r.replace("&", " and "))))
    r = re.sub(r"\bmgr\b", "manager", re.sub(r"\bmgmt\b", "management", r))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", r).split())


def host_of(url):
    from urllib.parse import urlsplit
    h = urlsplit(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


class LoggedIndex:
    """Everything already logged, keyed three ways. find() -> ("dupe"|"repost", label, how) or None.
    A repost = same company + title on the same site under a different posting ID (a LinkedIn view of an
    ATS posting is the same posting, not a repost)."""

    def __init__(self):
        self.by_url, self.by_id, self.by_ct = {}, {}, {}

    def add(self, company, role, url, label):
        e = dict(label=label, url=url or "", host=host_of(url), ids=posting_keys(url, role))
        if url:
            self.by_url.setdefault(url_key(url), e)
        for i in e["ids"]:
            self.by_id.setdefault(i, e)
        ct = (company_key(company), title_key(role))
        if ct[0] and ct[1]:
            self.by_ct.setdefault(ct, []).append(e)

    def find(self, company, role, url):
        if url and url_key(url) in self.by_url:
            return ("dupe", self.by_url[url_key(url)]["label"], "same URL")
        ids = posting_keys(url, role)
        for i in ids:
            if i in self.by_id:
                return ("dupe", self.by_id[i]["label"], f"posting ID {i}")
        hits = self.by_ct.get((company_key(company), title_key(role)), [])
        if not hits:
            return None
        h = host_of(url)
        for e in hits:  # a match that isn't a same-site, different-ID posting is the same posting
            if not (ids and e["ids"] and h and h == e["host"] and not ids & e["ids"]):
                return ("dupe", e["label"], "company + title")
        e = hits[0]
        return ("repost", e["label"], f"same company + title, new ID {', '.join(sorted(ids))}")


def logged_index(include_seen=True):
    """Index of applications.json + archive.json (+ seen.txt, for checkouts that only carry it)."""
    idx = LoggedIndex()
    for n, r in enumerate(load_json(APPS)["applications"] if APPS.exists() else []):
        idx.add(r.get("company"), r.get("role"), r.get("url"), f"#{n} {r.get('company')} [{r.get('status')}]")
    for r in load_archive():
        idx.add(r.get("company"), r.get("role"), r.get("url"), f"archive: {r.get('company')} [{r.get('status')}]")
    if include_seen and SEEN.exists():
        for line in SEEN.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "|" not in line:
                continue
            p = [x.strip() for x in line.split("|")]
            idx.add(p[0], p[1], p[2] if len(p) > 2 else "", f"seen.txt: {p[0]} [{p[3] if len(p) > 3 else ''}]")
    return idx


def hours_since_last(out_file, floor=2):
    """--since-last (streamline #5): hours since the previous run's run_at in out_file, plus 1 for overlap.
    PM waves top off from the AM run instead of a full 24h pass. Falls back to 24 when there's no earlier run."""
    import json as _j
    from datetime import datetime as _dt
    try:
        prev = _dt.fromisoformat(_j.loads(out_file.read_text(encoding="utf-8"))["run_at"])
        h = (_dt.now(prev.tzinfo) - prev).total_seconds() / 3600
        return max(floor, int(h) + 2)
    except Exception:
        return 24


DROPS = ROOT / "data" / "drops"
DROP_KEEP_DAYS = 14


def drop_row(source, rule, company="", title="", location="", url="", pay="", posted="", **extra):
    """One dropped posting for the drop log (audit trail for postings that fell through)."""
    d = dict(source=source, rule=rule, company=company or "", title=title or "", location=location or "",
             url=url or "", pay=pay or "", posted=str(posted or ""))
    d.update({k: v for k, v in extra.items() if v not in (None, "")})
    return d


def write_drops(source, rows):
    """Append this run's drops to data/drops/<date>_<source>.jsonl.gz (gzip: thousands of rows a day) and prune
    files older than DROP_KEEP_DAYS. Scripts only, no AI cost. Kept for a manual audit of what the sweeps dropped."""
    import gzip as _gz, json as _j
    from datetime import date as _d, timedelta as _td
    DROPS.mkdir(parents=True, exist_ok=True)
    f = DROPS / f"{_d.today().isoformat()}_{source}.jsonl.gz"
    with _gz.open(f, "at", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(_j.dumps(r, ensure_ascii=False, default=str) + "\n")
    cutoff = (_d.today() - _td(days=DROP_KEEP_DAYS)).isoformat()
    for old in DROPS.glob("*.jsonl.gz"):
        if old.name[:10] < cutoff:
            old.unlink()
    rules = {}
    for r in rows:
        rules[r["rule"]] = rules.get(r["rule"], 0) + 1
    print(f"Drop log: {len(rows)} dropped postings → {f.relative_to(ROOT)} · "
          + ", ".join(f"{k} {v}" for k, v in sorted(rules.items(), key=lambda x: -x[1])[:8]), flush=True)
    return f


SCREEN_STAMPS = ROOT / "work" / "screen_stamps"   # local only (work/ is gitignored)


def screen_stamp_path(jd_path):
    """Where screen_leads.py records that a saved JD was screened. Keyed by the file's hash, so any edit to the
    JD after screening (or a fresh paste) means it has to be screened again before a verdict is logged."""
    import hashlib
    digest = hashlib.sha256(Path(jd_path).read_bytes()).hexdigest()
    return SCREEN_STAMPS / f"{digest}.json"


def host_is(url_or_host, *domains):
    """True when the host is one of `domains` or a subdomain of one. A substring test ("lever.co" in host) also
    matches lever.co.evil.example (CodeQL py/incomplete-url-substring-sanitization)."""
    from urllib.parse import urlsplit
    s = str(url_or_host or "").strip()
    h = (urlsplit(s).hostname if "://" in s else s.split("/")[0].split(":")[0]) or ""
    h = h.lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in domains)


# --- read vs unread, from data (a saved posting file), never from memory -------------------------------------------
THIN_JD_CHARS = 1500  # saved posting text shorter than this is a thin read, not a full one
_QUALS_HEADING = re.compile(
    r"(?im)^[ \t#*_-]*(?:required|minimum|basic|preferred)?[ \t]*(?:qualifications?|requirements?|what you(?:'|’)?ll bring|"
    r"what you bring|who you are|about you|must[- ]haves?|skills(?: and experience)?|experience|you have)\b")


def partial_reason(text):
    """'' when the saved text looks like a whole posting; else why it reads as partial (thin, or no qualifications part)."""
    body = (text or "").strip()
    if len(body) < THIN_JD_CHARS:
        return f"thin text ({len(body)} chars)"
    if not _QUALS_HEADING.search(body):
        return "no required-qualifications section found"
    return ""


def jd_state(path, root=None):
    """(state, why) for a saved posting path: 'none' (no path, missing or empty file), 'partial' (thin text or the
    qualifications part cut off; a READ: partial header from the capture counts too) or 'full'."""
    if not path:
        return "none", "no jd_file"
    p = Path(path)
    if not p.is_absolute():
        p = (root or ROOT) / p
    try:
        head, text = read_jd(p)
    except (OSError, UnicodeDecodeError):
        return "none", "saved file missing"
    if not text.strip():
        return "none", "saved file empty"
    flagged = (head.get("read") or "")
    if flagged.lower().startswith("partial"):
        return "partial", flagged.split("-", 1)[-1].strip() or "marked partial"
    why = partial_reason(text)
    return ("partial", why) if why else ("full", "")


def lead_slug(l):
    """work/<slug>.txt is where a lead's posting is saved in a chat (screen_leads.py --fetch, capture_jd.py)."""
    return re.sub(r"\W+", "_", f"{l.get('company', '')}_{l.get('role', '')}")[:60]


def lead_read(l, root=None):
    """(state, why, where) for one lead from the files themselves: its committed jd_file, else this chat's work/ capture.
    state is full | partial | none; where is the path used ('' when none)."""
    root = root or ROOT
    for rel in (l.get("jd_file"), f"work/{lead_slug(l)}.txt"):
        if rel:
            st, why = jd_state(rel, root)
            if st != "none":
                return st, why, rel
    return "none", "no saved posting text", ""


def read_label(state, why=""):
    return {"full": "read in full", "partial": f"PARTIAL read ({why})",
            "none": "unread, script-only"}[state]
