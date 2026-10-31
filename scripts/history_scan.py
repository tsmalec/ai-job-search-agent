"""history_scan.py — scan EVERY commit reachable from any ref for personal data, not just the working tree.

Why: a squashed main branch can still serve old commits through other refs (GitHub keeps refs/pull/N/head
forever, Dependabot PRs included). sync.py's guard only sees the current tree.

Usage (from the repo root):
    git fetch origin '+refs/pull/*/head:refs/remotes/pr/*'    first, so PR refs are scanned too
    python scripts/history_scan.py

Checks every reachable commit (author, committer, message, time zone) and every text blob for:
  - real-looking emails and phone numbers (same rules as sync.py's public-repo guard)
  - commit time zones other than +0000 (they place the author)
  - PII_DENYLIST: optional env var, one case-insensitive regex per line (your name, city, employers...).
    Keep it in a GitHub Actions secret, never in the repo. Matches are reported by pattern number, not text.
Exit 1 when anything is found.
"""
import os, re, subprocess, sys
from pathlib import Path

# Same rules as the public sync.py guard, kept here so this script needs nothing else from the repo.
OK_MAIL = re.compile(r"^noreply@github\.com$|@(?:example\.(?:com|org)|b\.com|co\.com|users\.noreply\.github\.com|"
                     r"anthropic\.com|[\w-]+\.greenhouse-mail\.io|(?:[\w-]+\.)*[\w-]+\.(?:example|test|invalid|localhost))$", re.I)
MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.I)
PHONE = re.compile(r"(?<![\d.])\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?![\d.])"
                   r"|(?<!\d)\+1[-. ]?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}(?!\d)"
                   r"|(?<![\w.$,#/=?-])[2-9]\d{2}[2-9]\d{6}(?![\w.])")


def contact_hit(text):
    """First real-looking email or phone number in text, else None (sample/test addresses and 555-01xx pass)."""
    return (next((x for x in MAIL.findall(text) if not OK_MAIL.search(x)), None)
            or next((x for x in PHONE.findall(text) if "555-01" not in x), None))

TEXT = (".md", ".txt", ".json", ".py", ".csv", ".html", ".yml", ".yaml", ".toml", ".cfg", ".ini", "")


def git(*a, **kw):
    return subprocess.run(["git", *a], capture_output=True, check=True, **kw).stdout  # the repo you run it in


def denylist():
    pats = [p.strip() for p in (os.environ.get("PII_DENYLIST") or "").splitlines() if p.strip()]
    return [re.compile(p, re.I) for p in pats]


def check(text, where, deny, out):
    hit = contact_hit(text)
    if hit:
        out.append(f"{where}: contact-like text ({hit})")
    for n, p in enumerate(deny, 1):
        if p.search(text):
            out.append(f"{where}: matches denylist pattern #{n}")


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__.strip())
        return 0
    deny, found = denylist(), []
    log = git("log", "--all", "--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%ai%x00%ci%x00%B%x01", text=True)
    commits = [c for c in log.split("\x01") if c.strip()]
    for c in commits:
        sha, author, committer, ad, cd, msg = c.strip("\n").split("\x00", 5)
        for label, stamp in (("author", ad), ("committer", cd)):
            if not stamp.endswith("+0000"):
                found.append(f"commit {sha[:8]}: {label} time zone {stamp[-5:]} (use +0000)")
        check(f"{author}\n{committer}\n{msg}", f"commit {sha[:8]} metadata", deny, found)
    objs = git("rev-list", "--all", "--objects", text=True).splitlines()
    blobs = {}
    for line in objs:
        sha, _, path = line.partition(" ")
        if path and Path(path).suffix.lower() in TEXT:
            blobs.setdefault(sha, path)
    batch = git("cat-file", "--batch", input="\n".join(blobs).encode() + b"\n")
    i = 0
    while i < len(batch):
        nl = batch.index(b"\n", i)
        sha, kind, size = batch[i:nl].decode().split()
        body = batch[nl + 1:nl + 1 + int(size)]
        i = nl + 1 + int(size) + 1
        if kind == "blob":
            check(body.decode("utf-8", "replace"), f"{blobs[sha]} @ blob {sha[:8]}", deny, found)
    refs = git("for-each-ref", "--format=%(refname)", text=True).split()
    print(f"history_scan: {len(commits)} commit(s), {len(blobs)} text blob(s), {len(refs)} ref(s), "
          f"{len(deny)} denylist pattern(s)")
    for f in found[:200]:
        print("  ✗ " + f)
    if found:
        print(f"{len(found)} finding(s). Rewrite or remove the history that holds them before publishing.")
        return 1
    print("✓ clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
