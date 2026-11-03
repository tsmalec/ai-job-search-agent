"""oracle source (scripts/source_sweep.py): Oracle Recruiting Cloud requisition parsing, date window,
dedupe across query terms, and fetch failures recorded (never crashed). Fictional employer/data only."""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qsl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import source_sweep  # noqa: E402

HOST = "https://acme.fa.oraclecloud.com"
SITE = "CX_9001"
SITES = {"note": "test", "sites": [{"company": "Acme Corp", "host": HOST, "site": SITE,
                                    "careers": "https://acme.example.com/careers", "verified": "2026-01-01"}]}


def term_of(url):
    q = dict(parse_qsl(urlsplit(url).query))
    finder = unquote(q.get("finder", ""))
    m = re.search(r"keyword=([^,]*)", finder)
    return unquote(m.group(1)) if m else ""


TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def req(rid, title, location="Remote - United States", posted=None, workplace="Remote"):
    posted = posted or TODAY
    return {"Id": rid, "Title": title, "PrimaryLocation": location, "PrimaryLocationCountry": "United States",
            "WorkplaceType": workplace, "JobFamily": "Operations", "ShortDescriptionStr": "desc",
            "PostedDate": posted}


def write_sites(tmp_path, sites=None):
    (tmp_path / "oracle_sites.json").write_text(json.dumps(sites or SITES), encoding="utf-8", newline="\n")


def base_opts(limit=0):
    return dict(hours=24, limit=limit, only="", pace=0, vet=True, all=False)


def test_parses_requisitions_into_candidates(tmp_path, monkeypatch):
    write_sites(tmp_path)
    monkeypatch.setattr(source_sweep, "DATA", tmp_path)

    def fake_get(url, pace=0.4, **kw):
        t = term_of(url)
        if t == "software engineer":
            return {"items": [{"requisitionList": [req("1001", "Senior Software Engineer")]}]}, ""
        return {"items": [{"requisitionList": []}]}, ""

    monkeypatch.setattr(source_sweep, "get", fake_get)
    monkeypatch.setattr(source_sweep, "ORACLE_QUERIES", ["software engineer"])
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    tally = dict(postings=0, window=0, known=0)
    out, fails, extra = source_sweep.oracle(base_opts(), since, (set(), set(), set()), tally)

    assert fails == {} and extra == {}
    assert len(out) == 1
    c = out[0]
    assert c["company"] == "Acme Corp"
    assert c["role"] == "Senior Software Engineer"
    assert c["posting_id"] == "1001"
    assert c["url"] == f"{HOST}/hcmUI/CandidateExperience/en/sites/{SITE}/job/1001"
    assert c["search"] == "oracle"


def test_date_window_drops_old_postings(tmp_path, monkeypatch):
    write_sites(tmp_path)
    monkeypatch.setattr(source_sweep, "DATA", tmp_path)
    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

    def fake_get(url, pace=0.4, **kw):
        return {"items": [{"requisitionList": [req("2001", "Software Engineer", posted=old_date)]}]}, ""

    monkeypatch.setattr(source_sweep, "get", fake_get)
    monkeypatch.setattr(source_sweep, "ORACLE_QUERIES", ["software engineer"])
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    tally = dict(postings=0, window=0, known=0)
    out, fails, extra = source_sweep.oracle(base_opts(), since, (set(), set(), set()), tally)

    assert out == []
    assert tally["postings"] >= 1


def test_dedupes_ids_across_queries(tmp_path, monkeypatch):
    write_sites(tmp_path)
    monkeypatch.setattr(source_sweep, "DATA", tmp_path)
    calls = []

    def fake_get(url, pace=0.4, **kw):
        calls.append(term_of(url))
        # the same requisition shows up under both search terms
        return {"items": [{"requisitionList": [req("3001", "Software Engineer")]}]}, ""

    monkeypatch.setattr(source_sweep, "get", fake_get)
    monkeypatch.setattr(source_sweep, "ORACLE_QUERIES", ["software engineer", "project manager"])
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    tally = dict(postings=0, window=0, known=0)
    out, fails, extra = source_sweep.oracle(base_opts(), since, (set(), set(), set()), tally)

    assert len(calls) == 2  # both terms queried
    assert len(out) == 1  # but the requisition is only kept once


def test_fetch_failure_is_recorded_not_crashed(tmp_path, monkeypatch):
    write_sites(tmp_path)
    monkeypatch.setattr(source_sweep, "DATA", tmp_path)

    def fake_get(url, pace=0.4, **kw):
        t = term_of(url)
        if t == "software engineer":
            return None, "HTTP 500"
        return {"items": [{"requisitionList": [req("4001", "Senior Software Engineer")]}]}, ""

    monkeypatch.setattr(source_sweep, "get", fake_get)
    monkeypatch.setattr(source_sweep, "ORACLE_QUERIES", ["software engineer", "project manager"])
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    tally = dict(postings=0, window=0, known=0)
    out, fails, extra = source_sweep.oracle(base_opts(), since, (set(), set(), set()), tally)

    assert fails == {"Acme Corp / software engineer": "HTTP 500"}
    assert len(out) == 1  # the other term's result still comes through
    assert out[0]["posting_id"] == "4001"


def test_no_sites_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(source_sweep, "DATA", tmp_path)  # no oracle_sites.json written here
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    tally = dict(postings=0, window=0, known=0)
    out, fails, extra = source_sweep.oracle(base_opts(), since, (set(), set(), set()), tally)
    assert out == [] and fails == {} and extra == {}


def test_watchlist_reports_covered_by_oracle(tmp_path, monkeypatch):
    import board_sweep  # noqa: E402
    monkeypatch.setattr(source_sweep, "DATA", tmp_path)
    write_sites(tmp_path)
    (tmp_path / "watchlist.json").write_text(json.dumps({"watching": [
        {"company": "Acme Corp", "careers": "https://acme.example.com/careers"},
        {"company": "Globex", "careers": "https://job-boards.greenhouse.io/globex"},
    ]}), encoding="utf-8", newline="\n")
    out, fails, extra = source_sweep.watchlist({}, None, None, None)
    assert out == [] and fails == {}
    assert extra["covered_by_oracle"] == ["Acme Corp"]
    assert extra["covered_by_board_sweep"] == ["Globex"]
    assert "Acme Corp" not in extra["manual"]
