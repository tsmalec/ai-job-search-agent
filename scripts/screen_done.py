"""screen_done.py: has every open lead been screened and left leads.json?

Usage (from the repo root):
    python3 scripts/screen_done.py                 exit 1 if any lead is still not_triaged (the screen isn't finished)
    python3 scripts/screen_done.py --source "A"    same, but only leads whose source label contains that text
    python3 scripts/screen_done.py --report [--since YYYY-MM-DD]
                                                   per-lead READ / PARTIAL / UNREAD table from the files themselves (default:
                                                   leads promoted today) and the counts line the reply must quote
    python3 scripts/screen_done.py --read --quiet  exit 1 only for a lead that was read in this chat (a saved posting in
                                                   work/) but not promoted; prints nothing otherwise (sync.py uses this)

The screen (phase 2, project/steps/screen-leads.md) ends when no not_triaged lead is left: every lead was screened and
promoted with log.py. "Read" = work/<company>_<role>.txt exists (written by `screen_leads.py --leads --fetch` or
capture_jd.py); work/ is gitignored, so it holds only this chat's reads. The reply ends with this script's line.

Read vs unread comes from data, never memory: a lead is READ only when a saved posting file exists and is not empty
(its jd_file, else this chat's work/ capture); PARTIAL when that text is thin or has no qualifications part; otherwise
UNREAD, script-only. A lead with no saved posting is never a "screen pass". Quote the --report counts, not a recollection.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402


def slug(l):
    return common.lead_slug(l)


LEAD_NOTE = ("Muse ", "Scheduled agent ")  # the note prefixes log.py promote has written


def promoted_since(since):
    """Rows promoted from a lead (promote sets jd_read; older rows: the note prefix) with a status date on or
    after `since`."""
    return [r for r in common.load_json(common.APPS).get("applications", [])
            if (r.get("jd_read") or str(r.get("note") or "").startswith(LEAD_NOTE))
            and str(r.get("status_date") or "") >= since]


def latest_note(note):
    """The newest ' | '-joined segment of a row's note; a correction note ('Correction: ...') counts as no claim."""
    last = str(note or "").rsplit(" | ", 1)[-1]
    return "" if "Correction:" in last else last


def report(since):
    """Print one line per lead (still open, or promoted since `since`) and the counts. Returns the unread count."""
    rows = []
    for l in common.load_json(common.LEADS).get("leads", []):
        if l.get("status") == "not_triaged":
            st, why, _ = common.lead_read(l)
            rows.append(("open", l.get("company", ""), l.get("role", ""), st, why))
    for r in promoted_since(since):
        jr = str(r.get("jd_read") or "")
        if jr:
            st, why = (jr.split(": ", 1) + [""])[:2] if jr.startswith("partial") else (jr, "")
        else:  # promoted before jd_read existed: judge by the row's saved jd path
            st, why = common.jd_state(r.get("jd"))
        note = latest_note(r.get("note"))
        bad = ""
        if st == "none" and re.search(r"screen pass", note, re.I):
            bad = "note says 'screen pass' but no posting was saved"
        elif st != "none" and re.search(r"\bnot read\b|\bunread\b", note, re.I):
            bad = "note says unread but a saved posting exists"
        rows.append(("promoted", r.get("company", ""), r.get("role", ""), st, why, bad))
    n = {"full": 0, "partial": 0, "none": 0}
    for row in rows:
        _, co, role, st, why, *bad = row
        n[st] += 1
        icon = {"full": "📄", "partial": "🟡", "none": "📭"}[st]
        print(f"{icon} {co} - {role[:50]}: {common.read_label(st, why)}" + (f"  ⛔ {bad[0]}" if bad and bad[0] else ""))
    total = len(rows)
    print(f"READ {n['full'] + n['partial']} of {total} (full {n['full']}, partial {n['partial']}); "
          f"unread, script-only {n['none']}. Counts come from saved files, not memory.")
    if n["none"]:
        print("Unread leads can only be read via the built-in browser, then `capture_jd.py \"<url>\"` with the full page "
              "text on stdin (cloud chats can't run fetch_jd.py); or run `python scripts/attach_jds.py` on the desktop first.")
    return n["none"]


def main(a):
    if any(x in ("-h", "--help") for x in a):
        print((__doc__ or "").strip())
        return 0
    quiet = "--quiet" in a
    open_ = [l for l in common.load_json(common.LEADS).get("leads", []) if l.get("status") == "not_triaged"]
    if "--report" in a:
        since = a[a.index("--since") + 1] if "--since" in a and a.index("--since") + 1 < len(a) else common.today()
        report(since)
        return 0
    if "--read" in a:
        read = [l for l in open_ if Path("work", slug(l) + ".txt").exists()]
        if read:
            names = "; ".join(f"{l['company']} - {l['role'][:40]}" for l in read[:10]) + (f"; +{len(read) - 10} more" if len(read) > 10 else "")
            print(f"⛔ {len(read)} lead(s) read in this chat but still in leads.json: {names}. Promote each with log.py promote.")
            return 1
        return 0
    if "--source" in a:
        i = a.index("--source")
        if i + 1 >= len(a):
            raise SystemExit('--source needs a label, e.g. --source "Wave A"')
        want = a[i + 1].lower()
        open_ = [l for l in open_ if want in (l.get("source") or "").lower()]
    if open_:
        print(f"⛔ {len(open_)} lead(s) still in leads.json. Screen and promote each with log.py promote, then rerun screen_done.py.")
        return 1
    if not quiet:
        print("✅ every open lead has been screened and has left leads.json. "
              "Run --report for which leads were actually read; none saved = unread, script-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
