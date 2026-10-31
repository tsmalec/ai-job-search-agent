"""closing_soon.py — which open rows have a posting that closes soon (read-only).

Usage (from the repo root):
    python scripts/closing_soon.py            closing within 7 days, soonest first
    python scripts/closing_soon.py 14         use a different window (days)
    python scripts/closing_soon.py --all      also list open rows with no closing date found

Reads closing dates from each open row's note and its archived JD (jds/<slug>.txt, see make_record.py):
"closes Nov 6", "window closes Nov 13, 2026", "closing date: 2026-11-06", "apply by 11/06/2026",
"posting end date ...". Three groups so nothing drops silently: closing within the window,
already past (still open in the log), and "close date mentioned but unparsed".
Open rows = triaged, deciding, resume_built, applied, interview. Nothing is changed.
"""
import re, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import APPS, MONTHS, ROOT, load_json  # noqa: E402

OPEN = {"triaged", "deciding", "resume_built", "applied", "interview"}
CUE = r"(?:closes|closing date|closing on|window closes|apply by|applications? (?:due|accepted through|accepted until)|deadline|posting end date|end date|expires?)"
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
PATS = [
    re.compile(CUE + r"[\s:,(~]*(?:on\s+)?" + MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?", re.I),
    re.compile(CUE + r"[\s:,(~]*(?:on\s+)?(\d{4})-(\d{2})-(\d{2})", re.I),
    re.compile(CUE + r"[\s:,(~]*(?:on\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", re.I),
]


def find_close(text, ref=None):
    """Return (date or None, cue_seen) for the first closing date in text."""
    ref = ref or date.today()
    t = text or ""
    m = PATS[0].search(t)
    if m:
        mon = MONTHS.get(m.group(1).lower()[:3])
        y = int(m.group(3)) if m.group(3) else ref.year
        try:
            return date(y, mon, int(m.group(2))), True
        except (TypeError, ValueError):
            pass  # best effort: carry on without it
    m = PATS[1].search(t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), True
        except ValueError:
            pass  # not a parseable value: leave the default
    m = PATS[2].search(t)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(1)), int(m.group(2))), True
        except ValueError:
            pass  # not a parseable value: leave the default
    cue = re.search(CUE, t, re.I)
    # "until the position is filled" is boilerplate, not a close date
    if cue and re.search(r"until (the )?(position|role|job) is filled|ongoing basis", t[cue.start():cue.start() + 120], re.I):
        cue = None
    return None, bool(cue)


def jd_text(row):
    p = row.get("jd")
    if p and (ROOT / p).exists():
        return (ROOT / p).read_text(encoding="utf-8", errors="replace")
    return ""


def scan(window=7, rows=None):
    today = date.today()
    rows = rows if rows is not None else load_json(APPS)["applications"]
    soon, past, unparsed, none = [], [], [], []
    for r in rows:
        if r.get("status") not in OPEN or r.get("live") == "closed":
            continue
        d, cue = find_close(r.get("note") or "", today)
        if not d:
            d2, cue2 = find_close(jd_text(r), today)
            d, cue = d2, cue or cue2
        if d:
            left = (d - today).days
            if left < 0:
                past.append((left, d, r))
            elif left <= window:
                soon.append((left, d, r))
        elif cue:
            unparsed.append(r)
        else:
            none.append(r)
    return sorted(soon, key=lambda x: x[0]), sorted(past, key=lambda x: -x[0]), unparsed, none


def line(r):
    return f"{r['company']} - {r['role'][:60]} [{r['status']}]"


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    a = sys.argv[1:]
    window = int(next((x for x in a if x.isdigit()), 7))
    soon, past, unparsed, none = scan(window)
    print(f"Closing within {window} days: {len(soon)}")
    for left, d, r in soon:
        print(f"  {d.strftime('%a %b %d')} ({'today' if left == 0 else f'{left}d'})  {line(r)}")
    if past:
        print(f"\nClose date already passed, row still open (check liveness, or let it ride if applied): {len(past)}")
        for left, d, r in past:
            print(f"  {d.strftime('%b %d')} ({-left}d ago)  {line(r)}")
    if unparsed:
        print(f"\nClose date mentioned but not parsed (read the note): {len(unparsed)}")
        for r in unparsed:
            print(f"  {line(r)}")
    if "--all" in a:
        print(f"\nNo closing date found: {len(none)}")
        for r in none:
            print(f"  {line(r)}")
    else:
        print(f"\n({len(none)} open rows have no closing date; --all lists them.)")
