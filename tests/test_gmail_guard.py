"""Risk flags for rejection emails: every rejection updates the row; the checks and risk record how far to trust it.

Generic names and wording only: this file ships in the public template.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from gmail_sweep import assess_rejection  # noqa: E402

ROW = {"company": "Acme Corp", "role": "Program Lead (R-100002)", "url": "", "status": "applied"}
GOOD_AUTH = "mx.google.com; dkim=pass header.i=@example.com; dmarc=pass (p=REJECT) header.from=example.com"
STRONG = "Thank you for applying to req R-100002. We have decided not to move forward with your application."
SOFT = "Thank you for applying to req R-100002. We moved in another direction for this role."


def msg(body=STRONG, sender="Acme Careers <careers@example.com>", auth=GOOD_AUTH, subject="Your application"):
    return {"from": sender, "subject": subject, "body": body, "auth": auth}


def test_verified_strong_rejection_is_low_risk():
    checks, risk = assess_rejection(msg(), ROW, sender_ok=True)
    assert risk == "low"
    assert "wording:strong" in checks and "sender:pass" in checks


def test_soft_wording_is_medium_risk():
    _, risk = assess_rejection(msg(body=SOFT), ROW, sender_ok=True)
    assert risk == "med"


def test_unverified_sender_is_high_risk():
    checks, risk = assess_rejection(msg(), ROW, sender_ok=False)
    assert risk == "high"
    assert "sender:fail" in checks


def test_company_not_named_is_high_risk():
    _, risk = assess_rejection(msg(sender="Other Co <jobs@other.example>", body=STRONG.replace("req", "requisition")),
                               ROW, sender_ok=True)
    assert risk == "high"


def test_rejection_on_interview_row_is_high_risk_but_still_updates():
    row = {**ROW, "status": "interview"}
    checks, risk = assess_rejection(msg(), row, sender_ok=True)
    assert risk == "high"


def test_missing_auth_header_is_recorded_as_none_not_failed():
    checks, _ = assess_rejection(msg(auth=""), ROW, sender_ok=True)
    assert "dkim:none" in checks and "dmarc:none" in checks


def test_requisition_mismatch_is_medium_risk():
    body = "Thank you for applying to req R-100001. We have decided not to move forward with your application."
    checks, risk = assess_rejection(msg(body=body), ROW, sender_ok=True)
    assert "req:fail" in checks
    assert risk == "med"


def test_short_company_word_counts_as_name():
    row = {**ROW, "company": "Beta Systems"}
    checks, _ = assess_rejection(msg(sender="Beta Careers <join@careers.beta.example>"), row, sender_ok=True)
    assert "name:pass" in checks
