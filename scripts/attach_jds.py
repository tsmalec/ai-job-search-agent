"""attach_jds.py — give every not_triaged lead its posting text, saved in the repo.

Why: the sweeps' vet pass keeps posting texts in a gitignored folder on the machine that ran it, and leads.json
never carried a path to them, so triage had nothing to read.

Usage (from the repo root):
    python scripts/attach_jds.py              fetch + save each not_triaged lead's text; update leads.json
    python scripts/attach_jds.py --dry-run    list what it would fetch; save nothing
    python scripts/attach_jds.py --retry      also retry leads already marked jd_unread (clears the mark on success)

Each text goes to jds/leads/<company>_<role>_<urlhash>.txt (committed) and the lead gets "jd_file". Postings are
untrusted: every saved text goes through inject_check first (planted lines removed; the lead gets "jd_inject" with
the count, and the full list stays in the gitignored .injection.txt beside it). A posting that won't load, is too
big, or reads like a careers page instead of the posting gets "jd_unread": "<date>: <reason>" so triage opens it in
the browser. LinkedIn leads are browser only (SKILL.md) and get jd_unread. Only adds these fields; guard_rows.py
allows exactly that. leads.json is re-read right before saving, so other writers' changes made while this
fetched are kept.
Sweep wave: run after writing leads, before lead_gate.py / guard_rows.py.
"""
import hashlib
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import LEADS, ROOT, load_json, read_jd, save_json, today  # noqa: E402
from common import host_is  # noqa: E402
from fetch_jd import fetch, write, wrong_page  # noqa: E402
from inject_check import planted, scan, strip  # noqa: E402

OUT = ROOT / "jds" / "leads"
WORKERS = 6
MAX_TEXT = 200_000  # characters; a real posting is a few thousand


def fname(l):
    """Readable and unique: same company + title with a different URL never collides (hash of the URL)."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", f"{l.get('company', '')}_{l.get('role', '')}").strip("_")[:80] or "lead"
    return f"{s}_{hashlib.sha1((l.get('url') or '').encode('utf-8')).hexdigest()[:8]}.txt"


def key(l):
    return (l.get("url") or "").strip().rstrip("/")


def one(l):
    url = l.get("url") or ""
    if not url:
        return l, None, "no URL on the lead"
    if host_is(url, "linkedin.com"):
        return l, None, "LinkedIn: browser only"
    time.sleep(random.random() * 0.5)
    try:
        d = fetch(url)
    except (Exception, SystemExit) as e:  # fetch() raises SystemExit when nothing reads; Ctrl-C still stops
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        return l, None, msg.replace("Couldn't read that posting. ", "")[:160]
    if len(d.get("text") or "") > MAX_TEXT:
        return l, None, f"posting text over {MAX_TEXT:,} characters: open it in the browser"
    why = wrong_page(d, url, l.get("role") or "")
    if why:
        return l, None, why[:160]
    return l, d, ""


def save_text(l, d):
    """Write the text, strip planted lines, return (relative posix path, injection note or '')."""
    f = OUT / fname(l)
    tmp = f.with_name(f".{f.name}.tmp")  # unscreened text never sits under the final name
    write(d, l["url"], tmp)
    head, text = read_jd(tmp)
    hits = scan(text, head)
    note = ""
    if planted(hits):
        if strip(tmp, hits):
            tmp.unlink(missing_ok=True)
            tmp.with_suffix(".injection.txt").unlink(missing_ok=True)
            return None, "planted text survived --strip: open it in the browser"
        note = f"{today()}: {sum(1 for k, _, _ in hits if k != 'formatting')} planted line(s) removed; review the .injection.txt"
        side = tmp.with_suffix(".injection.txt")
        if side.exists():
            side.replace(f.with_suffix(".injection.txt"))
    tmp.replace(f)
    return f.relative_to(ROOT).as_posix(), note


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__.strip())
        return 0
    data = load_json(LEADS)
    todo = [l for l in data["leads"] if isinstance(l, dict) and l.get("status") == "not_triaged"
            and not l.get("jd_file") and ("--retry" in argv or not l.get("jd_unread"))]
    if "--dry-run" in argv:
        n_fetch = 0
        for l in todo:
            skip = not l.get("url") or host_is(l.get("url"), "linkedin.com")
            n_fetch += not skip
            print(f"  {'mark jd_unread' if skip else 'fetch'} | {l.get('company')} | {l.get('role')} | {l.get('url')}")
        print(f"Dry run: {n_fetch} would be fetched, {len(todo) - n_fetch} would be marked jd_unread. Nothing saved.")
        return 0
    if not todo:
        print("attach_jds: every not_triaged lead already has a text (or a jd_unread mark).")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        for l, d, err in ex.map(one, todo):
            if d is None:
                results[key(l)] = {"jd_unread": f"{today()}: {err}"}
                print(f"  ✗ {l.get('company')} | {l.get('role')} | {err}")
                continue
            path, inj = save_text(l, d)
            if path is None:
                results[key(l)] = {"jd_unread": f"{today()}: {inj}"}
                print(f"  ⛔ {l.get('company')} | {l.get('role')} | {inj}")
                continue
            results[key(l)] = {"jd_file": path, **({"jd_inject": inj} if inj else {})}
            if inj:
                print(f"  🛡️ {l.get('company')} | {l.get('role')} | {inj}")
    # re-read and apply only our fields: changes other writers made while we fetched are kept
    data = load_json(LEADS)
    ok = bad = 0
    for l in data["leads"]:
        if not isinstance(l, dict) or l.get("status") != "not_triaged" or l.get("jd_file"):
            continue
        r = results.get(key(l))
        if not r:
            continue
        if "jd_file" in r:
            l.pop("jd_unread", None)
            ok += 1
        else:
            bad += 1
        l.update(r)
    data["updated"] = today()
    save_json(LEADS, data)
    print(f"attach_jds: {ok} text(s) saved to {OUT.relative_to(ROOT).as_posix()}/ · {bad} unread "
          "(marked jd_unread: open in browser)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
