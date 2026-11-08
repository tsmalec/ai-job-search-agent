import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from check_extract import check  # noqa: E402

SAMPLE = json.loads((ROOT / "tests" / "fixtures" / "sample_loop" / "jd.json").read_text(encoding="utf-8"))


def test_sample_jd_passes_without_failures():
    fails, _ = check(SAMPLE)
    assert fails == []


def test_missing_title_and_required_fail():
    fails, _ = check({"title": "", "required": {}, "preferred": {}, "keywords": {}})
    assert "no title" in fails
    assert "required list is empty" in fails


def test_thin_and_bundled_entries_warn():
    jd = {"title": "analyst", "required": {"excel": ["excel"], "3+ years operations reporting project coordination experience": []},
          "preferred": {}, "keywords": {}}
    fails, warns = check(jd)
    assert fails == []
    assert any("only 2 required" in w for w in warns)
    assert any("probably bundled" in w for w in warns)


def test_preferred_keyword_overlap_warns():
    jd = {"title": "analyst", "required": {"a": [], "b": [], "c": []}, "preferred": {"six sigma": []},
          "keywords": {"six sigma": []}}
    _, warns = check(jd)
    assert any("both preferred and keyword" in w for w in warns)


def test_one_word_title_warns():
    jd = {"title": "ahead", "required": {"a": [], "b": [], "c": []}, "preferred": {}, "keywords": {}}
    fails, warns = check(jd)
    assert fails == []
    assert any("one word" in w for w in warns)


def test_oversized_required_list_warns():
    jd = {"title": "product manager", "required": {f"item {i}": [] for i in range(30)}, "preferred": {}, "keywords": {}}
    _, warns = check(jd)
    assert any("over-extracted" in w for w in warns)


def test_reference_coverage_flags_missing_items():
    ref = {"required": {"advanced excel": []}, "preferred": {"six sigma": []}}
    jd = {"title": "analyst", "required": {"excel": []}, "preferred": {}, "keywords": {}}
    _, warns = check(jd, ref)
    assert not any("advanced excel" in w for w in warns)
    assert any("six sigma" in w for w in warns)
