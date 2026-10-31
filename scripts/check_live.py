"""check_live.py — is the posting still open? Rules only, no AI.

Idea from the public career-ops repo (career-ops-hq/career-ops, MIT); our own code.

Usage (from the repo root):
    python scripts/check_live.py jd.txt [more.txt ...]   check saved postings
    python scripts/check_live.py URL                      fetch, then check

Answers per posting:
    OPEN       nothing says it's closed
    CLOSED     a closed banner ("no longer accepting applications", "position has been filled"),
               or the site answered 404/410
    UNCERTAIN page too thin to judge, redirected to (or rendered) a search/listing page
               (some company posting URLs render "All Openings"), expected title missing, or the fetch failed
               another way. UNCERTAIN is a flag for a human look, never a skip.

Lesson from career-ops: sites write banners with curly quotes and accents ("isn’t", "n’est plus"),
so a pattern with a plain apostrophe silently never matches. Text is normalized first
(ASCII quotes, no accents, collapsed spaces, lowercase) and every pattern is written in that form.
screen_leads.py calls check() on every posting: CLOSED = FAIL, UNCERTAIN = FLAG.
"""
import re, sys, unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

QUOTES = str.maketrans({"‘": "'", "’": "'", "‛": "'", "′": "'", "´": "'", "`": "'",
                        "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})

CLOSED = [
    r"no longer (?:accepting|taking) (?:applications|applicants|candidates)",
    r"(?:job|position|posting|role|requisition|opening|vacancy) (?:is|has) (?:been )?(?:closed|filled|expired|removed)",
    r"(?:job|position|posting|role|opening) is no longer (?:available|open|active|posted)",
    r"(?:this|the) (?:job|position|posting|role|opening) (?:has )?(?:closed|expired)",
    r"(?:job|position|posting|page) (?:you(?:'re| are) looking for|you requested) (?:is no longer|no longer|does not|doesn't|could not|cannot|can't)",
    r"applications? (?:are|is) (?:now )?closed",
    r"this job (?:is )?no longer exists",
    r"sorry,? (?:this|that) (?:job|position|posting) (?:is|has)",
    r"the job (?:posting )?you are trying to (?:view|apply)",
    r"n'est plus disponible",          # French banners (kept from the career-ops lesson)
    r"ya no esta disponible",          # Spanish
]
# Boilerplate that mentions filling but means the posting is OPEN (one board's
# "accepted on an ongoing basis until the position is filled" tripped the closed banner).
# Removed from the text before the CLOSED patterns run.
NOT_CLOSED = [
    r"(?:until|till|once|when|before|after|as soon as|unless) (?:the |this |a )?(?:job|position|posting|role|requisition|opening|vacancy) (?:is|has been|have been|are) (?:closed|filled)",
    r"(?:until|till) (?:the |this )?(?:job|position|posting|role|opening) (?:is )?(?:closed|filled)",
]
# A listing/search page, not a single posting (redirects land here when a job is pulled)
LISTING = [r"\b\d{2,} (?:jobs|open (?:positions|roles)|results)\b", r"search (?:all )?jobs", r"filter by (?:location|team|department)"]
# a posting URL can render the company's "All Openings" page (JS app,
# same text for every job). The page title/top says so, and the posting's own title never appears.
LIST_TITLE = r"^.{0,80}?\b(?:all openings|all jobs|open positions|open roles|current openings|job openings|search (?:all )?jobs|job search|browse jobs|find (?:your next )?(?:job|role)s?|view all jobs|careers? (?:home|search))\b"
APPLY = r"\b(?:apply(?: now| for this job| here)?|submit (?:your )?application|easy apply)\b"


def norm(text):
    t = unicodedata.normalize("NFKD", (text or "").translate(QUOTES))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().lower()


def listing_page(t, title="", page_title=""):
    """Reason string if the normalized text t looks like a job-list page instead of one posting."""
    if re.search(LIST_TITLE, norm(page_title)) or re.search(LIST_TITLE, t[:200]):
        return "landed on the company's job list page, not the posting"
    want = norm(re.sub(r"\(.*?\)", "", title or ""))
    if want and len(want) > 6 and want not in t:
        words = [w for w in want.split() if len(w) > 3]
        if words and sum(w in t for w in words) / len(words) < 0.6:
            return f'posting title "{title[:60]}" isn\'t on the page (job list or another posting?)'
    return ""


def check(text, error="", final_url="", url="", title="", page_title=""):
    """Return (status, reason). error = the fetch error text, if any.
    title = the posting title we expect (lead/row); page_title = the fetched page's own title."""
    err = norm(error)
    if re.search(r"\b(?:http error )?(?:404|410)\b|not found|gone", err) and "not on board" not in err:
        return "CLOSED", "site answered 404/410 (page gone)"
    if err:
        return "UNCERTAIN", f"couldn't fetch: {error.strip()[:120]}"
    t = norm(text)
    for p in NOT_CLOSED:
        t = re.sub(p, " ", t)
    for p in CLOSED:
        m = re.search(p, t)
        if m:
            i = m.start()
            return "CLOSED", f'banner: "{t[max(0, i - 20):m.end() + 20].strip()}"'
    if final_url and url and final_url.rstrip("/") != url.rstrip("/"):
        if re.search(r"/(?:jobs|careers|search)/?(?:\?|$)", final_url):
            return "UNCERTAIN", f"redirected to a listing page ({final_url[:90]})"
    lp = listing_page(t, title, page_title)
    if lp:
        return "UNCERTAIN", lp
    words = len(t.split())
    if words < 150:
        return "UNCERTAIN", f"only {words} words: may be a pulled posting or a page that needs the browser"
    if sum(bool(re.search(p, t)) for p in LISTING) >= 2:
        return "UNCERTAIN", "reads like a job search/listing page, not one posting"
    return "OPEN", "no closed banner" + ("" if re.search(APPLY, t) else " (no apply button text seen; fine for saved text)")


def show(name, status, reason):
    icon = {"OPEN": "🟢", "CLOSED": "⛔", "UNCERTAIN": "❔"}[status]
    print(f"{icon} {status}  {name}: {reason}")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    from fetch_jd import fetch  # noqa: E402
    from common import read_jd  # noqa: E402
    from common import host_is  # noqa: E402
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    for a in args:
        if re.match(r"https?://", a):
            if host_is(a, "linkedin.com"):
                print(f"❔ UNCERTAIN  {a}: LinkedIn = browser only (SKILL.md); save 'About the job' and check the file")
                continue
            try:
                d = fetch(a)
                show(d.get("title") or a, *check(d["text"], url=a, page_title=d.get("title", "")))
            except SystemExit as e:
                show(a, *check("", error=str(e)))
        else:
            head, text = read_jd(a)
            show(head.get("title") or a, *check(text))
