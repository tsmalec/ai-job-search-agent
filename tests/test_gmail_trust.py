"""Red team, mail that only claims to be the company must never change a row on its own."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from gmail_sweep import sender_check  # noqa: E402

ROW = {"company": "Acme Corp", "role": "Program Manager"}


def test_spoofed_display_name_not_trusted():
    msg = {"from": '"Acme Corp" <x@evil.example>', "auth": "mx.google.com; dkim=pass header.d=evil.example"}
    assert sender_check(msg, ROW)[0] is False


def test_company_domain_needs_dkim_pass():
    assert sender_check({"from": "Acme <jobs@acme.example>", "auth": "dkim=fail"}, ROW)[0] is False
    assert sender_check({"from": "Acme <jobs@acme.example>", "auth": "dkim=pass; dmarc=fail"}, ROW)[0] is False
    assert sender_check({"from": "Acme <jobs@acme.example>", "auth": "dkim=pass header.d=acme.example; dmarc=pass"}, ROW)[0] is True
    assert sender_check({"from": "Acme <jobs@acme.example>", "auth": "dkim=pass header.d=evil.example"}, ROW)[0] is False


def test_company_name_inside_another_domain_not_trusted():
    assert sender_check({"from": "jobs@acme-hiring.example", "auth": "dkim=pass header.d=acme-hiring.example"}, ROW)[0] is False
    assert sender_check({"from": "jobs@mail.acme.example", "auth": "dkim=pass header.i=@acme.example"}, ROW)[0] is True


def test_ats_domain_trusted_and_lookalike_not():
    assert sender_check({"from": "no-reply@us.greenhouse-mail.io", "auth": "dkim=pass header.d=greenhouse-mail.io"}, ROW)[0] is True
    assert sender_check({"from": "no-reply@greenhouse-mail.io.evil.example", "auth": "dkim=pass"}, ROW)[0] is False
    assert sender_check({"from": "no-reply@evilgreenhouse-mail.example", "auth": "dkim=pass"}, ROW)[0] is False


def test_no_header_live_mail_not_trusted():
    assert sender_check({"from": "Acme <jobs@acme.example>", "auth": ""}, ROW)[0] is False
