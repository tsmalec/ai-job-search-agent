"""linkedin_sweep.py's read-failure fix: a cut-short read (IncompleteRead et al.) must never be treated as a clean
end-of-results. Fictional data only."""
import http.client
import json
import os
import re
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import linkedin_sweep  # noqa: E402
import board_sweep  # noqa: E402
import common  # noqa: E402


class _FakeResp:
    def __init__(self, text):
        self.text = text

    def read(self):
        return self.text.encode("utf-8")

    def geturl(self):
        return "https://www.linkedin.com/"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_incomplete_read_retries_then_raises_read_failed(monkeypatch):
    """A read that keeps breaking must be retried a bounded number of times, then surface as ReadFailed —
    never silently swallowed into "" (which the caller would read as a real empty page)."""
    calls = []

    def boom(req, timeout=None):
        calls.append(1)
        raise http.client.IncompleteRead(b"", 10)
    monkeypatch.setattr(linkedin_sweep.urllib.request, "urlopen", boom)
    monkeypatch.setattr(linkedin_sweep.time, "sleep", lambda s: None)
    client = linkedin_sweep.Client(max_calls=100)
    try:
        client.get("https://example.invalid/search", (0, 0))
        assert False, "expected ReadFailed"
    except linkedin_sweep.ReadFailed as e:
        assert "IncompleteRead" in str(e)
    assert len(calls) == linkedin_sweep.MAX_READ_ERRORS


def test_recovers_if_a_retry_succeeds(monkeypatch):
    """The first tries can fail (IncompleteRead) as long as one inside the bound succeeds; that's a normal
    page load, not a failure."""
    calls = []

    def flaky(req, timeout=None):
        calls.append(1)
        if len(calls) < linkedin_sweep.MAX_READ_ERRORS:
            raise http.client.IncompleteRead(b"", 10)
        return _FakeResp("<html>ok</html>")
    monkeypatch.setattr(linkedin_sweep.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(linkedin_sweep.time, "sleep", lambda s: None)
    client = linkedin_sweep.Client(max_calls=100)
    assert client.get("https://example.invalid/search", (0, 0)) == "<html>ok</html>"
    assert len(calls) == linkedin_sweep.MAX_READ_ERRORS


def test_a_real_404_is_unaffected_by_the_retry_change(monkeypatch):
    """HTTPError handling (a genuine 404) is untouched: still returns "" with no retry, as before."""
    calls = []

    def not_found(req, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError("https://example.invalid/", 404, "not found", {}, None)
    monkeypatch.setattr(linkedin_sweep.urllib.request, "urlopen", not_found)
    monkeypatch.setattr(linkedin_sweep.time, "sleep", lambda s: None)
    client = linkedin_sweep.Client(max_calls=100)
    assert client.get("https://example.invalid/search", (0, 0)) == ""
    assert len(calls) == 1


def test_search_phase_logs_a_failed_group_instead_of_ending_the_run(tmp_path, monkeypatch):
    """Groups on an overnight run failed with IncompleteRead on their search pages, and the
    old code treated that exactly like end-of-results, so those groups were never actually searched. A page
    that keeps failing must show up as a logged failure, not as a silent (and false) "nothing more to find"."""
    data = tmp_path / "data"
    data.mkdir()
    for mod in (linkedin_sweep, board_sweep, common):
        monkeypatch.setattr(mod, "ROOT", tmp_path)
    for mod in (linkedin_sweep, board_sweep):
        monkeypatch.setattr(mod, "DATA", data)
    monkeypatch.setattr(common, "DROPS", data / "drops")
    monkeypatch.setattr(linkedin_sweep, "OUT", data / "li_candidates.json")
    monkeypatch.setattr(linkedin_sweep, "STATE", data / "li_state.json")
    monkeypatch.setattr(linkedin_sweep, "CKPT", data / "li_checkpoint.json")
    monkeypatch.setattr(linkedin_sweep, "STATUS", data / "li_status.json")

    def always_fails(self, url, pace):
        raise linkedin_sweep.ReadFailed("IncompleteRead after 3 tries: https://www.linkedin.com/jobs-guest/...")
    monkeypatch.setattr(linkedin_sweep.Client, "get", always_fails)
    monkeypatch.setattr(linkedin_sweep.time, "sleep", lambda s: None)
    monkeypatch.setattr(linkedin_sweep, "RETRY_PAUSE_S", 0)

    linkedin_sweep.main(["--terms", "chief of staff", "--pages", "1", "--no-postings", "--no-covered-skip"])

    out = json.loads((data / "li_candidates.json").read_text(encoding="utf-8"))
    failed = out["stats"]["failed_groups"]
    assert len(failed) == 2  # one per WHERE place, one page each
    assert all("IncompleteRead" in f["reason"] for f in failed)
    assert out["stats"]["stopped_early"] == ""  # a read failure is not the same as a 429/999 block
    assert out["candidates"] == [] and out["screened_out"] == []


def _env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    for mod in (linkedin_sweep, board_sweep, common):
        monkeypatch.setattr(mod, "ROOT", tmp_path)
    for mod in (linkedin_sweep, board_sweep):
        monkeypatch.setattr(mod, "DATA", data)
    monkeypatch.setattr(common, "DROPS", data / "drops")
    for name, fn in (("OUT", "li_candidates.json"), ("STATE", "li_state.json"), ("CKPT", "li_checkpoint.json"),
                     ("STATUS", "li_status.json"), ("PREV", "li_candidates.prev.json")):
        monkeypatch.setattr(linkedin_sweep, name, data / fn)
    monkeypatch.setattr(linkedin_sweep.time, "sleep", lambda s: None)
    monkeypatch.setattr(linkedin_sweep, "RETRY_PAUSE_S", 0)
    # the fake cards' title passes whatever title list the config holds (the public template's differs)
    monkeypatch.setattr(linkedin_sweep, "TITLE_RE", re.compile(r"chief of staff"))
    monkeypatch.setattr(linkedin_sweep, "DROP_TITLE", re.compile(r"(?!x)x"))
    return data


def _card(n, where):
    city = "United States" if where == "remote" else linkedin_sweep.WHERE[1][1]["location"]
    return (f'<li><div data-entity-urn="urn:li:jobPosting:{n}"></div>'
            f'<h3 class="base-search-card__title">Chief of Staff</h3>'
            f'<h4 class="base-search-card__subtitle">Acme{n} Corp</h4>'
            f'<span class="job-search-card__location">{city}</span></li>')


def _fake_linkedin(monkeypatch, fail_first=0, fail_always=False):
    """Search pages: 3 cards per place. Posting pages: a short remote role. The first `fail_first` search calls
    (or all of them) raise ReadFailed. Returns the list of search URLs asked for."""
    asked = []

    def get(self, url, pace):
        self.calls += 1
        if "seeMoreJobPostings" in url:
            asked.append(url)
            if fail_always or len(asked) <= fail_first:
                raise linkedin_sweep.ReadFailed("IncompleteRead after 3 tries: " + url[:40])
            remote = "f_WT=2" in url
            base = 100 if remote else 200
            return "".join(_card(base + k, "remote" if remote else "local") for k in range(3))
        return '<div class="show-more-less-html__markup">Fully remote role. Pay is competitive.</div>'
    monkeypatch.setattr(linkedin_sweep.Client, "get", get)
    return asked


def _runlog(data):
    return [json.loads(l) for l in (data / "logs" / "li_runs.jsonl").read_text(encoding="utf-8").splitlines()]


BASE = ["--pages", "1", "--no-covered-skip"]


def test_server_errors_are_retried_then_fail_not_empty(monkeypatch):
    """A 5xx is a page that never loaded: retried, then ReadFailed, never "" (end of results)."""
    calls = []

    def err(req, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError("https://example.invalid/", 503, "unavailable", {}, None)
    monkeypatch.setattr(linkedin_sweep.urllib.request, "urlopen", err)
    monkeypatch.setattr(linkedin_sweep.time, "sleep", lambda s: None)
    try:
        linkedin_sweep.Client(max_calls=100).get("https://example.invalid/search", (0, 0))
        assert False, "expected ReadFailed"
    except linkedin_sweep.ReadFailed as e:
        assert "503" in str(e)
    assert len(calls) == linkedin_sweep.MAX_READ_ERRORS


def test_failed_search_page_is_retried_after_the_search_phase(tmp_path, monkeypatch):
    """One dropped page gets a second chance once the rest of the search is done, so a transient drop
    doesn't leave a group uncovered."""
    data = _env(tmp_path, monkeypatch)
    asked = _fake_linkedin(monkeypatch, fail_first=1)
    linkedin_sweep.main(["--terms", "chief of staff", "--no-postings"] + BASE)
    out = json.loads((data / "li_candidates.json").read_text(encoding="utf-8"))
    assert out["stats"]["failed_groups"] == []
    assert len(asked) == 3  # failed remote page, local-area page, retried remote page
    assert len(out["candidates"]) == 6
    assert _runlog(data)[-1]["failed_groups"] == []


def test_page_that_keeps_failing_is_recorded_in_the_run_log(tmp_path, monkeypatch):
    data = _env(tmp_path, monkeypatch)
    _fake_linkedin(monkeypatch, fail_always=True)
    linkedin_sweep.main(["--terms", "chief of staff", "--no-postings"] + BASE)
    out = json.loads((data / "li_candidates.json").read_text(encoding="utf-8"))
    assert len(out["stats"]["failed_groups"]) == 2
    assert all(f["retried"] for f in out["stats"]["failed_groups"])
    logged = _runlog(data)[-1]["failed_groups"]
    assert {f["where"] for f in logged} == {w[0] for w in linkedin_sweep.WHERE}
    assert all(f["page"] == 1 and f["term"] == "chief of staff" for f in logged)


def test_custom_terms_run_resumes_with_its_own_checkpoint(tmp_path, monkeypatch):
    """A gap-fill (--terms) that stops at the chunk limit resumes with --resume and the same --terms, and
    leaves a full run's checkpoint alone."""
    data = _env(tmp_path, monkeypatch)
    full = data / "li_checkpoint.json"
    full.write_text('{"sig": "full run"}', encoding="utf-8")
    _fake_linkedin(monkeypatch)
    args = ["--terms", "chief of staff", "--max-postings", "4"] + BASE
    res = linkedin_sweep.main(args)
    assert res["paused"] == 2
    assert len(list(data.glob("li_checkpoint.*.json"))) == 1
    res = linkedin_sweep.main(args + ["--resume"])
    assert res["paused"] == 0
    assert not list(data.glob("li_checkpoint.*.json"))  # finished: its checkpoint is cleaned up
    assert full.read_text(encoding="utf-8") == '{"sig": "full run"}'  # the full run's is untouched
    out = json.loads((data / "li_candidates.json").read_text(encoding="utf-8"))
    assert len(out["candidates"]) + len(out["screened_out"]) == 6


def test_resume_with_other_terms_does_not_find_the_checkpoint(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    _fake_linkedin(monkeypatch)
    linkedin_sweep.main(["--terms", "chief of staff", "--max-postings", "4"] + BASE)
    try:
        linkedin_sweep.main(["--terms", "program manager", "--resume"] + BASE)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "No checkpoint" in str(e)


def test_drain_runs_chunks_until_nothing_is_left(tmp_path, monkeypatch):
    data = _env(tmp_path, monkeypatch)
    _fake_linkedin(monkeypatch)
    res = linkedin_sweep.drain(["--drain", "--terms", "chief of staff", "--max-postings", "2"] + BASE)
    assert res["paused"] == 0
    assert [r["paused_left"] for r in _runlog(data)] == [4, 2, 0]
    assert not list(data.glob("li_checkpoint*.json"))


def test_drain_stops_at_the_chunk_cap_and_on_a_block():
    calls = []

    def run(argv):
        calls.append(argv)
        return {"paused": 5, "stopped": ""}
    linkedin_sweep.drain(["--drain", "--max-chunks", "3", "--fresh", "--terms", "a"], run=run)
    assert len(calls) == 3
    assert "--fresh" in calls[0] and "--fresh" not in calls[1] and "--resume" in calls[1]
    calls.clear()
    linkedin_sweep.drain(["--drain"], run=lambda argv: calls.append(argv) or {"paused": 5, "stopped": "blocked"})
    assert len(calls) == 1


def test_a_gap_run_keeps_earlier_candidates_and_backs_up_the_file(tmp_path, monkeypatch):
    """A second --terms run in the same window must not drop what the first one left in the file."""
    data = _env(tmp_path, monkeypatch)
    _fake_linkedin(monkeypatch)
    linkedin_sweep.main(["--terms", "chief of staff", "--no-postings"] + BASE)
    first = json.loads((data / "li_candidates.json").read_text(encoding="utf-8"))
    assert len(first["candidates"]) == 6
    monkeypatch.setattr(linkedin_sweep.Client, "get", lambda self, url, pace: "")
    linkedin_sweep.main(["--terms", "program manager", "--no-postings"] + BASE)
    second = json.loads((data / "li_candidates.json").read_text(encoding="utf-8"))
    assert {c["id"] for c in second["candidates"]} == {c["id"] for c in first["candidates"]}
    assert json.loads((data / "li_candidates.prev.json").read_text(encoding="utf-8")) == first
