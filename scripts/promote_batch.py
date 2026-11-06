"""promote_batch.py - move a whole screen's leads out of leads.json from one plan file.

Usage (from the repo root):
    python scripts/promote_batch.py --template          write work/promote_plan.json: one entry per open lead, blanks to fill
    python scripts/promote_batch.py work/promote_plan.json            check the plan only (nothing changes)
    python scripts/promote_batch.py work/promote_plan.json --apply    check, then promote every entry

Each plan entry is {"url", "status", "live", "note", "verdict"}: status triaged|skipped|passed, live open|closed|uncertain,
note = the reason, in words ("read from saved text" is fine; "unread" only when no posting text was saved),
verdict = the preliminary fit ending in "?" (green?, yellow?, orange?, red?) or blank to keep the lead's own.
The plan is judged by you, the script only does the plumbing: it refuses a plan that misses an open lead, repeats
a URL, names a URL that is not open, or leaves a field blank, and nothing is promoted until the whole plan checks.
Each promote runs through log.py itself (same rules: the note can't contradict the saved read), so data rows still
change only through log.py. Ends with the leads still open (should be none) and the screen_done.py line.
"""
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
LEADS = ROOT / "data" / "leads.json"
PLAN = ROOT / "work" / "promote_plan.json"
STATUS = {"triaged", "skipped", "passed"}
LIVE = {"open", "closed", "uncertain"}
VERDICT = {"green?", "green/yellow?", "yellow?", "orange?", "red?"}  # a final verdict needs a read and --jd, so only preliminary ones here


def key(u):
    return (u or "").strip().rstrip("/")


def open_leads(path=None):
    d = json.loads(Path(path or LEADS).read_text(encoding="utf-8"))
    return [l for l in d.get("leads", []) if l.get("status") == "not_triaged"]


def check(plan, leads):
    """Every problem with the plan, as a list of strings (empty = good to apply)."""
    errs, seen = [], set()
    want = {key(l.get("url")): l for l in leads}
    if not isinstance(plan, list):
        return ["the plan must be a JSON list of entries"]
    for n, e in enumerate(plan, 1):
        u = key(e.get("url")) if isinstance(e, dict) else ""
        tag = f"entry {n} ({u or 'no url'})"
        if not u:
            errs.append(f"{tag}: no url"); continue
        if u in seen:
            errs.append(f"{tag}: url repeated")
        seen.add(u)
        if u not in want:
            errs.append(f"{tag}: not an open lead")
        if e.get("status") not in STATUS:
            errs.append(f"{tag}: status must be one of {sorted(STATUS)}")
        if e.get("live") not in LIVE:
            errs.append(f"{tag}: live must be one of {sorted(LIVE)}")
        if (e.get("verdict") or "") and e["verdict"] not in VERDICT:
            errs.append(f"{tag}: verdict must be blank or one of {sorted(VERDICT)} (a final verdict is logged after the owner's go)")
        if not (e.get("note") or "").strip():
            errs.append(f"{tag}: note is empty")
        if e.get("live") == "closed" and e.get("status") not in ("skipped", "passed"):
            errs.append(f"{tag}: a closed posting is skipped or passed, not triaged")
    for u, l in want.items():
        if u not in seen:
            errs.append(f"missing: {l.get('company')} - {l.get('role')} ({u or 'no url'})")
    return errs


def template(leads, table_path=None):
    rows = {}
    tp = Path(table_path or ROOT / "work" / "screen_table.json")
    if tp.exists():
        try:
            for r in json.loads(tp.read_text(encoding="utf-8")):
                rows[key(r.get("url"))] = r
        except (ValueError, AttributeError):
            pass  # unreadable or oddly shaped table: treat as no saved rows
    out = []
    for l in leads:
        r = rows.get(key(l.get("url")), {})
        out.append({"url": l.get("url"), "company": l.get("company"), "role": l.get("role"),
                    "screen": r.get("verdict", ""), "read": r.get("read", ""),
                    "status": "", "live": "", "verdict": "", "note": ""})
    return out


def promote(e):
    """One promote through log.py; returns '' on success or the refusal text."""
    import log
    try:
        args = ["promote", e["url"], "--status", e["status"], "--live", e["live"], "--note", e["note"]]
        if e.get("verdict"):
            args += ["--verdict", e["verdict"]]
        log.main(args)
    except SystemExit as x:
        return str(x.code) if x.code not in (None, 0) else ""
    return ""


def main(argv, apply=promote):
    if "--template" in argv:
        leads = open_leads()
        PLAN.parent.mkdir(exist_ok=True)
        PLAN.write_text(json.dumps(template(leads), indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"Wrote {PLAN.relative_to(ROOT)}: {len(leads)} open leads. Fill status, live and note on each.")
        return 0
    paths = [a for a in argv if not a.startswith("--")]
    if len(paths) != 1:
        print(__doc__); return 2
    plan = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
    errs = check(plan, open_leads())
    if errs:
        print("Plan refused, nothing changed:\n  " + "\n  ".join(errs)); return 1
    if "--apply" not in argv:
        print(f"Plan OK: {len(plan)} entries cover every open lead. Add --apply to promote them."); return 0
    bad = []
    for e in plan:
        why = apply(e)
        if why:
            bad.append(f"{e['url']}: {why}")
    left = open_leads()
    print(f"Promoted {len(plan) - len(bad)} of {len(plan)}; still open: {len(left)}")
    for b in bad:
        print("  refused: " + b)
    return 1 if bad else 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    os.system(f'"{sys.executable}" "{ROOT / "scripts" / "screen_done.py"}" | tail -1') if code == 0 and "--apply" in sys.argv else None
    sys.exit(code)
