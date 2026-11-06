"""The closed-row archive is split into size-bounded shards behind one loader (common.Archive / load_archive).

Covers: sharding, the loader across shards, the migration (dry run, row-loss abort, idempotence), matching and
updating a role that lives only in the archive, and the size alarm. Fictional rows only; every data path is
redirected to a temp folder, so nothing here touches real data or .git.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common  # noqa: E402
import log  # noqa: E402


def row(n, status="skipped", **kw):
    r = {"added": "2026-01-05", "company": f"Acme{n}", "role": f"Analyst {n}", "verdict": "red", "ats": None,
         "resume": None, "status": status, "status_date": "2026-01-06", "confirmed": None, "referral": None,
         "note": "x" * 400, "next_step": "Closed", "url": f"https://example.com/jobs/{100000 + n}"}
    r.update(kw)
    return r


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    paths = {"APPS": data / "applications.json", "LEADS": data / "leads.json", "SEEN": data / "seen.txt",
             "ARCHIVE": data / "archive.json"}
    paths["APPS"].write_text(json.dumps({"applications": []}), encoding="utf-8")
    paths["LEADS"].write_text(json.dumps({"leads": []}), encoding="utf-8")
    for mod in (common, log):
        for k, v in paths.items():
            monkeypatch.setattr(mod, k, v, raising=False)
    monkeypatch.setattr(log, "JOURNAL", tmp_path / "log_journal.jsonl", raising=False)
    # tiny shards so a handful of rows spans several files
    monkeypatch.setattr(common, "SHARD_TARGET", 3_000)
    monkeypatch.setattr(common, "SHARD_MAX", 4_000)
    monkeypatch.setattr(common, "SIZE_CEILING", 6_000)
    return paths


def write_legacy(env, rows):
    env["ARCHIVE"].write_text(json.dumps({"note": "old", "applications": rows}, indent=2), encoding="utf-8")


def shard_files(env):
    d = common.shard_dir()
    return sorted(d.glob("*.json")) if d.is_dir() else []


def test_save_splits_rows_into_bounded_shards_and_loader_reads_them_in_order(env):
    rows = [row(n) for n in range(30)]
    a = common.Archive()
    a.append(rows)
    a.save()
    files = shard_files(env)
    assert len(files) > 3
    assert all(f.stat().st_size <= common.SIZE_CEILING for f in files)
    assert [f.name for f in files] == [f"{n:04d}.json" for n in range(1, len(files) + 1)]
    assert common.load_archive() == rows  # same rows, same order, shards invisible to the caller


def test_in_place_edit_rewrites_only_the_shard_that_changed(env):
    a = common.Archive()
    a.append([row(n) for n in range(30)])
    a.save()
    before = {f.name: f.read_bytes() for f in shard_files(env)}
    b = common.Archive()
    b.rows()[0]["note"] = "edited"
    b.save()
    after = {f.name: f.read_bytes() for f in shard_files(env)}
    changed = [n for n in after if before.get(n) != after[n]]
    assert changed == ["0001.json"]


def test_appended_rows_only_touch_the_last_shard(env):
    a = common.Archive()
    a.append([row(n) for n in range(30)])
    a.save()
    before = {f.name: f.read_bytes() for f in shard_files(env)}
    b = common.Archive()
    b.append([row(99)])
    b.save()
    after = {f.name: f.read_bytes() for f in shard_files(env)}
    changed = [n for n in after if before.get(n) != after[n]]
    assert len(changed) == 1 and changed[0] == max(after)
    assert common.load_archive()[-1]["company"] == "Acme99"


def test_all_apps_and_seen_cover_live_and_archived_rows(env):
    env["APPS"].write_text(json.dumps({"applications": [row(1, "applied")]}), encoding="utf-8")
    a = common.Archive()
    a.append([row(2), row(3)])
    a.save()
    assert [r["company"] for r in common.all_apps()] == ["Acme1", "Acme2", "Acme3"]
    common.write_seen()
    lines = env["SEEN"].read_text(encoding="utf-8").splitlines()
    assert any(l.startswith("Acme3 | Analyst 3 | https://example.com/jobs/100003 | skipped") for l in lines)
    idx = common.logged_index(include_seen=False)
    assert idx.find("Acme2", "Analyst 2", "https://example.com/jobs/100002")[0] == "dupe"


def test_legacy_file_still_reads_whole_until_migrated(env):
    write_legacy(env, [row(n) for n in range(5)])
    assert len(common.load_archive()) == 5


def test_migration_dry_run_writes_nothing(env, capsys):
    write_legacy(env, [row(n) for n in range(30)])
    before = env["ARCHIVE"].read_bytes()
    log.main(["archive-shard", "--dry-run"])
    assert "Dry run: row check passed" in capsys.readouterr().out
    assert env["ARCHIVE"].read_bytes() == before and not shard_files(env)


def test_migration_moves_every_row_and_leaves_a_stub(env):
    rows = [row(n) for n in range(30)]
    write_legacy(env, rows)
    log.main(["archive-shard"])
    assert common.load_archive() == rows
    stub = json.loads(env["ARCHIVE"].read_text(encoding="utf-8"))
    assert stub["applications"] == [] and "data/archive/" in stub["shards"]
    assert all(f.stat().st_size <= common.SIZE_CEILING for f in shard_files(env))
    lines = env["SEEN"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 + len(rows)  # two header lines, one per row


def test_migration_is_idempotent(env, capsys):
    write_legacy(env, [row(n) for n in range(30)])
    log.main(["archive-shard"])
    snapshot = {f.name: f.read_bytes() for f in shard_files(env)}
    capsys.readouterr()
    log.main(["archive-shard"])
    assert "Already sharded" in capsys.readouterr().out
    assert {f.name: f.read_bytes() for f in shard_files(env)} == snapshot


def test_migration_aborts_when_a_row_would_be_lost(env, monkeypatch):
    write_legacy(env, [row(n) for n in range(10)])
    real = common._partition
    monkeypatch.setattr(common, "_partition", lambda lists: [p[:-1] for p in real(lists)])  # drops one row per shard
    with pytest.raises(SystemExit) as e:
        log.main(["archive-shard"])
    assert "ABORT" in str(e.value) and "row count" in str(e.value)
    assert not shard_files(env)
    assert len(json.loads(env["ARCHIVE"].read_text(encoding="utf-8"))["applications"]) == 10  # source untouched


def test_migration_aborts_when_a_row_would_be_duplicated(env, monkeypatch):
    write_legacy(env, [row(n) for n in range(10)])
    real = common._partition

    def dup(lists):
        out = real(lists)
        out[0] = out[0] + [out[0][0]]
        return out

    monkeypatch.setattr(common, "_partition", dup)
    with pytest.raises(SystemExit) as e:
        log.main(["archive-shard", "--dry-run"])
    assert "ABORT" in str(e.value)


def test_save_refuses_a_shrinking_archive(env):
    a = common.Archive()
    a.append([row(n) for n in range(5)])
    a.save()
    b = common.Archive()
    b.shards[0].pop()
    with pytest.raises(SystemExit) as e:
        b.save()
    assert "shrink" in str(e.value)
    assert len(common.load_archive()) == 5


def test_log_archive_command_appends_closed_rows_to_shards(env):
    env["APPS"].write_text(json.dumps({"applications": [row(1, "applied"), row(2, "skipped"), row(3, "rejected")]}),
                           encoding="utf-8")
    log.main(["archive"])
    live = json.loads(env["APPS"].read_text(encoding="utf-8"))["applications"]
    assert [r["company"] for r in live] == ["Acme1"]
    assert [r["company"] for r in common.load_archive()] == ["Acme2", "Acme3"]


def test_find_lists_a_role_that_lives_only_in_the_archive(env, capsys):
    a = common.Archive()
    a.append([row(7)])
    a.save()
    log.main(["find", "acme7"])
    assert "archive: 1" in capsys.readouterr().out


def test_rejection_for_an_archived_only_role_updates_it_and_it_stays_archived(env, capsys):
    a = common.Archive()
    a.append([row(n) for n in range(10)])
    a.save()
    log.main(["set", "https://example.com/jobs/100004", "rejected", "--note", "Rejection email", "--date", "2026-02-01"])
    rows = common.load_archive()
    r = next(x for x in rows if x["company"] == "Acme4")
    assert r["status"] == "rejected" and r["status_date"] == "2026-02-01" and "Rejection email" in r["note"]
    assert len(rows) == 10
    assert json.loads(env["APPS"].read_text(encoding="utf-8"))["applications"] == []  # not moved back to the live file
    assert "Acme4 | Analyst 4 | https://example.com/jobs/100004 | rejected" in env["SEEN"].read_text(encoding="utf-8")


def test_confirmation_note_for_an_archived_only_role(env):
    a = common.Archive()
    a.append([row(n) for n in range(6)])
    a.save()
    log.main(["note", "acme2 analyst", "Confirmation email 10:02", "--confirmed", "true"])
    r = next(x for x in common.load_archive() if x["company"] == "Acme2")
    assert r["confirmed"] is True and "Confirmation email 10:02" in r["note"] and r["status"] == "skipped"


def test_a_live_process_on_an_archived_row_is_refused_not_moved(env):
    a = common.Archive()
    a.append([row(1)])
    a.save()
    with pytest.raises(SystemExit) as e:
        log.main(["set", "acme1", "interview", "--note", "Recruiter screen"])
    assert "stays archived" in str(e.value)
    assert common.load_archive()[0]["status"] == "skipped"


def test_live_row_wins_over_an_archived_row_with_the_same_words(env):
    env["APPS"].write_text(json.dumps({"applications": [row(1, "applied")]}), encoding="utf-8")
    a = common.Archive()
    a.append([row(1)])
    a.save()
    log.main(["note", "acme1", "Confirmation email", "--confirmed", "true"])
    live = json.loads(env["APPS"].read_text(encoding="utf-8"))["applications"][0]
    assert live["confirmed"] is True
    assert common.load_archive()[0]["confirmed"] is None


def test_a_row_number_never_reaches_into_the_archive(env):
    a = common.Archive()
    a.append([row(1)])
    a.save()
    with pytest.raises(SystemExit):
        log.main(["note", "0", "text"])


def test_size_alarm_flags_files_over_the_ceiling_and_warns_when_near(env, monkeypatch):
    monkeypatch.setattr(common, "SIZE_CEILING", 1_000)
    (env["ARCHIVE"].parent / "big.json").write_text("x" * 1_500, encoding="utf-8")
    (env["ARCHIVE"].parent / "close.json").write_text("x" * 900, encoding="utf-8")
    (env["ARCHIVE"].parent / "small.json").write_text("x" * 100, encoding="utf-8")
    (env["ARCHIVE"].parent / "sweep_slugs.json").write_text("x" * 5_000, encoding="utf-8")  # bulk dump: exempt
    sizes = {p: kind for p, _, kind in common.size_report()}
    assert sizes == {"data/big.json": "over", "data/close.json": "near"}
    alarm = common.size_alarm()
    assert len(alarm) == 1 and "data/big.json" in alarm[0] and "SIZE ALARM" in alarm[0]


def test_size_alarm_covers_archive_shards(env, monkeypatch):
    a = common.Archive()
    a.append([row(1)])
    a.save()
    monkeypatch.setattr(common, "SIZE_CEILING", 100)
    assert any(p.startswith("data/archive/") for p, _, k in common.size_report() if k == "over")


def test_shard_over_the_ceiling_aborts_the_write(env, monkeypatch):
    monkeypatch.setattr(common, "SHARD_TARGET", 50_000)
    monkeypatch.setattr(common, "SHARD_MAX", 60_000)
    a = common.Archive()
    a.append([row(n) for n in range(30)])  # ~13 KB in one shard, ceiling is 6 KB
    with pytest.raises(SystemExit) as e:
        a.save()
    assert "ceiling" in str(e.value)
