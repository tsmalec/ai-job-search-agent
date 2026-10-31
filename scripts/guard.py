"""guard.py — the truth rules as code. Run before every build; a resume that fails doesn't go out.

Usage (from the repo root):
    python scripts/guard.py content.json

Checks (✗ = must fix, · = look at it):
  ✗ Role titles, companies and dates must match B's roles block exactly (no merged or invented titles);
    approved alternate titles live in config.json resume.title_aliases
  ✗ Banned phrases: config.json guard.banned_phrases, plus every "Never write" term in C_Gaps.md
  ✗ Numbers: every figure on the resume must appear somewhere in B
  ✗ Years overreach: "a decade"/"10+ years" attached to programs, leading, leadership or managing
  ✗ Role placement: every bullet ID must list that job's role code on its B `role:` line
  ✗ Every job with bullets carries "bullet_ids" (same order, real B IDs or "new")
  ✗ Big-number wording: "$NB budget" / "owned $NB" read as P&L authority
  ✗ Level word on the summary identity ("Senior ... leader" overstating level)
  · Wording that matches B's interview-only or context-only notes (unofficial facts stay off paper)
A C tool name that is the target employer's own name passes (pass --company NAME, or "target_company" in content.json).
Exits with code 1 when anything must be fixed, so other scripts can stop on it.
"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import CFG, B_FILE, ROLE_CODES, TITLE_ALIASES, b_items, b_roles, role_codes, c_never_write, load_json, norm, parse_month  # noqa: E402

BANNED = [  # (pattern, why): generic ones here, your own in config.json
    (r"\$\d+\s?b(?:illion)?\s+(?:annual\s+)?budget|\bown(?:ed|ing|s)\s+(?:a\s+)?\$\d+\s?b(?:illion)?\b",
     "reads as P&L/budget authority; say what the team did with the money"),
] + [tuple(x) for x in CFG.get("guard", {}).get("banned_phrases", [])]  # your never-say list (config.json)
SKILLS_OK = {t.lower() for t in CFG.get("resume", {}).get("skills_line_ok_gaps", [])}  # C gaps allowed in skills only


LEVEL_OPENER = re.compile(r"^\W*(?:an?\s+|the\s+)?(?:(?:ai|data)[- ]focused\s+|results[- ]driven\s+|seasoned\s+|accomplished\s+)?"
                          r"(senior|sr\.?|lead|head|principal|staff|chief|executive|director|vp|vice president)(?!\w)"
                          r"(?=[^.]{0,80}?\b(leader|manager|professional|operator|strategist|executive)\b)", re.I)


YEARS = re.compile(r"\b(a decade|ten years|1\d\+? years)\b(?P<mid>[^.;]{0,100}?)\b(program management|project management|"
                   r"programs?\b|leading|leadership|managing|people manag|team lead)", re.I)


def text_of(c):
    parts = [("summary", c.get("summary", "")), ("skills", c.get("skills", ""))]
    for j in c.get("jobs", []):
        for k, b in enumerate(j.get("bullets") or [], 1):
            parts.append((f"{j.get('title','?')} #{k}", b))
    for ex in c.get("extra_sections", []):
        parts.append((ex.get("heading", "extra"), ex.get("text", "")))
    return parts


def figures(s):
    """Meaningful numbers: $ amounts, percents, and anything 13+ (small counts like 'three' or '2-day' are fine)."""
    out = []
    for m in re.finditer(r"\$?\d[\d,]*(?:\.\d+)?\s?(?:%|[kKmMbB]\b|million|billion)?\+?", s):
        tok = m.group(0).strip()
        digits = re.sub(r"[^\d.]", "", tok)
        if not digits or digits == ".":
            continue
        try:
            val = float(digits.replace(",", ""))
        except ValueError:
            continue
        if "$" in tok or "%" in tok or val >= 13:
            out.append(tok)
    return out


def shingles(s, n=4):
    w = norm(s).split()
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


TWO_B = re.compile(r"(?!x)x")  # candidate-specific big-number wording check; add your own in config.json guard.banned_phrases


def check(path, company=None):
    c = load_json(path)
    company = (company or c.get("target_company") or "").lower()
    errors, warns = [], []
    roles = b_roles()
    by_title = {r["title"].lower(): r for r in roles}
    for j in c.get("jobs", []):
        t = j.get("title", "")
        tl = t.lower().replace(" (self-employed)", "")
        r = by_title.get(TITLE_ALIASES.get(tl, tl))
        if not r:
            errors.append(f"Role title not in B: '{t}'")
            continue
        comp = re.sub(r"\s*\(self-employed\)", "", j.get("company", ""))
        if norm(comp) != norm(r["company"]):
            errors.append(f"{t}: company '{j.get('company')}' ≠ B '{r['company']}'")
        d = j.get("dates", "")
        parts = re.split(r"\s+-\s+", d)
        if len(parts) != 2 or (parse_month(parts[0]), parse_month(parts[1])) != (r["start"], r["end"]):
            errors.append(f"{t}: dates '{d}' ≠ B ({r['raw'].split('|')[-1].strip()})")

    by_id = {i["id"]: i for i in b_items()}
    ids = set(by_id)
    for j in c.get("jobs", []):
        bl, bid = j.get("bullets") or [], j.get("bullet_ids") or []
        if bl and len(bid) != len(bl):
            errors.append(f"{j.get('title','?')}: {len(bl)} bullets but {len(bid)} bullet_ids "
                          "(list each bullet's B ID, '+'-joined if merged, or 'new')")
        for x in bid:
            bad = [k for k in re.split(r"\s*\+\s*", x or "") if k and k.lower() != "new" and k not in ids]
            if bad:
                errors.append(f"{j.get('title','?')}: bullet_id {', '.join(bad)} isn't in B")
            code = ROLE_CODES.get(j.get("title", "").lower().replace(" (self-employed)", ""))
            for k in re.split(r"\s*\+\s*", x or ""):
                if not k or k.lower() == "new" or k not in by_id or not code:
                    continue
                have = role_codes(by_id[k])
                if not have:
                    errors.append(f"{j.get('title','?')}: {k} has no role line in B (add one before using it)")
                elif code not in have:
                    errors.append(f"{j.get('title','?')}: {k} is tagged {by_id[k]['role'].strip()}, "
                                  f"not {code} (move it to a job it belongs to)")

    raw_b = B_FILE.read_text(encoding="utf-8").lower()
    never = c_never_write()
    items = b_items()
    official = set().union(*(shingles(" ".join([i["full"], i["short"], i["facts"]])) for i in items))
    unofficial = set().union(*(shingles(" ".join([i["interview"], i["context"]])) for i in items)) - official

    lv = LEVEL_OPENER.search(c.get("summary", ""))
    if lv:
        errors.append(f"summary: opens with level word \"{lv.group(1)}\" before the identity phrase "
                      "(B's SUM opener has none: e.g. \"Program and operations leader who ...\")")

    for where, s in text_of(c):
        low = s.lower()
        for pat, why in BANNED:
            m = re.search(pat, low)
            if m:
                errors.append(f"{where}: \"{m.group(0)}\" ({why})")
        low_gap = low
        for m in TWO_B.finditer(s):
            if not re.search(r"review|rout", s[max(0, m.start() - 60):m.start()], re.I):
                errors.append(f"{where}: \"{m.group(0)}\" alone reads as budget authority; use B's exact "
                              "approved wording for that figure")
        for term in never:
            if where == "skills" and term.lower() in SKILLS_OK:
                continue
            if company and term.lower() in company:
                continue  # the employer's own name (e.g. a tool company's own name in its posting), not a tool claim
            if re.search(rf"(?<![a-z]){re.escape(term.lower())}(?![a-z])", low_gap):
                errors.append(f"{where}: \"{term}\" is a confirmed gap (C_Gaps.md 'Never write')")
        for f in figures(s):
            core = re.sub(r"\s", "", f.lower()).rstrip("+")
            num = re.sub(r"[^\d.,]", "", core)
            alt = {core, num, num.replace(",", ""), core.replace("$", "")}
            if not any(a and a in raw_b.replace(" ", "") for a in alt):
                errors.append(f"{where}: figure \"{f}\" isn't anywhere in B (invented or new? add it to B first)")
        y = YEARS.search(s)
        if y and not re.search(r"including\s+(?:nearly\s+|about\s+|over\s+)?\w+\s+years", y.group("mid").lower()):
            errors.append(f"{where}: \"{y.group(0)[:80]}\" puts total tenure on leadership; give leadership its own "
                          "number from B (e.g. 'a decade of experience, including three years leading ...')")
        leak = shingles(s) & unofficial
        if len(leak) >= 2:
            warns.append(f"{where}: matches interview/context-only notes (\"{sorted(leak)[0]}…\"); keep it off paper")

    for e in errors:
        print("✗ " + e)
    for w in warns:
        print("· " + w)
    if not errors and not warns:
        print("✓ Clean: roles match B, no banned phrases or gap terms, every figure is in B.")
    elif not errors:
        print("✓ No hard failures (look at the notes above).")
    return 1 if errors else 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    co = sys.argv[sys.argv.index("--company") + 1] if "--company" in sys.argv else None
    sys.exit(check(sys.argv[1], co))
