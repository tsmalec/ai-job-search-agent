"""skimpack.py — what a recruiter's eye hits in the first ~7 seconds, for a blind skim test.

Usage (from the repo root):
    python3 scripts/skimpack.py content.json [--out skim.md]
    (one-page builds: pass content_1page.json)

Eye-tracking research (published recruiter eye-tracking studies): the first screen lasts ~7.4 s and
goes to name, current title, current employer and dates, the first 1-2 bullets under the current
role, the previous title and employer, education and the skills line. This writes only those parts
plus reader instructions. A separate agent that has NOT seen the job posting reads skim.md and says
what role it thinks the person is for and what the strongest proof is. Claude then compares that
answer to the JD's target title; it never shows the target to the skimmer.
A fifth line, JARGON, lists any term in the first two bullets an outside recruiter
wouldn't know; Claude swaps each for plain words from B.
Standard library only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_resume import EDU  # noqa: E402
from common import CFG, load_json  # noqa: E402

PROMPT = """# 7-second skim test

You are a busy recruiter giving this resume its first glance: about 7 seconds. Below is everything
your eye lands on in that time. You have not seen any job posting. Answer in exactly five lines:

ROLE: the job title you'd put this person forward for (one title, your words)
PROOF: the single strongest piece of evidence you noticed, in under 15 words
LEVEL: individual contributor, team lead, manager of managers, or director-level, as it reads to you
SNAG: anything that slowed you down or confused you in those 7 seconds (or "none")
JARGON: every term in the first two bullets under the current role that a recruiter outside this
        company wouldn't know (internal team names, program acronyms, system names), comma-separated (or "none")

Judge only what's here. Don't guess at what the rest of the resume might say, and don't count
missing bullets on older roles as a snag: you simply didn't read that far.

---
"""


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("skim.md")
    c = load_json(argv[0])
    jobs = c.get("jobs", [])
    lines = [PROMPT, f"**{c.get('name', CFG['candidate']['name'].upper())}**", ""]
    if jobs:
        j = jobs[0]
        lines += [f"**{j.get('title', '')}**", f"{j.get('company', '')} | {j.get('dates', '')}"]
        lines += [f"- {b}" for b in (j.get("bullets") or [])[:2]]
        lines.append("")
    if len(jobs) > 1:
        j = jobs[1]
        lines += [f"**{j.get('title', '')}**", f"{j.get('company', '')} | {j.get('dates', '')}",
                  "(its bullets are below the fold; you didn't get to them in 7 seconds)", ""]
    lines += ["EDUCATION", c.get("education", EDU), "", c.get("skills_heading", "SKILLS & CERTIFICATIONS"),
              c.get("skills", "")]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"skimpack: → {out}")
    return 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
