"""sync.py must never commit or wipe another chat's uncommitted files (`git add -A` swept two of them into an
unrelated commit). Each test builds a throwaway git repo with fictional files. Run: python3 -m pytest tests"""
import os, subprocess, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import sync  # noqa: E402


def git(root, *a):
    return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    for rel in ("scripts/acme_tool.py", "tests/test_acme.py", "data/leads.json"):
        write(tmp_path, rel, "v1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def committed(root):
    return git(root, "show", "--name-only", "--format=", "HEAD").splitlines()


def status(root):
    return {l[3:]: l[:2] for l in subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=root, capture_output=True, text=True).stdout.splitlines()}


def test_paths_commit_only_named_files(repo):
    write(repo, "scripts/acme_tool.py", "mine\n")
    write(repo, "tests/test_acme.py", "theirs, unfinished\n")          # another chat's edit (tracked)
    write(repo, "scripts/globex_new.py", "theirs, brand new\n")        # another chat's untracked file
    assert sync.commit_changes("Fix acme", paths=["scripts/acme_tool.py"], root=repo)
    assert committed(repo) == ["scripts/acme_tool.py"]
    assert status(repo) == {"tests/test_acme.py": " M", "scripts/globex_new.py": "??"}
    assert (repo / "tests/test_acme.py").read_text() == "theirs, unfinished\n"


def test_paths_ignore_files_other_chats_already_staged(repo):
    write(repo, "scripts/acme_tool.py", "mine\n")
    write(repo, "tests/test_acme.py", "theirs\n")
    git(repo, "add", "tests/test_acme.py")                              # staged by someone else
    assert sync.commit_changes("Fix acme", paths=["scripts/acme_tool.py"], root=repo)
    assert committed(repo) == ["scripts/acme_tool.py"]
    assert status(repo) == {"tests/test_acme.py": "M "}


def test_default_commits_data_files_only_when_nothing_else_is_dirty(repo):
    write(repo, "data/leads.json", "logged\n")
    assert sync.commit_changes("Logged Globex", root=repo)
    assert committed(repo) == ["data/leads.json"]
    assert status(repo) == {}


def test_default_refuses_and_names_foreign_files(repo, capsys):
    write(repo, "data/leads.json", "logged\n")
    write(repo, "tests/test_acme.py", "theirs\n")
    head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(SystemExit) as e:
        sync.commit_changes("Logged Globex", root=repo)
    assert "tests/test_acme.py" in str(e.value) and "--path" in str(e.value)
    assert git(repo, "rev-parse", "HEAD") == head                       # no commit
    assert status(repo) == {"data/leads.json": " M", "tests/test_acme.py": " M"}  # nothing staged or lost


def test_paths_with_nothing_dirty_makes_no_commit(repo):
    head = git(repo, "rev-parse", "HEAD")
    assert sync.commit_changes("Nothing", paths=["scripts/acme_tool.py"], root=repo) is False
    assert git(repo, "rev-parse", "HEAD") == head


def test_all_flag_keeps_old_sweep_behaviour(repo):
    write(repo, "tests/test_acme.py", "x\n")
    write(repo, "scripts/globex_new.py", "y\n")
    assert sync.commit_changes("Everything", commit_all=True, root=repo)
    assert sorted(committed(repo)) == ["scripts/globex_new.py", "tests/test_acme.py"]


def test_foreign_dirty_excludes_data_and_lists_untracked_files_individually(repo):
    write(repo, "data/leads.json", "logged\n")
    write(repo, "scripts/new_dir/a.py", "a\n")
    assert sync.foreign_dirty(repo) == ["scripts/new_dir/a.py"]


def test_paths_also_commit_dirty_data_files(repo):
    write(repo, "scripts/acme_tool.py", "mine\n")
    write(repo, "data/leads.json", "logged\n")
    write(repo, "tests/test_acme.py", "theirs\n")
    assert sync.commit_changes("Fix acme", paths=["scripts/acme_tool.py"], root=repo)
    assert sorted(committed(repo)) == ["data/leads.json", "scripts/acme_tool.py"]
    assert status(repo) == {"tests/test_acme.py": " M"}
