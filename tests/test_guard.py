"""guard.py is the truth gate (A_Rules §1): it must pass the clean example and block each known overreach.
Run: python3 -m pytest tests"""
import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import guard  # noqa: E402

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "scripts", "resume_example.json")


def run(content, tmp_path):
    f = tmp_path / "content.json"
    f.write_text(json.dumps(content), encoding="utf-8", newline="\n")
    return guard.check(str(f))


def base():
    with open(EXAMPLE, encoding="utf-8") as f:
        return json.load(f)


def test_clean_example_passes(tmp_path):
    assert run(base(), tmp_path) == 0


def test_title_not_in_b_blocked(tmp_path):
    c = base()
    c["jobs"][0]["title"] = "Director of Operations"
    assert run(c, tmp_path) == 1


def test_bullet_id_not_in_b_blocked(tmp_path):
    c = base()
    c["jobs"][0]["bullet_ids"][1] = "ZZZ-99"
    assert run(c, tmp_path) == 1


def test_bullet_under_wrong_role_blocked(tmp_path):
    c = base()  # a bullet can't sit under a role B doesn't file it under
    c["jobs"][1]["bullets"].append("<bullet>")
    c["jobs"][1]["bullet_ids"].append("LEAD-01")
    assert run(c, tmp_path) == 1


def test_decade_of_leadership_blocked(tmp_path):
    c = base()
    c["summary"] += " Brings a decade of program management leadership."
    assert run(c, tmp_path) == 1


def test_invented_figure_blocked(tmp_path):
    c = copy.deepcopy(base())
    c["summary"] += " Cut cycle time by 87%."
    assert run(c, tmp_path) == 1
