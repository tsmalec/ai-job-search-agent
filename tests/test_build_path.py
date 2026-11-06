"""The build path end to end: shop -> guard -> build -> ats_check -> onepage -> truthpack -> skimpack.

Runs on tests/fixtures/sample_loop only: a fictional person ("Alex Example", a full-stack engineer) and a
sample Senior Full-Stack Engineer posting, so this file and its fixtures are safe to publish. The real catalog is never read:
JOBSEARCH_B_FILE / JOBSEARCH_C_FILE point the scripts at the sample (common.py).

Run: python3 -m pytest tests
"""
import json, os, shutil, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FIX = ROOT / "tests" / "fixtures" / "sample_loop"
sys.path.insert(0, str(SCRIPTS))

ENV = {**os.environ, "JOBSEARCH_B_FILE": str(FIX / "B_Bullet_Catalog.md"),
       "JOBSEARCH_C_FILE": str(FIX / "C_Gaps.md"), "PYTHONIOENCODING": "utf-8"}
HAS_SOFFICE = bool(shutil.which("soffice") or shutil.which("libreoffice"))


def run(*args, check=True):
    r = subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, env=ENV,
                       capture_output=True, text=True, encoding="utf-8", timeout=240)
    if check:
        assert r.returncode == 0, f"{args[0]} failed:\n{r.stdout}\n{r.stderr}"
    return r


@pytest.fixture
def work(tmp_path):
    shutil.copy(FIX / "content.json", tmp_path / "content.json")
    return tmp_path


def docx_text(path):
    import docx
    return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)


def test_shop_picks_sample_items_and_flags_gap(work):
    out = run(SCRIPTS / "shop.py", FIX / "B_Bullet_Catalog.md", FIX / "C_Gaps.md", FIX / "keywords.txt",
              "--basket", work / "basket.md").stdout
    assert "FE-01" in out and "BE-01" in out
    assert "Known gaps (don't claim): Go" in out
    assert (work / "basket.md").exists()


def test_guard_passes_sample(work):
    assert "Clean" in run(SCRIPTS / "guard.py", work / "content.json").stdout


def test_guard_blocks_invented_number_and_gap(work):
    c = json.loads((work / "content.json").read_text(encoding="utf-8"))
    c["jobs"][0]["bullets"][0] = "Built React apps for 99 product teams on Kubernetes."
    bad = work / "bad.json"
    bad.write_text(json.dumps(c), encoding="utf-8", newline="\n")
    r = run(SCRIPTS / "guard.py", bad, check=False)
    assert r.returncode == 1
    assert '"99"' in r.stdout and "Kubernetes" in r.stdout


def test_build_has_only_sample_identity(work):
    import build_resume
    out = work / "out.docx"
    run(SCRIPTS / "build_resume.py", work / "content.json", out)
    text = docx_text(out)
    assert text.startswith("ALEX EXAMPLE")
    assert "Example State University" in text
    # The builder's built-in defaults may hold a real header (private copy); the sample must override them.
    # In the public template the defaults are the sample person, so pieces the fixture itself uses are skipped.
    sample = json.loads((FIX / "content.json").read_text(encoding="utf-8"))
    own = " | ".join([sample["name"], sample["contact"], sample["education"]])
    for default in (build_resume.CONTACT, build_resume.EDU):
        for piece in default.split(" | "):
            if piece not in own:
                assert piece not in text, "a built-in default leaked into the sample resume"


def test_ats_check_passes_sample(work):
    out = work / "out.docx"
    run(SCRIPTS / "build_resume.py", work / "content.json", out)
    r = run(SCRIPTS / "ats_check.py", out, FIX / "jd.json", "--jd-text", FIX / "jd.txt").stdout
    assert "Parse: PASS" in r
    assert "Required 7/7" in r


@pytest.mark.skipif(not HAS_SOFFICE, reason="LibreOffice not installed (page count needs it)")
def test_onepage_fits_and_keeps_pin(work):
    r = run(SCRIPTS / "onepage.py", work / "content.json", work / "out1.docx", "--jd", FIX / "jd.json").stdout
    assert "1 page(s)" in r
    trimmed = json.loads((work / "content_1page.json").read_text(encoding="utf-8"))
    ids = [i for j in trimmed["jobs"] for i in j.get("bullet_ids", [])]
    assert "FE-01" in ids  # pinned in content.json


def test_truthpack_and_skimpack_cover_every_line(work):
    run(SCRIPTS / "truthpack.py", work / "content.json", "--out", work / "review.md")
    run(SCRIPTS / "skimpack.py", work / "content.json", "--out", work / "skim.md")
    review = (work / "review.md").read_text(encoding="utf-8")
    skim = (work / "skim.md").read_text(encoding="utf-8")
    for b in ("4.1 to 1.6 seconds", "18 million rows", "30 storefront features"):
        assert b in review
    assert "ALEX EXAMPLE" in skim or "Alex Example" in skim
    assert "4.1 to 1.6 seconds" in skim  # first bullet of the latest role is what a recruiter sees


def test_atomic_save_json(tmp_path):
    from common import save_json, load_json
    p = tmp_path / "rows.json"
    save_json(p, {"applications": [{"company": "Example Corp"}]})
    save_json(p, {"applications": []})
    assert load_json(p) == {"applications": []}
    assert [x.name for x in tmp_path.iterdir()] == ["rows.json"]  # no temp file left behind
