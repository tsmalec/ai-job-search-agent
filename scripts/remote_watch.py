"""remote_watch.py — tricky-remote employer watchlist.

Companies whose "remote" has burned us before (hub/commute clauses, "remote may be considered", perks dressed
as remote, state lists, on-site behind a remote tag). screen_leads.screen(company=...) adds a 🔍 flag for them
so the reviewer reads the whole location/workplace section, not the tag. A FLAG, never a skip.

data/remote_watch.json:
  manual: {company: trick}       hand-added entries (kept forever unless removed by hand)
  auto:   {company: {count, last}} rebuilt from logged rows: 2+ remote-integrity rejects (skipped/passed/dropped)

Usage (repo root):
    python scripts/remote_watch.py            rebuild "auto" from applications.json + archive.json; print the list
    python scripts/remote_watch.py --show     print only
    python scripts/remote_watch.py --add "Company" "the trick seen"
sync.py rebuilds it on every push, so a company joins on its 2nd remote-integrity reject without anyone asking.
"""
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import APPS, ARCHIVE, ROOT, company_key, load_json  # noqa: E402

FILE = ROOT / "data" / "remote_watch.json"
MIN_HITS = 2
REMOTE_REJECT = re.compile(
    r"bait and switch|\bhub\b|not remote|hybrid|on-?site|onsite|in-person|in person|commut|state list|"
    r"remote only|may be considered|remote tag wrong|workplace tag|remote[- ]filter leak|in-office", re.I)
DUPE = re.compile(r"dup", re.I)

SEED = {  # example entries; replace with employers you have seen this from
    "Example Corp": "\"Remote\" but must live near a hub office",
}


def load():
    if FILE.exists():
        return json.loads(FILE.read_text(encoding="utf-8"))
    return {"manual": dict(SEED), "auto": {}}


def save(d):
    FILE.write_text(json.dumps(d, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def rebuild(d):
    hits = {}
    for f in (APPS, ARCHIVE):
        if not f.exists():
            continue
        for r in load_json(f)["applications"]:
            note = r.get("note", "") or ""
            if r.get("status") in ("skipped", "passed", "dropped") and REMOTE_REJECT.search(note) and not DUPE.search(note):
                co = (r.get("company") or "").strip()
                if not co:
                    continue
                h = hits.setdefault(co, {"count": 0, "last": ""})
                h["count"] += 1
                h["last"] = max(h["last"], str(r.get("status_date", "")))
    d["auto"] = {k: v for k, v in hits.items() if v["count"] >= MIN_HITS}
    return d


_KEYS = None


def lookup(company):
    """Return the watch reason for a company, or ''. Cached per process."""
    global _KEYS
    if not company:
        return ""
    if _KEYS is None:
        d = load()
        _KEYS = {company_key(k): v for k, v in d.get("manual", {}).items()}
        for k, v in d.get("auto", {}).items():
            _KEYS.setdefault(company_key(k), f"{v['count']} remote-integrity rejects before")
    return _KEYS.get(company_key(company), "")


def flag(company):
    why = lookup(company)
    return f"🔍 Tricky-remote employer ({why}): read the whole location/workplace section, not the tag" if why else ""


def show(d):
    print(f"Tricky-remote watchlist: {len(d['manual'])} manual + {len(d['auto'])} auto")
    for k, v in sorted(d["manual"].items()):
        print(f"  ✋ {k}: {v}")
    for k, v in sorted(d["auto"].items(), key=lambda x: -x[1]["count"]):
        print(f"  🔁 {k}: {v['count']} rejects (last {v['last']})")


def main(argv):
    d = load()
    if argv[:1] == ["--add"] and len(argv) >= 3:
        d["manual"][argv[1]] = " ".join(argv[2:])
        save(d)
    elif argv[:1] != ["--show"]:
        save(rebuild(d))
    show(d)


if __name__ == "__main__":
    main(sys.argv[1:])
