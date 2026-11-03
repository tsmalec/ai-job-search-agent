"""guard_rows.py — pre-push row guard for the scheduled sweep agent's lead pushes.

Usage (from the wave working dir):
    python3 scripts/guard_rows.py <live_leads.json> <staged_leads.json>

Compares the staged leads.json the worker is about to push against the live
leads.json it fetched for dedup. Exits 0 if safe, non-zero listing problems
otherwise. The push step must STOP and report to the user instead of pushing when
this fails.

Checks (mirrors the repo's scripts/sync.py row guard, adapted for the sweep
agent's leads-only pushes — it never moves rows to applications/archive, so every
live row must still be present):
- staged file parses and is non-empty (a wave with no keepers reports instead
  of pushing an empty file)
- every live lead row still present in staged (matched by URL, fallback
  company+role); a valid empty live list is a real post-triage inbox, not a
  failure — only a 0-byte/unparseable/keyless fetch fails
- no duplicate URLs in staged
- staged row count is not smaller than live's
- every live row is unchanged, except: status -> "dropped_by_agent" (or the legacy "dropped_by_muse") with a note, or attach_jds.py on a lead with
  no jd_file adding either jd_file (a jds/leads/*.txt path that exists, optionally with jd_inject, clearing
  jd_unread) or a dated jd_unread mark (never clearing one without adding jd_file)
- duplicate keys already in the live baseline are reported as GUARD NOTE and never fail the push;
  only duplicates the push itself adds fail
- no lead the push adds as not_triaged is already logged (applications, archive, seen.txt); run
  lead_gate.py --no-notes first
"""
import json
import re
import sys
from pathlib import Path


# The scheduled agent's drop status. "dropped_by_muse" is the pre-rename value, still accepted.
DROPPED = {"dropped_by_agent", "dropped_by_muse"}

def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", str(t or "").lower()).strip()


def load(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or "leads" not in d or not isinstance(d["leads"], list):
        raise ValueError(f"{path}: not a leads.json-shaped file")
    return d["leads"]


JD_FILE = re.compile(r"^jds/leads/[A-Za-z0-9_]+\.txt$")
JD_MARK = re.compile(r"^\d{4}-\d{2}-\d{2}: .{1,200}$", re.S)


def jd_edit_ok(live, staged, changed, root=None):
    """The one edit attach_jds.py makes, and nothing looser: on a lead with no jd_file, either attach a text
    (jd_file under jds/leads/ that exists, jd_unread cleared, jd_inject optional) or mark it unread with a dated
    reason. Blanking an existing jd_unread without attaching a text, or pointing jd_file anywhere else, fails."""
    if live.get("jd_file") or not changed <= {"jd_file", "jd_unread", "jd_inject"}:
        return False
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    f = staged.get("jd_file")
    if f is not None:
        return (isinstance(f, str) and bool(JD_FILE.match(f)) and (root / f).is_file()
                and "jd_unread" not in staged and (not staged.get("jd_inject") or JD_MARK.match(staged["jd_inject"])))
    m = staged.get("jd_unread")
    return "jd_inject" not in changed and isinstance(m, str) and bool(JD_MARK.match(m))


def key(r):
    u = (r.get("url") or "").strip().rstrip("/")
    if u:
        return ("url", u, "")
    return ("cr", norm(r.get("company")), norm(r.get("role")))


def main():
    if len(sys.argv) != 3:
        print("usage: guard_rows.py <live_leads.json> <staged_leads.json>", flush=True)
        return 2
    live_path, staged_path = sys.argv[1], sys.argv[2]
    try:
        live = load(live_path)
    except Exception as e:
        print(f"GUARD FAIL: live file unreadable: {e}", flush=True)
        return 1
    if not live:
        # A valid file with an empty leads list is a REAL state: triage moves
        # rows to applications/archive, so leads.json is legitimately [] after
        # a full triage. Allow it — the
        # load() above already rejects the actual failure mode (0-byte /
        # unparseable / missing leads key).
        print("GUARD NOTE: live baseline is a valid empty inbox (post-triage state).", flush=True)
    try:
        staged = load(staged_path)
    except Exception as e:
        print(f"GUARD FAIL: staged file unreadable: {e}", flush=True)
        return 1
    problems = []
    if not staged:
        # A wave with no keepers reports instead of pushing; an empty staged
        # file pushed over a non-empty live file is a wipe.
        problems.append(f"staged leads.json is empty (live has {len(live)} rows) — nothing to push; report instead")
    live_keys = {}
    live_dupes = []
    for r in live:
        if key(r) in live_keys:
            live_dupes.append(r)
        live_keys.setdefault(key(r), r)
    if live_dupes:
        print(f"GUARD NOTE: live baseline already has {len(live_dupes)} duplicate row(s): "
              + "; ".join(f"{r.get('company')} - {r.get('role')}" for r in live_dupes[:5]), flush=True)
    staged_keys = {}
    for r in staged:
        staged_keys.setdefault(key(r), []).append(r)
    for k, r in live_keys.items():
        if k not in staged_keys:
            problems.append(f"lead lost: {r.get('company')} - {r.get('role')}")
        else:
            s = staged_keys[k][0]
            if s != r:
                changed = {f for f in set(r) | set(s) if r.get(f) != s.get(f)}
                allowed = s.get("status") in DROPPED and changed <= {"status", "note"} and s.get("note")
                allowed = allowed or jd_edit_ok(r, s, changed)
                if not allowed:
                    problems.append(f"lead edited in place ({', '.join(sorted(changed))}): {r.get('company')} - {r.get('role')}")
    live_count = {}
    for r in live:
        live_count[key(r)] = live_count.get(key(r), 0) + 1
    for k, v in staged_keys.items():
        if len(v) > max(1, live_count.get(k, 0)):  # only dupes this push added; pre-existing ones are a NOTE
            problems.append(f"duplicate in staged file: {v[0].get('company')} - {v[0].get('role')}")
    if len(staged) < len(live_keys):
        problems.append(f"staged has {len(staged)} rows, fewer than live's {len(live_keys)}")
    # Already-logged postings can come back as not_triaged leads (a restore or a stale push).
    # Any lead this push adds as not_triaged must not already be logged; lead_gate.py marks them.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from common import logged_index
        idx = logged_index()
        live_set = set(live_keys)
        # Also check against leads already in the inbox and other new leads in this push: the logged index alone
        # misses an inbox-vs-inbox duplicate when a wave skips lead_gate.py.
        for r in live:
            if r.get("status") not in DROPPED:
                idx.add(r.get("company"), r.get("role"), r.get("url"),
                        f"inbox lead: {r.get('company')} ({r.get('source', '')})")
        for r in staged:
            if r.get("status") == "not_triaged" and key(r) not in live_set:
                hit = idx.find(r.get("company"), r.get("role"), r.get("url"))
                if not hit:
                    idx.add(r.get("company"), r.get("role"), r.get("url"),
                            f"new lead this push: {r.get('company')} ({r.get('source', '')})")
                if hit and not len(staged_keys.get(key(r), [])) > 1:  # same-key dupes are already reported above
                    what = "duplicate lead" if hit[1].startswith(("inbox lead", "new lead this push")) else "already logged"
                    problems.append(f"{what} ({hit[0]} of {hit[1]}, {hit[2]}): {r.get('company')} - "
                                    f"{r.get('role')} -> run python3 scripts/lead_gate.py --no-notes, then guard again")
    except FileNotFoundError as e:  # a checkout without the data files: a documented skip
        print(f"GUARD NOTE: logged-posting check skipped, data file missing ({e.filename})", flush=True)
    except Exception as e:  # anything else is a failed check, not a skip (outside review)
        problems.append(f"logged-posting check failed ({type(e).__name__}: {e}); fix it before pushing")
    if problems:
        print(f"GUARD FAIL ({len(problems)}):", flush=True)
        for p in problems[:40]:
            print("  " + p, flush=True)
        if len(problems) > 40:
            print(f"  ... +{len(problems) - 40} more", flush=True)
        return 1
    print(f"GUARD OK: {len(staged)} staged rows cover all {len(live_keys)} live rows, no dupes.", flush=True)
    return 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    sys.exit(main())
