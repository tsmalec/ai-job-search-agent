"""followups.py — which applications have gone quiet and may need a nudge.

Usage (from the repo root):
    python scripts/followups.py            applied 7+ days ago with no newer status
    python scripts/followups.py 14         use a different cutoff (days)

Groups by age (7-13 days: light follow-up; 14-29: second touch or move on; 30+: likely dead).
Interviews with no update in 5+ days are listed first. Nothing is changed.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import APPS, load_json  # noqa: E402


def age(d):
    try:
        return (date.today() - date.fromisoformat(d)).days
    except Exception:
        return None


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    cutoff = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    apps = load_json(APPS)["applications"]
    iv = [(age(a["status_date"]), a) for a in apps if a["status"] == "interview"]
    iv = [x for x in iv if x[0] is not None and x[0] >= 5]
    if iv:
        print("Interviews with no update in 5+ days:")
        for d, a in sorted(iv, key=lambda x: -x[0]):
            print(f"  {d:>3}d  {a['company']} - {a['role'][:60]}  · {(a.get('note') or '')[:80]}")
    closed = [a for a in apps if a["status"] == "applied" and a.get("live") == "closed"]
    quiet = [(age(a["status_date"]), a) for a in apps if a["status"] == "applied" and a.get("live") != "closed"]
    quiet = sorted([x for x in quiet if x[0] is not None and x[0] >= cutoff], key=lambda x: -x[0])
    bands = [(30, 10**6, "30+ days (likely dead; close unless you hear back)"),
             (14, 29, "14-29 days (second touch, or let it go)"),
             (cutoff, 13, f"{cutoff}-13 days (light follow-up: recruiter or referral)")]
    for lo, hi, label in bands:
        rows = [x for x in quiet if lo <= x[0] <= hi]
        if rows:
            print(f"\n{label}: {len(rows)}")
            for d, a in rows:
                ref = f" · referral: {a['referral']}" if a.get("referral") else ""
                print(f"  {d:>3}d  {a['company']} - {a['role'][:60]}{ref}")
    if closed:  # posting closed since you applied; no nudge, wait for the outcome email
        print(f"\nPosting closed since you applied (no nudge): {len(closed)}")
        for a in closed:
            print(f"  {a['company']} - {a['role'][:60]} · closed as of {a.get('live_checked')}")
    if not quiet and not iv:
        print(f"Nothing older than {cutoff} days without an update.")
