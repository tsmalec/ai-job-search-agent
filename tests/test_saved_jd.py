import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import saved_jd  # noqa: E402

URL = "https://example.com/jobs/123"
TAG = hashlib.sha1(URL.encode("utf-8")).hexdigest()[:8]
FULL = "TITLE: X\nURL: " + URL + "\n\n" + ("Build things with teams. " * 80) + "\n\n## Required Qualifications\n- 5 years of experience\n"


def test_find_by_url_hash(tmp_path):
    (tmp_path / f"Acme_Role_{TAG}.txt").write_text(FULL)
    (tmp_path / "Other_Role_deadbeef.txt").write_text(FULL)
    assert [p.name for p in saved_jd.find(URL, tmp_path)] == [f"Acme_Role_{TAG}.txt"]


def test_none_when_missing(tmp_path):
    st, _, p = saved_jd.check(URL, tmp_path)
    assert st == "none" and p is None


def test_partial_is_not_full(tmp_path):
    (tmp_path / f"Acme_Role_{TAG}.txt").write_text("TITLE: X\nURL: " + URL + "\n\nshort")
    st, _, _ = saved_jd.check(URL, tmp_path)
    assert st == "partial"


def test_full_text_found(tmp_path):
    (tmp_path / f"Acme_Role_{TAG}.txt").write_text(FULL)
    st, _, p = saved_jd.check(URL, tmp_path)
    assert st == "full" and p.name.endswith(f"{TAG}.txt")
