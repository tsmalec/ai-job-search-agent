"""backlog.py's title-line date refresh: every mutating action (done/drop/ok/add) must rewrite the
title line's date to today, so it can't go stale the way a hardcoded-looking old date did.
Fictional data only."""
import datetime
import os
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(__file__)
TODAY = datetime.date.today().isoformat()

FIXTURE = """# Backlog — the to-do tracker (2026-01-02)

Checklist intro text.

| ID | Item | Group | Due | Status |
|---|---|---|---|---|
| B01 | Fictional item one | batch | 2026-01-02 | open |
| B02 | Recurring chore (every 7d) | chore | 2026-01-02 | open |
"""


def make_root(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "backlog.py").write_text(
        Path(HERE, "..", "scripts", "backlog.py").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    (root / "BACKLOG.md").write_text(FIXTURE, encoding="utf-8", newline="\n")
    return root


def run(root, *args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "scripts/backlog.py", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", env=env)


def title_line(root):
    return (root / "BACKLOG.md").read_text(encoding="utf-8").splitlines()[0]


def test_done_refreshes_the_title_date(tmp_path):
    root = make_root(tmp_path)
    assert title_line(root) == "# Backlog — the to-do tracker (2026-01-02)"
    r = run(root, "done", "B01", "shipped it")
    assert r.returncode == 0
    assert title_line(root) == f"# Backlog — the to-do tracker ({TODAY})"


def test_drop_refreshes_the_title_date(tmp_path):
    root = make_root(tmp_path)
    r = run(root, "drop", "B01", "no longer needed")
    assert r.returncode == 0
    assert title_line(root) == f"# Backlog — the to-do tracker ({TODAY})"


def test_add_refreshes_the_title_date(tmp_path):
    root = make_root(tmp_path)
    r = run(root, "add", "A new fictional item", "--due", "2026-12-01", "--group", "batch")
    assert r.returncode == 0
    assert title_line(root) == f"# Backlog — the to-do tracker ({TODAY})"


def test_recurring_done_also_refreshes_the_title_date(tmp_path):
    """The recurring-chore 'done' path rewrites lines by hand (re-opens with a next due date) instead of
    going through set_status; it must still refresh the title."""
    root = make_root(tmp_path)
    r = run(root, "done", "B02", "did the chore")
    assert r.returncode == 0
    assert title_line(root) == f"# Backlog — the to-do tracker ({TODAY})"
    assert "next due" in r.stdout


def test_listing_never_mutates_the_title_date(tmp_path):
    """A read-only listing must not touch the file at all, title included."""
    root = make_root(tmp_path)
    r = run(root)
    assert r.returncode == 0
    assert title_line(root) == "# Backlog — the to-do tracker (2026-01-02)"
