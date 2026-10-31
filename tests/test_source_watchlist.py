"""board_sweep.py owns board fetching; source_sweep's watchlist only reports coverage, and every
watchlist link stays visible (covered, off by default, or manual)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import board_sweep  # noqa: E402
import source_sweep  # noqa: E402

ROWS = [
    {"company": "Acme", "careers": "https://job-boards.greenhouse.io/acme"},
    {"company": "Wdco", "careers": "https://wdco.wd5.myworkdayjobs.com/External"},
    {"company": "Bamco", "careers": "https://bamco.bamboohr.com/careers"},
    {"company": "Ownco", "careers": "https://careers.ownco.com"},
    {"company": "Nolink", "careers": None},
]


def write_watchlist(tmp_path):
    (tmp_path / "watchlist.json").write_text(json.dumps({"watching": ROWS}), encoding="utf-8", newline="\n")


def test_every_watchlist_link_is_reported(tmp_path, monkeypatch):
    write_watchlist(tmp_path)
    monkeypatch.setattr(source_sweep, "DATA", tmp_path)
    out, fails, extra = source_sweep.watchlist({}, None, None, None)
    assert out == [] and fails == {}
    assert extra["covered_by_board_sweep"] == ["Acme", "Wdco"]
    assert extra["board_sweep_off_by_default"] == ["Bamco (bamboohr: board_sweep.py --with bamboohr)"]
    assert extra["manual"] == ["Ownco (https://careers.ownco.com)", "Nolink (no careers link)"]


def test_board_sweep_learns_watchlist_boards(tmp_path, monkeypatch):
    write_watchlist(tmp_path)
    monkeypatch.setattr(board_sweep, "DATA", tmp_path)
    monkeypatch.setattr(board_sweep, "WATCH_EXTRA", [("Extraco", "https://extraco.wd1.myworkdayjobs.com/Careers")])
    b = {"slugs": {}, "names": {}}
    new = board_sweep.learn_boards(b)
    assert "greenhouse:acme" in b["added"] and "workday:wdco|wd5|External" in b["added"]
    assert "workday:extraco|wd1|Careers" in b["added"]  # extra boards beyond watchlist links
    assert all("Ownco" not in n for n in new)
