"""sync.py replay, end to end on real git (outside review): a data-file conflict is rebuilt on the remote's latest,
a failed or interrupted replay never pushes or loses work. Uses a local bare repo as "GitHub"."""
import json, os, shutil, subprocess, sys
import pytest

SRC = os.path.join(os.path.dirname(__file__), "..")
APPS = "data/applications.json"


def sh(*a, cwd, check=True):
    r = subprocess.run(list(a), cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f"{' '.join(a)}\n{r.stdout}\n{r.stderr}")
    return r


def git(cwd, *a, check=True):
    return sh("git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a, cwd=cwd, check=check)


@pytest.fixture
def repos(tmp_path):
    seed = tmp_path / "seed"
    shutil.copytree(os.path.join(SRC, "scripts"), seed / "scripts", ignore=shutil.ignore_patterns("__pycache__", "local"))
    for f in ("config.example.json", "config.json"):
        if os.path.exists(os.path.join(SRC, f)):
            shutil.copy(os.path.join(SRC, f), seed / f)
    (seed / "data").mkdir()
    (seed / "data" / "applications.json").write_text(json.dumps({"applications": []}), newline="\n")
    (seed / "data" / "archive.json").write_text(json.dumps({"applications": []}), newline="\n")
    (seed / "data" / "leads.json").write_text(json.dumps({"leads": []}), newline="\n")
    (seed / "data" / "seen.txt").write_text("", newline="\n")
    (seed / "NOTES.md").write_text("notes\n", newline="\n")
    (seed / ".gitignore").write_text("__pycache__/\nconfig.json\n", newline="\n")
    origin = tmp_path / "origin.git"
    sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git(seed, "init", "-q", "-b", "main")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        sh("git", "clone", "-q", str(origin), str(d), cwd=tmp_path)
        if (seed / "config.json").exists():
            shutil.copy(seed / "config.json", d / "config.json")
    return origin, a, b


def log(repo, *args):
    return sh(sys.executable, "scripts/log.py", *args, cwd=repo)


def sync(repo, msg):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run([sys.executable, "scripts/sync.py", msg], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def companies(origin, tmp):
    d = tmp / "check"
    shutil.rmtree(d, ignore_errors=True)
    sh("git", "clone", "-q", str(origin), str(d), cwd=tmp)
    return sorted(r["company"] for r in json.loads((d / APPS).read_text())["applications"])


def other_side_pushes(b):
    log(b, "add", "--company", "Bravo", "--role", "Ops Manager", "--url", "https://example.com/jobs/2000001",
        "--verdict", "yellow", "--status", "triaged")
    git(b, "add", "-A")
    git(b, "commit", "-q", "-m", "B adds Bravo")
    git(b, "push", "-q", "origin", "main")


def test_conflict_is_replayed_on_latest(repos, tmp_path):
    origin, a, b = repos
    log(a, "add", "--company", "Alpha", "--role", "Ops Manager", "--url", "https://example.com/jobs/1000001",
        "--verdict", "yellow", "--status", "triaged")
    (a / "NOTES.md").write_text("notes\nA's non-data edit\n", newline="\n")
    other_side_pushes(b)
    r = sync(a, "A adds Alpha")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "replayed this chat's log.py calls" in r.stdout  # really went through replay, not a clean rebase
    assert companies(origin, tmp_path) == ["Alpha", "Bravo"]  # both sides' rows survive
    assert "A's non-data edit" in (tmp_path / "check" / "NOTES.md").read_text()


def test_failed_replay_call_pushes_nothing_and_restores(repos, tmp_path):
    origin, a, b = repos
    log(a, "add", "--company", "Alpha", "--role", "Ops Manager", "--url", "https://example.com/jobs/1000001",
        "--verdict", "yellow", "--status", "triaged")
    git(a, "add", "-A")
    git(a, "commit", "-q", "-m", "A adds Alpha")
    before = git(a, "rev-parse", "HEAD").stdout.strip()
    journal = a / ".git" / "log_journal.jsonl"
    with open(journal, "a", newline="\n") as f:  # a call that can't succeed on the remote's data (the row isn't there)
        f.write(json.dumps(["set", "https://example.com/jobs/9999999", "applied"]) + "\n")
    other_side_pushes(b)
    r = sync(a, "")
    assert r.returncode != 0 and "Replay stopped" in (r.stdout + r.stderr)
    assert companies(origin, tmp_path) == ["Bravo"]  # nothing of A's reached the remote
    assert git(a, "rev-parse", "HEAD").stdout.strip() == before  # back where it was
    assert journal.exists() and not journal.with_suffix(".replaying").exists()
    assert "Alpha" in (a / APPS).read_text()  # A's own work is intact


def test_interrupted_replay_blocks_the_next_sync(repos):
    origin, a, b = repos
    (a / ".git" / "log_journal.replaying").write_text(json.dumps(["note", "x", "y"]) + "\n", newline="\n")
    before = git(a, "rev-parse", "HEAD").stdout.strip()
    r = sync(a, "anything")
    assert r.returncode != 0 and "interrupted mid-replay" in (r.stdout + r.stderr)
    assert git(a, "rev-parse", "HEAD").stdout.strip() == before
