"""Regression cases for gmail_sweep.classify(), from real rejection and acknowledgment wording (Oct 2026).

Text is generic on purpose (no real names or companies): this file ships in the public template.
"""
import os, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from gmail_sweep import classify  # noqa: E402

REJECTIONS = [
    "Thank you for taking the time to apply for the role. We received many qualified applicants and ultimately moved in another direction.",
    "Thank you for your interest in the position. After careful consideration, we have determined that our current position is not ideally suited to your talents.",
    "Thank you for taking the time to apply for the position. We've determined your background isn't the strongest match for the role at this time.",
    "Thank you for taking the time to apply for the role. Unfortunately, your qualifications are not a match for the role at this time.",
    "Thank you for applying. Our hiring timeline has shifted, and we will no longer be moving forward with the interview process for this role.",
    "Thank you for applying for the position. Although we were impressed, we decided to move forward with other applicants who demonstrated a stronger alignment.",
    "Thank you for considering a role with us. Our team has chosen to proceed with other candidates whose qualifications align more closely.",
    "Thank you for your application. We regret to inform you that we won't be moving forward with your application at this time.",
    "Thank you for applying. Unfortunately, this position has been filled.",
    "Thank you for applying. After careful review, we will not be moving forward with your application at this time.",
    "Thank you for applying. After careful consideration, we've decided not to move forward with your candidacy at this time.",
    "Thank you for your interest in joining the team. We are writing to let you know that we have filled the Director, Revenue Strategy & Planning role you applied for.",
]

# Acknowledgments and disclaimers that must NOT read as rejections or offers.
NOT_REJECTIONS = [
    "Thank you for taking the time to submit your application. It's being evaluated. Or you were not selected for the role. How's your profile?",
    "Thank you for applying. Note: this email is not an offer of employment.",
    "Thank you for your interest in the position. We have received your application and will be in touch if your experience matches the role.",
    "Thank you for applying. We have received your job application. A member of our Recruiting team will contact you.",
]

NOT_OFFERS = [
    "Thank you for applying. Prepare for an interview and a potential offer of employment.",
    "Thank you for applying. Note: this email is not an offer of employment.",
]


@pytest.mark.parametrize("text", REJECTIONS)
def test_real_rejection_wording_is_rejection(text):
    assert classify(text)[0] == "rejection"


@pytest.mark.parametrize("text", NOT_REJECTIONS)
def test_acknowledgment_is_not_rejection(text):
    assert classify(text)[0] != "rejection"


@pytest.mark.parametrize("text", NOT_OFFERS)
def test_not_an_offer_is_not_offer(text):
    assert classify(text)[0] != "offer"
