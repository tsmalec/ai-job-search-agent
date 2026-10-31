"""backlog.py — the to-do tracker in BACKLOG.md, so approved changes actually get made.

Usage (from the repo root):
    python scripts/backlog.py                        open items, overdue first
    python scripts/backlog.py --overdue              one-line overdue summary (sync.py prints this)
    python scripts/backlog.py done B07 "what shipped"
    python scripts/backlog.py drop B07 "why"
    python scripts/backlog.py ok B07                 the candidate approved a needs-user item -> open
    python scripts/backlog.py add "item" --due 2026-11-03 --group batch [--needs-user]

Status lives in the table's last column. "what shipped" / "why" go in the commit message via sync.py.
"""
import re, sys
from datetime import date
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "BACKLOG.md"
ROW = re.compile(r"^\| (B\d+) \| (.*) \| (\w[\w-]*) \| (\d{4}-\d{2}-\d{2}) \| (.*) \|$")


def load():
    lines = FILE.read_text(encoding="utf-8").splitlines()
    rows = {}
    for i, line in enumerate(lines):
        m = ROW.match(line)
        if m:
            rows[m.group(1)] = dict(i=i, id=m.group(1), item=m.group(2), group=m.group(3),
                                    due=m.group(4), status=m.group(5).strip())
    return lines, rows


def save(lines):
    FILE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def set_status(rid, status):
    lines, rows = load()
    r = rows.get(rid.upper())
    if not r:
        raise SystemExit(f"No backlog item {rid}.")
    lines[r["i"]] = f"| {r['id']} | {r['item']} | {r['group']} | {r['due']} | {status} |"
    save(lines)
    print(f"{r['id']} → {status}: {r['item'][:90]}")


def is_open(r):
    return r["status"] in ("open", "needs-user")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    a = sys.argv[1:]
    today = date.today().isoformat()
    if a and a[0] == "done":
        # recurring chores ("(every 7d)" in the item) re-open with the next due date
        _, rows = load()
        r = rows.get(a[1].upper()) or {}
        m = re.search(r"\(every (\d+)d\)", r.get("item", ""))
        if m:
            from datetime import timedelta
            lines, rows = load()
            nxt = (date.today() + timedelta(days=int(m.group(1)))).isoformat()
            lines[r["i"]] = f"| {r['id']} | {r['item']} | {r['group']} | {nxt} | open |"
            save(lines)
            print(f"{r['id']} done {today}; next due {nxt}: {r['item'][:80]}")
        else:
            set_status(a[1], f"done {today}")
    elif a and a[0] == "drop":
        set_status(a[1], f"dropped {today}")
    elif a and a[0] == "ok":
        set_status(a[1], "open")
    elif a and a[0] == "add":
        lines, rows = load()
        nid = f"B{max([int(k[1:]) for k in rows] or [0]) + 1:02d}"
        due = a[a.index("--due") + 1] if "--due" in a else today
        group = a[a.index("--group") + 1] if "--group" in a else "batch"
        status = "needs-user" if "--needs-user" in a else "open"
        last = max(r["i"] for r in rows.values())
        lines.insert(last + 1, f"| {nid} | {a[1]} | {group} | {due} | {status} |")
        save(lines)
        print(f"Added {nid} (due {due}, {status}).")
    else:
        _, rows = load()
        live = sorted((r for r in rows.values() if is_open(r)), key=lambda r: (r["due"], r["id"]))
        late = [r for r in live if r["due"] < today]
        due_now = [r for r in live if r["due"] == today]
        if "--overdue" in a:
            if late or due_now:
                print(f"📋 Backlog: {len(late)} overdue, {len(due_now)} due today "
                      f"({', '.join(r['id'] for r in late + due_now)}). Run: python scripts/backlog.py")
            sys.exit(0)
        for label, group in (("⚠️ OVERDUE", late), ("📌 DUE TODAY", due_now),
                             ("Upcoming", [r for r in live if r["due"] > today])):
            if group:
                print(f"\n{label}")
                for r in group:
                    tag = " [needs the candidate]" if r["status"] == "needs-user" else ""
                    print(f"  {r['id']} {r['due']} {r['group']:<6} {r['item'][:95]}{tag}")
        print(f"\n{len(live)} open · {sum(not is_open(r) for r in rows.values())} closed")
