"""skip_blockers.py — which required qualifications keep blocking roles, ranked by count (read-only).

Usage (from the repo root):
    python scripts/skip_blockers.py            all red / skipped / passed rows (applications + archive)
    python scripts/skip_blockers.py --days 30  only rows added in the last N days
    python scripts/skip_blockers.py --show pmp list the rows behind one blocker

Reads each row's note and years_req. Two tables:
  1. Qualification blockers: years lines, certifications, domains, level reach. These are the ones that
     can move with learning or time, so cert/learning priorities (certifications, tools, domains) rest on counts.
  2. Screen blockers: location, pay, bait-and-switch, closed, stale. Not learnable; shown for scale.
A row can count under several blockers. Pattern matching on free-text notes, so counts are approximate.
"""
import re, sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import all_apps  # noqa: E402

QUAL = {  # generic starter set: add the blockers that show up in your own skip notes
    "Years: people management / manager of managers": r"manager[- ]of[- ]managers|people[- ]manage|direct reports|years? (?:of )?(?:people )?management|managing (?:a )?team",
    "Years: program/project management": r"years? (?:of )?(?:program|project|pmo)|program leadership|\bprogram\b.{0,20}\byrs?\b",
    "Years: total experience": r"(?:1[0-9]|8|9)\+? ?(?:yrs|years)[^.|]{0,60}(?:short|gap|not met|✗|under)|(?:short|gap|✗)[^.|]{0,40}(?:1[0-9]|8|9)\+? ?(?:yrs|years)",
    "Required certification": r"\bpmp\b|six sigma|green belt|black belt|\bcpa\b|scrum master|\bcsm\b|certif\w* required",
    "Domain or industry gap": r"domain|industry experience|background in",
    "Technical depth": r"engineering|machine learning|data science|architect|python",
    "Level reach (Director/VP/Head/Principal)": r"level reach|director|\bvp\b|vice president|\bhead of\b|principal|title-level",
}
SCREEN = {
    "Location: outside remote / commute radius": r"commute radius|\d+[- ]mile|on-?site|hybrid|city-based|states? exclude|relocat|not near|remote restricted",
    "Pay: no base / under floor / OTE-only": r"no base|pay (?:under|fail)|under (?:the )?floor|\bote\b|total cash|salary range stated|pay screen|no pay",
    "Bait and switch (office at discretion)": r"bait and switch|discretion|as agreed with your manager",
    "Closed / no longer accepting": r"no longer accepting|closed|no longer available",
    "Stale (older than 7 days)": r"older than 7 days",
}


def rows(days=None):
    out = []
    for r in all_apps():
        if not (r.get("verdict") == "red" or r.get("status") in ("skipped", "passed")):
            continue
        if days:
            try:
                if (date.today() - date.fromisoformat(r.get("added", "")[:10])).days > days:
                    continue
            except ValueError:
                continue
        out.append(r)
    return out


def tally(rs):
    q, s, who = Counter(), Counter(), defaultdict(list)
    for r in rs:
        text = (r.get("note") or "").lower()
        y = r.get("years_req") or {}
        if y.get("status") in ("short", "borderline"):
            label = {"people": "Years: people management / manager of managers", "program": "Years: program/project management",
                     "total": "Years: total experience"}.get(y.get("type"), f"Years: {y.get('type')} (domain)")
            q[label] += 1
            who[label].append(r)
            text = text.replace("years", "")  # don't double count the same line from the note
        for label, pat in QUAL.items():
            if re.search(pat, text) and r not in who[label]:
                q[label] += 1
                who[label].append(r)
        for label, pat in SCREEN.items():
            if re.search(pat, text):
                s[label] += 1
                who[label].append(r)
    return q, s, who


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    a = sys.argv[1:]
    days = int(a[a.index("--days") + 1]) if "--days" in a else None
    rs = rows(days)
    q, s, who = tally(rs)
    if "--show" in a:
        want = a[a.index("--show") + 1].lower()
        for label, lst in who.items():
            if want in label.lower():
                print(f"{label}: {len(lst)}")
                for r in lst:
                    print(f"  {r.get('company')} - {r.get('role', '')[:60]} [{r.get('status')}/{r.get('verdict')}]")
        raise SystemExit
    print(f"Skip blockers across {len(rs)} red/skipped/passed rows" + (f" (last {days} days)" if days else "") +
          ". Approximate (read from notes). Read-only.\n")
    print("Qualification blockers (learnable or time-bound; rank cert/learning priorities on these):")
    for label, n in q.most_common():
        print(f"  {n:4}  {label}")
    print("\nScreen blockers (not learnable):")
    for label, n in s.most_common():
        print(f"  {n:4}  {label}")
