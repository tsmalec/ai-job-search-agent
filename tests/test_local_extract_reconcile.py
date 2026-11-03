import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from local_extract import reconcile  # noqa: E402

M_REQ = ["2+ years experience of provider data experience with a health insurance company",
         "1+ year of direct people management experience",
         "1+ years of experience using data and metrics to drive improvements"]


def test_model_wins_and_fragments_are_not_conflicts():
    rules = ["2+ years provider data health insurance", "1+ years direct people management"]
    req, pref, conflicts, source = reconcile(M_REQ, [], rules, [], True)
    assert req == M_REQ and conflicts == [] and source == "model"


def test_disagreements_are_reported_not_merged():
    req, pref, conflicts, _ = reconcile(M_REQ, ["travel up to 25%"], ["work according work location", "travel 25%"], [], True)
    assert "work according work location" not in req
    assert ("rules only (required)", "work according work location") in conflicts
    assert ("rules says required, model says preferred", "travel 25%") in conflicts


def test_falls_back_to_rules_when_no_sections():
    rules = ["bachelor's degree in business", "7+ years of program management"]
    req, pref, conflicts, source = reconcile([], [], rules, ["sql"], False)
    assert req == rules and pref == ["sql"] and conflicts == [] and "no sections" in source


def test_falls_back_when_model_found_too_few():
    req, _, _, source = reconcile(["one item"], [], ["a second real requirement here"], [], True)
    assert req == ["one item", "a second real requirement here"] and "only 1" in source
