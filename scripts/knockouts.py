"""knockouts.py — list the JD's likely knockout questions with the candidate's honest answer to each.

Usage (from the repo root):
    python3 scripts/knockouts.py jd.txt

Knockout questions (yes/no screens on the application form) are the one automatic rejection
most ATS make: years of X, location/relocation, work authorization, clearance, required license,
certification or degree (ATS research). This finds those lines in the JD and answers each:
  - years lines: typed (program / people / total) and checked against D's leadership-years table,
    read live from D_Fit_Calibration.md so it never drifts; D's figure is rounded to the nearest whole
    year before comparing
  - location, certifications, degree: from data/form_answers.json and C_Gaps.md
  - authorization, sponsorship: from data/form_answers.json eligibility
  - clearance, travel: the candidate answers (never guessed)
Marks: ✗ honest answer is "no" on a required line (D: that's 🔴) · ⚠ short or borderline / the candidate confirms
       ✓ clears it. Lines that say preferred / plus / nice to have are shown but never a knockout.
Exits 1 when any required line is ✗. Standard library only.

Parsing rules:
  - Sections: lines are read under their heading (Required/Basic/Minimum qualifications vs Preferred vs
    Responsibilities vs About/Benefits). A certification only counts as ✗ under required/basic quals
    (or a line that says required/must); elsewhere it's ⚠ (a certification named under Responsibilities).
  - SAFe only as case-sensitive "SAFe" inside a qualifications/requirements line (never "safe", "Safety",
    "Sick and Safe Leave", or the TITLE/URL header). Certs match as case-sensitive whole words; CSM needs
    Scrum/certification nearby; "asset"/"plus" lines are soft.
  - Years lines must describe the candidate's experience: company age ("For over 25 years, Acme has"),
    "years of age", "years in a row" and "20 years of impact" are skipped. "N or more years" is read.
  - Each years line is typed from its own wording, per D's table: people (managing people/analysts/
    managers, direct reports, managerial experience), program (only program/project management is named),
    domain (a named industry, function or tool the candidate lacks: biotech, actuarial,
    Strategy Consulting, NetSuite...; honest years 0 when C lists it), total (a list that includes
    operations/strategy/business/analytics/transformation work or "or related").

Domain and location rules:
  - Required domain lines with no years ("Past experience in a Clinical Research
    role") are listed as "domain (no years)": ✗ when C lists the domain as a gap, else ⚠.
    Only under required/basic quals or on a line that says required/must.
  - Location is read only from the header LOCATION line, a Location/Travel/Workplace section, or a
    required-quals line, never from the company blurb or duties ("job site" in a company's brand text).
"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, C_FILE, load_json  # noqa: E402
from common import CFG, SEARCH, HOME  # noqa: E402

D_FILE = ROOT / "D_Fit_Calibration.md"
FORM = ROOT / "data" / "form_answers.json"

SOFT = re.compile(r"\b(prefer\w*|a plus|nice to have|ideal\w*|bonus|desired|an asset|asset\b|advantage\w*)\b", re.I)
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
YEARS = re.compile(r"\b(\d{1,2}|" + "|".join(WORDNUM) + r")\s*\+?\s*(?:\(\d+\)\s*)?(?:or more|or greater|plus)?\s*"
                   r"(?:-|–|to)?\s*\d*\s*\+?\s*years?\b", re.I)
# Case-sensitive on purpose: "safe", "Safety", "CSM" as a job role are not certifications.
CERTS = re.compile(r"(?<![\w-])(PMP|PgMP|CAPM|CSM|PSM|SAFe|Prosci|PROSCI|CPA|CFA|CMA|Six Sigma|Lean Six Sigma|ITIL|"
                   r"PMI-ACP|Scrum Master|CCMP|CISSP|Security\+)(?![\w-])")
CERT_NEEDS_CONTEXT = {"CSM": r"scrum|certif", "CMA": r"certif|accountant", "PSM": r"scrum|certif"}
# Section headings: a cert or years line counts as a knockout only under required/basic quals.
HEAD = [("preferred_first", r"^(?:preferred|desired|nice[- ]to[- ]have|bonus)\b"),
        ("required", r"(?:basic|minimum|required|must[- ]have)\s+(?:qualifications?|requirements?|skills|experience)|"
                     r"what (?:you(?:'ll)? need|we(?:'re| are) looking for|you bring)|requirements?:?$|qualifications:?$|"
                     r"who you are|you have|about you|must haves?"),
        ("preferred", r"(?:preferred|desired|nice[- ]to[- ]have|bonus|additional|plus)\s*(?:qualifications?|skills|experience|points)?|"
                      r"it(?:'s| is| would be) (?:a plus|great|nice)|ideally"),
        ("duties", r"responsibilit|what you(?:'ll| will) do|the role|your impact|day[- ]to[- ]day|in this role|key duties|"
                   r"what you(?:'ll| will) (?:own|lead|drive)"),
        ("location", r"^(?:job |work |office )?locations?$|^(?:location|travel|workplace|work (?:arrangement|model|setting|environment))"
                     r"(?: (?:&|and) (?:travel|schedule|location))?(?: requirements?)?$|where you(?:'ll| will) work"),
        ("about", r"about (?:us|the company|the team|[A-Z]\w+)$|benefits|perks|compensation|pay range|salary|equal opportunity|"
                  r"eeo|our (?:culture|values|mission)|why join|life at|privacy")]
CANDIDATE = re.compile(r"experience|background|track record|years?'?\s+(?:of\s+)?(?!age\b)[a-z]+ing\b|history of (?:leading|managing|working)|"
                       r"years?\s+(?:of\s+)?(?:work|working|managing|leading|in |as |within|supporting|running|building)|"
                       r"years'? (?:of )?(?:professional|progressive|relevant|related|industry|hands-on|direct)", re.I)
NOT_CANDIDATE = re.compile(r"years of age|years? old|in a row|for (?:over|more than|nearly|almost) \d+ years|"
                           r"\d+ years (?:ago|of (?:impact|history|innovation|growth|service to|excellence|serving))|"
                           r"(?:has|have) been [^.]{0,40}\d+ years|founded|anniversary|since \d{4}", re.I)
HAS = r"operations|ops\b|s&o|strategy(?!\s+consult)|business|analytic|analysis|transformation(?<!finance transformation)|" \
      r"process improvement|program management|project management|or (?:a )?related|related (?:roles?|field|experience)|similar"
PROGRAM = r"program|project|pmo|portfolio"
PEOPLE = (r"direct reports?|people (?:manage|lead)|supervis\w*|managing (?:a |the )?team|leading (?:a |the )?team|"
          r"team (?:lead|manage)|managing people|people leadership|manag\w* (?:analysts|managers|staff|employees|others|"
          r"a group|direct)|managerial|management of (?:people|staff|teams)|leading (?:people|managers|analysts)|"
          r"hiring and (?:coaching|developing)")
GAPS_TEXT = C_FILE.read_text(encoding="utf-8").lower() if C_FILE.exists() else ""
DOMAINS = (r"accounting|internal audit|deal desk|biotech|pharma\w*|life sciences|healthcare|clinical|finance transformation|org(?:anizational)? transformation|"
           r"strategy consulting|management consulting|consulting firm|big ?4|public accounting|audit|banking|wealth|"
           r"capital markets|broker[- ]dealer|insurance|cyber ?security|fintech|payments|saas|renewals?|supply chain|"
           r"manufacturing|legal|crypto|e-?commerce|marketing|advertising|retail media|customer data platform|cdp\b|industry experience|in the \w+ industry|"
           r"anaplan|varicent|spiff|xactly|pigment|oic\b|salesforce|hubspot|tableau|snowflake|workday|netsuite|sap\b|"
           r"oracle|servicenow administration|jira|commissions?|incentive compensation|sales compensation|lending|mortgage")
DEGREE = re.compile(r"\b(master'?s|MBA|M\.S\.|Ph\.?D|doctorate|bachelor'?s|B\.S\.|B\.A\.|degree)\b", re.I)
LOCATION = re.compile(r"\b(on-?site|in[- ]office|hybrid|relocat\w*|must (?:live|reside)|reside in|commut\w*|"
                      r"located (?:in|within))\b", re.I)
AUTH = re.compile(r"\b(authoriz\w* to work|sponsorship|visa|U\.?S\.? citizen\w*|citizenship|"
                  r"security clearance|clearance|public trust)\b", re.I)
DOMAIN_LINE = re.compile(r"\b(?:past|prior|previous|proven|direct|hands-on|demonstrated|recent)?\s*"
                         r"(?:experience|background|track record)\s+(?:working\s+)?(?:in|with|within|as|at|from|supporting)\b", re.I)
TRAVEL = re.compile(r"\btravel\b[^.]{0,40}\d+\s*%|\d+\s*%[^.]{0,20}\btravel\b", re.I)


def d_years():
    """{'program': 3.5, 'people': 2.5, 'total': 8.0} from D's leadership-years table."""
    out = {}
    for line in D_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        m = re.search(r"~(\d+(?:\.\d+)?)", cells[2])
        if not m:
            continue
        k = cells[0].lower()
        key = ("program" if "programs" in k and "leading" in k else
               "people" if "people" in k or "direct report" in k else
               "total" if "total tenure" in k else None)
        if key:
            out[key] = float(m.group(1))
    return out


def in_gaps(term):
    t = re.sub(r"s$", "", term.lower().strip())
    return bool(re.search(r"\b" + re.escape(t) + r"s?\b", GAPS_TEXT))


def years_type(ctx):
    """people / program / domain / total, from the line's own wording (D's leadership-years table)."""
    c = ctx.lower()
    if re.search(PEOPLE, c):
        return "people", None
    # Split the "experience in A, B, or C" list into items; any item that is plain ops/strategy/program work
    # (no named domain in it) makes it a total-tenure line; all items domain-named -> domain line.
    tail = re.split(r"years?'?\s*(?:of\s+)?(?:[\w-]+\s+){0,3}?(?:experience|exp)?\s*(?:in|within|as|across|with)?\s", c, maxsplit=1)[-1]
    tail = re.split(r"[.;:]| including| with a| where| ideally| preferably", tail)[0]
    items = [x.strip() for x in re.split(r",|\bor\b|\band\b", tail) if x.strip()]
    doms = [re.search(DOMAINS, x) for x in items]
    plain = [x for x, d in zip(items, doms) if not d and re.search(HAS, x)]
    named = [d.group(0) for d in doms if d]
    if named and not plain:
        return "domain", next((d for d in named if in_gaps(d)), named[-1])
    if re.search(PROGRAM, c) and not re.search(HAS.replace(r"program management|project management|", ""), c):
        return "program", None
    return "total", None


def section_of(line, cur):
    """Return the section a heading line starts, or cur if the line isn't a heading."""
    t = line.strip().strip("#*:- ").strip()
    if not t or len(t) > 70 or t.endswith(".") and len(t) > 40:
        return cur
    for name, pat in HEAD:
        if re.search(pat, t, re.I) and (len(t.split()) <= 8):
            return "preferred" if name == "preferred_first" else name
    return cur


def sentences(text):
    """[(section, sentence)] in order; section = the latest heading above it (or None)."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\b([A-Z])\.([A-Z])\.", r"\1\2", text)          # U.S. -> US, so it doesn't end a sentence
    out, sec = [], None
    for raw in text.split("\n"):
        new = section_of(raw, sec)
        if new != sec:
            sec = new
            if len(raw.strip()) <= 70:
                continue
        for p in re.split(r"(?<=[.!?])\s+(?=[A-Z(])|\s•\s|\s·\s", raw):
            p = p.strip(" -•·*\t")
            if len(p) > 8:
                out.append((sec, p))
    return out


def short(s, n=110):
    return s if len(s) <= n else s[:n - 1] + "…"


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    from common import read_jd  # header lines (TITLE/URL/...) are never requirement lines
    head, jd = read_jd(argv[0])
    yrs = d_years()
    form = load_json(FORM) if FORM.exists() else {}
    have_certs = " ".join(form.get("certifications", [])).lower()
    gaps = C_FILE.read_text(encoding="utf-8").lower() if C_FILE.exists() else ""
    rows, seen = [], set()
    hl = " ".join(head.get(k, "") for k in ("location", "remote"))

    def add(mark, kind, line, answer, sec=None):
        key = (kind, line[:60])
        if key in seen:
            return
        seen.add(key)
        core = re.split(r",?\s+(?:preferably|ideally)\b", line)[0]  # "5+ yrs ..., preferably in SaaS" is still required
        soft = bool(SOFT.search(core)) or sec == "preferred"
        if soft:
            mark = "·"
        rows.append((mark, kind + (" (preferred)" if soft else ""), short(line), answer))

    for sec, s in sentences(jd):
        for m in (YEARS.finditer(s) if sec != "about" else []):  # company blurb/benefits: never a requirement
            n = m.group(1).lower()
            n = WORDNUM.get(n, None) or int(n)
            if n > 30 or NOT_CANDIDATE.search(s) or not CANDIDATE.search(s):
                continue
            t, dom = years_type(s)
            if t == "domain":
                lacks = in_gaps(dom)
                add("✗" if lacks else "⚠", "years (domain)", s,
                    f"asks {n} in {dom}: " + ("honest ~0 (C lists it as a gap)" if lacks
                                              else "count by hand from B (named domain/tool, not total tenure)"), sec)
                continue
            have = yrs.get(t)
            if have is None:
                add("⚠", f"years ({t})", s, "D table not readable; count by hand", sec)
                continue
            whole = int(have + 0.5)  # the candidate, round to the nearest year (3.5 -> 4); half rounds up
            gap = n - whole
            mark = "✓" if gap <= 0 else ("⚠" if gap < 2 else "✗")
            word = "met" if gap <= 0 else ("borderline" if gap < 2 else "short")
            add(mark, f"years ({t})", s, f"asks {n}, honest ~{have} → rounds to {whole} ({word})"
                + ("; 'or equivalent' clause can lift one step" if re.search(r"equivalent", s, re.I) else ""), sec)
        for m in (CERTS.finditer(s) if sec != "about" else []):
            c = m.group(1)
            if c in CERT_NEEDS_CONTEXT and not re.search(CERT_NEEDS_CONTEXT[c], s, re.I):
                continue
            if c == "SAFe" and sec not in ("required", "preferred") and not re.search(r"certif|requir|must", s, re.I):
                continue  # SAFe only inside a qualifications/requirements line
            if c.lower() in have_certs:
                add("✓", "certification", s, f"{c}: yes", sec)
                continue
            note = " (listed in C as a gap)" if c.lower() in gaps else ""
            required = sec == "required" or re.search(r"\b(required|must)\b", s, re.I)
            add("✗" if required else "⚠", "certification", s,
                f"{c}: no{note}" + ("" if required else " (not under required quals: not a knockout)"), sec)
        if DEGREE.search(s) and re.search(r"\b(required|must|minimum|basic qualification)", s, re.I):
            if re.search(r"master'?s|MBA|M\.S\.|Ph\.?D|doctorate", s, re.I) and not re.search(r"bachelor", s, re.I):
                add("✗", "degree", s, f"{CFG['candidate']['degree_summary']} only")
            else:
                add("✓", "degree", s, CFG["candidate"]["degree_summary"])
        dl = DOMAIN_LINE.search(s)
        if dl and sec != "about" and not YEARS.search(s) and (sec == "required" or re.search(r"\b(required|must)\b", s, re.I)):
            dm = re.search(DOMAINS, s[dl.end():].lower())
            if dm:
                dom = dm.group(0)
                lacks = in_gaps(dom)
                add("✗" if lacks else "⚠", "domain (no years)", s,
                    f"required {dom} experience: " + ("C lists it as a gap" if lacks else "check B/C by hand"), sec)
        loc_ok = sec in ("location", "required") or (sec is None and re.search(r"^\s*(?:location|travel)\b", s, re.I))
        if LOCATION.search(s) and loc_ok:
            add("⚠", "location", s, f"only remote or within {SEARCH['commute_radius_mi']} mi of {HOME}; no relocation. Check the office city")
        if AUTH.search(s):
            elig = form.get("eligibility", {}) or {}
            if re.search(r"clearance|public trust", s, re.I):
                add("⚠", "clearance", s, "the candidate answers (clearance not on file; never guessed)")
            elif elig.get("authorized_to_work_us") is True and elig.get("needs_sponsorship") is False:
                add("✓", "authorization", s, "authorized to work in the US; no sponsorship needed (form_answers.json)")
            else:
                add("⚠", "authorization", s, "the candidate answers (not on file; never guessed)")
        if TRAVEL.search(s):
            add("⚠", "travel", s, "the candidate answers (travel limit not on file)")

    if hl.strip() and LOCATION.search(hl):
        add("⚠", "location", "LOCATION: " + hl.strip(), f"only remote or within {SEARCH['commute_radius_mi']} mi of {HOME}; no relocation. Check the office city")
    if not rows:
        print("Knockouts: none found in the JD text. Still check the application form's own questions.")
        return 0
    print("LIKELY KNOCKOUT QUESTIONS (honest answers; ✗ on a required line = 🔴 per D)")
    for mark, kind, line, ans in rows:
        print(f"{mark} {kind}: {ans}\n    JD: {line}")
    hard = [r for r in rows if r[0] == "✗"]
    print(f"\n{len(rows)} found · {len(hard)} ✗ · {sum(r[0] == '⚠' for r in rows)} ⚠")
    return 1 if hard else 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
