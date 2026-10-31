"""proposals.py — local/other models propose edits; the candidate approves; only then do they apply.

    python scripts/proposals.py new --target B_Bullet_Catalog.md --why "..." --find "old" --replace "new"
    python scripts/proposals.py new --target B_Bullet_Catalog.md --why "..." --append "text to add at the end"
    python scripts/proposals.py new --why "..." --command "python scripts/log.py set acme applied"
    python scripts/proposals.py list [--all]
    python scripts/proposals.py show P001
    python scripts/proposals.py approve P001      # ONLY after the candidate says yes
    python scripts/proposals.py reject P001 "why"

Files: data/proposals/P###.json. Targets limited to B, C, D, E, 07 and log.py commands (data rows
always go through log.py). Approve checks the find text still occurs exactly once.
"""
import argparse, json, shlex, subprocess, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "proposals"
TARGETS = {"B_Bullet_Catalog.md", "C_Gaps.md", "D_Fit_Calibration.md", "E_Style_ATS_Layout.md", "07_Interview_Story_Bank.md"}


def load(pid):
    f = DIR / f"{pid.upper()}.json"
    if not f.exists():
        sys.exit(f"No proposal {pid}.")
    return f, json.loads(f.read_text(encoding="utf-8"))


def save(f, p):
    f.write_text(json.dumps(p, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def all_props():
    return [(f, json.loads(f.read_text(encoding="utf-8"))) for f in sorted(DIR.glob("P*.json"))]


SAFE_LOG = {"add", "set", "note", "find", "promote"}  # red team: single-row edits only
UNSAFE_FLAGS = ("--allow-drop", "--all", "--force", "--public-ok")


def command_problem(cmd):
    """Why a proposed command may not run ('' = fine): scripts/log.py only, one single-row subcommand, no bulk or
    override flags. Proposals come from models that may have read injected text, so this is checked at approve too."""
    try:
        a = shlex.split(cmd)
    except ValueError:
        return "unparseable command"
    if len(a) < 3 or a[0] not in ("python", "python3") or a[1] != "scripts/log.py":
        return "commands are limited to scripts/log.py"
    if a[2] not in SAFE_LOG:
        return f"log.py {a[2]} isn't allowed in a proposal (allowed: {', '.join(sorted(SAFE_LOG))})"
    if any(x.split("=")[0] in UNSAFE_FLAGS for x in a):
        return "bulk/override flags aren't allowed in a proposal"
    return ""


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("new"); n.add_argument("--target"); n.add_argument("--why", required=True)
    n.add_argument("--find"); n.add_argument("--replace"); n.add_argument("--append"); n.add_argument("--command")
    n.add_argument("--by", default="local model")
    l = sub.add_parser("list"); l.add_argument("--all", action="store_true")
    s = sub.add_parser("show"); s.add_argument("id")
    a = sub.add_parser("approve"); a.add_argument("id")
    r = sub.add_parser("reject"); r.add_argument("id"); r.add_argument("why", nargs="?", default="")
    x = ap.parse_args()
    DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="minutes")

    if x.cmd == "new":
        if x.command:
            bad = command_problem(x.command)
            if bad:
                sys.exit(bad)
            kind = "command"
        else:
            if x.target not in TARGETS:
                sys.exit(f"Target must be one of {sorted(TARGETS)} (data rows go through --command log.py).")
            if x.append is None and (x.find is None or x.replace is None):
                sys.exit("Give --find + --replace, or --append.")
            kind = "append" if x.append is not None else "replace"
        nums = [int(f.stem[1:]) for f, _ in all_props()]
        pid = f"P{(max(nums) + 1) if nums else 1:03d}"
        p = dict(id=pid, kind=kind, target=x.target, find=x.find, replace=x.replace, append=x.append,
                 command=x.command, why=x.why, by=x.by, created=now, status="pending")
        save(DIR / f"{pid}.json", p)
        print(f"{pid} proposed ({kind}). the candidate approves with: python scripts/proposals.py approve {pid}")
    elif x.cmd == "list":
        rows = [p for _, p in all_props() if x.all or p["status"] == "pending"]
        if not rows:
            print("No pending proposals.")
        for p in rows:
            print(f"{p['id']}  {p['status']:9} {p['kind']:8} {p.get('target') or 'log.py'}  — {p['why'][:80]}")
    elif x.cmd == "show":
        print(json.dumps(load(x.id)[1], indent=2, ensure_ascii=False))
    elif x.cmd == "approve":
        f, p = load(x.id)
        if p["status"] != "pending":
            sys.exit(f"{p['id']} is already {p['status']}.")
        if p["kind"] == "command":
            bad = command_problem(p["command"])
            if bad:
                sys.exit(f"{p['id']} refused: {bad}")
            argv = shlex.split(p["command"])[1:]
            print("Running exactly: python " + " ".join(shlex.quote(a) for a in argv))
            res = subprocess.run([sys.executable] + argv, cwd=ROOT)
            if res.returncode:
                sys.exit(f"Command failed; {p['id']} left pending.")
        else:
            t = ROOT / p["target"]
            text = t.read_text(encoding="utf-8")
            if p["kind"] == "replace":
                c = text.count(p["find"])
                if c != 1:
                    sys.exit(f"Find text occurs {c} times in {p['target']}; {p['id']} left pending.")
                text = text.replace(p["find"], p["replace"])
            else:
                text = text.rstrip("\n") + "\n" + p["append"].rstrip("\n") + "\n"
            t.write_text(text, encoding="utf-8", newline="\n")
        p.update(status="approved", decided=now)
        save(f, p)
        print(f"{p['id']} applied. Push with sync.py.")
    elif x.cmd == "reject":
        f, p = load(x.id)
        p.update(status="rejected", decided=now, reject_why=x.why)
        save(f, p)
        print(f"{p['id']} rejected.")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
