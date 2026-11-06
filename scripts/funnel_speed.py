"""funnel_speed.py — how long each stage takes, and how long silence lasts before a rejection (read-only, advisory)

Usage (from the repo root):
    python scripts/funnel_speed.py

Stages from what the log has: applied (timing.applied_at, else the row's added date for applied rows),
first screen signal (`screen` field date), interview (status_date of interview rows or
timing.outcome_at with outcome_type interview), decision (rejected/offer: timing.outcome_at).
Date-only stamps count as noon. Also prints the silence curve: of rejections, what share arrived
within 1/3/7/14/30 days, and how many applied rows are past each mark with no reply.
Don't change rules from these numbers until enough outcomes are in.
"""
import re, sys
from datetime import datetime
from statistics import median
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import all_apps  # noqa: E402


def when(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v) if "T" in v else datetime.fromisoformat(v[:10] + "T12:00")
    except ValueError:
        return None


def screen_date(r):
    m = re.search(r"\d{4}-\d{2}-\d{2}", r.get("screen") or "")
    return when(m.group(0)) if m else None


def stages(rows=None):
    out = {"applied→screen": [], "applied→interview": [], "applied→rejected": [], "applied→offer": [], "screen→decision": []}
    for r in rows if rows is not None else all_apps():
        t = r.get("timing") or {}
        a = when(t.get("applied_at"))
        if not a:
            continue
        sc, oc, ot = screen_date(r), when(t.get("outcome_at")), t.get("outcome_type")
        if sc:
            out["applied→screen"].append((sc - a).total_seconds() / 86400)
        if oc and ot in ("rejected", "interview", "offer"):
            out[f"applied→{ot}"].append((oc - a).total_seconds() / 86400)
        if sc and oc and ot in ("rejected", "offer") and oc >= sc:
            out["screen→decision"].append((oc - sc).total_seconds() / 86400)
        if r.get("status") == "interview" and not (oc and ot == "interview"):
            iv = when(r.get("status_date"))
            if iv:
                out["applied→interview"].append((iv - a).total_seconds() / 86400)
    return out


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    s = stages()
    print("Funnel speed (days). Advisory: no rule changes from these until enough outcomes are in.\n")
    print(f"{'stage':20} {'n':>4} {'median':>7} {'min':>6} {'max':>6}")
    for k, v in s.items():
        v = [max(0.0, x) for x in v]
        if v:
            print(f"{k:20} {len(v):4} {median(v):7.1f} {min(v):6.1f} {max(v):6.1f}")
        else:
            print(f"{k:20} {0:4}       –")
    rej = sorted(max(0.0, x) for x in s["applied→rejected"])
    today = datetime.now()
    waiting = []
    for r in all_apps():
        if r.get("status") == "applied":
            a = when((r.get("timing") or {}).get("applied_at")) or when(r.get("status_date"))
            if a:
                waiting.append((today - a).total_seconds() / 86400)
    print("\nSilence before a rejection:")
    print(f"{'within':>8} {'rejections':>11} {'share':>6}   {'applied, still silent past this mark':>36}")
    for d in (1, 3, 7, 14, 30):
        n = sum(1 for x in rej if x <= d)
        past = sum(1 for x in waiting if x > d)
        share = f"{100 * n / len(rej):5.0f}%" if rej else "    –"
        print(f"{d:>6}d {n:11} {share:>6}   {past:36}")
    if len(rej) < 20:
        print(f"\nOnly {len(rej)} timed rejections: read the curve loosely.")
