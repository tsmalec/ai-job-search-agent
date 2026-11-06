"""Regression: onepage.py cut pinned bullets on a real build even though they were listed in "pin".
Fix: a pin that matches no bullet_ids entry now stops the run instead of printing a warning and trimming
anyway, and a final check refuses to write output if a cut bullet's ID was ever in the pin list. These
tests drive the real trim loop without needing LibreOffice: build_resume's build()/page_count() are
replaced with fakes that count how many bullets are left in the in-memory content.
Run: python3 -m pytest tests/test_onepage_pins.py
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import onepage  # noqa: E402


def content(pin, extra_filler=4):
    """One job (Example Team Lead): filler bullets first, then 3 pinned bullets last — so a pinned
    bullet surviving isn't an accident of onepage's default --keep 3 "never touch the top 3" floor."""
    ids = [f"FILLER-{i}" for i in range(extra_filler)] + ["ACME-01", "ACME-02", "ACME-03"]
    bullets = [f"Filler bullet {i}" for i in range(extra_filler)] + [f"Pinned bullet {i}" for i in range(3)]
    return {
        "summary": "x", "skills": "x",
        "pin": pin,
        "jobs": [{"title": "Example Team Lead", "company": "Acme", "dates": "Jan 2020 - Jan 2021",
                  "bullets": bullets, "bullet_ids": ids}],
    }


def fake_build_page_count(monkeypatch, target_bullets):
    """Make build() a no-op that stashes the content, and page_count() read bullets left from it."""
    state = {}

    def fake_build(c, out):
        state["c"] = c

    def fake_page_count(out):
        total = sum(len(j.get("bullets") or []) for j in state["c"]["jobs"])
        return ("1" if total <= target_bullets else "2"), None

    monkeypatch.setattr(onepage, "build", fake_build)
    monkeypatch.setattr(onepage, "page_count", fake_page_count)


def test_dead_pin_stops_the_run_instead_of_trimming(monkeypatch, tmp_path):
    """A pin that doesn't match any bullet_ids entry must never silently protect nothing."""
    c = content(pin=["ACME-01", "NOT-A-REAL-ID"])
    cj = tmp_path / "content.json"
    cj.write_text(json.dumps(c), encoding="utf-8")
    fake_build_page_count(monkeypatch, target_bullets=2)
    with pytest.raises(SystemExit, match="matches no bullet_ids entry"):
        onepage.main([str(cj), str(tmp_path / "out.docx"), "--keep", "0"])


def test_pinned_bullets_survive_trimming_down_to_the_pins(monkeypatch, tmp_path):
    """Trimming to fewer bullets than exist must cut the unpinned filler, never the 3 pinned bullets."""
    c = content(pin=["ACME-01", "ACME-02", "ACME-03"])
    cj = tmp_path / "content.json"
    cj.write_text(json.dumps(c), encoding="utf-8")
    fake_build_page_count(monkeypatch, target_bullets=3)  # must cut all 4 filler bullets, keep only the 3 pins
    onepage.main([str(cj), str(tmp_path / "out.docx"), "--keep", "0"])
    trimmed = json.loads((tmp_path / "content_1page.json").read_text(encoding="utf-8"))
    kept_ids = set(trimmed["jobs"][0]["bullet_ids"])
    assert {"ACME-01", "ACME-02", "ACME-03"} <= kept_ids
    assert not any(k.startswith("FILLER") for k in kept_ids)


def test_regression_in_pin_matching_refuses_to_cut_a_pinned_bullet(monkeypatch, tmp_path):
    """Belt-and-suspenders: even if candidates()/pinned() regress and offer a pinned bullet for cutting,
    the final check must refuse to write output rather than deliver a resume missing a must-keep bullet."""
    c = content(pin=["ACME-01", "ACME-02", "ACME-03"], extra_filler=0)  # only pinned bullets exist
    cj = tmp_path / "content.json"
    cj.write_text(json.dumps(c), encoding="utf-8")
    fake_build_page_count(monkeypatch, target_bullets=0)  # forces a cut even though everything is pinned
    monkeypatch.setattr(onepage, "pinned", lambda c, ji, bi: False)  # simulate the matching regressing
    with pytest.raises(SystemExit, match="BUG: about to cut pinned bullet"):
        onepage.main([str(cj), str(tmp_path / "out.docx"), "--keep", "0"])
