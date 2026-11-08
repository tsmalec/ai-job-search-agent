"""Read vs unread comes from saved files, not memory: jd_state, partial marking, screen_done --report, promote wording.
Fictional data only; every data path and the log journal are redirected into tmp_path."""
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import capture_jd  # noqa: E402
import common  # noqa: E402
import log  # noqa: E402
import screen_done  # noqa: E402

FULL = ("Acme is hiring an Operations Manager.\n\nResponsibilities\n" + "Run weekly reporting and process work. " * 40
        + "\n\nRequired Qualifications\n- 5 years of operations experience\n")
NO_QUALS = "Acme is hiring an Operations Manager. " + "Run weekly reporting and process work. " * 60
THIN = "Acme is hiring.\nRequirements: a few years."


def lead(company, **kw):
    return {"company": company, "role": "Operations Manager", "url": f"https://jobs.example.com/{company.lower()}",
            "status": "not_triaged", "source": "Wave A", "where_pay": "Remote", "note": "", **kw}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "jds" / "leads").mkdir(parents=True)
    (tmp_path / "work").mkdir()
    apps, leads = tmp_path / "applications.json", tmp_path / "leads.json"
    apps.write_text(json.dumps({"applications": []}), encoding="utf-8")
    leads.write_text(json.dumps({"leads": []}), encoding="utf-8")
    monkeypatch.setattr(log, "JOURNAL", tmp_path / "log_journal.jsonl", raising=False)
    monkeypatch.setattr(common, "ROOT", tmp_path)
    for mod in (log, common, screen_done, capture_jd):
        monkeypatch.setattr(mod, "APPS", apps, raising=False)
        monkeypatch.setattr(mod, "LEADS", leads, raising=False)
        monkeypatch.setattr(mod, "SEEN", tmp_path / "seen.txt", raising=False)
        monkeypatch.setattr(mod, "ARCHIVE", tmp_path / "archive.json", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path, apps, leads


def put_leads(leads, rows):
    leads.write_text(json.dumps({"leads": rows}), encoding="utf-8")


def save(root, rel, text):
    (root / rel).write_text(text, encoding="utf-8", newline="\n")
    return rel


def test_jd_state_full_partial_none(repo):
    root, _, _ = repo
    assert common.jd_state(save(root, "jds/leads/a.txt", FULL), root)[0] == "full"
    assert common.jd_state(save(root, "jds/leads/b.txt", NO_QUALS), root) == ("partial", "no required-qualifications section found")
    assert common.jd_state(save(root, "jds/leads/c.txt", THIN), root)[0] == "partial"
    assert common.jd_state(save(root, "jds/leads/d.txt", ""), root)[0] == "none"
    assert common.jd_state("jds/leads/missing.txt", root)[0] == "none"
    assert common.jd_state(None, root)[0] == "none"


def test_capture_marks_partial_and_warns(repo, monkeypatch, capsys):
    root, _, leads = repo
    put_leads(leads, [lead("Acme")])
    monkeypatch.setattr(sys, "stdin", io.StringIO(NO_QUALS))
    capture_jd.main(["https://jobs.example.com/acme"])
    out = capsys.readouterr().out
    assert "PARTIAL READ" in out
    saved = (root / "work" / "Acme_Operations_Manager.txt").read_text(encoding="utf-8")
    assert "READ: partial" in saved
    assert common.jd_state("work/Acme_Operations_Manager.txt", root)[0] == "partial"


def test_capture_full_has_no_partial_mark(repo, monkeypatch, capsys):
    root, _, leads = repo
    put_leads(leads, [lead("Acme")])
    monkeypatch.setattr(sys, "stdin", io.StringIO(FULL))
    capture_jd.main(["https://jobs.example.com/acme"])
    assert "PARTIAL" not in capsys.readouterr().out
    assert common.jd_state("work/Acme_Operations_Manager.txt", root)[0] == "full"


def test_report_counts_come_from_files(repo, capsys):
    root, _, leads = repo
    put_leads(leads, [lead("Acme", jd_file=save(root, "jds/leads/a.txt", FULL)),
                      lead("Globex", jd_file=save(root, "jds/leads/g.txt", NO_QUALS)),
                      lead("Initech", jd_file="jds/leads/gone.txt"),
                      lead("Hooli")])
    screen_done.report("2000-01-01")
    out = capsys.readouterr().out
    assert "READ 2 of 4 (full 1, partial 1); unread, script-only 2" in out
    assert "Hooli" in out and "unread, script-only" in out.split("Hooli")[1].splitlines()[0]
    assert "capture_jd.py" in out


def test_promote_refuses_screen_pass_without_saved_text(repo):
    root, apps, leads = repo
    put_leads(leads, [lead("Acme")])
    with pytest.raises(SystemExit, match="screen pass"):
        log.main(["promote", "https://jobs.example.com/acme", "--status", "triaged", "--note", "screen pass, not read"])
    assert json.loads(leads.read_text())["leads"]  # still open
    log.main(["promote", "https://jobs.example.com/acme", "--status", "triaged", "--note", "unread, script-only"])
    row = json.loads(apps.read_text())["applications"][0]
    assert row["jd_read"] == "none"


def test_promote_refuses_not_read_when_saved_text_exists_and_report_reads_row(repo, capsys):
    root, apps, leads = repo
    put_leads(leads, [lead("Acme", jd_file=save(root, "jds/leads/a.txt", FULL))])
    with pytest.raises(SystemExit, match="saved posting"):
        log.main(["promote", "https://jobs.example.com/acme", "--status", "triaged", "--note", "not read"])
    log.main(["promote", "https://jobs.example.com/acme", "--status", "triaged", "--note", "read from saved text"])
    row = json.loads(apps.read_text())["applications"][0]
    assert row["jd_read"] == "full"
    screen_done.report("2000-01-01")
    assert "READ 1 of 1 (full 1, partial 0); unread, script-only 0" in capsys.readouterr().out


def test_report_flags_mislabelled_rows(repo, capsys):
    root, apps, leads = repo
    rows = [{"company": "Acme", "role": "Ops", "status": "triaged", "status_date": "2026-01-05",
             "note": "Muse Wave A: x | 2026-01-05 Claude: screen pass, not read", "jd": None},
            {"company": "Globex", "role": "Ops", "status": "triaged", "status_date": "2026-01-05",
             "note": "Muse Wave A: x | 2026-01-05 Claude: screen pass, not read",
             "jd": save(root, "jds/leads/g.txt", FULL)}]
    apps.write_text(json.dumps({"applications": rows}), encoding="utf-8")
    screen_done.report("2026-01-01")
    out = capsys.readouterr().out
    assert "no posting was saved" in out and "saved posting exists" in out


def test_jdread_repoints_a_promoted_row(repo, capsys):
    root, apps, leads = repo
    put_leads(leads, [lead("Acme")])
    log.main(["promote", "https://jobs.example.com/acme", "--status", "triaged", "--note", "unread, script-only"])
    assert json.loads(apps.read_text())["applications"][0]["jd_read"] == "none"
    with pytest.raises(SystemExit, match="Capture the posting first"):
        log.main(["jdread", "Acme", "work/missing.txt"])
    save(root, "work/Acme.txt", FULL)
    log.main(["jdread", "Acme", "work/Acme.txt"])
    assert json.loads(apps.read_text())["applications"][0]["jd_read"] == "full"
    capsys.readouterr()
    screen_done.report("2000-01-01")
    assert "READ 1 of 1" in capsys.readouterr().out


def test_report_judges_only_the_latest_note(repo, capsys):
    root, apps, leads = repo
    old = "Muse Wave A: x | 2026-01-05 Claude: screen pass, not read"
    rows = [{"company": "Acme", "role": "Ops", "status": "triaged", "status_date": "2026-01-05",
             "note": old + " | 2026-01-06: Correction: read from saved posting text, not 'screen pass, not read'.",
             "jd": save(root, "jds/leads/a.txt", FULL)},
            {"company": "Globex", "role": "Ops", "status": "triaged", "status_date": "2026-01-05",
             "note": old + " | 2026-01-06: read the posting; pay looks fine", "jd": save(root, "jds/leads/g.txt", FULL)}]
    apps.write_text(json.dumps({"applications": rows}), encoding="utf-8")
    screen_done.report("2026-01-01")
    assert "⛔" not in capsys.readouterr().out
