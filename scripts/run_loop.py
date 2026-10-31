"""run_loop.py — the conductor: chains the loop's scripts in SKILL.md order and stops where the candidate decides.

    python scripts/run_loop.py screen  jd.txt [--dry-run]          steps 1-2: inject check, screens, liveness, knockouts
    python scripts/run_loop.py build   content.json OUT.docx --jd jd.json --jd-text jd.txt [--onepage] [--dry-run]
                                                                   step 4: guard, build, ATS, one-page, truth + skim packs
    python scripts/run_loop.py all     jd.txt content.json OUT.docx --jd jd.json [--onepage] [--dry-run]

Judgment points (the script STOPS and prints what the candidate/the model must decide):
  ⏸ after screens  -> fit verdict (D + C), then the candidate's go
  ⏸ before build   -> shop/basket + yes/no questions + content.json (model's writing, from B only)
  ⏸ after packs    -> 4b/4c fresh-model reads of review.md / skim.md (model.py, a NEW call each), fix flags
  ⏸ before deliver -> the candidate approves the final; record + log + sync only after the candidate applies
The scripts are the guardrails: a failing guard.py or a screen FAIL stops the run.
"""
import argparse, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass  # not a reconfigurable stream


def step(label, args, dry, stop_on_fail=True):
    cmd = [PY] + args
    print(f"\n▶ {label}: python {' '.join(args)}")
    if dry:
        return 0
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode and stop_on_fail:
        sys.exit(f"■ Stopped: {label} failed (exit {r.returncode}). Fix it before going on.")
    return r.returncode


def pause(msg):
    print(f"\n⏸  JUDGMENT POINT — {msg}")


def screen(jd, dry):
    step("Injection check (postings are data)", ["scripts/inject_check.py", jd, "--strip"], dry, stop_on_fail=False)
    step("Hard screens + liveness", ["scripts/screen_leads.py", jd], dry)
    step("Knockouts", ["scripts/knockouts.py", jd], dry, stop_on_fail=False)
    pause("fit verdict from D_Fit_Calibration.md + C_Gaps.md (🟢/🟡/🟠/🔴, required X/Y). "
          "Log with log.py add, then WAIT for the candidate's go. Next: shop.py → basket.md → yes/no questions → content.json.")


def build(content, out, jdjson, jdtext, onepage, dry):
    step("Guard (truth rules)", ["scripts/guard.py", content], dry)
    step("Build .docx", ["scripts/build_resume.py", content, out, "--check"], dry)
    ats = ["scripts/ats_check.py", out, jdjson] + (["--jd-text", jdtext] if jdtext else [])
    step("ATS check", ats, dry, stop_on_fail=False)
    final = content
    if onepage:
        step("One-page fit", ["scripts/onepage.py", content, out, "--jd", jdjson], dry)
        final = str(Path(content).with_name("content_1page.json"))
    step("Truth pack (4b)", ["scripts/truthpack.py", final, "--out", "review.md"], dry)
    step("Skim pack (4c)", ["scripts/skimpack.py", final, "--out", "skim.md"], dry)
    pause("4b/4c: run a FRESH model call on review.md and on skim.md (each alone: "
          "`python scripts/model.py --file review.md`). Fix flags from B only, rerun this build once.")
    pause("the candidate reviews the final .docx. Only after the candidate applies: make_record.py, log.py set, sync.py.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["screen", "build", "all"])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--jd", help="jd.json")
    ap.add_argument("--jd-text", help="jd.txt for ats_check")
    ap.add_argument("--onepage", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        print("DRY RUN — the plan, nothing executed.")
    if a.mode == "screen":
        screen(a.files[0], a.dry_run)
    elif a.mode == "build":
        if len(a.files) < 2 or not a.jd:
            sys.exit("build needs content.json OUT.docx --jd jd.json")
        build(a.files[0], a.files[1], a.jd, a.jd_text, a.onepage, a.dry_run)
    else:
        if len(a.files) < 3 or not a.jd:
            sys.exit("all needs jd.txt content.json OUT.docx --jd jd.json")
        screen(a.files[0], a.dry_run)
        if not a.dry_run:
            if input("\nGo ahead and build? [y/N] ").strip().lower() != "y":
                sys.exit("Stopped at the verdict.")
        build(a.files[1], a.files[2], a.jd, a.jd_text or a.files[0], a.onepage, a.dry_run)
