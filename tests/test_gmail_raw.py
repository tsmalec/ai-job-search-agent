"""The cloud sweep reads raw MIME from the Gmail connector. Parsed messages must keep what the checks need.

Generic names only: this file ships in the public template.
"""
import base64, os, sys
from email.message import EmailMessage
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from gmail_sweep import parse_raw, assess_rejection  # noqa: E402


def raw_b64(sender="Acme Careers <careers@example.com>", auth="mx.google.com; dkim=pass header.i=@example.com; dmarc=pass"):
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = sender, "Your application", "Wed, 28 Oct 2026 23:50:45 +0000"
    if auth:
        m["Authentication-Results"] = auth
    m.set_content("Thank you for applying to req R-100002. We have decided not to move forward with your application.")
    return base64.urlsafe_b64encode(m.as_bytes()).decode().rstrip("=")


def test_parse_raw_keeps_sender_auth_subject_and_body():
    msg = parse_raw(raw_b64())
    assert "careers@example.com" in msg["from"]
    assert "dkim=pass" in msg["auth"] and "dmarc=pass" in msg["auth"]
    assert msg["subject"] == "Your application"
    assert "not to move forward" in msg["body"]


def test_parse_raw_converts_date_to_eastern():
    msg = parse_raw(raw_b64())
    assert msg["date"].startswith("2026-10-28T19:50")  # 23:50 UTC is 7:50 pm Eastern (EDT)


def test_raw_message_gets_the_same_risk_as_a_live_one():
    row = {"company": "Acme Corp", "role": "Program Lead (R-100002)", "url": "", "status": "applied"}
    checks, risk = assess_rejection(parse_raw(raw_b64()), row, sender_ok=True)
    assert "dkim:pass" in checks and "dmarc:pass" in checks
    assert risk == "low"


def test_missing_auth_header_is_none_not_fail():
    checks, _ = assess_rejection(parse_raw(raw_b64(auth="")), {"company": "Acme Corp", "role": "", "url": ""},
                                 sender_ok=True)
    assert "dkim:none" in checks
