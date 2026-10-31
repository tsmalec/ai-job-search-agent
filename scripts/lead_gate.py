"""lead_gate.py — keep already-logged postings out of the scheduled agent's inbox.

Usage (from the repo root):
    python scripts/lead_gate.py               mark every not_triaged lead that's already logged; save leads.json
    python scripts/lead_gate.py --dry-run     show what it would mark; save nothing
    python scripts/lead_gate.py --check       exit 1 if any not_triaged lead is already logged (guard_rows.py, CI)
    python scripts/lead_gate.py --no-notes    don't add repost notes to applications.json (scheduled agent runs)

What counts as already logged (common.LoggedIndex): same URL with tracking params stripped, same posting ID
(6+ digit runs, UUIDs, R/JR/RQ req IDs; the same ID on two hosts matches), or same
company + normalized title. Checked against applications.json, archive.json, seen.txt and every earlier lead
in leads.json (the oldest copy of an in-inbox duplicate stays).

A dupe is never deleted (scheduled agent protocol: no deletions; guard_rows.py allows only this edit): its status becomes
"dropped_by_agent" with a "Gate:" note naming the row it matched. A repost (same company + title, same site,
new posting ID) is dropped the same way with a repost flag, and the logged row it repeats gets a
"Repost seen" note (skip with --no-notes), so it shows in reposts.py and triage instead of as a new lead.

sync.py runs this before every commit; the scheduled agent runs it before guard_rows.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import APPS, LEADS, load_json, logged_index, save_json, today, url_key, write_seen  # noqa: E402


def gate(leads, idx):
    """Mark logged leads in place. Returns [(lead, kind, label, how)] for every lead it marked.
    Leads are indexed oldest first (the scheduled agent adds to the top), so the oldest copy of an inbox dupe survives."""
    marked = []
    for n in range(len(leads) - 1, -1, -1):
        l = leads[n]
        if not isinstance(l, dict):
            continue
        hit = idx.find(l.get("company"), l.get("role"), l.get("url")) if l.get("status") == "not_triaged" else None
        if hit:
            kind, label, how = hit
            why = (f"Gate {today()}: repost of {label} ({how})" if kind == "repost"
                   else f"Gate {today()}: already logged as {label} ({how})")
            l["status"] = "dropped_by_agent"
            l["note"] = f"{l['note']} | {why}" if l.get("note") else why
            marked.append((l, kind, label, how))
        else:
            idx.add(l.get("company"), l.get("role"), l.get("url"), f"lead: {l.get('company')} ({l.get('source', '')})")
    return marked


def note_reposts(marked):
    """Add 'Repost seen <date>: <url>' to each applications.json row a repost repeats. Returns rows noted."""
    reps = [(l, label) for l, kind, label, _ in marked if kind == "repost" and label.startswith("#")]
    if not reps:
        return 0
    data = load_json(APPS)
    apps, n = data["applications"], 0
    for l, label in reps:
        i = int(label[1:].split()[0])
        if i >= len(apps) or url_key(l.get("url") or "") in (apps[i].get("note") or ""):
            continue
        add = f"{today()}: Repost seen: {url_key(l.get('url') or '')}"
        apps[i]["note"] = f"{apps[i]['note']} | {add}" if apps[i].get("note") else add
        n += 1
    if n:
        data["updated"] = today()
        save_json(APPS, data)
        write_seen()
    return n


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__.strip())
        return 0
    data = load_json(LEADS)
    marked = gate(data["leads"], logged_index())
    for l, kind, label, how in marked:
        icon = "🔁" if kind == "repost" else "♻️"
        print(f"{icon} {l.get('company')} - {(l.get('role') or '')[:60]} → {label} ({how})")
    if "--check" in argv:
        if marked:
            print(f"✗ {len(marked)} not_triaged lead(s) are already logged. Run: python3 scripts/lead_gate.py")
            return 1
        print("✓ No logged postings in the inbox.")
        return 0
    if "--dry-run" in argv:
        print(f"Dry run: {len(marked)} lead(s) would be marked. Nothing saved.")
        return 0
    if marked:
        data["updated"] = today()
        save_json(LEADS, data)
        noted = 0 if "--no-notes" in argv else note_reposts(marked)
        print(f"Marked {len(marked)} lead(s) dropped_by_agent"
              + (f"; repost noted on {noted} application row(s)" if noted else ""))
    else:
        print("Lead gate: no logged postings in the inbox.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
