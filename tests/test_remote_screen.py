"""Regression cases for the remote screen. Run: python3 -m pytest tests  (or python3 tests/test_remote_screen.py)
Illustrative cases the screener must fail, plus controls that must still pass."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from screen_leads import screen

PAY = " Base salary $110,000 - $125,000."
CASES = [
    # (name, location, text, should_fail_remote)
    ("Acme Company Ops Mgr 1333333333 (hub commute)", "Remote",
     "This role is remote. You must live within commuting distance of one of our hub offices; "
     "time in the office is expected." + PAY, True),
    ("Acme Logistics Ops 1444444444 (remote co-located)", "Chicago, IL (On-site)",
     "As a remote co-located team, we're anchored in regional hubs." + PAY, True),
    ("Globex R00001 (WFA perk, on-site)", "Springfield, IL",
     "This position is located in Springfield, IL or Boston, MA. On-site.\n"
     "Benefits include a few weeks a year of working from anywhere." + PAY, True),
    ("LinkedIn tag On-site, blurb says remote", "San Francisco, CA (On-site)",
     "Join our remote-friendly team." + PAY, True),
    ("Control: fully remote", "Remote - US",
     "This is a fully remote role open to candidates anywhere in the US." + PAY, False),
    ("Control: primarily remote + offsites", "Remote",
     "This role is primarily remote with occasional in-person travel for team offsites and conferences." + PAY, False),
]


def remote_fail(fails):
    return any(k in f.lower() for f in fails for k in ("remote", "hybrid", "on-site", "onsite", "workplace", "hub"))


import pytest  # noqa: E402


@pytest.mark.parametrize("name,loc,text,want", CASES, ids=[c[0] for c in CASES])
def test_remote_screen(name, loc, text, want):
    _, fails, _ = screen(text, loc)
    assert remote_fail(fails) == want, fails


if __name__ == "__main__":  # still runnable directly: python3 tests/test_remote_screen.py
    bad = 0
    for name, loc, text, want in CASES:
        _, fails, _ = screen(text, loc)
        ok = remote_fail(fails) == want
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}  -> {fails or 'no fails'}")
    sys.exit(1 if bad else 0)


def test_streamline_knockouts_and_watch():
    import sys; sys.path.insert(0, "scripts")
    from screen_leads import screen
    v, f, _ = screen("Remote US. Base $150,000-$165,000. Must have an active Top Secret clearance.", company="Acme")
    assert v == "FAIL" and f[0].startswith("Knockout")
    assert screen("Remote US. Base $150,000-$165,000. Able to obtain a Secret clearance.", company="Acme")[0] == "PASS"
    _, _, fl = screen("Remote US. Base $150,000-$165,000. PMP required.", company="Example Corp")
    assert any("PMP" in x for x in fl) and any(x.startswith("🔍") for x in fl)


def test_drop_audit_title_rules():
    import re, sys; sys.path.insert(0, "scripts")
    from board_sweep import DROP_TITLE, DROP_TITLE_DEFAULT, TITLE_RE, LANE_RE, offlist_needs_pay
    # The built-in drop list (an operations search): senior titles kept, engineer/specialist titles dropped
    d = re.compile(DROP_TITLE_DEFAULT)
    assert not d.search("associate director, strategic operations") and not d.search("lead specialist, program management")
    assert not d.search("sr. engineering manager - incentive compensation ops") and not d.search("operational readiness lead")
    assert d.search("benefits specialist") and d.search("senior software engineer")
    # config.json search.drop_title replaces it: the sample engineer's search keeps engineer titles
    keep = lambda t: not DROP_TITLE.search(t.lower()) and (TITLE_RE.search(t.lower()) or LANE_RE.search(t.lower()))
    assert keep("Senior Full-Stack Engineer") and not keep("Software Engineering Intern")
    assert offlist_needs_pay("substation project manager") and not offlist_needs_pay("director of operations")
