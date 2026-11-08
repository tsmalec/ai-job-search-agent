"""fetch_jd.fetch(): LinkedIn is browser-only unless --linkedin-guest is passed; a block page is never read as a posting."""
import os, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_jd  # noqa: E402

URL = "https://www.linkedin.com/jobs/view/1234567/"
PAGE = ('<h2 class="top-card-layout__title">Operations Manager</h2>'
        '<a class="topcard__org-name-link">Acme</a>'
        '<div class="show-more-less-html__markup">' + "<p>Acme needs an operations manager to run weekly reporting.</p>" * 8 + "</div>")
BLOCK = "<html><body>Sign in to LinkedIn. " + "Join now. " * 40 + "</body></html>"


def test_refused_by_default(monkeypatch):
    monkeypatch.setattr(fetch_jd, "get", lambda *a, **k: pytest.fail("must not fetch"))
    with pytest.raises(SystemExit) as e:
        fetch_jd.fetch(URL)
    assert "browser only" in str(e.value)


def test_guest_flag_reads_the_posting_block(monkeypatch):
    monkeypatch.setattr(fetch_jd, "get", lambda u, *a, **k: PAGE)
    d = fetch_jd.fetch(URL, linkedin_guest=True)
    assert d["source"] == "LinkedIn guest page" and d["title"] == "Operations Manager" and "weekly reporting" in d["text"]
    assert d["remote"] == ""  # the guest page has no workplace tag: the screen keeps its "confirm location" flag


def test_block_page_is_never_read_as_the_posting(monkeypatch):
    monkeypatch.setattr(fetch_jd, "get", lambda u, *a, **k: BLOCK)
    with pytest.raises(SystemExit) as e:
        fetch_jd.fetch(URL, linkedin_guest=True)
    assert "no posting text" in str(e.value)  # and the generic page reader never ran on a LinkedIn URL
