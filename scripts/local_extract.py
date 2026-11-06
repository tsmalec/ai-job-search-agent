"""local_extract.py — turn a saved posting into jd.json + keywords.txt on your own PC.

Usage (from the repo root):
    python scripts/local_extract.py jd.txt                 rules + local model (Ollama)
    python scripts/local_extract.py jd.txt --no-llm        rules only (no Ollama needed)
    python scripts/local_extract.py jd.txt --model qwen3.5:9b

Two passes, merged:
  1. Rules (always): reads the "Required / Minimum / Basic qualifications" and "Preferred" sections line
     by line, and finds every B tag and C gap term that appears in the posting.
  2. Local model (if Ollama is running): asks for title, title core words, required, preferred, and
     keywords as JSON. Anything the model returns that isn't actually in the posting is thrown out,
     so a small model can't invent requirements.
Outputs (same shapes ats_check.py and shop.py already read):
  jd.json       title, title_core, required, preferred, keywords
  keywords.txt  one keyword per line, in the order the posting mentions them
Then run shop.py with keywords.txt. Model: --model, else $OLLAMA_MODEL, else qwen3.5:9b if installed, else the first one installed.
"""
import json, os, re, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import b_items, c_never_write, norm, stem_hit  # noqa: E402
from common import read_jd  # noqa: E402

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
SYSTEM = "You extract facts from a job posting. Use only words that appear in the posting. Never add anything."
KEYWORD_CAP = 30
QUESTIONS = {
    "title": "What is the job title? Reply with the title only, lowercase, nothing else.",
    "keywords":"give a bulleted list of the top 15-30 skills, tools, and responsibilities mentioned in the posting. One per line. Copy each as a short phrase of 1-4 words from the posting. no explanations.",
}

REQ_HEAD = re.compile(r"(required|minimum|basic|must[- ]have|what you(?:'ll)? (?:need|bring|have)|qualifications|requirements|you have|about you|"
                      r"what we(?:'|’)?re looking for|who you are|ideal candidate|your background|you bring|skills|experience|education|essential(?! functions)|seeking|need to see|certification|licen[sc]|registration)", re.I)
PREF_HEAD = re.compile(r"(preferred|nice[- ]to[- ]have|bonus|plus|desired|desirable|additional qualifications|stand out|sets? you apart)", re.I)


def sections(text):
    """Split lines into required / preferred buckets by the nearest heading above them."""
    req, pref, mode, bullets_seen = [], [], None, False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        is_head = len(s) < 70 and not s.startswith("- ") and (s.endswith(":") or s.istitle() or s.isupper() or len(s.split()) <= 6)
        if is_head:
            mode = "pref" if PREF_HEAD.search(s) else ("req" if REQ_HEAD.search(s) else None)
            bullets_seen = False
            continue
        if not mode:
            continue
        bucket = pref if mode == "pref" else req
        if s.startswith("- "):
            bucket.append(s[2:].strip())
            bullets_seen = True
        elif not bullets_seen and len(s) < 150:
            bucket.append(s)
        else:
            mode, bullets_seen = None, False
    return req, pref


QUAL_CUE = re.compile(r"\b(\d+\+? ?(?:years?|yrs)|degree|bachelor|master|mba|phd|certif\w*|proficien\w*|knowledge|familiar\w*|"
                      r"experience|ability to|skilled|proven|strong|background|understanding|expertise|fluen\w*)\b", re.I)
DUTY_START = re.compile(r"^(lead|manage|own|drive|build|run|coordinate|develop|support|serve|maintain|produce|deliver|track|"
                        r"raise|help|partner|facilitate|design|create|work|ensure|identify|analy[sz]e|collaborate|prepare|"
                        r"provide|oversee|define|execute|monitor|conduct|implement|establish|translate|act|plan|report)\b", re.I)


def content_class(items):
    """Classify a section by what its items say: mostly preferred cues -> 'pref'; mostly qualification
    cues with few duty-verb openers -> 'req'; anything else -> None (header unknown, content unclear)."""
    if len(items) < 2:
        return None
    n = len(items)
    pref = sum(bool(PREF_CUE.search(i)) for i in items) / n
    qual = sum(bool(QUAL_CUE.search(i)) for i in items) / n
    duty = sum(bool(DUTY_START.search(i)) for i in items) / n
    if pref >= 0.5:
        return "pref"
    if qual >= 0.6 and duty <= 0.3:
        return "req"
    return None


MAX_HEAD = 100
REQ_STRONG = re.compile(r"(qualifications|requirements|required|minimum|must[- ]have|what you(?:'ll| will)? (?:need|bring|have)|you(?:'ll| will) (?:need|bring|have)|you have|about you)", re.I)
COOKIE_HEAD = re.compile(r"\bcookies?\b|cookie consent|privacy (?:policy|notice|preferences)", re.I)
LOGISTICS_HEAD = re.compile(r"^\W*(?:travel|(?:mental|physical)(?:[ /&]+(?:mental|physical))?|drug screen\w*|shift|duration|schedule|job function|job family|posting date|application (?:window|deadline))\b", re.I)
ESSENTIAL_DUTIES = re.compile(r"essential (?:functions|duties)", re.I)  # "Essential Functions Required for Job" lists duties, not qualifications
NOT_HEAD = re.compile(r"(responsibilit|essential functions|duties|additional information|physical demands|working conditions|work environment|what you(?:'ll| will) (?:do|work on|be doing|contribute)|you will|day[- ]to[- ]day|"
                      r"benefits|compensation|salary|pay (?:range|transparency)|perks|about (?:us|the (?:company|team|role))|"
                      r"who we are|our (?:mission|culture|values|story|team|offer)|why (?:this|join|work|you|make)|"
                      r"equal opportunity|culture|location|shift|how we|apply|overview|introduction|the (?:role|opportunity)|"
                      r"measures? of success|(?:what )?success (?:looks like|profile)|first (?:30|60|90)|key outcomes)", re.I)

JUDGE_Q = ("Which best describes this section of a job posting? Reply with one word: REQUIRED (qualifications the candidate "
           "must have), PREFERRED (nice-to-have qualifications), DUTIES (what the person will do), or OTHER (company "
           "information, benefits, pay, culture, anything else).")


def judge_section(model, header, items):
    """One short model call about one section: returns 'req', 'pref' or 'other'."""
    sample = "\n".join(f"- {i[:160]}" for i in items[:4])
    answer = ask_field(model, JUDGE_Q, f"Header: {header}\n{sample}").strip().upper()
    for word, cls in (("REQUIRED", "req"), ("PREFERRED", "pref"), ("DUTIES", "other"), ("OTHER", "other")):
        if answer.startswith(word) or word in answer.split()[:3]:
            return cls
    return None


def has_mixed_section(text, min_items=12):
    """True when a big section with an unknown header reads like qualifications mixed in with other text
    (e.g. one long 'Job Description'). The headings can't split that, so another sectioner should."""
    cur, found, secs = None, False, []
    for line in text.splitlines():
        t = line.strip()
        if t.startswith("## ") and len(t) - 3 <= MAX_HEAD:
            cur = {"head": t[3:].strip(), "items": []}
            secs.append(cur)
        elif cur and SUBLABEL.match(t):  # bare Required/Preferred lines label what follows: not a mixed section
            cur = None
        elif cur and t:
            cur["items"].append(t[2:].strip() if t.startswith("- ") else t)
    for sec in secs:
        h = sec["head"]
        if PREF_HEAD.search(h) or REQ_HEAD.search(h) or NOT_HEAD.search(h) or len(sec["items"]) < min_items:
            continue
        found = found or sum(bool(QUAL_CUE.search(i)) for i in sec["items"]) / len(sec["items"]) >= 0.3
    return found


STRONG_PREF = re.compile(r"\b(preferred|preference for|encouraged|desirable|desired|nice[- ]to[- ]have|bonus)\b", re.I)
SUBLABEL = re.compile(r"^(required|preferred|minimum|desired|desirable|nice[- ]to[- ]have)(?: (?:qualifications|skills|experience|requirements))?\s*:?$", re.I)


REQ_MOVE = re.compile(r"(\b(?:is|are)? ?required\W*$|^\W*(?:must|minimum)\b|\bmust (?:have|be)\b)", re.I)


def cue_moves(line, cue):
    """A cue decides a whole line when it is the point of the line: a short line, the cue among the first
    five words, or the cue in the last three words. In a long line a mid-sentence 'preferred' usually
    qualifies one clause ('...; administration experience strongly preferred')."""
    m = cue.search(line)
    if not m:
        return False
    words_before = len(line[:m.start()].split())
    total = len(line.split())
    return total <= 16 or words_before < 5 or total - words_before <= 3


def weak_moves(line):
    """A weak cue ('a plus', 'ideally') moves a short line, or any line that ends on it ('...CAPM certification is a plus')."""
    m = PREF_CUE.search(line)
    if not m:
        return False
    total = len(line.split())
    return total <= 10 or total - len(line[:m.start()].split()) <= 3


def split_mixed_clauses(items):
    """Split a ';' line whose clauses disagree (one carries a strong 'preferred' cue, another does not) into
    separate items. Returns (items that stay, clauses that are preferred)."""
    keep, pref = [], []
    for item in items:
        clauses = [c.strip(" .") for c in re.split(r";|(?<=\.)\s+", item) if c.strip(" .")]
        flags = [bool(STRONG_PREF.search(c)) and not REQ_CUE.search(c) for c in clauses]
        if len(clauses) > 1 and any(flags) and not all(flags):
            keep += [c for c, f in zip(clauses, flags) if not f]
            pref += [c for c, f in zip(clauses, flags) if f]
        else:
            keep.append(item)
    return keep, pref


def apply_cues(req, pref):
    """An item that says 'preferred' belongs in preferred, and one that says 'required' or 'must' in
    required, whichever section it sat in."""
    # 'Advanced SQL required; proficiency in Python strongly preferred' is two requirements in one bullet
    req, split_pref = split_mixed_clauses(req)
    pref = split_pref + pref
    # strong cues ("preferred") move a line wherever they sit; weak ones ("ideally", "a plus") often qualify
    # just one clause of a longer requirement, so they only move short lines
    to_pref =[r for r in req if not REQ_CUE.search(r) and (cue_moves(r, STRONG_PREF) or weak_moves(r))]
    to_req = [p for p in pref if REQ_MOVE.search(p) and not PREF_CUE.search(p)]
    return [r for r in req if r not in to_pref] + to_req, to_pref + [p for p in pref if p not in to_req]


STRICT_QUAL = re.compile(r"\b(\d+\+? ?(?:years?|yrs)|degree|bachelor|master|certif\w*|proficien\w*|experience (?:with|in|of))\b", re.I)


INTRO_SENT = re.compile(r"^\W*(?:we are|we're|we have|we seek|we want|in this role|this role|you will|you'll|our |as (?:a|an|the) )", re.I)


def section_items(sec):
    """A section's bullets, plus plain lines that state a qualification (a bulleted list often has a lead-in or a
    stray sentence beside it). Plain text with no bullets is the list itself; a long paragraph is split into
    sentences, and only the sentences that name a qualification are kept."""
    if (not sec["bullets"] and len(sec["plain"]) >= 5
            and all(len(p.split()) <= 4 and not re.search(r"[.;!?]", p) for p in sec["plain"])):
        return []  # a skills tag cloud ("Budgeting / Forecasting / Risk Management ...")
    plain = []
    for p in sec["plain"]:
        if is_keyword_dump(p):
            continue
        if len(p) < 250:
            if not INTRO_SENT.match(p) or (re.match(r"\W*you(?:'ll| will)\b", p, re.I) and STRICT_QUAL.search(p)):  # "You'll need 5+ years..." is a requirement
                plain.append(p)
        else:
            plain += [s.strip() for s in re.split(r"(?<=[.!?])\s+", p)
                      if STRICT_QUAL.search(s) and len(s) < 400 and not INTRO_SENT.match(s)]
    if sec["bullets"]:
        return sec["bullets"] + [p for p in plain if (STRICT_QUAL.search(p) or is_statement(p)) and p not in sec["bullets"]]
    return plain


LABEL_FILLER = re.compile(r"\b(?:and|or|the|a|to|of|you|your|we|are|is|what|who|will|ll|re|s|for|in)\b", re.I)
STATEMENT_END = re.compile(r"\b(?:required|preferred|desired|plus|bonus|ideal|strongly|is|are)\W*$", re.I)


def is_statement(s):
    """'MBA preferred' / 'Bachelor's degree required' state a requirement; 'Preferred Qualifications' only labels one."""
    rest = " ".join(w for w in re.sub(r"[^A-Za-z0-9+ ]", " ", PREF_HEAD.sub(" ", REQ_STRONG.sub(" ", REQ_HEAD.sub(" ", s)))).split()
                    if not LABEL_FILLER.fullmatch(w))
    if not rest:
        return False
    return bool(re.search(r"\d", s) or STATEMENT_END.search(s))


def classify_head_detail(head):
    """(cls, how, match) -- classify_head()'s exact priority chain, but also returns the
    match object that decided it, so a caller can judge how much of the line that match
    actually covers (trustworthy_header() below)."""
    for pat, cls, how in ((COOKIE_HEAD, "other", "header"), (LOGISTICS_HEAD, "other", "logistics"),
                          (PREF_HEAD, "pref", "header"), (ESSENTIAL_DUTIES, "other", "header"), (REQ_STRONG, "req", "header"),
                          (NOT_HEAD, "other", "header"), (REQ_HEAD, "req", "header")):
        m = pat.search(head)
        if m:
            return cls, how, m
    return None, None, None


def classify_head(head):
    """('req' | 'pref' | 'other' | None, 'header' | None) from a heading's own words."""
    return classify_head_detail(head)[:2]


def trustworthy_header(head):
    """classify_head(), but a 'req' or 'pref' call is trusted only when the match that
    earned it covers at least half the line's characters. A bare generic word embedded in
    an unrelated multi-word phrase ("Basic Life & Supplemental Life" via REQ_HEAD's "basic";
    "Annual Bonus & Profit Sharing" via PREF_HEAD's "bonus") almost always names a benefit,
    not a header -- its match covers a small fraction of the line. A compound header PHRASE
    that spans most or all of the line ("who you are", "what you'll bring", "you bring",
    "what sets you apart") is trusted regardless of length -- the match basically IS the
    line. 'other' classifications (NOT_HEAD/LOGISTICS_HEAD/COOKIE_HEAD) are never guarded;
    they're already the safe direction to fall into.

    Replaces an earlier word-count cap (<=2 words), which broke
    "Who You Are"/"Ideal Candidate"/"Your Background"/"You Bring" -- real, common header
    phrases that are 3 words long but match their pattern end to end. Match-coverage
    fraction distinguishes "the whole line IS the header phrase" from "a bare word happens
    to appear in an unrelated line" without an exception list for every known-good phrase.
    Applies wherever a plain line is being judged as a possible header, colon-terminated or
    not -- a trailing colon is some evidence a line is a label, but "Basic Life Insurance:"
    is still a benefit name with a colon, not a header, so the colon branch needs this guard
    too, not just the no-colon one."""
    cls, how, m = classify_head_detail(head)
    if cls in ("req", "pref") and m and (m.end() - m.start()) < 0.5 * max(len(head.strip()), 1):
        return None, None
    return cls, how


TITLE_CASE = re.compile(r"^(?:[A-Z][\w&'-]*|of|and|the|to|in|for|a|&)(?: (?:[A-Z][\w&'-]*|of|and|the|to|in|for|a|&))*:?$")


BULLET = re.compile(r"^[-*•]\s+")  # marker_sections() itself only ever
# checked "- " -- promote_plain_headings() already handles "*" and the bullet character too (its own
# startswith check, below). Generalized here so a "*"- or bullet-character-marked posting gets the
# same treatment a "-"-marked one already does.


def is_bullet(s):
    return bool(BULLET.match(s))


def strip_bullet(s):
    return BULLET.sub("", s, count=1).strip()


def join_split_bullets(text):
    """A bullet marker alone on its own line, with its text on the NEXT line (some Workday
    postings format benefit lists this way: '-' then 'Medical, Rx, Dental & Vision
    Insurance' on the following line), is joined back into one real '- text' bullet.

    This is a pre-pass, run before any header
    detection, not a patch to what counts as a header. Without it, the orphaned text line
    ('Basic Life & Supplemental Life') reaches the plain-line header-candidate branches
    looking exactly like a short, punctuation-free label -- and REQ_HEAD's bare-word match
    on "basic" promotes it to a fake requirements header, dragging every benefit under it
    into `required`. Once it's real bullet content (starts with "- "), none of those
    branches ever look at it; it's just the next item under whatever section already
    contains it. Cheaper and more durable than tightening REQ_HEAD's word list, which
    only trades one substring collision for the next one."""
    lines_ = text.splitlines()
    out, i = [], 0
    while i < len(lines_):
        s = lines_[i].strip()
        nxt_raw = lines_[i + 1] if i + 1 < len(lines_) else ""
        if s in ("-", "*", "•") and nxt_raw.strip() and not nxt_raw.strip().startswith(("-", "*", "•")):
            out.append(f"{s} {nxt_raw.strip()}")
            i += 2
            continue
        out.append(lines_[i])
        i += 1
    return "\n".join(out)


def promote_plain_headings(text):
    """A posting pasted as plain text has no '## ' lines. Short label lines that name a list ("Preferred
    Qualifications", "Experience") become headings so the same heading rules apply."""
    lines_ = text.splitlines()
    if any(l.strip().startswith("## ") for l in lines_):
        return text
    out = []
    for i, l in enumerate(lines_):
        s = l.strip()
        nxt = next((x for x in lines_[i + 1:] if x.strip()), "")
        prev_blank = i == 0 or not lines_[i - 1].strip()
        if (s and prev_blank and nxt and 1 <= len(s.split()) <= 6 and not re.search(r"[.,;!?]", s)
                and not s.startswith(("-", "*", "•"))
                and not is_statement(s)
                and (classify_head(s.rstrip(":"))[0] or (len(s.split()) >= 2 and TITLE_CASE.match(s)))):
            out.append("## " + s.rstrip(":"))
        else:
            out.append(l)
    return "\n".join(out)


def marker_sections(text, use_content=True, judge=None):
    """Sections from the '## Header' lines fetch_jd.py writes from the page's own headings.
    A header that names a list decides it. A header that names duties, benefits or company info marks the
    section 'other'. Otherwise the items decide (content_class), and judge(header, items), if given, has
    the last word on those sections. Returns (required, preferred, report of (header, class, items, how))."""
    req, pref, report, cur = [], [], [], None
    all_lines = [l.strip() for l in promote_plain_headings(join_split_bullets(text)).splitlines()]
    for idx, s in enumerate(all_lines):
        nxt = next((x for x in all_lines[idx + 1:] if x), "")
        if s.startswith("## "):
            head = s[3:].strip()
            if len(head) > MAX_HEAD:  # a whole sentence styled as a heading: its words say nothing about the list
                cls, how = None, None
                head = head[:MAX_HEAD].rstrip() + "..."
            else:
                cls, how = classify_head(head)
                # "EXPERIENCE:" under "PREFERRED QUALIFICATIONS:" is still the preferred list
                if (cls == "req" and cur and cur["class"] == "pref" and not REQ_STRONG.search(head)
                        and not re.search(r"minimum|required|basic", head, re.I)):
                    cls = "pref"
            cur = {"header": head, "class": cls, "how": how, "bullets": [], "plain": []}
            report.append(cur)
        elif cur and SUBLABEL.match(s):  # a bare "Required" / "Preferred" line inside a section starts a sub-section
            label = s.strip(" :").lower()
            cls = "req" if label.startswith(("required", "minimum")) else "pref"
            cur = {"header": cur["header"].split(" / ")[0] + " / " + label, "class": cls, "how": "header", "bullets": [], "plain": []}
            report.append(cur)
        elif (cur and s and not is_bullet(s) and nxt and not nxt.startswith("## ") and 1 <= len(s.split()) <= 5
              and not re.search(r"[.,;:!?]", s) and trustworthy_header(s)[0] in ("req", "pref", "other")
              and not is_statement(s)):
            # a plain short label with no colon ("What Sets You Apart", "Preferred Qualifications", or an
            # "other" label like "Benefits"/"Equal Opportunity Employer") opens a sub-section. The colon-branch
            # below already opens "other" sub-sections ("What You'll Bring to Acme:"); this no-colon branch only
            # accepted req/pref, so a bare
            # "Benefits" label with no colon left every line under it in the PARENT section's class, and
            # every benefit bullet leaked into required/preferred. Mirrors the colon-branch's own behavior now.
            # trustworthy_header(), not classify_head() directly: a 'req'/'pref'
            # call resting only on a bare generic word needs the match to cover most of the line, not just
            # appear in it ("Basic Life & Supplemental Life", "Annual Bonus & Profit Sharing").
            cls, how = trustworthy_header(s)
            cur = {"header": cur["header"].split(" / ")[0] + " / " + s, "class": cls, "how": how, "bullets": [], "plain": []}
            report.append(cur)
        elif cur and s.endswith(":") and not is_bullet(s) and len(s) <= 60 and trustworthy_header(s.rstrip(":"))[0]:
            # a plain "What You'll Bring to Acme:" / "Nice to Have:" line inside a section opens a sub-section.
            # trustworthy_header(), not classify_head(): a trailing colon is some
            # evidence a line is a label, but "Basic Life Insurance:" is still a benefit name with a colon,
            # not a header -- the same match-coverage guard applies here too, not just the no-colon branch.
            cls, how = trustworthy_header(s.rstrip(":"))
            cur = {"header": cur["header"].split(" / ")[0] + " / " + s.rstrip(": "), "class": cls, "how": how, "bullets": [], "plain": []}
            report.append(cur)
        elif (cur and s and is_bullet(nxt) and len(s) <= 150
              and re.search(r"(?:…|\.\.\.|:)\s*$", s)):
            # An unambiguous lead-in sentence right before a bullet list ("...you will have…",
            # "Responsibilities include:") still opens a sub-section even when classify_head()
            # doesn't recognize its words -- the colon-branch above already tried and failed
            # (elif chain), and an ellipsis-ending sentence never matches it at all. Real quals bullets after
            # "In this exciting career opportunity, you will have…" had no recognized header at all, so they silently
            # stayed in the PRECEDING section (duties) and vanished. Split them off here with
            # class=None/how=None and let the existing content-judged fallback below decide
            # req/pref/other from what the bullets actually say -- the same fallback every
            # unclassified '## ' heading already gets, just reached from a plain lead-in too.
            # trustworthy_header(), not classify_head(): this branch's
            # own condition (colon/ellipsis ending) overlaps the colon-branch's above -- a line the
            # colon-branch guard just blocked ("Basic Life Insurance:") would otherwise fall through
            # to HERE and get re-classified with the unguarded classify_head() the moment it's
            # followed by a real bullet, silently undoing the guard one elif branch down.
            head_text = re.sub(r"(?:…|\.\.\.|:)+\s*$", "", s).strip()
            cls, how = trustworthy_header(head_text)
            cur = {"header": cur["header"].split(" / ")[0] + " / " + head_text, "class": cls, "how": how,
                   "bullets": [], "plain": []}
            report.append(cur)
        elif cur and s:
            (cur["bullets"] if is_bullet(s) else cur["plain"]).append(strip_bullet(s) if is_bullet(s) else s)
    for sec in report:
        items = section_items(sec)
        sec["items"] = len(items)
        if not sec["how"] and len(items) >= 2:
            guess = content_class(items) if use_content else None
            if judge:
                verdict = judge(sec["header"], items)
                if verdict:
                    sec["class"], sec["how"] = verdict, "model"
                elif guess:
                    sec["class"], sec["how"] = guess, "content"
            elif guess:
                sec["class"], sec["how"] = guess, "content"
        elif not sec["how"] and len(items) == 1:  # one line, e.g. "PMP certification is strongly preferred"
            if STRONG_PREF.search(items[0]):
                sec["class"], sec["how"] = "pref", "cue"
            elif REQ_CUE.search(items[0]):
                sec["class"], sec["how"] = "req", "cue"
            elif judge:  # no cue and no header words: ask rather than drop it silently
                verdict = judge(sec["header"], items)
                if verdict:
                    sec["class"], sec["how"] = verdict, "model"
        if sec["class"] == "req" and len(items) == 1 and STRONG_PREF.search(items[0]) and not REQ_CUE.search(items[0]):
            sec["class"], sec["how"] = "pref", "cue"  # "Licensure and Certification: PMP is strongly preferred"
        # "Travel Requirements: Must reside in the listed state" still states a requirement; physical/mental demands and
        # drug-screen headings do not
        if sec["how"] == "logistics" and not re.match(r"\W*(?:mental|physical|drug)", sec["header"], re.I):
            req.extend(i for i in items if re.match(r"\W*must\b", i, re.I))
        if sec["class"] in ("req", "pref"):
            (pref if sec["class"] == "pref" else req).extend(items)
        sec["sample"] = items[:2]
    return req, pref, [(s["header"], s["class"], s["items"], s["how"]) for s in report]


def short_label(line):
    """'5+ years of experience in sales operations or analytics' -> '5+ years sales operations'."""
    yrs = re.search(r"(\d+\+?)\s*(?:\+\s*)?years?", line, re.I)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z+/&.'’-]*", line)
             if w.lower() not in {"experience", "years", "year", "of", "in", "with", "a", "an", "the", "and", "or",
                                  "to", "for", "strong", "proven", "demonstrated", "ability", "including", "related",
                                  "field", "equivalent", "minimum", "at", "least", "plus", "excellent", "skills"}]
    label = " ".join(words[:4]).lower()
    return (f"{yrs.group(1)} years {label}" if yrs else label).strip()


GENERIC = {"hiring", "execution", "operations", "alignment", "scale", "growth", "team", "experience", "business",
           "management", "data", "leadership", "strategy", "impact", "results", "communication"}


def b_tags():
    tags = set()
    for it in b_items():
        tags |= {v.strip().lower() for v in it["tags"].split(",") if 2 < len(v.strip()) < 40}
    return tags - GENERIC


def vocab_hits(text):
    """B tags (stem match) and C gap terms (whole-word match) that appear in the posting, in posting order."""
    t = norm(text)
    hits = []
    for v in b_tags():
        w = norm(v)
        if " " in w:  # the words must sit next to each other ("rhythm of business", not scattered)
            stems = [re.escape(x if len(x) <= 4 else x[:6]) for x in w.split()]
            m = re.search(r"\b" + r"\w*\s+".join(stems), t)
            ok = bool(m)
        else:  # single words: a longer stem, so "experiment" doesn't match "experience"
            m = re.search(r"\b" + re.escape(w if len(w) <= 4 else w[:max(6, len(w) - 3)]), t)
            ok = bool(m)
        if ok:
            hits.append((m.start(), v))
    for g in c_never_write():
        m = re.search(r"(?<![a-z])" + re.escape(g.lower()) + r"(?![a-z])", t)
        if m:
            hits.append((m.start(), g.lower()))
    return [v for _, v in sorted(hits)]


TAGS = set()


def variants(label):
    """Honest match variants for ats_check.py (which matches plain substrings): the phrase,
    its 2-word runs, and a stem for single words ('forecasting' -> 'forecast')."""
    core = re.sub(r"^\d+\+? years ?", "", label).strip() or label
    if re.search(r"bachelor|degree|\bmba\b", core):
        level = next((w for w in ("bachelor", "master", "doctorate", "phd") if w in core), None)
        if re.search(r"\bmba\b", core):  # an MBA is a master's: match resumes that say either
            return list(dict.fromkeys([level or "master", "mba"]))
        return [level] if level else ["degree"]
    w = core.split()
    singles = [x for x in w if x in TAGS]
    if len(w) == 1:
        return [w[0][:-3] if len(w[0]) > 8 and w[0].endswith("ing") else w[0]]
    return list(dict.fromkeys([core] + [" ".join(w[i:i + 2]) for i in range(len(w) - 1)] + singles))


def ask_field(model, question, text):
    body = json.dumps({"model": model, "stream": False, "think": False,
                       "options": {"temperature": 0, "num_ctx": 16384},
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": f"{question}\n\nPOSTING:\n{text[:24000]}"}]}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["message"]["content"]


SECTION_Q = (
    "Read this section of a job posting. Return a bulleted list "
    "of its individual qualifications, copying each item word "
    "for word from the section.\n\n"

    "RULES:\n"
    "Extract only explicit candidate qualifications: skills, technologies, certifications, education, "
    "years of experience, and explicitly stated candidate capabilities.\n"
    "- Exclude job duties, activities the employee will perform, business outcomes, "
    "descriptions of successful performance, company values, and generic personality descriptions.\n"
    "- A statement is not a qualification just because it describes what a successful employee should do.\n"
    "- Personality or work-style descriptions (for example 'highly organized' or 'proactive') are not qualifications "
    "unless they name a specific skill or experience.\n"
    "- Statements about what the employee will do or be responsible for after hire are not qualifications.\n"
    "- Copy the complete qualification, preserving its original "
    "wording and any qualifying language.\n"
    "- Do not shorten, summarize, paraphrase, or combine "
    "separate qualifications.\n"
    "- Do not omit minor or secondary qualifications.\n"
    "- Skip company descriptions, culture statements, benefits, "
    "and responsibilities.\n"
    "- Do not infer qualifications from the job title or "
    "responsibilities.\n"
    "- Do not classify items as required or preferred.\n"
    "- Do not add explanations or commentary.\n\n"

    "Return only the bulleted list."
)
NUMBER_Q = (
    "Below are the numbered lines of a job posting.\n\n"
    "Find each section that lists candidate qualifications, "
    "requirements, experience, skills, or education.\n\n"
    "Skip responsibilities, benefits, company descriptions, "
    "and general statements about success or performance.\n\n"
    "IMPORTANT:\n"
    "- Section headers are short titles or labels, not sentences.\n"
    "- Do not treat descriptive sentences as section headers.\n"
    "- Headers such as 'What You Bring' and 'Nice to Have' "
    "are valid section headers.\n"
    "- A sentence beginning with 'Success means', 'You will', "
    "'In this role', or similar wording is not a header.\n"
    "- Include only lines belonging to actual qualification sections.\n\n"
    "For each section return:\n"
    "header | first line number | last line number\n\n"
    "Copy headers exactly. Use the original line numbers. "
    "Return no other text."
)


def numbered(text):
    return "\n".join(f"{i}: {l.strip()}" for i, l in enumerate(text.splitlines(), 1) if l.strip())


def parse_ranges(answer):
    out = []
    for raw in answer.splitlines():
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit() and not parts[0].endswith((".", "?", "!")):
            out.append((parts[0], int(parts[1]), int(parts[2])))
    return out


def clip_ranges(ranges):
    ordered = sorted(ranges, key=lambda r: r[1])
    out = []
    for i, (header, first, last) in enumerate(ordered):
        nxt = ordered[i + 1][1] if i + 1 < len(ordered) else None
        end = min(last, nxt - 1) if nxt is not None and nxt > first else last
        out.append((header, first, end))
    return out


def slice_lines(text, first, last):
    return "\n".join(l.strip() for l in text.splitlines()[first - 1:last] if l.strip())


REQ_CUE = re.compile(r"\b(must|required|mandatory|essential|minimum)\b", re.I)
PREF_CUE = re.compile(r"\b(preferred|preference for|encouraged|desirable|desired|a plus|ideally|advantageous|beneficial|nice[- ]to[- ]have|bonus)\b", re.I)


def restore_cue(item, body):
    """The model sometimes trims a sentence in a run-on paragraph and drops its closing cue
    ('Healthcare experience preferred.' -> 'Healthcare experience'). Return the posting's own sentence when it
    starts with the item and says more."""
    want = norm_item(item).rstrip(".;: ")
    for sent in re.split(r"(?<=[.!?])\s+", body):
        have = norm_item(sent)
        if len(want) > 8 and have.startswith(want) and len(have.rstrip(".;: ")) > len(want):
            return sent.strip()
    # the model may also have cut a fragment out of a clause that ends in the cue
    # ('Licensed Clinical Social Worker (LCSW), preferred; CPHQ preferred' -> 'licensed clinical social worker (lcsw)')
    for clause in re.split(r"(?<=[.;!?])\s+", body):
        if len(want) > 8 and want in norm_item(clause) and STRONG_PREF.search(clause) and not REQ_CUE.search(clause):
            return clause.strip(" ;")
    return item


def classify_reason(header, item):
    head_clause = item.split(";")[0]
    if ";" in item and PREF_CUE.search(item) and not PREF_CUE.search(head_clause):
        # '5+ years of SaaS; experience supporting a technical product is preferred': the cue belongs to the later clause
        return "REQ", "req_clause"
    if cue_moves(item, STRONG_PREF) or weak_moves(item):
        return "PREF", "pref_cue"
    if REQ_CUE.search(item):
        return "REQ", "req_cue"
    return ("PREF", "header") if PREF_HEAD.search(header) else ("REQ", "header")


def norm_item(s):
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("‘", "'")).strip().lower()


WHY = {"ranges": [], "sections": []}


def ask_sections(model, text, ranges):
    req, pref, seen = [], [], set()
    for header, first, last in ranges:
        body = slice_lines(text, first, last)
        if not body:
            continue
        raw = ask_field(model, SECTION_Q, body)
        records = []
        header_keys = {norm_item(h) for h, _, _ in ranges}
        for item in lines(raw):
            if is_none_answer(item) or norm_item(item) in header_keys:
                records.append({"item": item, "list": "-", "reason": "none_or_header", "duplicate": False})
                continue
            item = restore_cue(item, body)
            kind, reason = classify_reason(header, item)
            key = norm_item(item)
            dup = key in seen
            records.append({"item": item, "list": kind, "reason": reason, "duplicate": dup})
            if dup:
                continue
            seen.add(key)
            (pref if kind == "PREF" else req).append(item)
        WHY["sections"].append({"header": header, "first": first, "last": last, "raw": raw, "items": records})
    return "\n".join(req), "\n".join(pref)


DEFAULT_MODEL = "qwen3.5:9b"


def ask_model(text, model):
    if not model:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as r:
            tags = [m["name"] for m in json.load(r).get("models", [])]
        if not tags:
            raise RuntimeError("Ollama has no models pulled")
        model = DEFAULT_MODEL if DEFAULT_MODEL in tags else tags[0]
    # 1. The page's own headings ('## ' lines from fetch_jd.py): Python reads the sections, the model only
    #    rules on headers it can't place. Needs real requirements, else fall through to the section finder.
    m_req, m_pref, WHY["markers"] = marker_sections(text, judge=lambda h, items: judge_section(model, h, items))
    mixed = has_mixed_section(text)
    header_req = any(c == "req" and how == "header" and n >= 1 for _, c, n, how in WHY["markers"])
    # an inline "What You'll Bring:" line is the posting's own sub-heading: it settles what a vague section could not
    explicit = any(c == "req" and how == "header" and n >= 1 and " / " in h for h, c, n, how in WHY["markers"])
    if (len(m_req) >= MIN_MODEL_REQ or header_req) and (not mixed or explicit):
        m_req, m_pref = apply_cues(m_req, m_pref)
        WHY["ranges"] = {"raw": "(not asked: headings found in the posting)", "used": []}
        WHY["kind"] = "markers"
        required, preferred = "\n".join(m_req), "\n".join(m_pref)
    else:
        # 2. The model finds the sections itself, then reads each one.
        WHY["kind"] = "section finder" + (" (a heading section mixes qualifications with other text)" if mixed else "")
        raw_ranges = ask_field(model, NUMBER_Q, numbered(text))
        ranges = clip_ranges(parse_ranges(raw_ranges))
        WHY["ranges"] = {"raw": raw_ranges, "used": ranges}
        if ranges:
            required, preferred = ask_sections(model, text, ranges)
        else:
            required, preferred = "\n".join(lines(ask_field(model, SECTION_Q, text))), ""
    answers = {"title": ask_field(model, QUESTIONS["title"], text),
               "keywords": ask_field(model, QUESTIONS["keywords"], text),
               "required": required, "preferred": preferred}
    return model, answers


def lines(answer):
    return [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", l).strip() for l in answer.splitlines() if l.strip()]


def blank_symbols(s):
    return re.sub(r"[$+&/%–—-]", " ", s)


NONE_ANSWER = re.compile(r"^(none|n/?a|no (?:qualifications|requirements|items)\b.*|none (?:provided|listed|found)\b.*)$", re.I)


def is_none_answer(item):
    return bool(NONE_ANSWER.match(item.strip().rstrip(".")))


def grounded(items, text):
    """Keep only phrases that really appear in the posting (by shop.py's matching)."""
    t = norm(blank_symbols(text))
    out = []
    for p in items or []:
        p = str(p).strip().lower()
        if 1 < len(p) < 800 and stem_hit(blank_symbols(p), t) and p not in out:
            out.append(p)
    return out


def order_by_text(phrases, text):
    t = norm(text)
    def pos(p):
        w = norm(p).split()
        m = re.search(r"\b" + re.escape(w[0][:6]), t) if w else None
        return m.start() if m else 10**9
    return sorted(dict.fromkeys(phrases), key=pos)


TRAIT_HINT = re.compile(r"\b(highly organized|willingness|works? effectively|brings|contributes?|support the|proactive|comfortable|"
                        r"self[- ]starter|self[- ]directed|passion\w*|team player|detail[- ]oriented|results[- ]oriented|thrives?|"
                        r"motivated|curious|collaborative|work ethic|attention to detail|mindset|forward[- ]thinking|"
                        r"problem[- ]solving)\b", re.I)
DUTY_HINT = re.compile(r"^(?:(?:lead|manage|own|drive|build|run|coordinate|develop|support|serve|maintain|produce|deliver|track|raise|"
                       r"help|partner|facilitate|design|create|ensure|identify|oversee|define|execute|monitor|conduct|implement|"
                       r"collaborate|represent|strengthen|stay|promote|translate|analy[sz]e|prepare|provide)\b|"
                       r"[A-Za-z]{2,}ing\b|"  # a gerund opener: 'Owning end-to-end execution of...' (the judge keeps 'Working knowledge of...')
                       r"(?:you will|you['’]ll)\b)|"
                       r"\b(?:responsible|accountable) for\b|\btasked with\b", re.I)

ITEM_Q = ("Which best describes this line from a job posting's requirements? Reply with one word: QUALIFICATION (a skill, "
          "experience, education, credential, tool or knowledge the candidate must already have), TRAIT (a personality or "
          "work-style description such as being proactive or detail-oriented), or DUTY (a task the person will perform on "
          "the job). A named skill or ability (communication, problem solving, leadership, a tool) is a QUALIFICATION "
          "even when it is phrased as 'strong ... skills'; a line that mixes a skill with a personal attitude is a "
          "QUALIFICATION too.")


def judge_item(model, item):
    """One short model call about one flagged line: 'qual', 'trait' or 'duty' (None if unclear)."""
    answer = ask_field(model, ITEM_Q, f"Line: {item[:300]}").strip().upper()
    for word, cls in (("QUALIFICATION", "qual"), ("TRAIT", "trait"), ("DUTY", "duty")):
        if answer.startswith(word) or word in answer.split()[:3]:
            return cls
    return None


BARE_LABEL = re.compile(r"^\W*(or|and|and/or|basic qualifications|minimum qualifications|required qualifications|"
                        r"preferred qualifications|qualifications|requirements|regular|full[- ]time|part[- ]time|exempt|non-exempt|"
                        r"my dashboard|my submissions|all jobs|this service is set)\W*$", re.I)
BOILERPLATE = re.compile(r"third-party recordings|represent your own experiences|prepared to provide accurate information|"
                         r"fair chance|criminal convictions|only job-related convictions|"
                         r"applications on an ongoing basis|pay range will vary|is a distributed company|"
                         r"relocation assistance is not available|"
                         r"lie detector|polygraph|piqued your curiosity|sound like the role for you|"
                         r"day care|company store|"
                         r"base salary range|(?:salary|pay|compensation) range|base pay|target (?:total )?cash|"
                         r"\d{2,3},\d{3}\s*[-–]\s*\$?\d{2,3},\d{3}|usd\s*/\s*(?:annual|hour)|pay to be determined|"
                         r"bend, squat|crouch|lift up to \d+|adverse weather|night and weekend work|driving for long periods|"
                         r"^\W*(?:pursuant to\b|confidential data\b|#li-|normal working hours)", re.I)
WORK_AUTH = re.compile(r"\b(?:(?:authori[sz]ed|authori[sz]ation|eligible|legally (?:able|permitted)|permitted) (?:to )?work|work authori[sz]ation|eligibility to work|(?:eligible|authori[sz]ed) for employment)\b", re.I)
WORK_AUTH_SAYS = re.compile(r"\b(?:must|requires?|required|need|only|without|cannot|unable|will not|not (?:eligible|able|provide|offer))\b|\bno\b[^.]*sponsor", re.I)


def is_keyword_dump(line):
    """Five or more short comma-separated tags with no verb phrase ('Analytics, Business Analysis, Data Analytics, ...'),
    trailing period or not. Only plain lines are checked: the same words in a bullet are a requirement."""
    s = line.strip().rstrip(".;")
    if re.match(r"(experience|proficien|knowledge|familiar|expertise|hands-on|working knowledge|understanding|ability)", s, re.I):
        return False
    parts = [p for p in s.split(",") if p.strip()]
    return len(parts) >= 5 and all(len(p.split()) <= 3 for p in parts)


NOT_A_REQUIREMENT = re.compile(r"^\W*(?:[$€£]\s?[\d,.]+|\d+%\s+(?:estimated\s+)?travel\b)", re.I)  # a bare salary figure or travel percentage

# BOILERPLATE had no benefits or EEO phrases at all, so
# unheaded lines like "Health savings account, dental/vision & dependent care flexible spending accounts"
# or "Example Co is an Equal Opportunity Employer" survived clean_required verbatim. Same bug class as benefit
# lines with no heading leaking in -- this is the line-level lexicon that
# was the implied fix then and never got built.
BENEFIT_EEO = re.compile(
    r"\b(?:health insurance|medical,? dental,?(?: and|,? &) vision|dental and vision|vision insurance|"
    r"health savings account|flexible spending accounts?|401\(k\)|retirement (?:plan|savings)|"
    r"paid time off|\bpto\b|paid vacation|parental leave|sick leave|short-term (?:&|and) long-term disability|"
    r"short-term disability|long-term disability|student loan (?:paydown|repayment|assistance)|"
    r"tuition reimbursement|tuition assistance|life insurance|employee assistance program|"
    r"employee recognition program|equal opportunity employer|equal employment opportunity|"
    r"reasonable accommodations?|affirmative action|protected veteran status|without regard to race|"
    r"all qualified applicants will receive consideration)\b", re.I)
# A requirement can legitimately name a benefit as a skill ("Experience with 401(k) plan administration" is
# a real HR-role requirement) -- the same guard is_keyword_dump already uses for its own false-positive risk.
REQ_FRAMING = re.compile(r"^(experience|proficien|knowledge|familiar|expertise|hands-on|working knowledge|"
                         r"understanding|ability)", re.I)


def is_junk(item):
    """Lines that are not a requirement: a bare label or connector ('OR'), a lead-in sentence ending in a colon,
    a benefits/EEO line with no requirement framing (keyword dumps are dropped earlier, from plain lines only)."""
    s = item.strip()
    if BARE_LABEL.match(s) or s.endswith(":") or NOT_A_REQUIREMENT.match(s) or BOILERPLATE.search(s):
        return True
    if BENEFIT_EEO.search(s) and not REQ_FRAMING.match(s):
        return True
    return False


def clean_required(required):
    return [r for r in required if not is_junk(r)]


def rescue_work_authorization(text, required, preferred):
    """'Candidates must be authorized to work in the US' is a requirement wherever the posting files it
    (often under 'Additional information', which the section reader treats as company text)."""
    have = " ".join(required + preferred).lower()
    out = list(required)
    for line in text.splitlines():
        for s in re.split(r"(?<=[.!?])\s+", line.strip().lstrip("-• ").strip()):
            if (WORK_AUTH.search(s) and WORK_AUTH_SAYS.search(s) and len(s) < 300 and s.lower() not in have
                    and not is_junk(s)):  # rescues run after clean_required, so they must check is_junk too
                out.append(s)
                have += " " + s.lower()
    return out


def rescue_must_lines(text, required, preferred):
    """A sentence that opens with 'Must' ('Must reside in the state of Florida.') states a requirement even when the
    posting puts it in a key-details line that no section covers."""
    have = " ".join(required + preferred).lower()
    out = list(required)
    for line in text.splitlines():
        body = line.split(":", 1)[1] if re.match(r"[A-Z][\w /&-]{2,30}:\s", line.strip()) else line
        for s in re.split(r"(?<=[.!?])\s+", body.strip().lstrip("-• ").strip()):
            if (re.match(r"must\b", s, re.I) and 3 < len(s.split()) < 40 and s.lower().rstrip(". ") not in have
                    and not is_junk(s)):  # rescues run after clean_required, so they must check is_junk too
                out.append(s.strip())
                have += " " + s.lower()
    return out


SPONSOR_NO = re.compile(r"sponsor", re.I)
SPONSOR_NEG = re.compile(r"\b(?:not|no|unable|cannot|without|unavailable)\b|n't", re.I)
TRAVEL_PCT = re.compile(r"travel", re.I)
PERCENT = re.compile(r"\d+\s?(?:-|to)?\s?\d*\s?%")
ONSITE = re.compile(r"\b(?:hybrid work|in the office|in-office|onsite|on-site|field[- ]based)\b", re.I)
ONSITE_WHERE = re.compile(r"\b(?:days?|week|located|based|schedule|market|locations?)\b", re.I)


def rescue_logistics(text, required, preferred):
    """Work-authorization statements, a travel percentage and an in-office schedule are conditions of the job a
    candidate must meet, wherever the posting puts them (often under 'Additional information' or a company note)."""
    have = " ".join(required + preferred).lower()
    out = list(required)
    for line in text.splitlines():
        for s in re.split(r"(?<=[.!?])\s+", line.strip().lstrip("-• ").strip()):
            if not 2 < len(s.split()) < 35 or s.lower().rstrip(". ") in have:
                continue
            hit = ((SPONSOR_NO.search(s) and SPONSOR_NEG.search(s))
                   or (TRAVEL_PCT.search(s) and PERCENT.search(s))
                   or (ONSITE.search(s) and ONSITE_WHERE.search(s)))
            if hit and not is_junk(s):  # is_junk covers BOILERPLATE plus the benefit/EEO check
                out.append(s.strip())
                have += " " + s.lower()
    return out


def screen_required(required, judge):
    """Ask about only the lines the cheap hints flag. A line judged a trait or duty leaves the required
    list. Returns (kept, dropped as [(item, label)])."""
    kept, dropped = [], []
    for item in required:
        if TRAIT_HINT.search(item) or DUTY_HINT.search(item):
            label = judge(item)
            if label in ("trait", "duty"):
                dropped.append((item, label))
                continue
        kept.append(item)
    return kept, dropped


MIN_MODEL_REQ = 3


def words(s):
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]{3,}", s.lower())}


def covered_by(item, others):
    w = words(item)
    return bool(w) and any(w <= words(o) for o in others)


def reconcile(model_req, model_pref, rules_req, rules_pref, sectioned, trusted=False):
    """Prefer the model's lists. Fall back to the rules lists when the model found no sections or too
    few requirements. Otherwise keep the model's lists and report where the rules disagree."""
    if not sectioned or (len(model_req) < MIN_MODEL_REQ and not trusted):
        reason = "no sections found" if not sectioned else f"only {len(model_req)} required items"
        req = model_req + [r for r in rules_req if not covered_by(r, model_req + model_pref)]
        pref = model_pref + [p for p in rules_pref if not covered_by(p, model_req + model_pref)]
        return req, pref, [], f"rules fallback ({reason})"
    conflicts = []
    for r in rules_req:
        if covered_by(r, model_req):
            continue
        kind = "rules says required, model says preferred" if covered_by(r, model_pref) else "rules only (required)"
        conflicts.append((kind, r))
    for p in rules_pref:
        if covered_by(p, model_pref):
            continue
        kind = "rules says preferred, model says required" if covered_by(p, model_req) else "rules only (preferred)"
        conflicts.append((kind, p))
    return model_req, model_pref, conflicts, "model"


def split_blob(text):
    """A posting that arrives as one long paragraph (some JSON-LD descriptions) has no lines to section.
    Break it at sentence ends so the section finder and rules have lines to work with."""
    body = [l for l in text.splitlines() if l.strip()]
    if len(body) > 4 or len(text) < 1500:
        return text
    return re.sub(r"(?<=[.!?])\s+(?=[A-Z0-9])", "\n", text)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    model = argv[argv.index("--model") + 1] if "--model" in argv else os.environ.get("OLLAMA_MODEL")
    if model in args:
        args.remove(model)
    TAGS.update(b_tags())
    head, text = read_jd(args[0])
    text = split_blob(text)
    # one sectioner for every path: the rules baseline (--no-llm, model failure, reconcile) reads the same
    # headings the model path does, and returns full lines rather than short labels
    req_lines, pref_lines, _ = marker_sections(text)
    if not (req_lines or pref_lines):
        req_lines, pref_lines = sections(text)
    required, preferred = apply_cues(req_lines, pref_lines)
    rescue_rules = lambda req, pref: rescue_logistics(
        text, rescue_must_lines(text, rescue_work_authorization(text, clean_required(req), pref), pref), pref)
    keywords = vocab_hits(text)
    title = (head.get("title") or "").lower()
    title_core = []
    used = "rules only"
    if "--no-llm" not in argv:
        try:
            try:
                used, d = ask_model(text, model)
            except (Exception, SystemExit):  # one transient hiccup should not cost the whole model pass
                time.sleep(2)
                WHY.update({"ranges": [], "sections": []})
                used, d = ask_model(text, model)
            Path("extract_warning.txt").unlink(missing_ok=True)
            title = title or d["title"].strip().lower()
            keywords += grounded(lines(d["keywords"]), text)
            model_req, model_pref = lines(d["required"]), lines(d["preferred"])
            sectioned = WHY.get("kind") == "markers" or bool(WHY["ranges"] and WHY["ranges"].get("used"))
            required, preferred, conflicts, WHY["source"] = reconcile(
                grounded(model_req, text), grounded(model_pref, text), required, preferred, sectioned,
                trusted=WHY.get("kind") == "markers")
            required = rescue_must_lines(text, rescue_work_authorization(text, clean_required(required), preferred), preferred)
            required = rescue_logistics(text, required, preferred)
            if "--no-screen" not in argv:
                required, WHY["screened"] = screen_required(required, lambda item: judge_item(used, item))
            Path("extract_conflicts.md").unlink(missing_ok=True)
            if conflicts:
                lines_out = [f"# Extractor conflicts: {args[0]}", f"Using: {WHY['source']}", ""]
                lines_out += [f"- {kind}: {item}" for kind, item in conflicts]
                Path("extract_conflicts.md").write_text("\n".join(lines_out) + "\n", encoding="utf-8", newline="\n")
                print(f"{len(conflicts)} item(s) where the rules extractor and the model disagree: extract_conflicts.md")
            WHY["ungrounded"] = [x for x in model_req + model_pref if x.strip().lower() not in grounded([x], text)]
        except Exception as e:
            print(f"WARNING: model pass failed twice ({e}); this posting is rules-only and lower quality: extract_warning.txt", file=sys.stderr)
            Path("extract_warning.txt").write_text(f"Model pass failed: {e}\nThe result in jd.json is rules-only.\n", encoding="utf-8", newline="\n")
            WHY["model_failed"] = str(e)
            used = "rules only"
    if used == "rules only":  # no model pass: give the rules baseline the same rescues the model path gets
        required = rescue_rules(required, preferred)
    if not title_core and title:
        core = re.sub(r"\b(senior|sr\.?|principal|lead|staff|head of|ii|iii|iv)\b", " ", title.split(",")[0].split(" - ")[0])
        title_core = [" ".join(core.replace(",", " ").split()[:3])] if core.strip() else [title]
    keywords = order_by_text([k for k in keywords if k not in required and k not in preferred], text)[:KEYWORD_CAP]
    jd = {"title": title, "title_core": title_core,
          "required": {r: variants(r) for r in dict.fromkeys(required)},
          "preferred": {p: variants(p) for p in dict.fromkeys(preferred) if p not in required},
          "keywords": {k: variants(k) for k in keywords}}
    Path("jd.json").write_text(json.dumps(jd, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    req_kw = [c for c in (re.sub(r"^\d+\+? years ?", "", r) for r in jd["required"]) if 0 < len(c.split()) <= 3]
    Path("keywords.txt").write_text("\n".join(dict.fromkeys(req_kw + keywords)) + "\n", encoding="utf-8", newline="\n")
    if "--why" in argv:
        used_ranges = WHY["ranges"].get("used", []) if WHY["ranges"] else []
        total = len([l for l in text.splitlines() if l.strip()])
        covered = sum(b - a + 1 for _, a, b in used_ranges)
        out = [f"# Why: {args[0]}", f"Model: {used}", f"Using: {WHY.get('source', 'rules only')}", f"Posting lines: {total}; lines inside sections: {covered}; sections: {len(used_ranges)}",
               f"Section source: {WHY.get('kind', 'rules only')}; fallback to whole posting: {not used_ranges and WHY.get('kind') != 'markers'}", "", "## Section finder", f"Raw answer:\n{WHY['ranges'].get('raw', '') if WHY['ranges'] else ''}", ""]
        if WHY.get("kind") == "markers":
            out += ["## Headings", "(header | list | items | decided by)"]
            out += [f"- {h} | {c} | {n} | {how}" for h, c, n, how in WHY["markers"]] + [""]
        for s in WHY["sections"]:
            out += [f"## {s['header']} (lines {s['first']}-{s['last']})", "Raw model answer:", s["raw"], ""]
            for i in s["items"]:
                note = " (duplicate, dropped)" if i["duplicate"] else ""
                out.append(f"- [{i['list']}] because {i['reason']}{note}: {i['item']}")
            out.append("")
        if WHY.get("screened"):
            out += ["## Left required by the trait/duty check"] + [f"- [{label}] {item}" for item, label in WHY["screened"]]
        if WHY.get("ungrounded"):
            out += ["## Dropped by grounding check"] + [f"- {x}" for x in WHY["ungrounded"]]
        Path("why.md").write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(f"jd.json + keywords.txt written ({used}): title '{title}', required {len(jd['required'])}, "
          f"preferred {len(jd['preferred'])}, keywords {len(keywords)}")
    print("Check jd.json's required list by eye: long qualification lines get shortened to their key words.")


if __name__ == "__main__":
    main(sys.argv[1:])
