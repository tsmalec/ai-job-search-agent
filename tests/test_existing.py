"""existing()/url_key regression: query-string posting IDs must not merge. Run: python3 -m pytest tests"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from common import existing
rows = [{"url": "https://acme.taleo.net/careersection/10000/jobdetail.ftl?job=1111111&lang=en", "role": "A"}]
checks = [
    (existing(rows, "https://acme.taleo.net/careersection/10000/jobdetail.ftl?job=2222222&lang=en"), None),
    (existing(rows, "https://acme.taleo.net/careersection/10000/jobdetail.ftl?lang=en&job=1111111"), 0),
    (existing([{"url": "https://careers.example.org/jobs/40001/job", "role": "B"}],
              "https://careers.example.org/jobs/40001/job?utm_source=indeed_integration&iis=Job%20Board"), 0),
]


def test_existing():
    for n, (got, want) in enumerate(checks):
        assert got == want, f"case {n}: got {got}, want {want}"


def test_bare_url_refused():  # a site home page would dedupe-match every row on that site
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import log
    assert log.bare_url("https://www.indeed.com/")
    assert not log.bare_url("https://www.indeed.com/viewjob?jk=1234567")


if __name__ == "__main__":
    bad = sum(got != want for got, want in checks)
    print("PASS" if not bad else f"FAIL {bad}")
    sys.exit(bool(bad))


def test_log_check_validates():  # verdict, status and bare-URL checks all fire
    import pytest
    import log
    for bad in ({"verdict": "purple"}, {"status": "maybe"}, {"url": "https://www.indeed.com/"}):
        with pytest.raises(SystemExit):
            log.check(dict(bad))
    log.check({"verdict": "green", "status": "applied", "url": "https://x.com/jobs/123456"})
