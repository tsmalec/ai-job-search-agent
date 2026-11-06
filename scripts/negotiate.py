"""negotiate.py — offer-stage talking points from confirmed B numbers only.

Usage (from the repo root):
    python scripts/negotiate.py <match>                  row must be at status offer (or interview with --early)
    python scripts/negotiate.py <match> --jd jd.txt      rank by a saved JD (default: the row's archived jds/ file)
    python scripts/negotiate.py <match> --n 6            how many value points (default 5)

What it prints: the pay facts on file (salary_gap.py), then value points built from B items' approved
`short` wording, picked by overlap with the JD. Safety gate (same idea as career-ops, our own code):
every number in a talking point must appear in that item's full/short/facts text in B. Interview-only
and context-only lines are never read. It never proposes an ask number: the target is the candidate's call.
Draft only; nothing is sent.
"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, b_items, norm, stem_hit  # noqa: E402
from salary_gap import find, load, show  # noqa: E402

NUMS = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s?(?:%|[KkMmBb]\b|billion|million)?")


def numbers(text):
    return {re.sub(r"[\s,]", "", n).lower() for n in NUMS.findall(text or "") if re.search(r"\d", n)}


def gate(point, item):
    """True if every number in point appears in the item's paper-safe text (full/short/facts)."""
    proof = numbers(" ".join([item["full"], item["short"], item["facts"]]))
    return numbers(point) <= proof


def pick(items, jd, n):
    jdn = norm(jd)
    scored = []
    for it in items:
        if it["id"].startswith("SUM") or not numbers(it["short"] or it["full"]):
            continue
        tags = [t.strip() for t in it["tags"].split(",") if t.strip()]
        score = sum(1 for t in tags if stem_hit(t, jdn)) if jdn else 0
        scored.append((score, len(numbers(it["short"] or it["full"])), it))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [it for _, _, it in scored[:n]]


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    a = sys.argv[1:]
    opt = {a[i][2:]: a[i + 1] for i in range(len(a) - 1) if a[i].startswith("--") and a[i] != "--early"}
    q = " ".join(x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--")) or x == "")
    if not q.strip():
        raise SystemExit(__doc__)
    r = find(q)
    if r.get("status") != "offer" and not (r.get("status") == "interview" and "--early" in a):
        raise SystemExit(f"{r['company']} is at '{r.get('status')}'. Talking points are for the offer stage "
                         "(use --early at interview stage to prepare).")
    jd = ""
    jp = opt.get("jd") or r.get("jd")
    if jp and (Path(jp).exists() or (ROOT / jp).exists()):
        jd = (Path(jp) if Path(jp).exists() else ROOT / jp).read_text(encoding="utf-8", errors="replace")
    show(r, load()["facts"])
    print("\nValue points (B `short` wording; every number checked against B's full/short/facts):")
    kept = 0
    for it in pick(b_items(), jd or r.get("role", ""), int(opt.get("n", 5))):
        point = (it["short"] or it["full"]).strip()
        if not gate(point, it):
            print(f"  [dropped {it['id']}: a number isn't in B's proof text]")
            continue
        kept += 1
        print(f"  {kept}. ({it['id']}) {point}")
    print("\nFrame: tie each point to the role's scope, then state the number you've decided on."
          " Pay facts above are context, not a target. Nothing here is sent.")
