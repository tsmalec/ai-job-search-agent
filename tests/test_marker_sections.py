import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from local_extract import marker_sections  # noqa: E402

TEXT = """Intro line.

## What you'll do
- Build things

## Minimum Qualifications
- 5+ years of ops
- Bachelor's degree

## Nice to Have
- MBA

## Our Culture
- We like pizza
"""


def test_headers_decide_the_list_and_unknown_headers_are_reported():
    req, pref, report = marker_sections(TEXT)
    assert req == ["5+ years of ops", "Bachelor's degree"]
    assert pref == ["MBA"]
    assert ("What you'll do", "other", 1, "header") in report and ("Our Culture", "other", 1, "header") in report


def test_plain_lines_count_when_a_section_has_no_bullets():
    req, _, _ = marker_sections("## Requirements\nFive years of ops\nA degree\n")
    assert req == ["Five years of ops", "A degree"]


def test_unknown_header_is_classified_by_its_items():
    text = ("## The Ideal Fit\n- 5+ years of ops experience\n- Bachelor's degree\n"
            "## Extras\n- Experience with Looker is a plus\n- MBA preferred\n")
    req, pref, report = marker_sections(text)
    assert req == ["5+ years of ops experience", "Bachelor's degree"]
    assert pref == ["Experience with Looker is a plus", "MBA preferred"]
    assert [r[3] for r in report] == ["content", "content"]


def test_duty_sections_stay_unclassified():
    req, _, _ = marker_sections("## The Day To Day\n- Lead the roadmap\n- Manage vendors\n- Own reporting\n")
    assert req == []


def test_judge_has_the_last_word_on_unknown_headers():
    text = "## Fit\n- 5+ years of ops experience\n- Bachelor's degree\n"
    req, pref, report = marker_sections(text, judge=lambda h, items: "other")
    assert req == [] and report == [("Fit", "other", 2, "model")]
    req, _, _ = marker_sections(text, judge=lambda h, items: None)
    assert req == ["5+ years of ops experience", "Bachelor's degree"]


def test_a_sentence_styled_as_heading_gets_no_header_class():
    long = "Guided by our core values we provide health, financial and lifestyle benefits and an employee experience " * 2
    req, pref, report = marker_sections(f"## Requirements\n- 5+ years of ops\n## {long}\n- dental\n- vision\n", use_content=False)
    assert req == ["5+ years of ops"] and report[1][1] is None and report[1][0].endswith("...")


def test_item_cues_override_the_section():
    from local_extract import apply_cues
    req, pref = apply_cues(["bachelor's degree required", "pmp certification preferred"], ["must have 3 years of ops", "mba"])
    assert req == ["bachelor's degree required", "must have 3 years of ops"]
    assert pref == ["pmp certification preferred", "mba"]


def test_big_unknown_section_full_of_qualifications_is_flagged_mixed():
    from local_extract import has_mixed_section
    body = "".join(f"- Strong experience with tool {i}\n" for i in range(14))
    assert has_mixed_section("## Job Description\n" + body)
    assert not has_mixed_section("## Requirements\n" + body)


def test_trait_and_duty_screen_asks_only_about_flagged_lines():
    from local_extract import screen_required
    asked = []

    def judge(item):
        asked.append(item)
        return {"curious and self-directed": "trait", "own reporting and metrics": "duty"}.get(item, "qual")

    kept, dropped = screen_required(["5+ years of ops", "curious and self-directed", "own reporting and metrics",
                                     "strong problem-solving skills"], judge)
    assert asked == ["curious and self-directed", "own reporting and metrics", "strong problem-solving skills"]
    assert kept == ["5+ years of ops", "strong problem-solving skills"]
    assert dropped == [("curious and self-directed", "trait"), ("own reporting and metrics", "duty")]


def test_unclear_judgement_keeps_the_line():
    from local_extract import screen_required
    kept, dropped = screen_required(["proactive attitude"], lambda item: None)
    assert kept == ["proactive attitude"] and dropped == []


def test_split_blob_breaks_a_one_paragraph_posting_into_sentences():
    from local_extract import split_blob
    blob = ("Own the roadmap. " * 120).strip()
    assert split_blob(blob).count("\n") >= 50
    assert split_blob("Short.\nPosting.") == "Short.\nPosting."


def test_reconcile_trusts_a_short_marker_list():
    from local_extract import reconcile
    req, _, _, source = reconcile(["bachelor's degree", "4 years in training"], [], ["rules extra thing here"], [], True, trusted=True)
    assert req == ["bachelor's degree", "4 years in training"] and source == "model"


def test_essential_functions_are_duties_not_requirements():
    req, _, report = marker_sections("## Essential Functions:\n- Develop the project schedule\n- Maintain RAID\n", use_content=False)
    assert req == [] and report[0][1] == "other"
    req, _, _ = marker_sections("## Essential Criteria:\n- 5+ years of pricing experience\n", use_content=False)
    assert req == ["5+ years of pricing experience"]


def test_bare_required_and_preferred_lines_split_a_section():
    text = "## Job Description\n- EDUCATION\nRequired\n- Bachelor's degree\nPreferred\n- Master's degree\n- MBA\n"
    req, pref, _ = marker_sections(text, use_content=False)
    assert "Bachelor's degree" in req and pref == ["Master's degree", "MBA"]


def test_weak_cues_only_move_short_lines():
    from local_extract import apply_cues
    long = "4+ years in program management, enablement, or L&D, ideally in a B2B SaaS or technical sales environment"
    req, pref = apply_cues([long, "SQL experience ideally", "MBA preferred in finance or related fields"], [])
    assert long in req and pref == ["SQL experience ideally", "MBA preferred in finance or related fields"]


def test_single_line_section_uses_its_own_cue():
    _, pref, _ = marker_sections("## Licensure and Certification:\n- PMP certification is strongly preferred\n", use_content=False)
    assert pref == ["PMP certification is strongly preferred"]


def test_sublabelled_section_is_not_mixed():
    from local_extract import has_mixed_section
    body = "".join(f"- Strong experience with tool {i}\n" for i in range(14))
    assert has_mixed_section("## Job Description\n" + body)
    assert not has_mixed_section("## Job Description\nRequired\n" + body)


def test_essential_duties_header_is_not_a_requirements_header():
    req, _, report = marker_sections("## Essential Duties/Principal Responsibilities:\n- Review project documents\n- Prepare billing\n", use_content=False)
    assert req == [] and report[0][1] == "other"
    req, _, _ = marker_sections("## Key Responsibilities and Qualifications\n- 5+ years of ops\n", use_content=False)
    assert req == ["5+ years of ops"]


def test_required_and_preferred_qualifications_lines_split_a_section():
    text = ("## Qualifications\nRequired Qualifications:\n- Bachelor's degree\nOther Requirements:\n- Background check\n"
            "Preferred Qualifications:\n- Master's degree\n- Power BI\n")
    req, pref, _ = marker_sections(text, use_content=False)
    assert "Bachelor's degree" in req and pref == ["Master's degree", "Power BI"]


def test_a_mid_sentence_preferred_in_a_long_line_does_not_move_it():
    from local_extract import apply_cues
    long = ("Extensive experience with SaaS tools such as Salesforce, Planhat, Zendesk, and exposure to LMS/CMS administration; "
            "administration experience with a CS platform or support platform strongly preferred and valued by the team")
    req, pref = apply_cues([long, "Experience working in large-scale assessment preferred"], [])
    # the ';' clause that says "strongly preferred" is split off; the required half stays required
    assert req == [long.split(";")[0]]
    assert long.split(";")[1].strip() in pref and "Experience working in large-scale assessment preferred" in pref


def test_the_word_required_inside_a_preferred_line_does_not_move_it():
    from local_extract import apply_cues
    line = "Experience partnering with Risk and Control functions on roadmaps, governance requirements, and required artifacts"
    req, pref = apply_cues([], [line, "Bachelor's degree is required"])
    assert pref == [line] and req == ["Bachelor's degree is required"]
