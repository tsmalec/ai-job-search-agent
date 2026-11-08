"""role_clarity.py — does a posting say who, what, where, how much and when?

Usage (from the repo root):
    python scripts/role_clarity.py jd.txt [more.txt ...]     one line per posting
    python scripts/role_clarity.py jd.txt -v                 also show what was found for each fact
    python scripts/role_clarity.py - < jd.txt                read stdin
From triage code:  from role_clarity import clarity; c = clarity(text); c["flag"]  ('' or the 🟡 line)

Six facts: boss/department, scope, team, pay, location, start/timeline.
Missing 3 or more = 🟡 "vague posting / possible ghost job" flag in triage. It is a flag, never a skip,
and never changes a verdict by itself. Read-only.
"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from screen_leads import ranges  # noqa: E402

FACTS = {
    "boss/department": [r"reports? (?:directly )?(?:in)?to (?:the )?[A-Z][\w&,\- ]{2,60}", r"reporting to (?:the )?[A-Z][\w&,\- ]{2,60}",
                        r"(?:department|division|organization|org|business unit|function)\s*[:\-]\s*\S.{2,60}",
                        r"\b(?:S?VP|AVP|Vice President|Chief [A-Z]\w+ Officer|C[EFOR]O|COO|Head of [A-Z]\w+|Director of [A-Z]\w+)\b",
                        r"(?:within|part of|join) (?:the|our) [A-Z][\w&\- ]{2,50} (?:team|organization|org|group|department|function)"],
    "scope": [r"(?:responsibilities|what you(?:'|’)ll do|what you will do|in this role,? you|key duties|the role|you will:)",
              r"(?:own|lead|manage|drive|oversee)s? (?:the |our )?[\w\- ]{3,60}(?:program|process|operations|strategy|initiatives?|portfolio)"],
    "team": [r"team of \d+|\d+[- ]person team|(?:manage|lead)s? (?:a team of )?\d+", r"(?:join|work with|partner with) (?:a |the |our )?[\w\- ]{0,40}team",
             r"direct reports?", r"\b(?:lean|small|growing|global|regional|cross-functional|distributed) team\b", r"individual contributor|\bIC\d?\b|people manager"],
    "pay": [r"(?:salary|base pay|pay range|compensation)[^.\n]{0,40}\$\s?\d"],
    "location": [r"\bremote\b", r"\bhybrid\b", r"on-?site", r"location\s*[:\-]", r"\b[A-Z][a-z]+,\s?(?:[A-Z]{2})\b"],
    "start/timeline": [r"start date", r"start(?:ing)? (?:in|on|by) (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|\d)",
                       r"\b\d{1,2}[- ](?:month|mo)\b.{0,20}(?:contract|assignment|engagement)", r"contract (?:length|duration|term)",
                       r"(?:immediate(?:ly)?|asap) (?:start|hire|need)", r"(?:posted|posting date|date posted)\s*[:\-]?\s*\w",
                       r"(?:closes|closing date|apply by|deadline)", r"full[- ]time|part[- ]time|permanent"],
}


def clarity(text):
    """Return {'found': {fact: snippet}, 'missing': [facts], 'flag': '' or the 🟡 line}."""
    t = text or ""
    found = {}
    for fact, pats in FACTS.items():
        for p in pats:
            flags = 0 if fact == "boss/department" or p.startswith(r"\b[A-Z]") else re.I
            m = re.search(p, t, flags)
            if m:
                found[fact] = t[m.start():m.end()][:70].replace("\n", " ")
                break
    if "pay" not in found and ranges(t):
        found["pay"] = "range found"
    missing = [f for f in FACTS if f not in found]
    flag = (f"🟡 vague posting / possible ghost job: missing {len(missing)}/6 ({', '.join(missing)})"
            if len(missing) >= 3 else "")
    return {"found": found, "missing": missing, "flag": flag}


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    verbose = "-v" in a
    for p in [x for x in a if x != "-v"]:
        text = sys.stdin.read() if p == "-" else Path(p).read_text(encoding="utf-8", errors="replace")
        c = clarity(text)
        n = 6 - len(c["missing"])
        print(f"{Path(p).name if p != '-' else 'stdin'}: {n}/6 clear" + (f"  {c['flag']}" if c["flag"] else
              (f"  (missing: {', '.join(c['missing'])})" if c["missing"] else "")))
        if verbose:
            for f in FACTS:
                print(f"    {f:16} {c['found'].get(f, '— missing')}")
