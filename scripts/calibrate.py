"""calibrate.py — do 🟢 rows convert better than 🟡/🟠? (read-only, advisory)

Usage (from the repo root):
    python scripts/calibrate.py             every row the candidate applied to (applications + archive)
    python scripts/calibrate.py --min 14    count silent rows as "no response" only after N days (default 14)

For each verdict: applied, progressed (screen signal, interview or offer), rejected, silent 14+ days,
and still young (<14 days, not counted in rates). Rates use only "settled" rows (progressed + rejected +
silent 14+). Numbers are advisory: don't change rules until enough outcomes are in.
"""
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import all_apps  # noqa: E402

APPLIED = {"applied", "interview", "offer", "rejected", "accepted", "declined"}
ORDER = ["green", "yellow", "orange", "red", "?"]


def bucket(v):
    v = (v or "?").lower()
    return "yellow" if v.startswith("green/") else (v if v in ORDER else "?")


def outcome(r, min_days):
    s = r.get("status")
    if s in ("interview", "offer", "accepted", "declined") or r.get("screen"):
        return "progressed"
    if s == "rejected":
        return "rejected"
    try:
        age = (date.today() - date.fromisoformat((r.get("status_date") or "")[:10])).days
    except ValueError:
        return "young"
    return "silent" if age >= min_days else "young"


def table(min_days=14, rows=None):
    t = defaultdict(lambda: defaultdict(int))
    for r in rows if rows is not None else all_apps():
        applied = r.get("status") in APPLIED or (r.get("timing") or {}).get("applied_at")
        if not applied:
            continue
        t[bucket(r.get("verdict"))][outcome(r, min_days)] += 1
    return t


def pct(a, b):
    return f"{100 * a / b:4.0f}%" if b else "   –"


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    a = sys.argv[1:]
    m = int(a[a.index("--min") + 1]) if "--min" in a else 14
    t = table(m)
    print(f"Calibration: verdict vs. outcome (silent = no reply {m}+ days). Advisory: no rule changes from these until enough outcomes are in.\n")
    print(f"{'verdict':8} {'applied':>7} {'progr':>6} {'rej':>5} {'silent':>6} {'young':>6} | {'progress%':>9} {'reject%':>8}  (of settled)")
    tot = defaultdict(int)
    for v in ORDER:
        if v not in t:
            continue
        c = t[v]
        n = sum(c.values())
        settled = c["progressed"] + c["rejected"] + c["silent"]
        for k_ in ("progressed", "rejected", "silent", "young"):
            tot[k_] += c[k_]
        print(f"{v:8} {n:7} {c['progressed']:6} {c['rejected']:5} {c['silent']:6} {c['young']:6} | {pct(c['progressed'], settled):>9} {pct(c['rejected'], settled):>8}")
    settled = tot["progressed"] + tot["rejected"] + tot["silent"]
    print(f"{'all':8} {sum(tot.values()):7} {tot['progressed']:6} {tot['rejected']:5} {tot['silent']:6} {tot['young']:6} | "
          f"{pct(tot['progressed'], settled):>9} {pct(tot['rejected'], settled):>8}")
    if settled < 20:
        print(f"\nOnly {settled} settled rows: too few to read anything into yet.")
    print("\nprogressed = screen signal (assessment/questionnaire/recruiter), interview or offer.")
