"""pipeline_export.py: stage bucketing, '?' prelim-fit stripping, and follow-up/closing bands.
Run: python3 -m pytest tests"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import pipeline_export as pe  # noqa: E402


@pytest.fixture(autouse=True)
def data_files(tmp_path, monkeypatch):
    apps = tmp_path / "applications.json"
    leads = tmp_path / "leads.json"
    archive = tmp_path / "archive.json"
    apps.write_text(json.dumps({"applications": []}), encoding="utf-8")
    leads.write_text(json.dumps({"leads": []}), encoding="utf-8")
    archive.write_text(json.dumps({"applications": [{"company": "Acme"}]}), encoding="utf-8")
    for mod in (pe, common):
        monkeypatch.setattr(mod, "APPS", apps, raising=False)
        monkeypatch.setattr(mod, "LEADS", leads, raising=False)
        monkeypatch.setattr(mod, "ARCHIVE", archive, raising=False)
    return apps, leads


def write_apps(path, rows):
    path.write_text(json.dumps({"applications": rows}), encoding="utf-8")


def write_leads(path, rows):
    path.write_text(json.dumps({"leads": rows}), encoding="utf-8")


def test_stage_of_buckets_each_status():
    assert pe.stage_of("applied") == "applied"
    assert pe.stage_of("triaged") == "triaged"
    assert pe.stage_of("resume_built") == "triaged"
    assert pe.stage_of("interview") == "outcome"
    assert pe.stage_of("dropped") == "outcome"
    assert pe.stage_of("not_triaged") == "other"


def test_row_for_app_strips_prelim_question_mark_but_flags_it():
    row = pe.row_for_app({"company": "Acme", "role": "PM", "status": "triaged", "verdict": "yellow?"})
    assert row["fit"] == "yellow"
    assert row["fit_prelim"] == "true"

    row2 = pe.row_for_app({"company": "Acme", "role": "PM", "status": "triaged", "verdict": "green"})
    assert row2["fit"] == "green"
    assert row2["fit_prelim"] == "false"


def test_followup_band_thresholds(data_files):
    apps, leads = data_files
    old = (date.today() - timedelta(days=10)).isoformat()
    rows = [{"company": "Acme", "role": "PM", "status": "applied", "status_date": old,
             "live": "open", "next_step": "", "verdict": None}]
    write_apps(apps, rows)
    out = pe.build_rows()
    assert len(out) == 1
    assert out[0]["followup_band"] == "7-13d"


def test_followup_band_skips_rows_marked_no_follow_up(data_files):
    apps, leads = data_files
    old = (date.today() - timedelta(days=20)).isoformat()
    rows = [{"company": "Acme", "role": "PM", "status": "applied", "status_date": old,
             "live": "open", "next_step": "No follow-up: emails outcomes reliably", "verdict": None}]
    write_apps(apps, rows)
    out = pe.build_rows()
    assert out[0]["followup_band"] == ""


def test_closed_posting_gets_no_nudge_band(data_files):
    apps, leads = data_files
    old = (date.today() - timedelta(days=20)).isoformat()
    rows = [{"company": "Acme", "role": "PM", "status": "applied", "status_date": old,
             "live": "closed", "verdict": None}]
    write_apps(apps, rows)
    out = pe.build_rows()
    assert out[0]["followup_band"] == "closed-no-nudge"


def test_not_triaged_lead_becomes_a_leads_stage_row(data_files):
    apps, leads = data_files
    write_leads(leads, [{"company": "Acme", "role": "PM", "status": "not_triaged", "url": "https://x"}])
    out = pe.build_rows()
    assert len(out) == 1
    assert out[0]["stage"] == "leads"
    assert out[0]["url"] == "https://x"


def test_dropped_by_agent_lead_is_excluded(data_files):
    apps, leads = data_files
    write_leads(leads, [{"company": "Acme", "role": "PM", "status": "dropped_by_agent"}])
    assert pe.build_rows() == []


def test_write_csv_has_a_header_even_with_zero_rows(tmp_path):
    out = tmp_path / "work" / "pipeline_table.csv"
    pe.write_csv([], out)
    text = out.read_text(encoding="utf-8")
    first_line = text.splitlines()[0]
    assert first_line.split(",") == pe.COLS
    assert len(text.splitlines()) == 1


def test_stage_counts_includes_archived_total(data_files):
    apps, leads = data_files
    write_apps(apps, [{"company": "Acme", "role": "PM", "status": "applied", "verdict": None}])
    rows = pe.build_rows()
    counts = pe.stage_counts(rows)
    assert counts["applied"] == 1
    assert counts["archived"] == 1


def test_scoreboard_row_counts_totals_and_today(monkeypatch, tmp_path):
    from datetime import date
    rows = [
        {"company": "Acme", "status": "applied", "status_date": "2026-01-10", "timing": {"applied_at": "2026-01-10T09:00"}},
        {"company": "Beta", "status": "rejected", "status_date": "2026-01-10"},
        {"company": "Gamma", "status": "interview", "status_date": "2026-01-09"},
        {"company": "Delta", "status": "skipped", "status_date": "2026-01-10"},
    ]
    monkeypatch.setattr(pe, "all_apps", lambda: rows)
    start = tmp_path / "start.json"
    start.write_text('{"start": "2026-01-01"}', encoding="utf-8")
    monkeypatch.setattr(pe, "SEARCH_START", start)
    r = pe.scoreboard_row(date(2026, 1, 10))
    assert r["stage"] == "scoreboard" and r["row_id"] == "SCOREBOARD"
    sb = json.loads(r["scoreboard"])
    assert (sb["applied"], sb["rejected"], sb["today_applied"], sb["today_rejected"]) == (3, 1, 1, 1)
    assert (sb["interviews"], sb["offers"], sb["open"]) == (1, 0, 2)
    assert sb["today_rejected_names"] == ["Beta"] and sb["today_interviews"] == 0
    assert sb["pace"] == round(3 / 9, 1)
    assert (sb["days"], sb["week"]) == (9, 2)


def test_scoreboard_row_without_start_file_still_has_totals(monkeypatch, tmp_path):
    from datetime import date
    monkeypatch.setattr(pe, "all_apps", lambda: [])
    monkeypatch.setattr(pe, "SEARCH_START", tmp_path / "missing.json")
    sb = json.loads(pe.scoreboard_row(date(2026, 1, 10))["scoreboard"])
    assert sb["applied"] == 0 and "days" not in sb
