"""sync.py — pull, commit and push the repo in one step.

Usage (from the repo root):
    python scripts/sync.py                    just pull the latest (do this before you start)
    python scripts/sync.py "Logged Acme applied"   pull, then commit everything and push

Pull uses --rebase so the scheduler's and Claude's pushes stack cleanly. If a rebase conflict happens,
it stops and tells you; nothing is forced. Exception: a conflict only on data files is rebuilt on
GitHub's latest by replaying this clone's journaled log.py calls.

Row guard: before pushing, every leads.json row on GitHub must still be
in leads.json, or have moved to applications.json / archive.json, or be listed in leads.json "dropped_by_agent"; every
applications.json row must still be there or be in archive.json. Matched on company + role. A file that won't parse
or comes back empty while GitHub has rows also stops the push. The commit stays local; nothing is pushed.
    python scripts/sync.py --allow-drop "Removed 3 duplicate leads"   push anyway when rows were removed on purpose

Public-repo guard: if the GitHub remote is publicly readable (or can't be checked) and the tree holds real
data (B/C/07/form answers no longer the Alex sample, a real email, non-sample rows, files in records/ or jds/,
contact-like text), nothing is committed or pushed. Make the repo private first.
    python scripts/sync.py --public-ok "<message>"   override, only for a scrubbed template
"""
import json
import os, re
import subprocess, sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))
from common import existing  # noqa: E402  (a lead closed into a row with the same posting isn't 'lost')
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", str(t or "").lower()).strip()


def rows_at(ref, path, key):
    """Rows of a data file at a git ref (None if the file isn't there; raises on a broken file)."""
    r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        return None
    return json.loads(r.stdout).get(key, [])


DOC_TYPES = {".docx", ".doc", ".pdf", ".rtf", ".odt", ".xlsx", ".xls", ".pptx", ".tex", ".eml", ".msg", ".ics", ".vcf"}
OK_MAIL = re.compile(r"^noreply@github\.com$|@(?:example\.(?:com|org)|b\.com|co\.com|users\.noreply\.github\.com|anthropic\.com|"
                     r"[\w-]+\.greenhouse-mail\.io|(?:[\w-]+\.)*[\w-]+\.(?:example|test|invalid|localhost))$", re.I)
MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.I)
PHONE = re.compile(r"(?<![\d.])\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?![\d.])"
                   r"|(?<!\d)\+1[-. ]?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}(?!\d)"
                   r"|(?<![\w.$,#/=?-])[2-9]\d{2}[2-9]\d{6}(?![\w.])")  # +1 and run-together 10-digit numbers


def contact_hit(text):
    """First real-looking email or phone number in text, else None (sample/test addresses and 555-01xx pass)."""
    return next((x for x in MAIL.findall(text) if not OK_MAIL.search(x)), None) or \
        next((x for x in PHONE.findall(text) if "555-01" not in x), None)


def personal_data():
    """Reasons this tree holds real (non-sample) data. Empty = still the Alex Example template."""
    why = []
    def txt(rel):
        f = ROOT / rel
        return f.read_text(encoding="utf-8") if f.exists() else ""
    for rel in ("B_Bullet_Catalog.md", "C_Gaps.md", "07_Interview_Story_Bank.md", "data/form_answers.json"):
        t = txt(rel)
        if t and "TEMPLATE-SAMPLE" not in t and "_template_sample" not in t:
            # red team: a name check ("Alex") let real files that mention an Alex pass. The marker line is
            # deleted on purpose when real data goes in (START_HERE.md), so its absence = real data.
            why.append(f"{rel} no longer holds the sample (no TEMPLATE-SAMPLE marker)")
    try:
        fa = json.loads(txt("data/form_answers.json") or "{}")
        email = (fa.get("contact") or {}).get("email") or ""
        if email and not email.endswith("@example.com"):
            why.append("form_answers.json has a real email")
    except ValueError:
        why.append("form_answers.json unreadable")
    for rel, key in (("data/applications.json", "applications"), ("data/archive.json", "applications"),
                     ("data/leads.json", "leads")):
        try:
            rows = json.loads(txt(rel) or "{}").get(key, [])
        except ValueError:
            rows = [None]
        if any("example.com" not in str((r or {}).get("url", "")) for r in rows):
            why.append(f"{rel} has non-sample rows")
    for d in ("records", "jds"):
        extra = [f.name for f in (ROOT / d).rglob("*") if f.is_file() and f.name not in ("README.md", ".gitkeep")]
        if extra:
            why.append(f"{d}/ has {len(extra)} file(s)")
    # contact-like text anywhere in the tracked or about-to-be-added tree (heuristic, not a full PII scan)
    r = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tracked = subprocess.run(["git", "ls-files", "--", "config.json", "data/model.json", ".env"], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.split()
    if tracked:  # your rules, contact and keys: never part of the template, whatever they contain
        why.append(f"{', '.join(tracked)} is tracked (it holds your contact, home and pay rules)")
    docs = [x for x in r.stdout.splitlines() if Path(x).suffix.lower() in DOC_TYPES]
    if docs:  # resumes, letters, exports: never part of the template
        why.append(f"document files in the tree ({', '.join(docs[:3])})")
    for rel in r.stdout.splitlines():
        f = ROOT / rel
        if f.suffix.lower() not in (".md", ".txt", ".json", ".py", ".csv", ".html", ".yml", ".yaml", "") or not f.is_file():
            continue
        try:
            t = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hit = contact_hit(t)
        if hit:
            why.append(f"{rel} contains contact-like text ({hit})")
            if len(why) > 8:
                break
    return why


def public_remote():
    """A refusal message when personal data would go to a publicly readable GitHub repo, else ''."""
    why = personal_data()
    if not why:
        return ""
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"github\.com[:/]([^/]+)/([^/.\s]+)", r.stdout)
    if not m:  # not GitHub: visibility can't be checked, so fail closed
        return ("⛔ The remote isn't GitHub, so its visibility can't be checked, and the tree holds real data: "
                + "; ".join(why[:3]) + ". Nothing was committed or pushed. If that remote is private: --public-ok.")
    import urllib.request, urllib.error
    try:  # unauthenticated: a private repo answers 404, a public one 200
        with urllib.request.urlopen(f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}", timeout=10) as resp:
            public = resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 403/429 rate limit, 5xx: visibility unknown, so fail closed
            return (f"⛔ Couldn't verify {m.group(1)}/{m.group(2)} is private (GitHub API HTTP {e.code}) and the tree "
                    "holds real data: " + "; ".join(why[:3]) + ". Nothing was committed or pushed. Retry later, or "
                    "--public-ok for a scrubbed template.")
        public = False  # 404 to an anonymous caller = private (or missing)
    except Exception:
        return ("⛔ Couldn't confirm the repo is private (offline?) and the tree holds real data: "
                + "; ".join(why[:3]) + ". Nothing was committed or pushed. Rerun online, or --public-ok for a scrubbed template.")
    if not public:
        return ""
    return (f"⛔ {m.group(1)}/{m.group(2)} is PUBLIC and the tree holds real data (" + "; ".join(why[:4]) + "). "
            "Nothing was committed or pushed. Make the repo private (GitHub > Settings > Danger Zone > Change "
            "visibility), then rerun. Scrubbed template only: --public-ok.")


def row_guard():
    """Rows on GitHub that this push would lose without a trace. Returns a list of problem lines."""
    problems = []
    try:
        old_leads = rows_at("origin/main", "data/leads.json", "leads")
        old_apps = rows_at("origin/main", "data/applications.json", "applications")
        new_leads = rows_at("HEAD", "data/leads.json", "leads")
        new_apps = rows_at("HEAD", "data/applications.json", "applications")
        new_arch = rows_at("HEAD", "data/archive.json", "applications") or []
        lj = json.loads(subprocess.run(["git", "show", "HEAD:data/leads.json"], cwd=ROOT, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace").stdout or "{}")
        dropped = lj.get("dropped_by_agent", []) + lj.get("dropped_by_muse", [])  # legacy key still read
    except Exception as e:
        return [f"a data file won't parse in this commit: {e}"]
    key = lambda r: (norm(r.get("company")), norm(r.get("role")))
    for name, old, new in (("leads.json", old_leads, new_leads), ("applications.json", old_apps, new_apps)):
        # an emptied inbox is fine when every lead was promoted (checked row by row below);
        # a missing leads file, or an emptied applications file, still stops it.
        if old and (new is None or (not new and name == "applications.json")):
            problems.append(f"{name} is missing or empty in this commit, but GitHub has {len(old)} rows")
    if problems:
        return problems
    moved = {}  # company -> roles in applications/archive (log.py promote appends "(where · pay)" to the role)
    for r in (new_apps or []) + new_arch:
        moved.setdefault(norm(r.get("company")), []).append(norm(r.get("role")))
    kept_leads = {key(r) for r in new_leads or []}
    drop_txt = [norm(x) for x in dropped if isinstance(x, str)]
    for r in old_leads or []:
        k = key(r)
        if k in kept_leads or any(x.startswith(k[1]) for x in moved.get(k[0], [])) \
                or (r.get("url") and existing((new_apps or []) + new_arch, r["url"]) is not None) \
                or any(d.startswith(f"{k[0]} {k[1]}") for d in drop_txt):
            continue
        problems.append(f"lead lost: {r.get('company')} - {r.get('role')}")
    kept_apps, arch = {key(r) for r in new_apps or []}, {key(r) for r in new_arch}
    for r in old_apps or []:
        if key(r) not in kept_apps and key(r) not in arch:
            problems.append(f"application lost: {r.get('company')} - {r.get('role')}")
    return problems


DATA = ["data/applications.json", "data/leads.json", "data/seen.txt", "data/archive.json"]
JOURNAL = ROOT / ".git" / "log_journal.jsonl"
REPLAYING = JOURNAL.with_suffix(".replaying")  # present only while a replay runs; left behind = it was interrupted


def replay():
    """Parallel chats each hit a rebase conflict on the data files and hand-fixed it (making duplicate
    rows). If only data files conflict and every data change came through log.py (journaled), rebuild on GitHub's
    latest: keep our non-data edits, take GitHub's data, re-run our log.py calls, recommit. Else stop as before."""
    conflicted = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.split()
    if not conflicted or any(f not in DATA for f in conflicted) or not JOURNAL.exists():
        return False
    calls = [json.loads(x) for x in JOURNAL.read_text().splitlines() if x.strip()]
    msg = subprocess.run(["git", "log", "-1", "--format=%B", "ORIG_HEAD"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout.strip() or "Replayed log.py changes"
    subprocess.run(["git", "rebase", "--abort"], cwd=ROOT, capture_output=True)
    excl = [f":(exclude){f}" for f in DATA]
    # bytes, not text: text mode turns \n into \r\n on Windows and git apply rejects the patch
    patch = subprocess.run(["git", "diff", "origin/main...HEAD", "--binary", "--", "."] + excl, cwd=ROOT,
                           capture_output=True).stdout
    keep = git("rev-parse", "HEAD")
    # red team: keep a recoverable copy of everything (incl. uncommitted edits) before the hard reset
    subprocess.run(["git", "stash", "push", "-u", "-q", "-m", f"sync replay backup {keep[:7]}"], cwd=ROOT, capture_output=True)
    git("branch", "-f", "sync-replay-backup", keep)
    print(f"  backup: branch sync-replay-backup ({keep[:7]}) + git stash list", flush=True)
    git("reset", "-q", "--hard", "origin/main")
    if patch.strip():
        a = subprocess.run(["git", "apply", "--3way", "-"], cwd=ROOT, input=patch, capture_output=True)
        if a.returncode:
            git("reset", "-q", "--hard", keep)
            print("Replay stopped: non-data changes didn't apply cleanly; back where you were.", flush=True)
            return False
    JOURNAL.replace(REPLAYING)
    failed = []
    for c in calls:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "log.py"), *c], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(f"  replayed: log.py {' '.join(c)[:90]}" + ("" if not r.returncode else f"  ⚠ {r.stderr.strip()[-120:]}"),
              flush=True)
        if r.returncode:
            failed.append(" ".join(c)[:120])
    if failed:  # outside review: a replay that drops a change must never be committed or pushed
        git("reset", "-q", "--hard", keep)
        REPLAYING.replace(JOURNAL)  # log.py re-journals during replay; rename() fails on Windows when it exists
        raise SystemExit("⛔ Replay stopped: these log.py calls failed on GitHub's latest data, so nothing was pushed "
                         "and you're back where you were:\n  " + "\n  ".join(failed)
                         + "\nFix them (the row may have changed on GitHub), then rerun sync.py.")
    REPLAYING.replace(JOURNAL)
    git("add", "-A")
    git("commit", "-q", "-m", msg)
    print("⚙️ Conflict on data files: rebuilt on GitHub's latest and replayed this chat's log.py calls.", flush=True)
    return True


def git(*a, check=True):
    r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(a)} failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    argv = sys.argv[1:]
    allow = ""
    if "--allow-drop" in argv:
        i = argv.index("--allow-drop")
        allow = argv[i + 1] if i + 1 < len(argv) else ""
        if not allow.strip():
            raise SystemExit('--allow-drop needs a reason, e.g. --allow-drop "Removed 3 duplicate leads"')
        del argv[i:i + 2]
    public_ok = "--public-ok" in argv
    argv = [a for a in argv if a != "--public-ok"]
    msg = " ".join(argv).strip()
    if REPLAYING.exists():  # outside review: an interrupted replay must not be built on silently
        raise SystemExit("⛔ A previous sync was interrupted mid-replay. Nothing was changed now.\n"
                         "Your work before it is on branch sync-replay-backup (and in `git stash list`). To recover:\n"
                         "  git reset --hard sync-replay-backup\n"
                         "  python -c \"import pathlib; pathlib.Path('.git/log_journal.replaying').replace('.git/log_journal.jsonl')\"\n"
                         "then rerun sync.py.")
    if msg and public_ok:
        # red team: one flag (which injected text could ask for) turned the whole guard off. Now it also needs
        # JOBSEARCH_PUBLIC_OK=yes in the environment, and never covers records/, jds/, documents or contact info.
        if os.environ.get("JOBSEARCH_PUBLIC_OK") != "yes":
            raise SystemExit("--public-ok also needs JOBSEARCH_PUBLIC_OK=yes set in your shell. Nothing was pushed.")
        hard = [w for w in personal_data() if re.search(r"^(?:records|jds)/|contact-like|document files|real email", w)]
        if hard:
            raise SystemExit("⛔ --public-ok can't cover: " + "; ".join(hard[:4]) + ". Nothing was committed or pushed.")
    if msg and not public_ok:
        why = public_remote()
        if why:
            raise SystemExit(why)
    dirty = git("status", "--porcelain")
    if dirty and msg:
        git("add", "-A")
        git("commit", "-q", "-m", msg)
        print(f"Committed: {msg}")
    elif dirty:
        print("You have uncommitted changes; pulling with them stashed.")
    import time

    def pull():
        for attempt in (1, 2):
            r = subprocess.run(["git", "pull", "--rebase", "--autostash", "-q", "origin", "main"],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if not r.returncode:
                return
            if "429" in r.stderr and attempt == 1:  # session proxy concurrency cap: wait once, retry once
                time.sleep(10)
                continue
            if replay():
                return
            raise SystemExit("Pull hit a conflict. Run `git status` and fix it (or ask Claude):\n" + r.stderr.strip())

    def lead_gate():
        """mark already-logged postings in leads.json (whoever wrote them) before anything is pushed."""
        # rebuild the tricky-remote watchlist so a company joins on its 2nd remote reject
        subprocess.run([sys.executable, str(ROOT / "scripts" / "remote_watch.py")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "lead_gate.py")], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout.strip()
        if r.returncode:  # fail closed (outside review): a broken dedupe check must not wave a push through
            raise SystemExit(f"⛔ lead_gate.py failed, so nothing was pushed: {(r.stderr or out)[-300:]}\n"
                             "Fix it (run python scripts/lead_gate.py --dry-run to see why), then rerun sync.py.")
        elif git("status", "--porcelain", "--", "data/"):
            print(out, flush=True)
            git("add", "-A", "--", "data/")
            git("commit", "-q", "-m", "Lead gate / remote watchlist refresh")
            return True
        return False

    pull()
    print("Pulled latest.")
    gated = lead_gate()
    msg = msg or ("Lead gate" if gated else "")
    if msg:
        # the scheduler or a scheduled task may push between our pull and push: re-pull and retry, up to 3 tries.
        for attempt in (1, 2, 3):
            lost = row_guard()  # re-checked after every pull: the push lands on whatever GitHub has now
            if lost and not allow:
                raise SystemExit("🛑 Push stopped: this commit would lose rows that GitHub has "
                                 f"({len(lost)}):\n  " + "\n  ".join(lost[:40])
                                 + (f"\n  … +{len(lost) - 40} more" if len(lost) > 40 else "")
                                 + "\nNothing was pushed (the commit is local). Restore the rows, or if they were "
                                 "removed on purpose: python scripts/sync.py --allow-drop \"<why>\" \"<message>\"")
            if lost:
                print(f"--allow-drop ({allow}): pushing with {len(lost)} row(s) removed on purpose", flush=True)
            r = subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if not r.returncode:
                print("Pushed.")
                JOURNAL.unlink(missing_ok=True)  # pushed: nothing left to replay
                break
            if attempt == 3:
                raise SystemExit("Push failed 3 times:\n" + r.stderr.strip())
            time.sleep(10 if "429" in r.stderr else 2)
            pull()
            lead_gate()
    print(git("log", "--oneline", "-3"))
    # every pull/push shows what's overdue in BACKLOG.md, so approved changes don't get lost.
    subprocess.run([sys.executable, str(ROOT / "scripts" / "backlog.py"), "--overdue"], cwd=ROOT)
