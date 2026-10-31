"""swap_test.py — before trusting a new model, rerun past builds with it and compare against Claude's.

    python scripts/swap_test.py --case work/swap/acme [--case work/swap/b ...] [--model ollama:qwen3:14b]

Each case folder holds what Claude's build used and produced:
    jd.txt          the posting
    content.json    Claude's final content.json (the baseline)
The new model gets jd.txt + shop.py's basket.md + the content.json shape and writes its own content.json
(content_<model>.json). Then both go through the same guardrails and the table compares:
    guard.py pass/fail · truthpack pre-flags · ATS keyword % · skim read (fresh call on skim.md)
Pass bar: guard passes, no more truth flags than Claude's, ATS within 5 pts, skim ROLE matches.
Nothing here writes B/C/data; outputs stay in the case folder.
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from model import ask, setting, ModelError  # noqa: E402

PY = sys.executable


def run(args):
    r = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def metrics(case, content, tag):
    out = {}
    rc, _ = run(["scripts/guard.py", str(content)])
    out["guard"] = "PASS" if rc == 0 else "FAIL"
    review = case / f"review_{tag}.md"
    run(["scripts/truthpack.py", str(content), "--out", str(review)])
    t = review.read_text(encoding="utf-8") if review.exists() else ""
    out["truth_preflags"] = len(re.findall(r"⚠|FLAG", t))
    docx = case / f"out_{tag}.docx"
    run(["scripts/build_resume.py", str(content), str(docx)])
    jdjson = case / "jd.json"
    if jdjson.exists() and docx.exists():
        _, ats = run(["scripts/ats_check.py", str(docx), str(jdjson), "--jd-text", str(case / "jd.txt")])
        m = re.search(r"(\d+)%", ats)
        out["ats_pct"] = int(m.group(1)) if m else None
    skim = case / f"skim_{tag}.md"
    run(["scripts/skimpack.py", str(content), "--out", str(skim)])
    try:
        read = ask(skim.read_text(encoding="utf-8"),
                   system="You are a recruiter skimming for 7 seconds. Answer ROLE / PROOF / LEVEL / SNAG / JARGON, one line each.")
        out["skim_role"] = (re.search(r"ROLE[:\s]+(.*)", read) or [None, read[:80]])[1].strip()
    except ModelError as e:
        out["skim_role"] = f"(no model: {e})"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", required=True)
    ap.add_argument("--model")
    a = ap.parse_args()
    m = setting(a.model)
    print(f"Swap test with {m}\n")
    for c in a.case:
        case = Path(c).resolve()
        base = case / "content.json"
        if not base.exists() or not (case / "jd.txt").exists():
            print(f"{case.name}: needs jd.txt and content.json"); continue
        basket = case / "basket.md"
        if not basket.exists():
            kw = case / "keywords.txt"
            if not kw.exists():
                print(f"{case.name}: add keywords.txt (JD responsibilities in order)"); continue
            run(["scripts/shop.py", "B_Bullet_Catalog.md", "C_Gaps.md", str(kw), "--basket", str(basket)])
        prompt = ("Follow project/SKILL.md step 4. Using ONLY the basket below, write content.json for this posting "
                  "in the exact shape of the example. Output JSON only.\n\nEXAMPLE:\n"
                  + (ROOT / "scripts/resume_example.json").read_text(encoding="utf-8")
                  + "\n\nBASKET:\n" + basket.read_text(encoding="utf-8")
                  + "\n\nPOSTING:\n" + (case / "jd.txt").read_text(encoding="utf-8"))
        tag = re.sub(r"\W+", "_", m)
        new = case / f"content_{tag}.json"
        try:
            txt = ask(prompt, system=(ROOT / "project/A_Rules.md").read_text(encoding="utf-8"), model=a.model)
            j = txt[txt.find("{"): txt.rfind("}") + 1]
            json.loads(j)
            new.write_text(j, encoding="utf-8", newline="\n")
        except (ModelError, ValueError) as e:
            print(f"{case.name}: model build failed: {e}"); continue
        b, n = metrics(case, base, "claude"), metrics(case, new, tag)
        print(f"== {case.name}")
        for k in ("guard", "truth_preflags", "ats_pct", "skim_role"):
            print(f"  {k:15} Claude: {b.get(k)!s:30} {m}: {n.get(k)}")


if __name__ == "__main__":
    main()
