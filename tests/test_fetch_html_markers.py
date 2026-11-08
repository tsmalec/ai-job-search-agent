import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_jd  # noqa: E402


def test_headings_and_bold_only_blocks_become_markers():
    out = fetch_jd.to_text("<p>Intro.</p><h3>What you'll do</h3><ul><li>Build <b>things</b></li></ul>"
                           "<p><strong>Requirements:</strong></p><ul><li>5+ years</li></ul>")
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines == ["Intro.", "## What you'll do", "- Build things", "## Requirements:", "- 5+ years"]


def test_bold_bullets_and_mixed_bold_stay_plain():
    out = fetch_jd.to_text("<ul><li><strong>Python</strong></li></ul><p><strong>Note:</strong> mixed bold text.</p>")
    assert "##" not in out and "- Python" in out


def test_long_bold_paragraph_is_not_a_heading():
    out = fetch_jd.to_text("<p><strong>" + "word " * 30 + "</strong></p>")
    assert "##" not in out


def test_last_block_is_not_lost():
    assert fetch_jd.to_text("<h2>Title</h2><p>final line").splitlines()[-1] == "final line"


def test_clean_chars():
    out = fetch_jd.clean_chars("bachelor\u2019s \u201cdegree\u201d \u2013 5+ yrs\u200b \u2022 x\ufffd")
    assert out == "bachelor's \"degree\" - 5+ yrs - x"
