"""stale_triage.py — clear out triaged rows that will never get built.

Usage (from the repo root):
    python scripts/stale_triage.py                 preview only: skip candidates + what stays
    python scripts/stale_triage.py --days 5        orange age cutoff (default 5 days)
    python scripts/stale_triage.py --stale 10      any-verdict age cutoff (default 10 days)
    python scripts/stale_triage.py --apply         skip every candidate via log.py
    python scripts/stale_triage.py --apply --keep 12,45   ...except these row numbers

A triaged row is a skip candidate when ANY of these hold:
  - verdict red (never built, per D)
  - live = closed
  - verdict orange and triaged --days+ days ago (light-build tier that never got picked)
  - any verdict triaged --stale+ days ago (rule: only triage what the candidate will build that week)
Green/yellow rows younger than --stale stay. Nothing changes without --apply, and every change
goes through `log.py set <#> skipped --note "Stale triage: <reason>"`, so seen.txt stays current
and the row moves to archive.json on the next `log.py archive`.
"""
import subprocess
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


def arg(name, default):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def reason(r, days, stale):
    v, a = (r.get("verdict") or "").rstrip("?"), age(r.get("status_date") or r.get("added"))
    if r.get("live") == "closed":
        return "posting closed"
    if v == "red":
        return "red verdict"
    if a is not None and a >= stale:
        return f"{a}d old, not built"
    if v == "orange" and a is not None and a >= days:
        return f"orange, {a}d old"
    return None


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        sys.exit(0)
    days, stale = int(arg("--days", 5)), int(arg("--stale", 10))
    keep = {int(x) for x in arg("--keep", "").split(",") if x.strip()}
    rows = load_json(APPS)["applications"]
    cands, stays = [], []
    for i, r in enumerate(rows):
        if r.get("status") != "triaged":
            continue
        why = reason(r, days, stale)
        (cands if why and i not in keep else stays).append((i, r, why or "keep"))

    def show(i, r, why):
        print(f"  #{i:<4} {(r.get('verdict') or '-'):<7} {r.get('company')} - {(r.get('role') or '')[:60]}  [{why}]")

    by = {}
    for c in cands:
        by.setdefault(c[2].split(",")[0].split(" ")[0] if "old" in c[2] else c[2], []).append(c)
    print(f"🧹 Skip candidates: {len(cands)} of {len(cands) + len(stays)} triaged")
    for k, grp in by.items():
        print(f"\n{k} ({len(grp)})")
        for c in grp:
            show(*c)
    print(f"\n✅ Stays triaged: {len(stays)}")
    for c in sorted(stays, key=lambda c: (c[1].get("verdict") != "green", c[0])):
        show(*c)

    if "--apply" not in sys.argv:
        print("\nPreview only. Add --apply (and --keep 12,45 to protect rows) to skip them.")
        sys.exit(0)
    log = str(Path(__file__).parent / "log.py")
    done = 0
    for i, r, why in cands:  # `set` never reorders rows, so row numbers stay valid
        res = subprocess.run([sys.executable, log, "set", str(i), "skipped", "--note", f"Stale triage: {why}"],
                             capture_output=True, text=True)
        if res.returncode == 0:
            done += 1
        else:
            print(f"  ⚠ #{i} failed: {(res.stdout + res.stderr).strip()[:200]}")
    print(f"\nSkipped {done}/{len(cands)}. Run `log.py archive` to move them out, then sync.py.")
