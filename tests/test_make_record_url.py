import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from make_record import canonical_url  # noqa: E402


def test_fetched_url_beats_a_board_root_link():
    text = "TITLE: PM\nURL: https://job-boards.greenhouse.io/acme/jobs/1\nSOURCE: Greenhouse\n\nBody"
    assert canonical_url(text, "https://job-boards.greenhouse.io/acme") == "https://job-boards.greenhouse.io/acme/jobs/1"


def test_pasted_text_keeps_the_given_url():
    assert canonical_url("Just pasted words\nmore", "https://x.com/j/1") == "https://x.com/j/1"
    assert canonical_url("Just pasted words", "") == "n/a"
