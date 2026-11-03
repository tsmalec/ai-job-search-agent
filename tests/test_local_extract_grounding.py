import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from local_extract import grounded, is_none_answer  # noqa: E402

POSTING = """Requirements
- 5+ years delivering programs
- Ability to travel 30%-50% of the time
- Manage a project of up to $3.5M in budget
- 7 + years of experience
- Financial Planning & Analysis
"""


def test_symbols_do_not_drop_real_requirements():
    items = ["5+ years delivering programs", "Ability to travel 30%-50% of the time",
             "Manage a project of up to $3.5M in budget", "7 + years of experience",
             "Financial Planning & Analysis"]
    assert len(grounded(items, POSTING)) == len(items)


def test_invented_item_is_still_dropped():
    assert grounded(["Ten years of rocket science"], POSTING) == []


def test_none_answers_are_detected():
    for s in ["None", "None.", "None provided in the section.", "N/A", "No qualifications listed"]:
        assert is_none_answer(s)
    assert not is_none_answer("None of the above travel restrictions apply to remote staff")
