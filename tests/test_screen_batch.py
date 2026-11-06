"""The lead screen (phase 2) can't skip the capture or the promote.
capture_jd.py refuses an excerpt and names its file like screen_leads --leads --fetch does;
screen_done.py says the screen isn't finished while any lead is still not_triaged."""
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import capture_jd  # noqa: E402
import common  # noqa: E402
import screen_done  # noqa: E402

LEAD = {"source": "Wave A", "company": "Acme", "role": "Operations Manager", "url": "https://boards.example.com/acme/jobs/123",
        "status": "not_triaged", "where_pay": "Remote", "note": ""}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    leads = tmp_path / "leads.json"
    leads.write_text(json.dumps({"leads": [dict(LEAD)]}), encoding="utf-8")
    monkeypatch.setattr(capture_jd, "LEADS", leads)
    monkeypatch.setattr(common, "LEADS", leads)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def feed(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_excerpt_is_refused(repo, monkeypatch):
    feed(monkeypatch, "Short excerpt of the posting.")
    with pytest.raises(SystemExit, match="excerpt or a summary"):
        capture_jd.main([LEAD["url"]])
    assert not (repo / "work").exists()


def test_full_text_is_saved_under_the_lead_name(repo, monkeypatch):
    feed(monkeypatch, "Responsibilities and qualifications. " * 60)
    assert capture_jd.main([LEAD["url"]]) == 0
    f = repo / "work" / "Acme_Operations_Manager.txt"
    head = f.read_text(encoding="utf-8").split("\n\n", 1)[0]
    assert "COMPANY: Acme" in head and "SOURCE: browser capture" in head


def test_unknown_url_needs_a_name_and_a_path(repo, monkeypatch):
    feed(monkeypatch, "x" * 2000)
    with pytest.raises(SystemExit, match="pass --company"):
        capture_jd.main(["https://boards.example.com/other/jobs/9"])


def test_any_open_lead_means_the_screen_is_not_done(repo, capsys):
    assert screen_done.main([]) == 1
    assert "still in leads.json" in capsys.readouterr().out


def test_empty_inbox_is_done(repo, capsys):
    (repo / "leads.json").write_text(json.dumps({"leads": [dict(LEAD, status="dropped_by_agent")]}), encoding="utf-8")
    assert screen_done.main([]) == 0
    assert "✅" in capsys.readouterr().out


def test_source_filter_only_counts_that_wave(repo):
    assert screen_done.main(["--source", "other wave"]) == 0
    assert screen_done.main(["--source", "wave a"]) == 1


def test_read_mode_flags_a_read_lead_not_a_merely_open_one(repo, monkeypatch, capsys):
    assert screen_done.main(["--read", "--quiet"]) == 0
    feed(monkeypatch, "Responsibilities and qualifications. " * 60)
    capture_jd.main([LEAD["url"]])
    assert screen_done.main(["--read", "--quiet"]) == 1
    assert "read in this chat but still in leads.json" in capsys.readouterr().out


def test_source_flag_without_a_label_is_a_clear_error(repo):
    with pytest.raises(SystemExit, match="--source needs a label"):
        screen_done.main(["--source"])
