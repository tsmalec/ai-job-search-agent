"""Regression tests for pay-parsing formats screen_leads.py's ranges() once missed.
Run: python3 -m pytest tests"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from screen_leads import ranges, screen  # noqa: E402


def test_plain_six_digit_range_no_commas_second_figure_no_dollar():
    """'$150000 - 160000 / yr' (no commas; the second figure has no $)."""
    rs = ranges("Base salary $150000 - 160000 / yr.")
    assert len(rs) == 1
    lo, hi, _, hourly = rs[0]
    assert (lo, hi, hourly) == (150000, 160000, False)


def test_monthly_range_is_annualized():
    """'USD Monthly: 6169-13572' -> x12 annual."""
    rs = ranges("Compensation: USD Monthly: 6169-13572.")
    assert len(rs) == 1
    lo, hi, _, hourly = rs[0]
    assert (lo, hi, hourly) == (74028, 162864, False)


def test_smartrecruiters_usd_prefixed_dash_yearly():
    """'USD 114,100 - USD 211,900 - yearly' (SmartRecruiters format)."""
    rs = ranges("Pay: USD 114,100 - USD 211,900 - yearly.")
    assert len(rs) == 1
    lo, hi, _, hourly = rs[0]
    assert (lo, hi, hourly) == (114100, 211900, False)


def no_pay_not_listed(flags):
    return not any("pay not listed" in f.lower() for f in flags)


def test_screen_no_longer_holds_on_any_of_the_three_formats():
    for pay_line in ("Base salary $150000 - 160000 / yr.",
                      "Compensation: USD Monthly: 6169-13572.",
                      "Pay: USD 114,100 - USD 211,900 - yearly."):
        _, _, flags = screen(f"Remote US. {pay_line}", "Remote", "Program Manager")
        assert no_pay_not_listed(flags), flags
