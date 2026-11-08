"""Lead dedupe (dedupe regressions). Run: python3 -m pytest tests"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from common import LoggedIndex, posting_keys, title_key  # noqa: E402
from lead_gate import gate  # noqa: E402


def idx(*rows):
    ix = LoggedIndex()
    for n, (co, role, url) in enumerate(rows):
        ix.add(co, role, url, f"#{n} {co}")
    return ix


def lead(co, role, url):
    return {"company": co, "role": role, "url": url, "status": "not_triaged", "source": ""}


def kind(ix, co, role, url):
    hit = ix.find(co, role, url)
    return hit[0] if hit else None


def test_tracking_params_stripped():
    ix = idx(("Acme", "Program Manager", "https://boards.greenhouse.io/acme/jobs/5550001"))
    assert kind(ix, "Acme", "Other title", "https://boards.greenhouse.io/acme/jobs/5550001?gh_src=li&utm_source=x") == "dupe"


def test_same_id_on_two_hosts():
    ix = idx(("Initech", "Program Manager", "https://jobs.initech.example/global/en/job/9999999999999999"))
    assert kind(ix, "Initech", "PM II", "https://apply.initech.example/careers?pid=9999999999999999") == "dupe"


def test_workday_req_id_in_role_or_url():
    ix = idx(("Initech", "Manager, Data Products (R-0012345)", ""))
    assert kind(ix, "Initech Inc.", "Mgr", "https://initech.wd5.myworkdayjobs.com/x/job/Remote/Manager_R0012345-1") == "dupe"


def test_linkedin_vs_ats_is_same_posting():
    ix = idx(("Globex", "Business Operations Partner (Remote US · $115K)",
              "https://www.linkedin.com/jobs/view/1234567890/"))
    assert kind(ix, "Globex", "Business Operations Partner",
                "https://jobs.globex.example/listing/ops/7311234") == "dupe"


def test_title_normalization():
    assert title_key("Sr. Program Mgr - Remote US") == title_key("Senior Program Manager")
    assert title_key("Senior Program Manager (Remote US · $101,000–$140") == "senior program manager"  # seen.txt cut
    assert title_key("Program Manager") != title_key("Senior Program Manager")  # seniority kept


def test_company_variants():
    ix = idx(("Hooli", "Senior Program Manager, Logistics", ""))
    assert kind(ix, "Hooli Inc", "Senior Program Manager, Logistics", "") == "dupe"
    ix = idx(("Pied Piper", "Senior Operations Manager", ""))
    assert kind(ix, "PiedPiper", "Senior Operations Manager", "") == "dupe"


def test_repost_same_site_new_id_is_flagged_not_new():
    ix = idx(("Umbrella", "Fulfillment Operations Manager", "https://job-boards.greenhouse.io/umbrella/jobs/1000000003"))
    leads = [lead("Umbrella", "Fulfillment Operations Manager", "https://job-boards.greenhouse.io/umbrella/jobs/1000000004")]
    marked = gate(leads, ix)
    assert marked[0][1] == "repost" and leads[0]["status"] == "dropped_by_agent" and "repost of #0" in leads[0]["note"]


def test_different_role_same_company_is_new():
    ix = idx(("Acme", "Program Manager", "https://boards.greenhouse.io/acme/jobs/5550001"))
    assert kind(ix, "Acme", "Director, Revenue Operations", "https://boards.greenhouse.io/acme/jobs/5550999") is None


def test_salary_digits_are_not_ids():
    assert not posting_keys("", "Ops Lead (Remote · $1600000 total comp)") - {"1600000"} and \
        not posting_keys("", "Ops Lead (Remote · $105,000–$125,000)")


def test_inbox_duplicate_keeps_oldest_and_never_deletes():
    leads = [lead("Acme", "Program Manager", "https://x.com/jobs/1234567?utm_source=a"),  # newest (top)
             lead("Acme", "Program Manager", "https://x.com/jobs/1234567")]               # oldest
    marked = gate(leads, LoggedIndex())
    assert len(leads) == 2 and len(marked) == 1
    assert leads[1]["status"] == "not_triaged" and leads[0]["status"] == "dropped_by_agent"


def test_triaged_or_dropped_leads_untouched():
    ix = idx(("Acme", "Program Manager", "https://x.com/jobs/1234567"))
    l = dict(lead("Acme", "Program Manager", "https://x.com/jobs/1234567"), status="dropped_by_agent", note="old")
    assert gate([l], ix) == [] and l["note"] == "old"
