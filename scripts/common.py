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


def save_json(path, data):
    """Write with the repo's house style (2-space indent, real unicode, trailing newline), atomically."""
    write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def all_apps():
    """Every logged posting: live applications.json rows first, then archive.json rows."""
    rows = load_json(APPS)["applications"]
    if ARCHIVE.exists():
        rows = rows + load_json(ARCHIVE)["applications"]
    return rows


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
    """Posting IDs worth matching on: runs of 6+ digits (LinkedIn, Greenhouse, Workday and other job IDs)."""
    return set(re.findall(r"\d{6,}", text or ""))


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


TRACKING = re.compile(r"^(utm_\w*|source|src|iis|iisn|indeed-apply-token|posteddate|ref|refid|trk|lang|gh_src|domain)$", re.I)


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
    if ARCHIVE.exists():
        for r in load_json(ARCHIVE)["applications"]:
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


def host_is(url_or_host, *domains):
    """True when the host is one of `domains` or a subdomain of one. A substring test ("lever.co" in host) also
    matches lever.co.evil.example (CodeQL py/incomplete-url-substring-sanitization)."""
    from urllib.parse import urlsplit
    s = str(url_or_host or "").strip()
    h = (urlsplit(s).hostname if "://" in s else s.split("/")[0].split(":")[0]) or ""
    h = h.lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in domains)
