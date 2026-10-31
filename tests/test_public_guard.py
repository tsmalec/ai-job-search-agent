"""The public-repo guard (sync.personal_data) must refuse real data by name, not only by contents."""
import os, shutil, subprocess, sys

ROOT = os.path.join(os.path.dirname(__file__), "..")


def run_guard(tmp_path, setup):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", "config.json"))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    shutil.copy(repo / "config.example.json", repo / "config.json")
    setup(repo)
    out = subprocess.run([sys.executable, "-c", ("import sys; sys.path.insert(0,'scripts'); import sync; "
                           "print('\\n'.join(sync.personal_data()))")], cwd=repo, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-300:]  # a crash must not read as "clean"
    return out.stdout


def test_clean_template_passes(tmp_path):
    assert run_guard(tmp_path, lambda r: subprocess.run(["git", "add", "-A"], cwd=r)).strip() == ""


def test_tracked_config_with_no_contact_info_is_blocked(tmp_path):
    def setup(r):
        import json
        cfg = json.loads((r / "config.example.json").read_text())
        cfg["search"].update(home_label="Springfield, IL")  # personal, but no email/phone
        (r / "config.json").write_text(json.dumps(cfg), newline="\n")
        subprocess.run(["git", "add", "-A"], cwd=r)
        subprocess.run(["git", "add", "-f", "config.json"], cwd=r)
    assert "config.json is tracked" in run_guard(tmp_path, setup)


def test_real_catalog_without_marker_is_blocked(tmp_path):
    def setup(r):
        (r / "B_Bullet_Catalog.md").write_text("# B\nRan a team with Alex.\n", newline="\n")
        subprocess.run(["git", "add", "-A"], cwd=r)
    assert "B_Bullet_Catalog.md no longer holds the sample" in run_guard(tmp_path, setup)
