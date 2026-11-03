"""Row matching for gmail_sweep: a requisition number in the email picks the right row when one company has several.

Generic names and numbers only: this file ships in the public template.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from gmail_sweep import find_rows, req_ids  # noqa: E402

ROWS = [
    {"company": "Acme Corp", "role": "Senior Analyst (R-100001)", "url": ""},
    {"company": "Acme Corp", "role": "Program Lead (R-100002)", "url": ""},
]


def test_requisition_number_picks_the_matching_row():
    msg = {"from": "Acme Careers <careers@example.com>", "subject": "Thank you for applying",
           "body": "Thank you for applying to requisition R-100002. We appreciate your interest."}
    assert find_rows(msg, ROWS) == [1]


def test_other_requisition_picks_the_other_row():
    msg = {"from": "Acme Careers <careers@example.com>", "subject": "Thank you for applying",
           "body": "Thank you for applying to requisition R-100001. We appreciate your interest."}
    assert find_rows(msg, ROWS) == [0]


def test_plain_digit_requisition_numbers_are_read():
    assert "100200" in req_ids("Director of Customer Success (#100200)")
    assert "JR100126" in req_ids("Subject: Jane Doe JR100126 Operations Manager")
    assert "14479" in req_ids("Product Operations Manager position 14479 at BDO")
