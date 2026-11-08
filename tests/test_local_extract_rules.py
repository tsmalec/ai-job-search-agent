import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import local_extract as le


def test_generic_subheadings_under_preferred_stay_preferred():
    text = ("## MINIMUM QUALIFICATIONS:\n\n## EXPERIENCE:\n\n- 5 years in planning\n\n"
            "## PREFERRED QUALIFICATIONS:\n\n## EXPERIENCE:\n\n- Experience in health plans\n- Experience leading planning cycles\n")
    req, pref, _ = le.marker_sections(text, use_content=False)
    assert req == ["5 years in planning"]
    assert pref == ["Experience in health plans", "Experience leading planning cycles"]


def test_plain_labels_without_colon_open_sub_sections():
    text = ("## Job Description & Summary\n\nWhat You Must Have\n- At least a Bachelor's degree\n- At least 6 years of experience\n"
            "What Sets You Apart\n- Preference for accounting or actuarial study\n- Leading client service operations\n")
    req, pref, _ = le.marker_sections(text, use_content=False)
    assert req == ["At least a Bachelor's degree", "At least 6 years of experience"]
    assert pref == ["Preference for accounting or actuarial study", "Leading client service operations"]


def test_plain_text_preferred_label_splits_a_run_of_plain_lines():
    text = ("## Posting\n\nExperience\nBachelor's degree or equivalent practical experience in business.\n7+ years of experience in program management.\n\n"
            "Preferred Qualifications\nExperience supporting a CEO or executive leadership team.\nExperience building a PMO.\n")
    req, pref, _ = le.marker_sections(text, use_content=False)
    assert len(req) == 2 and len(pref) == 2 and pref[0].startswith("Experience supporting a CEO")


def test_posting_with_no_markdown_headings_gets_plain_headings_promoted():
    text = ("About the Role\nWe run programs for Acme.\n\nExperience\nBachelor's degree in business.\n7+ years of program management.\n\n"
            "Preferred Qualifications\nExperience building a PMO.\nExperience supporting a CEO.\n\nMeasures of Success\nOn-time delivery.\n")
    req, pref, _ = le.marker_sections(text, use_content=False)
    assert req == ["Bachelor's degree in business.", "7+ years of program management."]
    assert pref == ["Experience building a PMO.", "Experience supporting a CEO."]
    assert le.classify_head("Measures of Success") == ("other", "header")
    assert le.promote_plain_headings("## Already\n\nExperience\nx\n").count("## ") == 1


def test_skills_tag_cloud_lines_are_dropped():
    text = "## Skills\n\nCollaboration\nTeam Building\nBudgeting\nForecasting\nRisk Management\n"
    assert le.marker_sections(text, use_content=False)[0] == []
    four = "## Skills\n\nCollaboration\nTeam Building\nProject Management Life Cycle\nCost Estimation\nDocument Control\n"
    assert le.marker_sections(four, use_content=False)[0] == []


def test_logistics_lines_are_rescued_as_requirements():
    text = ("Globex does not intend to hire job seekers who will need H-1B sponsorship now or in the future.\n"
            "Up to 60% travel.\nIn the office 4 days per week based in Springfield.\nWe love snacks and sunshine.\n")
    out = le.rescue_logistics(text, [], [])
    assert len(out) == 3 and not any("snacks" in o for o in out)
    assert le.rescue_logistics(text, out, []) == out


def test_boilerplate_and_navigation_lines_are_junk():
    for line in ["a. third-party recordings are prohibited unless authorized by Acme.",
                 "Pursuant to state law, applicants are not obligated to disclose sealed records.",
                 "regular", "My Dashboard", "relocation assistance is not available for this position"]:
        assert le.is_junk(line), line


def test_gerund_duty_openers_are_flagged_but_judge_can_keep_qualifications():
    assert le.DUTY_HINT.search("Owning end-to-end execution of enterprise leadership programs")
    assert le.DUTY_HINT.search("Collaborate closely with the executives of the system")
    kept, dropped = le.screen_required(["Working knowledge of SQL"], lambda item: "qual")
    assert kept == ["Working knowledge of SQL"] and dropped == []


def test_work_authorization_phrasings_are_rescued():
    for s in ["Eligibility to work in the United States is required.", "This position requires US work authorization.",
              "Candidates must be eligible for employment in the US."]:
        assert le.rescue_work_authorization(s + "\n", [], []) == [s], s


def test_no_llm_baseline_uses_marker_sections_and_full_lines(monkeypatch, tmp_path):
    (tmp_path / "jd.txt").write_text(
        "TITLE: Analyst\nCOMPANY: Acme\nURL: http://example.com\n\n## Requirements\n\n- 5+ years of sales operations experience\n"
        "- Bachelor's degree\n\n## Preferred Qualifications\n\n- Experience with Salesforce\n\n## Additional information\n\n"
        "- Candidates must be authorized to work in the United States.\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    le.main(["jd.txt", "--no-llm"])
    import json
    jd = json.loads((tmp_path / "jd.json").read_text(encoding="utf-8"))
    assert "5+ years of sales operations experience" in jd["required"]
    assert any("authorized to work" in r for r in jd["required"])
    assert "Experience with Salesforce" in jd["preferred"]


def test_short_requirement_statements_are_items_not_headings():
    for line in ["Bachelor's degree required", "MBA preferred", "PMP certification is strongly preferred", "5+ years of experience required"]:
        text = "## Qualifications\n\n- Strong SQL\n" + line + "\n"
        req, pref, _ = le.marker_sections(text, use_content=False)
        assert line in req + pref, line

    plain = "Skills and Experience\nStrong SQL\nExcel modeling\n\nPreferred Qualifications\nExperience building a PMO.\n"
    req, pref, _ = le.marker_sections(plain, use_content=False)
    assert req == ["Strong SQL", "Excel modeling"] and pref == ["Experience building a PMO."]
    assert not le.is_statement("Preferred Qualifications") and le.is_statement("MBA preferred")


def test_you_will_need_requirement_line_survives_intro_filter():
    req, _, _ = le.marker_sections("## Qualifications\n\n- Strong SQL\nYou'll need 5+ years of finance experience.\nYou will love our team.\n", use_content=False)
    assert "You'll need 5+ years of finance experience." in req and "You will love our team." not in req


def test_source_has_no_backspace_characters():
    # a "\b" typed through a shell heredoc silently becomes chr(8) and breaks the regex
    for f in ["scripts/local_extract.py", "tests/test_local_extract_rules.py"]:
        assert chr(8) not in (Path(__file__).resolve().parents[1] / f).read_text(encoding="utf-8"), f


def test_you_will_only_routes_to_the_judge_at_the_start_of_a_line():
    for line in ["You will own the roadmap", "You'll lead the migration", "You\u2019ll manage vendors"]:
        assert le.DUTY_HINT.search(line), line
    assert not le.DUTY_HINT.search("5+ years of experience; the role means you will travel monthly")
    assert not le.DUTY_HINT.search("Proficiency in SQL so that you will build dashboards")


def test_essential_functions_and_mental_physical_headings_are_not_qualification_lists():
    text = ("## Essential Functions Required for Job\n\n- Leads planning across programs by partnering with leads\n\n"
            "## Mental/Physical Requirements:\n\n- Must be able to sit for long periods\n\n"
            "## Drug Screening Requirements\n\n- Offers are contingent on a drug screen\n")
    req, pref, _ = le.marker_sections(text, use_content=False)
    assert req == [] and pref == []


def test_pay_physical_and_boilerplate_lines_are_junk():
    for line in ["The base salary range for this role is $100,000 - $150,000", "United States - CA Bay Area : 186,000 - 280,000 USD / Annual",
                 "Must be able to bend, squat, crouch and/or reach and lift up to 25 pounds", "Night and weekend work.",
                 "It is unlawful in Massachusetts to require or administer a lie detector test", "#LI-REMOTE", "Have we piqued your curiosity?"]:
        assert le.is_junk(line), line
    for real in ["Experience managing budgets of $5M", "5+ years of experience in operations"]:
        assert not le.is_junk(real), real


def test_anchored_alternatives_match_at_line_start_only():
    """Every anchored branch of DUTY_HINT / BOILERPLATE matches at the start of a line (and after leading
    punctuation for BOILERPLATE), and the unanchored ones still match anywhere."""
    for line in ("Lead the team", "Owning end-to-end delivery", "You'll partner with sales", "You will report to the director"):
        assert le.DUTY_HINT.search(line), line
    assert le.DUTY_HINT.search("The person is responsible for forecasting")
    assert le.DUTY_HINT.search("A manager tasked with growth")
    assert not le.DUTY_HINT.search("Experience to lead teams")
    for line in ("Pursuant to the applicable law", "  - confidential data is handled", "#LI-REMOTE", "Normal working hours apply"):
        assert le.BOILERPLATE.search(line), line
    assert not le.BOILERPLATE.search("Work done pursuant to a plan")
    assert le.BOILERPLATE.search("Candidates may take a polygraph")
