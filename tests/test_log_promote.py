"""Regression tests for log.py promote: the lead's saved JD (jd_file) must carry into the new
(or merged) application row's jd field, in both the normal and already-logged-duplicate branches.
Run: python3 -m pytest tests"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import log  # noqa: E402


@pytest.fixture(autouse=True)
def data_files(tmp_path, monkeypatch):
    apps = tmp_path / "applications.json"
    leads = tmp_path / "leads.json"
    archive = tmp_path / "archive.json"
    seen = tmp_path / "seen.txt"
    apps.write_text(json.dumps({"applications": []}), encoding="utf-8")
    leads.write_text(json.dumps({"leads": []}), encoding="utf-8")
    for mod in (log, common):
        monkeypatch.setattr(mod, "APPS", apps, raising=False)
        monkeypatch.setattr(mod, "LEADS", leads, raising=False)
        monkeypatch.setattr(mod, "ARCHIVE", archive, raising=False)
        monkeypatch.setattr(mod, "SEEN", seen, raising=False)
    return apps, leads


def add_lead(leads_path, **fields):
    data = json.loads(leads_path.read_text(encoding="utf-8"))
    data["leads"].append({"company": "Acme", "role": "Program Manager", "status": "not_triaged",
                           "source": "muse", **fields})
    leads_path.write_text(json.dumps(data), encoding="utf-8")


def test_promote_carries_jd_file_into_the_new_rows_jd_field(data_files):
    apps, leads = data_files
    add_lead(leads, url="https://example.com/jobs/1", jd_file="jds/leads/Acme_PM_abc123.txt")
    log.main(["promote", "Acme", "Program", "Manager"])
    row = json.loads(apps.read_text(encoding="utf-8"))["applications"][0]
    assert row["jd"] == "jds/leads/Acme_PM_abc123.txt"


def test_promote_without_a_saved_jd_leaves_the_jd_field_unset(data_files):
    apps, leads = data_files
    add_lead(leads, url="https://example.com/jobs/2")
    log.main(["promote", "Acme", "Program", "Manager"])
    row = json.loads(apps.read_text(encoding="utf-8"))["applications"][0]
    assert "jd" not in row


def test_promote_duplicate_branch_also_carries_the_jd_file(data_files):
    apps, leads = data_files
    data = json.loads(apps.read_text(encoding="utf-8"))
    data["applications"].append({"company": "Acme", "role": "Program Manager", "status": "triaged",
                                  "status_date": "2026-11-01", "url": "https://example.com/jobs/3"})
    apps.write_text(json.dumps(data), encoding="utf-8")
    add_lead(leads, url="https://example.com/jobs/3", jd_file="jds/leads/Acme_PM_def456.txt")
    log.main(["promote", "Acme", "Program", "Manager"])
    row = json.loads(apps.read_text(encoding="utf-8"))["applications"][0]
    assert row["jd"] == "jds/leads/Acme_PM_def456.txt"
