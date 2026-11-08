import os

# tests commit in throwaway repos; the hook tests unset this
os.environ.setdefault("JOBSEARCH_ALLOW_RAW_COMMIT", "1")
os.environ.setdefault("JOBSEARCH_ALLOW_ALL", "1")


import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path, monkeypatch):
    """No test may append to the real .git/log_journal.jsonl (sync.py would replay fictional log.py calls). A test that
    exercises the journal sets log.JOURNAL itself; that later monkeypatch wins."""
    import log
    monkeypatch.setattr(log, "JOURNAL", tmp_path / ".git" / "log_journal.jsonl")


@pytest.fixture(autouse=True)
def _isolated_archive(tmp_path_factory, monkeypatch):
    """The archive (data/archive.json plus its shard folder) is derived from common.ARCHIVE. Point it at an empty
    temp folder for every test so none can read or write the real archive; a test that needs rows patches it itself."""
    import common
    monkeypatch.setattr(common, "ARCHIVE", tmp_path_factory.mktemp("archive_guard") / "archive.json")
