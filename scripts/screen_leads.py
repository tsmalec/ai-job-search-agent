"""screen_leads.py — the candidate's hard filters as rules. No AI, so it costs nothing to run.

Usage (from the repo root):
    python scripts/screen_leads.py jd.txt [more.txt ...]   screen saved postings (from fetch_jd.py)
    python scripts/screen_leads.py --leads                 screen every not_triaged lead from the scheduler's line only
    python scripts/screen_leads.py --leads --fetch         fetch each lead's posting first (more accurate)

Each posting gets PASS / FLAG / FAIL with the reasons. FAIL = one of the candidate's knockouts:
  - Not remote and not within 50 miles of home (hybrid or on-site elsewhere)
  - "Remote" with in-office time at the company's or manager's discretion (bait and switch)
  - Remote limited to a list of states that leaves out the candidate's home state
  - Only OTE (on-target earnings), no guaranteed base
  - Pay not listed AND the title's market base (data/title_pay.json) is under the pay floor
  - Top of the base range under the pay floor
  - Pay buried under layers of "may vary / may be modified / depends on location" (3 or more)
FLAG = worth a human look (range straddles the floor, one or two pay caveats, travel, Salesforce, etc.).
Pay not listed = HOLD, not a fail: check the title's market base in data/title_pay.json.
  Market clears the pay floor -> proceed, "pay unconfirmed", pay first in the recruiter screen. Unknown title -> research once, add it.
every posting also gets its own injection check (scripts/inject_check.py): a 🛡️ line with the exact
  planted text, separate from PASS/FLAG/FAIL. It never changes the verdict; Claude ignores the text and tells the candidate.
every fetched or saved posting also gets the liveness check (scripts/check_live.py):
  CLOSED (closed banner, 404/410) = FAIL; UNCERTAIN (thin page, redirect, fetch error) = FLAG, never a skip.
  Without --fetch there's no page to check, so leads aren't liveness-checked.
a careers landing page read in place of the posting (job-board link read only by the plain-page fallback,
  or a page whose title doesn't match the role / URL title) = FLAG "Read a careers page, not the posting".
Rules only: it can't judge fit. Anything that passes still goes to Claude for the verdict.
"""
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import APPS, LEADS, ROOT, existing, load_json  # noqa: E402
from common import host_is, k_fmt, pay_rules  # noqa: E402
from common import CFG, SEARCH, HOME, STATE_RE, HOME_CODES, HOME_NAMES  # noqa: E402
from inject_check import scan as inject_scan, report as inject_report, planted  # noqa: E402
from check_live import check as live_check  # noqa: E402
from remote_watch import flag as remote_watch_flag  # noqa: E402

TITLE_PAY = ROOT / "data" / "title_pay.json"


def market_pay(title):
    """Best title_pay.json match for a role title: (entry, None) or (None, None)."""
    if not title or not TITLE_PAY.exists():
        return None
    t = title.lower()
    hits = [e for e in load_json(TITLE_PAY)["titles"] if any(k in t for k in e["match"])]
    return max(hits, key=lambda e: max(len(k) for k in e["match"] if k in t)) if hits else None

_PAY = pay_rules()
FLOOR = _PAY["screen_min"]    # hard screen (data/pay_rules.json)
TARGET = _PAY["vet_floor"]    # vetting floor; between the two = the user's call
NEAR = SEARCH["near_cities"]
STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
          "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
          "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
          "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
          "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
          "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
          "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
          "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
          "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
          "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
          "district of columbia": "DC"}
DISCRETION = [r"office visits?", r"based on business needs?", r"at (?:the|your) (?:manager'?s?|company'?s?|leader'?s?) discretion",
              r"may be (?:required|asked|expected) to (?:come|work|report) (?:in|into|on-?site|from (?:the|an) office)",
              r"occasional(?:ly)? (?:in[- ]office|on-?site|in[- ]person)", r"in[- ]person (?:as needed|when needed|periodically)",
              r"(?:could|may|might) (?:change|transition|become) (?:to )?(?:hybrid|in[- ]office|on-?site)"]  # 'primarily remote' + a few trips/yr is still remote
HYBRID = [r"\bhybrid\b", r"\bin[- ]office\b", r"\bon-?site\b", r"\bin the office\b", r"\d\s*days?\s*(?:a|per|/)\s*week\s*(?:in|on)",
          r"return to office", r"(?:weekly|every week|each week)\s+(?:travel|in[- ]office|on-?site|in[- ]person)", r"must (?:live|reside|be located) within \d+ ?miles"]
NOT_IN_OFFICE = r"0 days\s*/?\s*(?:a|per)?\s*week in[- ]office"
REMOTE = [r"\bremote\b", r"work from home", r"\bwfh\b", r"telecommute", r"work from anywhere", NOT_IN_OFFICE]
PERK = r"\d+\s*weeks?\s*(?:a|per|each|/)\s*year|weeks? of (?:remote|work from anywhere)|\bperks?\b|\bbenefits?\b.{0,40}(?:remote|anywhere)"
HUB = [r"(?:within|in) (?:a )?commut\w* distance", r"commutable (?:distance|to)", r"anchored in (?:our )?hubs?",
       r"remote[- ]co-?located", r"near (?:one of )?(?:our )?(?:offices|hubs)",
       r"(?:live|reside|be based|be located) (?:in|near|close to) (?:one of )?(?:our|the following|these)? ?(?:hubs?|offices|cities|metros?)"]
TAG = r"\((on-?site|hybrid)\)|^\s*(on-?site|hybrid)\s*$|workplace type:?\s*(on-?site|hybrid)"
PAY_CAVEATS = [r"may vary", r"may be modified", r"subject to change", r"(?:depend|based)\w* on (?:your )?(?:geograph|location|market|zone)",
               r"\bpay zones?\b", r"\bgeographic (?:zone|differential|market)", r"not a guarantee", r"at (?:the company'?s )?(?:sole )?discretion",
               r"\btiers?\b.{0,30}\blocation", r"individual pay (?:is|will be) determined"]
# Pay parsing: $ optional, currency code after the number ("$131,000 USD"), /yr, /hr,
# "per year", decimal K ("$103.8K/yr"), "between X and Y", "$X+" open floors, hourly ("$54-57 an hour").
NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{2,3}(?:\.\d+)?\s?[kK]\b|\d{2,3}(?:\.\d{1,2})?)"
UNIT = r"(?:\s?(?:USD|usd|US\$))?(?:\s?(?:/\s?(?:yr|year|annum|hr|hour)|per (?:year|annum|hour)|an hour|a year|annually|hourly))?"
SEP = r"\s*(?:-|–|—|to|and)\s*"
PAYWORD = re.compile(r"salary|compensation|\bpay\b|base|wage|\bote\b|\bttc\b|range|usd|per year|/yr|hourly|an hour", re.I)


OTE_PAT = (r"\bote\b|\bttc\b|on[- ]target (?:earnings|compensation)|total target cash|includes? (?:sales )?(?:incentives?|commissions?|variable)"
           r"|base (?:is )?unknown")
# Total-comp / total-cash ranges (base not split out) are NOT a fail; only OTE-only fails
TOTAL_PAT = r"total (?:target )?(?:cash|compensation|comp)\b"



def loc_near(loc):
    """A NEAR city on the location line counts unless the line names another state
    (a same-named city in another state read as home). No state at all ("<city> Metro") still counts."""
    if not any(re.search(rf"\b{re.escape(c)}\b", loc) for c in NEAR):
        return False
    if re.search(STATE_RE, loc):
        return True
    other = [a.lower() for n, a in STATES.items() if a not in HOME_CODES] + \
            [n for n in STATES if n not in HOME_NAMES]
    return not any(re.search(rf"(?:,\s*|\b){re.escape(x)}\b" if len(x) == 2 else rf"\b{re.escape(x)}\b", loc)
                   for x in other if len(x) > 2 or re.search(rf",\s*{x}\b", loc))

def money(s, hourly_ctx=False):
    s = s.replace("$", "").replace(" ", "").lower()
    v = float(s[:-1]) * 1000 if s.endswith("k") else float(s.replace(",", ""))
    return v


def ranges(text):
    """All salary ranges found, as (low, high, the sentence around them, hourly?)."""
    out = []
    text = re.sub(r"(\d),\s+(\d{3})\b", r"\1,\2", text)  # "$150, 000" -> "$150,000"
    pat = re.compile(r"(\$)?\s?" + NUM + UNIT + SEP + r"\$?\s?" + NUM + r"(\+)?" + UNIT, re.I)
    for m in pat.finditer(text):
        whole = m.group(0)
        ctx = text[max(0, m.start() - 160): m.end() + 120]
        if not m.group(1) and not re.search(r"usd|/\s?yr|per year|annum|annually", whole, re.I) \
                and not PAYWORD.search(text[max(0, m.start() - 80): m.start()]):
            continue  # a bare number pair with no pay context (e.g. "5-7 years") is not pay
        try:
            lo, hi = money(m.group(2)), money(m.group(3))
        except ValueError:
            continue
        hourly = bool(re.search(r"/\s?h(?:ou)?r|per hour|an hour|hourly", whole + text[m.end():m.end() + 15], re.I))
        if hourly and lo < 1000 and hi < 1000:
            lo, hi = lo * 2080, hi * 2080
        elif lo < 1000 <= hi:          # "$125 - $150,000" means 125,000
            lo *= 1000
        elif lo < 1000 and hi < 1000:
            continue
        if 20_000 <= lo <= hi <= 2_000_000:
            out.append((lo, hi, ctx, hourly))
    return out


def open_floor(text):
    """A single open-ended base like "$115,000+ USD": the stated floor, or None."""
    for m in re.finditer(r"\$\s?" + NUM + r"\s?\+" + UNIT, text):
        if PAYWORD.search(text[max(0, m.start() - 100): m.end() + 40]):
            try:
                v = money(m.group(1))
            except ValueError:
                continue
            if 20_000 <= v <= 2_000_000:
                return v, text[max(0, m.start() - 160): m.end() + 80]
    return None


def is_ote(ctx, start=160):
    """A range is OTE/TTC when its own heading says so (e.g. "Compensation Ranges (TTC / OTE)"), or when
    the nearby text says OTE/commission and never says base. "does not include incentive pay" is base."""
    before = re.sub(r"(?:does|do|will) not include[^.]*", "", ctx[:start].lower())
    head = ctx[max(0, start - 80):start].lower()
    if re.search(r"\bote\b|\bttc\b|on[- ]target|total target cash", head):
        return True
    if re.search(r"\bbase (?:salary|pay|range|compensation)\b|\$[\d,.]+k?\s*base\b", before + ctx[start:start + 60].lower()):
        return False
    return bool(re.search(OTE_PAT, before))


def linkedin_body(text):
    """Cut LinkedIn page text before its sidebars ("similar jobs", "people also viewed") so their pay
    figures can't be read as this posting's."""
    cut = re.search(r"\n\s*(?:similar jobs|people also viewed|more jobs|jobs you may be interested in|"
                    r"explore collaborative articles|show more jobs like this)\b", text, re.I)
    return text[:cut.start()] if cut else text


EXCLUDE = r"not (?:currently )?(?:considering|hiring|eligible|able to (?:hire|employ))|unable to (?:hire|employ)|cannot (?:hire|employ)|" \
          r"(?:excluding|except(?: for)?|with the exception of)\b|won'?t (?:hire|be able)|do(?:es)? not (?:hire|employ)"
LOCWORDS = r"office|on-?site|in[- ]person|location|hybrid|remote|relocat|commut|workplace|work site|in the office"


def sentences_of(text):
    """Sentences, also split on line breaks (a state list must not merge with the next paragraph)."""
    return [x.strip() for x in re.split(r"(?<=[.;!?])\s+|\n+", text) if x.strip()]


def screen(text, location="", title="", card_pay="", company=""):
    text = linkedin_body(text)
    t = " ".join(text.split())
    low = t.lower()
    loc = (location or "").lower()
    fails, flags = [], []

    # The first 4 NEAR cities count on their own; the rest need a state tag.
    # Beyond the first 4, require a state tag so common town names elsewhere don't match.
    # Workday URL paths can carry the city (e.g. "./CITY-ST/.").
    near = (loc_near(loc)
            or any(re.search(rf"\b{re.escape(c)}\b", low[:600]) for c in NEAR[:4])
            or any(re.search(rf"\b{re.escape(c)}\b,?\s*" + STATE_RE, low[:600]) for c in NEAR[4:]))
    sents = sentences_of(text)
    # "work from anywhere 3 weeks a year" is a perk, not a remote role.
    # Remote words only count in sentences that aren't a perk/benefit or a weeks-per-year allowance.
    real = [x.lower() for x in sents if not re.search(PERK, x.lower())]
    is_remote = (any(re.search(p, x) for p in REMOTE for x in real)
                 or "telecommute" in loc or "remote" in loc)
    # Hub/commute clauses = remote bait and switch, whatever the tag says.
    hub = next((m.group(0) for x in sents for p in HUB for m in [re.search(p, x.lower())] if m), None)
    # The structured workplace tag outranks the blurb (LinkedIn "(On-site)"/"Hybrid", Workday location).
    m = re.search(TAG, (loc + "\n" + text[:800]).lower(), re.M)
    tag = next((g for g in m.groups() if g), None) if m else None
    # "hybrid" only as a work arrangement, not "Agile, Waterfall, or hybrid methodologies"
    hybrid_hits = [p for p in HYBRID if any(re.search(p, x.lower()) and (p != r"\bhybrid\b" or re.search(
        r"office|on-?site|days?|week|work|schedule|role|position|location|remote", x.lower().replace("hybrid", "")))
        and not re.search(r"methodolog|waterfall|agile|cloud|hybrid (?:cloud|model of|approach)", x.lower())
        for x in sents)]
    if re.search(NOT_IN_OFFICE, low):
        hybrid_hits = []
    # discretion phrases only count in a sentence about where you work (not, e.g., an application-deadline line)
    discretion = []
    for p in DISCRETION:
        for x in sents:
            m = re.search(p, x.lower())
            if m and re.search(LOCWORDS, x.lower().replace(m.group(0), "")):
                if re.search(r"off-?sites?|gatherings?|conferences?|customer (?:meetings?|visits?|events?)|events?\b|"
                             r"team (?:meetings|summits?)|kick-?offs?", x.lower()) and not re.search(
                             r"weekly|each week|every week|\d\s*days?\s*(?:a|per|/)\s*(?:week|month)|regular(?:ly)?", x.lower()):
                    flags.append(f"Occasional in-person trips (\"{m.group(0)}\"): work trips, still remote (D)")
                    break
                discretion.append(m.group(0))
                break

    if near:
        flags.append(f"{HOME}-area location: hybrid/on-site is OK within {SEARCH['commute_radius_mi']} mi; confirm the office")
    elif hub:
        fails.append(f"Bait and switch: \"remote\" tied to a hub/commute (\"{hub}\")")
    elif tag:
        fails.append(f"Workplace tag says {tag}, not remote (tag outranks the blurb)")
    elif hybrid_hits and not is_remote:
        fails.append(f"Hybrid or on-site, not near {HOME}")
    elif not is_remote:
        flags.append("No remote language found: confirm the work location")
    elif discretion:
        fails.append(f"Bait and switch: remote but in-office at their discretion (\"{discretion[0]}\")")
    elif hybrid_hits:
        flags.append("Remote, but the text also mentions hybrid/office work: read that line")

    # Remote limited to listed states. Exclusion lists ("not considering candidates in ...",
    # "not eligible to be hired in ...") only fail when the home state is on them; a list carrying $ amounts is
    # a pay-tier list = FLAG to check the home state's tier, never a FAIL; "any of the 50 states" = open.
    for sent in sents:
        sl = sent.lower()
        if "state" not in sl and "eligible" not in sl and "remote" not in sl and "hire" not in sl:
            continue
        if re.search(r"any (?:of the )?50 (?:united )?states|all 50 states", sl):
            continue
        names = {code for name, code in STATES.items() if re.search(rf"\b{name}\b", sl)}
        names |= {code for code in STATES.values() if re.search(rf"(?<![A-Za-z]){code}(?![A-Za-z])", sent)
                  and code not in ("IN", "OR", "ME", "OK", "HI", "DE", "PA", "MA", "CO", "AL", "ID", "LA", "MO")}
        if len(names) < 4:
            continue
        lst = ", ".join(sorted(names))[:80]
        if re.search(EXCLUDE, sl):
            if names & HOME_CODES:
                fails.append(f"{HOME} state is on the excluded-states list ({lst})")
        elif "$" in sent or re.search(r"salary|pay|compensation|\d{2,3},\d{3}", sl):
            flags.append(f"State list is a pay-tier list, not a remote limit ({lst}): check which tier {HOME} falls in")
        elif not names & HOME_CODES:
            fails.append(f"Remote only in listed states; {HOME} isn't in one ({lst})")
        break

    # Pay. When the LinkedIn card and the posting body disagree, screen on the body's lower base.
    body_rs, card_rs = ranges(text), ranges(card_pay or "")
    rs = body_rs or card_rs
    if body_rs and card_rs:
        bmin, cmin = min(r[1] for r in body_rs), min(r[1] for r in card_rs)
        if abs(bmin - cmin) > 5_000 or abs(min(r[0] for r in body_rs) - min(r[0] for r in card_rs)) > 5_000:
            flags.append(f"Pay conflict (card vs. body): card {card_pay.strip()[:60]}; screened on the body's base")
    ote = re.search(OTE_PAT, low)
    floor = None if rs else open_floor(text + " " + (card_pay or ""))
    if floor:
        v, ctx = floor
        if is_ote(" " * 160 + ctx[160:], 160) and re.search(OTE_PAT, ctx.lower()):
            fails.append("Only OTE shown, no guaranteed base")
        elif v < FLOOR:
            flags.append(f"Open-ended base ${v:,.0f}+ (no top): floor under the pay floor, no top stated. The candidate's call; ask the range first")
        else:
            flags.append(f"Open-ended base ${v:,.0f}+ (no top stated): clears the floor; confirm the band in the screen")
    elif not rs:
        m = market_pay(title)
        if m and m["market_base_hi"] < FLOOR:
            fails.append(f"Pay not listed; market base for {m['title']} tops out ~${m['market_base_hi']:,.0f}, under the ${FLOOR/1000:,.0f}K floor")
        elif m:
            flags.append(f"HOLD: pay not listed; market base for {m['title']} ~${m['market_base_lo']:,.0f}–${m['market_base_hi']:,.0f} "
                         f"(median ~${m['market_median']:,.0f}). Proceed, pay unconfirmed: ask pay first")
        else:
            flags.append("HOLD: pay not listed and title not in data/title_pay.json: research its market base once, add it, rerun")
    else:
        base_rs = [r for r in rs if not is_ote(r[2])]
        if not base_rs:
            fails.append("Only OTE/TTC shown, no guaranteed base")
        else:
            nc = [r for r in base_rs if re.search(STATE_RE + "|" + "|".join(map(re.escape, NEAR)), r[2].lower())]
            lo, hi, _, hourly = (nc or [min(base_rs, key=lambda r: r[1])])[0]
            if hourly:
                flags.append(f"Hourly pay (x2080 = ${lo:,.0f}–${hi:,.0f}/yr): likely a W2 contract, not salaried")
            if len(base_rs) > 1 and not nc:
                flags.append(f"{len(base_rs)} pay zones; used the lowest (${lo:,.0f}–${hi:,.0f}). Check which applies to {HOME}")
            if hi < FLOOR:
                fails.append(f"Base tops out at ${hi:,.0f}, under the ${FLOOR/1000:,.0f}K floor")
            else:
                if lo < FLOOR:
                    flags.append(f"Base ${lo:,.0f}–${hi:,.0f} straddles the ${FLOOR/1000:,.0f}K floor")
                if hi < TARGET:
                    flags.append(f"Base top ${hi:,.0f} is under the ${TARGET/1000:,.0f}K target (discretionary, the candidate's call)")
            if ote:
                flags.append("Mentions OTE: make sure the range quoted is base")
            if re.search(TOTAL_PAT, low):
                flags.append("Total-comp range, base not split out: OK to pursue (only OTE fails); ask base first in a screen")
        cav = sorted({re.search(p, low).group(0) for p in PAY_CAVEATS if re.search(p, low)})
        if len(cav) >= 3:
            fails.append(f"Pay buried in caveats ({'; '.join(cav[:4])})")
        elif cav:
            flags.append(f"Pay caveat: {'; '.join(cav)}")

    trav = re.search(r"(\d{1,2})\s*%\s*(?:of the time\s*)?travel|travel[^.]{0,40}?(\d{1,2})\s*%", low)
    if trav:
        flags.append(f"Travel ~{trav.group(1) or trav.group(2)}%")
    for tool, msg in CFG.get("resume", {}).get("flag_tools", {}).items():
        if tool.lower() in low:
            flags.append(msg)
    # Knockouts at sweep time: a required active clearance is a hard fail;
    # a required PMP and heavy travel are flags (knockouts.py / D decide the verdict, not the screen).
    clr = re.search(r"(?:active|current)[^.]{0,30}(?:top secret|ts/sci|secret|security) clearance[^.]{0,40}(?:required|must)|"
                    r"(?:must|required to) (?:have|hold|possess)[^.]{0,30}(?:top secret|ts/sci|secret) clearance", low)
    if clr:
        fails.append(f"Knockout: active security clearance required (\"{clr.group(0)[:80]}\")")
    if re.search(r"pmp[^.]{0,40}(?:required|must)|(?:required|must have)[^.]{0,40}\bpmp\b", low) and \
            not re.search(r"pmp[^.]{0,30}(?:preferred|a plus|desired|nice)", low):
        flags.append("Knockout risk: PMP listed as required")
    if trav and int(trav.group(1) or trav.group(2)) >= 25:
        flags.append("Knockout risk: travel 25%+")
    wf = remote_watch_flag(company)
    if wf:
        flags.append(wf)
    if re.search(r"\b(?:visa )?sponsorship\b", low) and re.search(r"(?:not|no|unable)[^.]{0,30}sponsor", low):
        flags.append("No visa sponsorship (fine for the candidate, FYI)")

    dom = domain_gaps(text)
    if dom:
        cap = "🟠 (2+ core gaps)" if len(dom) >= 2 else "🟡"
        flags.append(f"Core-domain gap in the role's purpose: {', '.join(dom)} (C_Gaps) → caps at {cap}; "
                     "read the full JD before any verdict")

    verdict = "FAIL" if fails else ("FLAG" if flags else "PASS")
    return verdict, fails, flags


def domain_gaps(text):
    """A generic title can hide a different domain. Check the
    JD's opening (purpose) text against C_Gaps domains; boilerplate further down is ignored."""
    try:
        cfg = json.loads((Path(__file__).resolve().parent.parent / "data" / "domain_gaps.json").read_text())
    except (OSError, ValueError):
        return []
    body = "\n".join(l for l in text.splitlines() if not re.match(r"^[A-Z]{2,10}:", l))
    # Openers are often values boilerplate; start at the first responsibilities heading.
    m = re.search(r"(?im)^\s*(what you'?ll do|responsibilities|key responsibilities|the role|about the role|what you will do|your role)\b", body)
    start = m.start() if m else 0
    head = body[start:].strip()[: cfg.get("purpose_chars", 900)].lower()
    return [d for d, pats in cfg["domains"].items() if any(re.search(p, head) for p in pats)]


def apply_live(v, fails, flags, live):
    """Fold the liveness result into the verdict: CLOSED = FAIL, UNCERTAIN = FLAG."""
    if not live:
        return v, fails, flags
    status, why = live
    if status == "CLOSED":
        return "FAIL", [f"Posting closed: {why}"] + fails, flags
    if status == "UNCERTAIN":
        return ("FLAG" if v == "PASS" else v), fails, flags + [f"Still open? {why}"]
    return v, fails, flags


def show(name, verdict, fails, flags):
    icon = {"PASS": "✅", "FLAG": "🟡", "FAIL": "❌"}[verdict]
    print(f"{icon} {verdict}  {name}")
    for r in fails:
        print(f"     ✗ {r}")
    for r in flags:
        print(f"     · {r}")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    from fetch_jd import fetch, write, wrong_page  # noqa: E402
    from common import read_jd  # noqa: E402
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--leads" in sys.argv:
        leads = [l for l in load_json(LEADS)["leads"] if l.get("status") == "not_triaged"]
        # leads already decided in applications.json (same URL / req ID) aren't re-screened.
        apps = load_json(APPS)["applications"]
        logged = [l for l in leads if existing(apps, l.get("url")) is not None]
        leads = [l for l in leads if existing(apps, l.get("url")) is None]
        if logged:
            print(f"♻️ already logged, not screened ({len(logged)}):")
            for l in logged:
                print(f"  {l['company']} - {l['role'][:60]} → #{existing(apps, l.get('url'))}")
        print(f"{len(leads)} not_triaged leads" + ("" if "--fetch" in sys.argv else " (the scheduler's line only; add --fetch for the full posting)"))
        tally = {"PASS": 0, "FLAG": 0, "FAIL": 0}
        injected, formatting = [], []
        for l in leads:
            d, live = {}, None
            name = f"{l['company']} - {l['role'][:70]}"
            text, loc = f"{l.get('where_pay','')}. ", l.get("where_pay", "")
            card = l.get("where_pay", "")
            if "--fetch" in sys.argv and l.get("url") and host_is(l["url"], "linkedin.com"):
                print(f"  ({l['company']}: LinkedIn = browser only (SKILL.md); screened on the scheduler's line, read it in the browser)")
            elif "--fetch" in sys.argv and l.get("url"):
                try:
                    d = fetch(l["url"])
                    Path("work").mkdir(exist_ok=True)
                    slug = re.sub(r"\W+", "_", f"{l['company']}_{l['role']}")[:60]
                    write(d, l["url"], f"work/{slug}.txt")
                    text, loc = f"{d.get('remote','')} {d.get('location','')}. {d['text']}", d.get("location", "")
                    card = f"{card} {d.get('pay','')}"
                    live = live_check(d["text"], url=l["url"])
                except SystemExit as e:
                    print(f"  (couldn't fetch {l['company']}: using the scheduler's line)")
                    live = live_check("", error=str(e))
            v, f_, fl = apply_live(*screen(text, loc, l.get('role', ''), card, l.get('company', '')), live)
            wp = wrong_page(d, l.get("url", ""), l.get("role", "")) if d else ""
            if wp:  # a careers landing page is not the posting; don't let its text decide
                v, f_, fl = ("FAIL" if v == "FAIL" and any(x.startswith("Posting closed") for x in f_) else "FLAG"), \
                    [x for x in f_ if x.startswith("Posting closed")], [wp + ": open it in the browser"]
            tally[v] += 1
            show(name, v, f_, fl)
            hits = inject_scan(text, {"hidden": d.get("hidden")})
            if hits:
                (injected if planted(hits) else formatting).append(name)
                inject_report(name, hits)
        print(f"\nPASS {tally['PASS']} · FLAG {tally['FLAG']} · FAIL {tally['FAIL']}")
        print(f"🛡️ Injection check: {len(injected)} posting(s) with planted AI instructions"
              + (f" ({'; '.join(injected)}). Show the candidate each line verbatim." if injected else ". None.")
              + (f" · 🔹 {len(formatting)} with formatting characters only (no instruction text)" if formatting else ""))
    elif args:
        injected, formatting = [], []
        for p in args:
            head, text = read_jd(p)
            v, f_, fl = screen(f"{head.get('remote','')} {head.get('location','')}. {text}",
                               f"{head.get('location', '')} {head.get('url', '')}",
                               head.get("title", "") or head.get("role", ""), head.get("pay", ""), head.get("company", ""))
            v, f_, fl = apply_live(v, f_, fl, live_check(text))
            wp = wrong_page(dict(source=head.get("source", ""), title=head.get("title", ""), text=text), head.get("url", ""))
            if wp:  # a careers landing page is not the posting; don't let its text decide
                v, f_, fl = ("FAIL" if any(x.startswith("Posting closed") for x in f_) else "FLAG"), \
                    [x for x in f_ if x.startswith("Posting closed")], [wp + ": open it in the browser"]
            show(head.get("title") or p, v, f_, fl)
            hits = inject_scan(text, head)
            if hits:
                (injected if planted(hits) else formatting).append(head.get("title") or p)
                inject_report(head.get("title") or p, hits)
        # file mode prints the 🛡️ tally too (it used to print only with --leads)
        print(f"\n🛡️ Injection check: {len(injected)} posting(s) with planted AI instructions"
              + (f" ({'; '.join(injected)}). Show the candidate each line verbatim." if injected else ". None.")
              + (f" · 🔹 {len(formatting)} with formatting characters only (no instruction text)" if formatting else ""))
    else:
        raise SystemExit(__doc__)
