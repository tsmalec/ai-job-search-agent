"""salary_gap.py — pay facts per posting, and where a number sits vs. the posted range and market.

Usage (from the repo root):
    python scripts/salary_gap.py add <match> posted 95000 120000 --source "JD pay section" [--date 2026-01-12]
    python scripts/salary_gap.py add <match> recruiter 110000 --source "Recruiter screen, Jane D." [--note "..."]
    python scripts/salary_gap.py add <match> offer 112000 --source "Offer letter" [--note "+10% bonus"]
    python scripts/salary_gap.py show <match>       every pay fact for one row + where the latest sits
    python scripts/salary_gap.py                    every row with a recruiter-stated or offer number

<match> works like log.py (words in "company role", a posting ID, or a URL); exactly one row must match.
Facts live in data/pay_facts.json, append-only (a fact is never edited or deleted; add a newer one).
Kinds: posted | recruiter | offer | asked (what the candidate said out loud, only if they gave it).
Read-only context it adds: the posted range found in the row's note (screen_leads.ranges) and the title's
market base from data/title_pay.json. It never suggests a target or ask number (salary rule stands);
it only says where a stated number sits vs. those ranges and vs. the vetting floor in data/pay_rules.json.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, all_apps, job_ids, load_json, norm, save_json  # noqa: E402
from common import k_fmt, pay_rules  # noqa: E402
from screen_leads import market_pay, ranges  # noqa: E402

FACTS = ROOT / "data" / "pay_facts.json"
KINDS = ("posted", "recruiter", "offer", "asked")
FLOOR = pay_rules()["vet_floor"]  # vetting floor (data/pay_rules.json)


def load():
    if FACTS.exists():
        return load_json(FACTS)
    return {"_about": "Append-only pay facts per posting (salary_gap.py). Never edit or delete; add a newer fact.",
            "facts": []}


def find(q):
    rows = all_apps()
    ids = job_ids(q)
    hits = [r for r in rows if ids and ids & (job_ids(r.get("url") or "") | job_ids(r.get("role") or ""))]
    if not hits:
        w = norm(q).split()
        hits = [r for r in rows if w and all(x in norm(f"{r.get('company','')} {r.get('role','')}") for x in w)]
    if len(hits) != 1:
        for r in hits[:15]:
            print(f"  {r.get('company')} - {r.get('role','')[:70]} [{r.get('status')}] {r.get('url') or ''}")
        raise SystemExit(f"{len(hits)} rows match '{q}'; need exactly one (add words or use the posting ID).")
    return hits[0]


def key(r):
    return r.get("url") or f"{r.get('company')} | {r.get('role')}"


def k(v):
    return f"${v/1000:,.0f}K"


def where(v, lo, hi):
    if v < lo:
        return f"below by {k(lo - v)}"
    if v > hi:
        return f"above by {k(v - hi)}"
    pct = 0 if hi == lo else round(100 * (v - lo) / (hi - lo))
    return f"inside, {pct}% of the way up"


def show(r, facts):
    mine = [f for f in facts if f["key"] == key(r)]
    print(f"{r['company']} - {r['role']}  [{r['status']}]")
    posted = [f for f in mine if f["kind"] == "posted"]
    note_rng = ranges(f"{r.get('role') or ''} {r.get('note') or ''}")
    if posted:
        lo, hi = posted[-1]["low"], posted[-1]["high"] or posted[-1]["low"]
        src = f"pay fact {posted[-1]['date']}"
    elif note_rng:
        lo, hi = note_rng[0][0], note_rng[0][1]
        src = "row note (parsed)"
    else:
        lo = hi = None
        src = ""
    print(f"  Posted range: {k(lo)}-{k(hi)} ({src})" if lo else "  Posted range: none on file")
    m = market_pay(r.get("role"))
    if m:
        print(f"  Market base (title_pay '{m['title'][:50]}'): {k(m['market_base_lo'])}-{k(m['market_base_hi'])},"
              f" median {k(m['market_median'])} (checked {m['checked']})")
    for f in mine:
        hi_txt = f"-{k(f['high'])}" if f.get("high") else ""
        print(f"  {f['date']}  {f['kind']:9} {k(f['low'])}{hi_txt}  · {f['source']}" + (f"  · {f['note']}" if f.get("note") else ""))
    latest = next((f for f in reversed(mine) if f["kind"] in ("offer", "recruiter")), None)
    if latest:
        v = latest["low"]
        print(f"  Latest {latest['kind']} {k(v)}:")
        if lo:
            print(f"    vs posted range: {where(v, lo, hi)}")
        if m:
            print(f"    vs market base:  {where(v, m['market_base_lo'], m['market_base_hi'])}")
        print(f"    vs your {k_fmt(FLOOR)} base floor: {'clears it' if v >= FLOOR else 'UNDER the floor by ' + k(FLOOR - v)}")
    if not mine and not lo:
        print("  No pay facts yet.")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    a = sys.argv[1:]
    data = load()
    if a and a[0] == "add":
        if len(a) < 4 or a[2] not in KINDS:
            raise SystemExit(__doc__)
        r = find(a[1])
        nums = [x for x in a[3:5] if x.replace(",", "").replace(".", "").isdigit()]
        opt = {a[i][2:]: a[i + 1] for i in range(3, len(a) - 1) if a[i].startswith("--")}
        if not opt.get("source"):
            raise SystemExit("--source is required (where the number came from).")
        vals = [float(x.replace(",", "")) for x in nums]
        vals = [v * 1000 if v < 1000 else v for v in vals]
        fact = {"key": key(r), "company": r["company"], "role": r["role"], "kind": a[2],
                "low": vals[0], "high": vals[1] if len(vals) > 1 else None,
                "source": opt["source"], "date": opt.get("date") or date.today().isoformat()}
        if opt.get("note"):
            fact["note"] = opt["note"]
        data["facts"].append(fact)
        save_json(FACTS, data)
        print("Added. ", end="")
        show(r, data["facts"])
    elif a and a[0] == "show":
        show(find(" ".join(a[1:])), data["facts"])
    else:
        keys = []
        for f in data["facts"]:
            if f["kind"] in ("recruiter", "offer") and f["key"] not in keys:
                keys.append(f["key"])
        if not keys:
            print("No recruiter-stated or offer numbers logged yet. Add one with:\n"
                  "  python scripts/salary_gap.py add <match> offer 112000 --source \"Offer letter\"")
        rows = {key(r): r for r in all_apps()}
        for kk in keys:
            if kk in rows:
                show(rows[kk], data["facts"])
                print()
