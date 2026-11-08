"""ats_probe.py — find job boards no public list has, by guessing each company's board name and asking the board
(idea from the CareerScout repo, Ramcharan747/careerscout, MIT; our own stdlib code).

Every confirmed board is added to data/sweep_boards.json under "added" (with a source note), so the next
board_sweep.py run reads it. Covers every lane: Greenhouse, Lever, Ashby and BambooHR boards the Feashliaa list
misses, plus SmartRecruiters, Recruitee, Rippling, Breezy and Workable, which have no public list at all.
Workday isn't here: its board names can't be guessed (see wd_discover.py).

Company names come from: data/seen.txt (everything logged), data/watchlist.json, and the S&P 500 list
(datasets/s-and-p-500-companies on GitHub). Add your own with --names file.txt (one company per line).

Usage (from the repo root):
    python scripts/ats_probe.py                         all sources, all platforms
    python scripts/ats_probe.py --only smartrecruiters  one platform (comma list works)
    python scripts/ats_probe.py --skip workable         every platform except these
    python scripts/ats_probe.py --names extra.txt       add company names from a file
    python scripts/ats_probe.py --no-sp500              logged + watchlist names only
    python scripts/ats_probe.py --limit 50              first 50 companies (quick test)
    python scripts/ats_probe.py --dry                   report finds, don't save them
    python scripts/ats_probe.py --retry                 re-ask names already tried (default skips them for 30 days)

Tried names are remembered in data/probe_tried.json (local only) so reruns only ask about new companies.
"""
import csv, io, json, re, sys, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_sweep as bs  # noqa: E402

DATA = bs.DATA
TRIED = DATA / "probe_tried.json"
SP500 = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
PROBE = ("greenhouse", "lever", "ashby", "bamboohr", "smartrecruiters", "recruitee", "rippling", "breezy", "workable")
DEFAULT_SKIP = {"workable"}  # 429 from Claude's workspace; turn on with --only workable from the candidate's PC
WORKERS = {"workable": 2}
PACE = {"workable": 1.5}
SUFFIX = re.compile(r"\b(?:inc|incorporated|corp|corporation|co|company|llc|ltd|plc|group|holdings?|the|n\.?a|"
                    r"technologies|technology|& co)\b\.?", re.I)


def variants(name, plat):
    """Board-name guesses for one company: 'Acme Health, Inc.' -> acmehealth, acme-health (+ AcmeHealth on SmartRecruiters)."""
    base = SUFFIX.sub(" ", re.sub(r"\(.*?\)", " ", name)).replace("&", " and ")
    words = re.findall(r"[A-Za-z0-9]+", base)
    if not words:
        return []
    # Full name only: a first-word guess ("first", "american") lands on a stranger's board too often.
    v = ["".join(words).lower(), "-".join(words).lower()]
    if plat == "smartrecruiters":  # SR company IDs are case-sensitive and often CamelCase (e.g. AcmeGroup)
        v = ["".join(w[:1].upper() + w[1:] for w in words)] + v
    out = []
    for x in v:
        if len(x) >= 3 and x not in out:
            out.append(x)
    return out[:4]


def names(opts):
    got = {}
    seen = DATA / "seen.txt"
    if seen.exists():
        for line in seen.read_text(encoding="utf-8").splitlines():
            if "|" in line and not line.startswith("#"):
                co = line.split("|")[0].strip()
                if co and not co.lower().startswith("muse"):
                    got.setdefault(co, "seen.txt")
    wl = DATA / "watchlist.json"
    if wl.exists():
        d = json.loads(wl.read_text(encoding="utf-8"))
        for k in ("watching", "reported"):
            for r in d.get(k) or []:
                if isinstance(r, dict) and r.get("company"):
                    got.setdefault(r["company"], "watchlist")
    if opts["names"]:
        for line in Path(opts["names"]).read_text(encoding="utf-8").splitlines():
            if line.strip():
                got.setdefault(line.strip(), Path(opts["names"]).name)
    if opts["sp500"]:
        try:
            req = bs.urllib.request.Request(SP500, headers={"User-Agent": bs.UA})
            with bs.urllib.request.urlopen(req, timeout=60) as r:
                for row in csv.DictReader(io.StringIO(r.read().decode("utf-8", "replace"))):
                    if row.get("Security"):
                        got.setdefault(row["Security"], "S&P 500")
        except Exception as e:
            print(f"S&P 500 list not reachable ({type(e).__name__}); going on without it.")
    return got


def main(argv):
    o = dict(only="", skip="", names="", sp500=True, limit=0, dry=False, retry=False)
    it = iter(argv)
    for a in it:
        if a == "--only": o["only"] = next(it)
        elif a == "--skip": o["skip"] = next(it)
        elif a == "--names": o["names"] = next(it)
        elif a == "--no-sp500": o["sp500"] = False
        elif a == "--limit": o["limit"] = int(next(it))
        elif a == "--dry": o["dry"] = True
        elif a == "--retry": o["retry"] = True
        else: raise SystemExit(__doc__)
    plats = o["only"].split(",") if o["only"] else [p for p in PROBE if p not in DEFAULT_SKIP | set(o["skip"].split(","))]
    bad = [p for p in plats if p not in PROBE]
    if bad:
        raise SystemExit(f"Unknown platform(s): {', '.join(bad)}. Choose from: {', '.join(PROBE)}")

    b = bs.load_boards()
    have = {p: {s.lower() for s in b["slugs"].get(p, [])} for p in plats}
    for k in b.get("added", {}):
        p_, _, s_ = k.partition(":")
        if p_ in have:
            have[p_].add(s_.lower())
    tried = json.loads(TRIED.read_text(encoding="utf-8")) if TRIED.exists() else {}
    cutoff = (datetime.now() - timedelta(days=30)).date().isoformat()
    today = datetime.now().date().isoformat()

    cos = names(o)
    items = list(cos.items())[: o["limit"]] if o["limit"] else list(cos.items())
    print(f"{len(items):,} companies x {len(plats)} platforms: {', '.join(plats)}", flush=True)

    jobs = []  # (plat, company, source, [slugs])
    for co, src in items:
        for p in plats:
            key = f"{p}:{co}"
            if not o["retry"] and tried.get(key, "") >= cutoff:
                continue
            vs = variants(co, p)
            if any(v.lower() in have[p] for v in vs):
                continue  # this company already has a board on this platform
            if vs:
                jobs.append((p, co, src, vs))
    print(f"{len(jobs):,} company/platform pairs to ask (skipping ones tried in the last 30 days or already known)",
          flush=True)

    found, lock, done = [], threading.Lock(), [0]

    def ask(job):
        p, co, src, vs = job
        bs.TRIES[0] = 1
        for v in vs:
            rows, err = bs.READERS[p](v, PACE.get(p, 0.3))
            if rows:  # a board with no open postings is no use to the sweep yet
                names_ = {r.get("company") for r in rows if r.get("company")}
                # Guessed names can land on a lookalike (a franchise agent, an old test board): flag, don't drop.
                odd = names_ and not any(bs.key_co(co)[:5] in bs.key_co(n) for n in names_)
                return p, co, src, v, f"{len(rows)}{' ⚠ board says: ' + sorted(names_)[0] if odd else ''}"
            if err not in ("404", "") and err.startswith("HTTP 429"):
                return p, co, src, None, "429"
        return p, co, src, None, None

    by = {}
    for j in jobs:
        by.setdefault(j[0], []).append(j)
    n429 = {}
    with ThreadPoolExecutor(max_workers=sum(WORKERS.get(p, 8) for p in by) or 1) as ex:
        futs = [ex.submit(ask, j) for p in by for j in by[p]]
        for f in as_completed(futs):
            p, co, src, slug, n = f.result()
            with lock:
                done[0] += 1
                if n == "429":
                    n429[p] = n429.get(p, 0) + 1
                else:
                    tried[f"{p}:{co}"] = today
                if slug:
                    found.append((p, slug, co, src, n))
                    print(f"  ✅ {p}:{slug}  ({co}, {n} open postings)", flush=True)  # n may carry a ⚠ name check
                if done[0] % 200 == 0:
                    print(f"  ... {done[0]:,}/{len(jobs):,} asked, {len(found)} found", flush=True)

    for p, k in n429.items():
        print(f"⚠️ {p}: {k} answers were 429 (rate-limited). Those companies weren't marked tried; rerun later "
              f"or from another machine.")
    if not o["dry"]:
        for p, slug, co, src, n in found:
            b.setdefault("added", {})[f"{p}:{slug}"] = dict(source=f"ats_probe: {co} ({src})", added=today)
        bs.save_boards(b)
        TRIED.write_text(json.dumps(tried, indent=0, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    tally = {}
    for p, *_ in found:
        tally[p] = tally.get(p, 0) + 1
    print(f"\nFound {len(found)} new boards: " + (" · ".join(f"{p} {k}" for p, k in sorted(tally.items())) or "none"))
    if found and not o["dry"]:
        print("Saved to data/sweep_boards.json \"added\". New platforms are off by default in board_sweep.py: "
              "run with --with " + ",".join(sorted({p for p, *_ in found} & bs.DEFAULT_OFF)) if
              ({p for p, *_ in found} & bs.DEFAULT_OFF) else "Saved to data/sweep_boards.json \"added\".")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    main(sys.argv[1:])
