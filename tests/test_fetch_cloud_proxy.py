"""Behind a cloud sandbox's policy-enforcing proxy, fetch_jd.py reads public postings; every other guard still holds."""
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_jd  # noqa: E402


def resolves_to(monkeypatch, addr):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (addr, 443))])


@pytest.fixture
def proxied(monkeypatch):
    monkeypatch.setattr(fetch_jd, "_PROXIED", True)
    monkeypatch.delenv("JOBSEARCH_ALLOW_PROXY", raising=False)


def test_a_proxy_without_the_cloud_flag_is_still_refused(proxied, monkeypatch):
    monkeypatch.setattr(fetch_jd, "_CLOUD_PROXY", False)
    with pytest.raises(ValueError, match="proxy is set"):
        fetch_jd._safe_url("https://boards.example.com/acme/jobs/1")


def test_cloud_proxy_accepts_its_dns_stub_address(proxied, monkeypatch):
    monkeypatch.setattr(fetch_jd, "_CLOUD_PROXY", True)
    resolves_to(monkeypatch, "198.18.129.233")
    assert fetch_jd._safe_url("https://boards.example.com/acme/jobs/1").startswith("https://")


@pytest.mark.parametrize("addr", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.9", "100.64.0.1"])
def test_cloud_proxy_still_refuses_every_other_non_public_answer(proxied, monkeypatch, addr):
    monkeypatch.setattr(fetch_jd, "_CLOUD_PROXY", True)
    resolves_to(monkeypatch, addr)
    with pytest.raises(ValueError, match="non-public address"):
        fetch_jd._safe_url("https://boards.example.com/acme/jobs/1")


@pytest.mark.parametrize("url", ["http://localhost/x", "https://db.internal/x", "http://198.18.0.5/x", "http://10.1.1.1/x", "file:///etc/passwd"])
def test_cloud_proxy_still_refuses_local_names_literal_ips_and_other_schemes(proxied, monkeypatch, url):
    monkeypatch.setattr(fetch_jd, "_CLOUD_PROXY", True)
    with pytest.raises(ValueError):
        fetch_jd._safe_url(url)
