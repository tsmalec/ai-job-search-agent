"""onepage.py — trim a resume to one page without losing the keywords that matter.

Usage (from the repo root):
    python scripts/onepage.py content.json OUT.docx [--jd jd.json] [--keep 3]

Builds the .docx; while it's over one page, it shortens first and cuts last:
  1. Tighten: swap a bullet for B's `short:` version (bottom of the page first, single-ID bullets only, never the
     first --keep bullets; with --jd, only when the swap loses no keyword). One swap per rebuild.
  2. Cut: only when no short swap is left, drop one bullet and rebuild. Which bullet:
  - never the first --keep bullets of the most recent job (default 3)
  - never a pinned bullet: list B IDs in content.json "pin": ["PROC-01", "LEAD-02+LEAD-01", ...] (the candidate's
    must-keeps, e.g. the headline result); matched against each job's "bullet_ids". Merged pins work:
    a pin matches a bullet when they share any component ID. A pin that matches no bullet is
    printed as a warning before anything is cut.
  - never a years-line bullet (E "Visible leadership years"): a role's last remaining bullet (an older role
    keeps the total-years count) or the only leadership bullet (config.json resume.leadership_bullet_ids)
    left in its role
  - prefers bullets near the bottom of the role with the most bullets (JD order puts the least important last)
  - with --jd: the team-lead bullet (config.json resume.team_lead_bullet_id) is auto-pinned when the JD asks for managing a team
  - with --jd, skips any bullet that's the only place a required qualification or title word appears,
    and picks the one that costs the fewest keywords
Writes the trimmed content to content_1page.json NEXT TO the input content.json (it used to land in
the current directory, i.e. the repo root) and lists what was cut.
Needs LibreOffice for the page count (same as build_resume.py --check).
"""
import copy, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_resume import EDU, build, page_count  # noqa: E402
from common import CFG, b_items, load_json, norm, stem_hit  # noqa: E402
import re  # noqa: E402

LEAD_IDS = set(CFG.get("resume", {}).get("leadership_bullet_ids", []))
TEAM_ID = CFG.get("resume", {}).get("team_lead_bullet_id", "")
TEAM_RX = re.compile(r"\b(manag\w*|lead\w*|supervis\w*|build\w*|coach\w*|develop\w*|grow\w*)\s+(a |an |the )?"
                     r"(high[- ]performing |small |growing |global |cross[- ]functional )?(team|staff|group)s?\b"
                     r"|\bdirect reports?\b|\bpeople (management|manager|leader)|\bpeople leadership\b"
                     r"|\bmanag\w* (and develop\w* )?(a team|teams|people|staff)\b", re.I)


def jd_text(jd):
    out = [jd.get("title", ""), jd.get("text", ""), jd.get("raw", "")]
    for sec in ("required", "preferred", "keywords"):
        for k, vs in (jd.get(sec) or {}).items():
            out.append(k)
            out += [v for v in vs if isinstance(v, str)]
    return " ".join(out)


def wants_team(jd, extra=""):
    return bool(TEAM_RX.search(jd_text(jd) + " " + extra))


def all_text(c):
    t = [c.get("summary", ""), c.get("skills", ""), c.get("education", EDU)]
    for j in c.get("jobs", []):
        t += j.get("bullets") or []
    return norm(" ".join(t))


def covered(c, jd):
    text = all_text(c)
    req = {k for k, vs in (jd.get("required") or {}).items() if any(stem_hit(v, text) for v in vs)}
    kws = {k for sec in ("preferred", "keywords") for k, vs in (jd.get(sec) or {}).items()
           if any(stem_hit(v, text) for v in vs)}
    title = {t for t in jd.get("title_core", []) if stem_hit(t, text)}
    return req, kws, title


def parts(x):
    return {p.strip() for p in str(x or "").split("+") if p.strip() and p.strip().lower() != "new"}


def pin_ids(c):
    """Every component ID named in "pin", merged pins ("PROC-02+PROC-01") split on '+'."""
    return set().union(*(parts(p) for p in c.get("pin") or [])) if c.get("pin") else set()


def pinned(c, ji, bi):
    ids = c["jobs"][ji].get("bullet_ids") or []
    return bi < len(ids) and bool(pin_ids(c) & parts(ids[bi]))


def dead_pins(c):
    """Pins that match no bullet on the page (typo, or the bullet isn't in content.json)."""
    have = set().union(*(parts(x) for j in c.get("jobs", []) for x in (j.get("bullet_ids") or [])))
    return [p for p in c.get("pin") or [] if not (parts(p) & have)]


def years_line(c, ji, bi):
    """True for a bullet that keeps a years line counting: the role's last bullet, or its only leadership one."""
    j = c["jobs"][ji]
    bl, ids = j.get("bullets") or [], j.get("bullet_ids") or []
    if len(bl) <= 1:
        return True
    if bi < len(ids) and parts(ids[bi]) & LEAD_IDS:
        others = [x for k, x in enumerate(ids) if k != bi and parts(x) & LEAD_IDS]
        return not others
    return False


def candidates(c, keep):
    out = []
    for ji, j in enumerate(c["jobs"]):
        bl = j.get("bullets") or []
        floor = keep if ji == 0 else 0
        for bi in range(len(bl) - 1, floor - 1, -1):
            if not pinned(c, ji, bi) and not years_line(c, ji, bi):
                out.append((ji, bi, len(bl)))
    # roles with the most bullets first, bottom-up within a role
    out.sort(key=lambda x: (-x[2], x[0] != 0, -x[1]))
    return [(ji, bi) for ji, bi, _ in out]


def short_swaps(c, shorts, done, keep=3):
    """(ji, bi, short_text) for single-ID bullets whose B `short:` is shorter, bottom of the page first."""
    out = []
    for ji in range(len(c["jobs"]) - 1, -1, -1):
        j = c["jobs"][ji]
        bl, ids = j.get("bullets") or [], j.get("bullet_ids") or []
        for bi in range(len(bl) - 1, -1, -1):
            if bi >= len(ids) or "+" in str(ids[bi]) or (ji, bi) in done or (ji == 0 and bi < keep):
                continue
            sh = shorts.get(str(ids[bi]).strip())
            if sh and len(sh) < len(bl[bi]) - 10:
                out.append((ji, bi, sh))
    return out


def without(c, ji, bi):
    d = copy.deepcopy(c)
    d["jobs"][ji]["bullets"].pop(bi)
    ids = d["jobs"][ji].get("bullet_ids")
    if ids and bi < len(ids):
        ids.pop(bi)
    return d


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        raise SystemExit(__doc__)
    jd = load_json(argv[argv.index("--jd") + 1]) if "--jd" in argv else None
    if jd:
        args = [a for a in args if a != argv[argv.index("--jd") + 1]]
    keep = int(argv[argv.index("--keep") + 1]) if "--keep" in argv else 3
    c, out = load_json(args[0]), args[1]
    if TEAM_ID and jd and wants_team(jd) and TEAM_ID not in pin_ids(c):
        has = any(TEAM_ID in parts(x) for j in c.get("jobs", []) for x in (j.get("bullet_ids") or []))
        if has:
            c["pin"] = list(c.get("pin") or []) + [TEAM_ID]
            print(f"📌 JD asks for managing a team: {TEAM_ID} auto-pinned.")
        else:
            print(f"⚠ JD asks for managing a team but {TEAM_ID} isn't on the page; add it to its role.")
    shorts = {i["id"]: i["short"].strip() for i in b_items() if i.get("short", "").strip()}
    swapped, done = [], set()
    for p in dead_pins(c):
        print(f"⚠ pin '{p}' matches no bullet_ids entry, so it protects nothing. Fix the ID in content.json.")
    cut = []
    for _ in range(60):
        build(c, out)
        pages = page_count(out)[0]
        if pages == "1":
            break
        swaps = short_swaps(c, shorts, done, keep)
        if jd and swaps:
            base = covered(c, jd)
            ok = []
            for ji, bi, sh in swaps:
                d = copy.deepcopy(c)
                d["jobs"][ji]["bullets"][bi] = sh
                req, kws, title = covered(d, jd)
                if not (base[0] - req) and not (base[1] - kws) and not (base[2] - title):
                    ok.append((ji, bi, sh))
            swaps = ok
        if swaps:
            ji, bi, sh = swaps[0]
            swapped.append((c["jobs"][ji]["title"], c["jobs"][ji]["bullet_ids"][bi]))
            c["jobs"][ji]["bullets"][bi] = sh
            done.add((ji, bi))
            continue
        cands = candidates(c, keep)
        if not cands:
            print("Nothing left to cut safely; shorten bullets or the summary by hand.")
            break
        if jd:
            base = covered(c, jd)
            scored = []
            for rank, (ji, bi) in enumerate(cands):
                req, kws, title = covered(without(c, ji, bi), jd)
                lost_req = len(base[0] - req) + len(base[2] - title)
                scored.append((lost_req, len(base[1] - kws), rank, ji, bi))
            scored.sort()
            _, _, _, ji, bi = scored[0]
        else:
            ji, bi = cands[0]
        cut.append((c["jobs"][ji]["title"], c["jobs"][ji]["bullets"][bi]))
        c = without(c, ji, bi)
        done = {(a, b if a != ji or b < bi else b - 1) for a, b in done if not (a == ji and b == bi)}
    dest = Path(args[0]).resolve().parent / "content_1page.json"
    dest.write_text(json.dumps(c, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{out}: {pages} page(s) after {len(swapped)} short-version swap(s) and cutting {len(cut)} bullet(s). "
          f"Trimmed content → {dest}")
    for t, i in swapped:
        print(f"  short ({t}): {i}")
    if pages != "1":
        print("Still over: merge two related bullets or trim the summary by hand (E), then rerun.")
    for t, b in cut:
        print(f"  cut ({t}): {b[:110]}{'…' if len(b) > 110 else ''}")
    if jd:
        req, kws, title = covered(c, jd)
        print(f"Still covered: required {len(req)}/{len(jd.get('required') or {})}, "
              f"keywords {len(kws)}/{sum(len(jd.get(s) or {}) for s in ('preferred', 'keywords'))}. "
              "Run ats_check.py on the final file to confirm.")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    main(sys.argv[1:])
