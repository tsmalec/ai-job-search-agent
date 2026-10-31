"""gmail_sweep.py — the candidate's Gmail, from any machine: the application email sweep + referral drafts/sends.

Replaces the Claude-only Gmail connector and any agent's own Gmail CLI with one stdlib-only script, so the
sweep runs the same for any agent or a local model. Talks to the Gmail API directly (no MCP, no pip).

One-time setup (your machine; see LOCAL_SETUP.md "Gmail"):
    1. Google Cloud console: create a project, enable the Gmail API, create an OAuth client of type
       "Desktop app", download its JSON. Keep it OUTSIDE the repo (e.g. ~/.jobsearch/client_secret.json).
    2. python scripts/gmail_sweep.py auth --client ~/.jobsearch/client_secret.json
       Opens a Google sign-in in the browser; the token lands in ~/.jobsearch/gmail_token.json
       (override with GMAIL_TOKEN). Never commit either file.

Usage (from the repo root):
    python scripts/gmail_sweep.py sweep                      since last_gmail_sweep (else 3 days); report only
    python scripts/gmail_sweep.py sweep --hours 72           fixed window
    python scripts/gmail_sweep.py sweep --apply              also run the safe log.py lines (see below)
    python scripts/gmail_sweep.py sweep --json out.json      machine-readable results (for an agent / run_loop)
    python scripts/gmail_sweep.py sweep --fixture scripts/gmail_example.json   offline test, no Gmail
    python scripts/gmail_sweep.py draft --to a@b.com --subject "..." --html note.html [--attach resume.docx]
    python scripts/gmail_sweep.py send  --to a@b.com --subject "..." --html note.html [--attach ...] --yes

Sweep rules (data/README.md "Status changes"):
  - Reads the email BODY, never just the subject. Only a clear phrase counts; nothing is guessed.
  - Precedence: offer > rejection > interview > confirmation (rejections often say "thanks for applying").
  - Gmail phrase search is exact ("not moving forward" misses "will not be moving forward"), so every
    class searches many phrasings, and the body is re-checked here with regexes that cover the variants.
  - Each email is matched to an applications.json row by company (sender name, domain, subject, body).
    A row needs a unique match; two rows at one company → role words decide, else it's shown as ambiguous.
  - Proposed lines are printed for every hit. --apply runs only:
        confirmation  → log.py note <row> "Confirmation email ..." --confirmed true   (status untouched)
        rejection     → log.py set <row> rejected --at <email time> --system <ATS>
    ...and only when the sender is verified (company or ATS domain, DKIM pass); anything else is shown, not run.
    Interviews and offers are NEVER auto-applied: they're flagged at the top for the candidate, and
    `invite_reader.py` handles the invite. Archive-only matches are flagged, never logged.
    After a rejection, add the one-line "likely why" with `log.py note` (data/README.md).
  - A live sweep (not --fixture) ends with `log.py sweep-stamp` when --apply is on.

Sending (referral emails): `draft` is the default and safe. `send` needs --yes, and is only
run after the candidate approved that exact note in chat. Bodies go as HTML with explicit <a href> links (plain
text links got rewritten into google.com/url redirects). One recipient per call: never bulk.
"""
import argparse, base64, hashlib, html, json, os, re, secrets, subprocess, sys, threading, time, webbrowser
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import APPS, ARCHIVE, ROOT, load_json, norm  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo(LOCAL_TZ)
except Exception:  # no tz database: fixed EDT offset is close enough for a stamp
    ET = timezone(timedelta(hours=-4))

SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = Path(os.environ.get("GMAIL_TOKEN") or Path.home() / ".jobsearch" / "gmail_token.json")


def _write_secret(path, text):
    """Write a token file readable only by you (created 0600, never world-readable even briefly)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # best effort: a missing or locked file isn't fatal here
LOG = [sys.executable, str(ROOT / "scripts" / "log.py")]

# ---------------------------------------------------------------- phrases
# Gmail search phrases (exact-match, so list the variants). Body regexes below do the real deciding.
SEARCH = {
    "confirmation": ['"thank you for applying"', '"thanks for applying"', '"application received"',
                     '"received your application"', '"application has been received"',
                     '"application was successfully submitted"', '"successfully submitted"',
                     '"thank you for your application"', '"thank you for your interest"'],
    "rejection": ['"not moving forward"', '"not be moving forward"', '"not to move forward"',
                  '"move forward with other candidates"', '"moving forward with other candidates"',
                  '"pursue other candidates"', '"decided not to proceed"', '"not to proceed"',
                  '"other candidates whose"', '"not been selected"', '"not selected"',
                  '"regret to inform"', '"unable to offer"', '"position has been filled"',
                  '"no longer under consideration"', '"no longer considering"', '"application_rejected"',
                  '"will not be progressing"', '"not progressing"', '"decided to go in a different direction"'],
    "interview": ['"schedule an interview"', '"schedule a call"', '"schedule a time"', '"phone screen"',
                  '"your availability"', '"invite you to interview"', '"recruiter screen"',
                  '"next step in the process"', '"would like to speak with you"', '"calendly.com"',
                  '"goodtime.io"', '"interview invitation"'],
    "offer": ['"pleased to offer"', '"offer letter"', '"extend an offer"', '"offer of employment"'],
}
BODY = {
    "offer": r"pleased to (?:extend|offer)|extend (?:you )?an offer|(?<!not an )(?<!not a )(?<!potential )offer of employment|your offer letter",
    "rejection": (r"(?:not|n't) (?:be )?(?:moving|move|progress\w*) forward|not to move forward"
                  r"|(?:move|moving|proceed\w*) forward with (?:other|another) candidate"
                  r"|pursue other candidates|decided not to (?:proceed|pursue|move)|not to proceed with"
                  r"|other candidates whose|(?:have|has) not been selected|(?:was|were|is|are) not selected|not selected for (?:further )?consideration"
                  r"|(?:won'?t|will not|not) (?:be )?proceeding with your candidacy|going in a direction|regret to inform"
                  r"|unable to offer you|position has been filled|(?:have|has) filled the [^.]{0,80}?(?:role|position)|no longer (?:under consideration|considering)"
                  r"|application_rejected|will not be progressing|go(?:ing)? in a different direction"
                  r"|(?:moved|moving|move) in another direction|no longer (?:be )?moving forward"
                  r"|(?:not|n't) (?:ideally )?suited|(?:isn'?t|is not|wasn'?t|was not) (?:the )?(?:strongest|best|right|ideal) (?:match|fit)"
                  r"|(?:not|n't) a (?:match|fit) for (?:the|this) (?:role|position)"
                  r"|(?:move|moving|proceed\w*) (?:forward )?with (?:other|another) (?:candidate|applicant)s?"),
    "interview": (r"schedule (?:an? )?(?:interview|call|time|phone screen|conversation|chat)"
                  r"|(?:share|send|provide) (?:me |us )?your availability|invite you to (?:an? )?interview"
                  r"|recruiter screen|phone screen|would (?:like|love) to (?:speak|chat|connect) with you"
                  r"|calendly\.com|goodtime\.io|interview invitation"),
    "confirmation": (r"thank(?:s| you) for (?:applying|your application|your interest)"
                     r"|(?:received|receipt of) (?:your|the) (?:job )?application|application (?:has been |was )?(?:received|submitted)"),
}
ORDER = ["offer", "rejection", "interview", "confirmation"]
SYSTEMS = {"greenhouse": "Greenhouse", "lever.co": "Lever", "ashbyhq": "Ashby", "myworkday": "Workday",
           "workday": "Workday", "jobvite": "Jobvite", "icims": "iCIMS", "smartrecruiters": "SmartRecruiters",
           "successfactors": "SuccessFactors", "taleo": "Taleo", "bamboohr": "BambooHR", "linkedin": "LinkedIn",
           "workable": "Workable", "rippling": "Rippling", "paylocity": "Paylocity", "oraclecloud": "Oracle"}
ATS_WORDS = {"greenhouse", "lever", "ashby", "workday", "myworkday", "jobvite", "icims", "smartrecruiters",
             "linkedin", "workable", "talent", "careers", "recruiting", "jobs", "noreply", "no", "reply",
             "team", "hr", "people", "the", "inc", "llc", "corp", "corporation", "company", "group", "us", "com"}
LIVE_STATUS = {"applied", "interview", "offer", "resume_built", "rejected"}


HIRING = r"\b(?:application|applied|applying|applicant|candida\w+|position|role|interview\w*|recruit\w*|requisition|hiring)\b"
# Acknowledgment templates carry "not selected" boilerplate; they are not rejections.
BOILERPLATE_NOT_REJECTION = r"being evaluated|hear from us as soon as the review|how.?s your profile"


# Explicit decisions. A rejection that matches only the softer wording ("moved in another direction",
# "not ideally suited", "not a match for the role") is still shown, but never written to a row on its own.
STRONG_REJECTION = (r"not (?:be )?(?:moving|move|progress\w*) forward|not to move forward|not to proceed|decided not to"
                    r"|(?:move|moving|proceed\w*) forward with (?:other|another)|proceed with other|pursue other candidates"
                    r"|other candidates whose|not been selected|was not selected|were not selected|not selected for"
                    r"|(?:won'?t|will not) be (?:proceeding|progressing)|will not be progressing|regret to inform"
                    r"|position has been filled|(?:have|has) filled the|unable to offer you"
                    r"|no longer (?:under consideration|considering|be moving forward)|application_rejected|not progressing")


def assess_rejection(msg, row, sender_ok):
    """Every rejection updates the row. Each test is recorded, and a risk flag says how much to trust it:
      high  = sender not verified, company name not in the email, or the row was already at interview/offer
      med   = soft wording, or DKIM/DMARC not clean, or a requisition number that doesn't match the row
      low   = verified sender, company named, strong wording
    Returns (checks string, risk). Written to the row as rejection_checks and risk_flag."""
    auth = (msg.get("auth") or "").lower()
    subj, body = msg.get("subject") or "", (msg.get("body") or "")[:3000]
    sender = msg.get("from") or ""
    name = norm(f"{sender} {subj} {body}")
    keys = company_keys(row.get("company"))
    first = norm(re.sub(r"\(.*?\)", "", row.get("company") or "")).split()[:1]
    if first and len(first[0]) >= 3 and first[0] not in keys:
        keys = keys + first  # "Beta Systems" → "beta" also counts as a name in the email
    email_ids = req_ids(f"{subj} {body}")
    checks = {
        "sender": "pass" if sender_ok else "fail",
        "dkim": "none" if not auth else ("pass" if "dkim=pass" in auth else "fail"),
        "dmarc": "fail" if "dmarc=fail" in auth else ("pass" if "dmarc=pass" in auth else "none"),
        "name": "pass" if hits_company(keys, name) else "fail",
        "req": "none" if not email_ids else ("pass" if email_ids & req_ids(f"{row.get('role') or ''} {row.get('url') or ''}") else "fail"),
        "wording": "strong" if re.search(STRONG_REJECTION, f"{subj}\n{body}".lower()) else "soft",
    }
    high = checks["sender"] == "fail" or checks["name"] == "fail" or row.get("status") in ("interview", "offer")
    med = checks["wording"] == "soft" or checks["dkim"] == "fail" or checks["dmarc"] == "fail" or checks["req"] == "fail"
    risk = "high" if high else "med" if med else "low"
    return " ".join(f"{k}:{v}" for k, v in checks.items()), risk


def classify(text):
    t = (text or "").lower()
    if not re.search(HIRING, t):  # newsletters and promos ("thank you for your interest in our newsletter")
        return None, ""
    for k in ORDER:
        if k == "rejection" and re.search(BOILERPLATE_NOT_REJECTION, t):
            continue
        m = re.search(BODY[k], t)
        if m:
            return k, t[max(0, m.start() - 60): m.end() + 60].replace("\n", " ").strip()
    return None, ""


def system_of(sender):
    s = (sender or "").lower()
    return next((v for k, v in SYSTEMS.items() if k in s), "")


# ---------------------------------------------------------------- matching
def company_keys(company):
    """'Acme Electronics (ACE)' → ['acme electronics', 'ace']: the name and any parenthesized short form."""
    c = company or ""
    keys = [norm(re.sub(r"\(.*?\)", "", c))] + [norm(x) for x in re.findall(r"\((.*?)\)", c)]
    out = []
    for k in keys:
        k = re.sub(r"\b(inc|llc|corp|corporation|co|ltd|plc|the)\b", "", k).strip()
        if len(k) >= 3 and k not in out:
            out.append(k)
    return out


def hits_company(keys, hay):
    return any(re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", hay) for k in keys)


def body_hit(company, raw):
    """Body-only match is case-sensitive on the company's own spelling, so 'Lattice' or 'Vector' don't fire on
    'lattice out time' or 'vector'. Sender/subject matches (case-insensitive) are the strong signal."""
    names = [re.sub(r"\(.*?\)", "", company or "").strip()] + re.findall(r"\((.*?)\)", company or "")
    return any(len(n) >= 3 and re.search(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", raw)
               for n in names)


def req_ids(text):
    """Requisition numbers as employers print them: Workday R-100002 / JR100002, and some employers plain digits."""
    return set(re.findall(r"R-\d{5,}|JR\d{5,}|CA\d{6,}|(?<![\d-])\d{5,9}(?!\d)", text or ""))


def find_rows(msg, rows):
    """Rows whose company appears in the sender, subject, or the first 3000 chars of body."""
    sender = norm(msg["from"])
    raw_body = msg["body"][:3000]
    dom = norm(re.sub(r".*@", "", re.findall(r"[\w.+-]+@[\w.-]+", msg["from"])[0]) if "@" in msg["from"] else "")
    strong, weak = [], []
    for i, r in enumerate(rows):
        keys = company_keys(r.get("company"))
        if not keys:
            continue
        compact = [k.replace(" ", "") for k in keys]
        if (hits_company(keys, sender) or body_hit(r.get("company"), msg["subject"])
                or any(c in dom.split() for c in compact)):
            strong.append(i)
        elif body_hit(r.get("company"), raw_body):
            weak.append(i)
    hits = strong or weak
    if len(hits) > 1:  # same company, several rows: a requisition number in the email is the strongest tie-break
        ids = req_ids(f"{msg['subject']} {msg['body'][:3000]}")
        by_id = [i for i in hits if ids & req_ids(f"{rows[i].get('role') or ''} {rows[i].get('url') or ''}")]
        if len(by_id) == 1:
            return by_id
    if len(hits) > 1:  # same company, several rows: let role words decide
        text = norm(f"{msg['subject']} {msg['body'][:3000]}")

        def role_score(i):
            words = [w for w in norm(re.sub(r"\(.*?\)", "", rows[i].get("role") or "")).split() if len(w) > 3]
            return sum(w in text for w in words) / max(1, len(words))
        scored = sorted(hits, key=role_score, reverse=True)
        best = role_score(scored[0])
        if best >= 0.5 and (len(scored) == 1 or role_score(scored[1]) < best):
            return [scored[0]]
    return hits


ATS_DOMAINS = ("greenhouse.io", "greenhouse-mail.io", "lever.co", "hire.lever.co", "ashbyhq.com", "myworkday.com",
               "myworkdayjobs.com", "workday.com", "jobvite.com", "icims.com", "smartrecruiters.com",
               "successfactors.com", "sapsf.com", "taleo.net", "bamboohr.com", "linkedin.com", "workable.com",
               "rippling.com", "paylocity.com", "oraclecloud.com")


def sender_check(msg, row):
    """Only mail that provably came from the company or its ATS may change a row on its own (red team:
    a display name like '"Acme Corp" <x@evil.example>' plus rejection wording used to auto-reject the row).
    Trusted = sender domain is a known ATS domain or contains the company's name, AND Gmail's
    Authentication-Results shows dkim=pass with no dmarc=fail. Fixture mail has no header; it skips that check."""
    m = re.findall(r"[\w.+-]+@([\w.-]+)", msg.get("from") or "")
    if not m:
        return False, "no sender address"
    dom = m[-1].lower().rstrip(".")
    compact = [k.replace(" ", "") for k in company_keys(row.get("company")) if len(k.replace(" ", "")) >= 3]
    labels = [x.replace("-", "") for x in dom.split(".")[:-1]]  # 'acme' in mail.acme.com, not acme-hiring.xyz
    ok_dom = any(dom == d or dom.endswith("." + d) for d in ATS_DOMAINS) or any(c in labels for c in compact)
    if not ok_dom:
        return False, f"sender domain {dom} isn't the company's or a known ATS"
    if "auth" in msg:
        auth = (msg.get("auth") or "").lower()
        if "dkim=pass" not in auth or "dmarc=fail" in auth:
            return False, f"{dom} didn't pass DKIM/DMARC"
        org = lambda d: ".".join(d.strip("@.").split(".")[-2:])
        signers = re.findall(r"dkim=pass[^;]*?header\.[di]=@?([\w.-]+)", auth)
        if not any(org(s) == org(dom) for s in signers):  # a pass for the attacker's own domain proves nothing
            return False, f"DKIM pass isn't for {dom}"
    return True, ""


def row_ref(r):
    """A log.py match string that points at this row: URL first (matched by ID/exact URL), else company + role."""
    if r.get("url"):
        return r["url"]
    words = [w for w in norm(re.sub(r"\(.*?\)", "", r.get("role") or "")).split()][:4]
    return " ".join(company_keys(r.get("company"))[:1] + words)


# ---------------------------------------------------------------- Gmail API (stdlib)
def http(url, data=None, token=None, method=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        if isinstance(data, dict) and method != "JSON":
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 2:
                time.sleep(2 ** attempt * 2)
                continue
            raise SystemExit(f"Gmail API {e.code}: {e.read().decode(errors='replace')[:400]}")
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(3)
                continue
            raise SystemExit(f"Network error reaching Google: {e.reason}")
    raise SystemExit("Gmail API: no answer after 3 tries")


def access_token():
    if not TOKEN.exists():
        raise SystemExit(f"No Gmail token at {TOKEN}. Run: python scripts/gmail_sweep.py auth --client <client_secret.json>")
    t = json.loads(TOKEN.read_text())
    if t.get("access_token") and t.get("expires_at", 0) > time.time() + 60:
        return t["access_token"]
    r = http(TOKEN_URL, {"client_id": t["client_id"], "client_secret": t["client_secret"],
                         "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
    t["access_token"], t["expires_at"] = r["access_token"], time.time() + int(r.get("expires_in", 3600))
    _write_secret(TOKEN, json.dumps(t, indent=2))
    return t["access_token"]


def cmd_auth(a):
    c = json.loads(Path(a.client).expanduser().read_text())
    c = c.get("installed") or c.get("web") or c
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, got = secrets.token_urlsafe(16), {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<p>Gmail connected. You can close this tab.</p>")

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Sign in with the Gmail account you're connecting in the browser that opens. If none opens, paste this link:\n" + url)
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    srv.timeout = 300
    srv.handle_request()
    if got.get("state") != state or "code" not in got:
        raise SystemExit(f"Sign-in didn't finish: {got.get('error', 'no code returned')}")
    r = http(TOKEN_URL, {"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                         "code": got["code"], "code_verifier": verifier, "grant_type": "authorization_code",
                         "redirect_uri": redirect})
    if "refresh_token" not in r:
        raise SystemExit("Google returned no refresh token. Remove the app's access at myaccount.google.com/permissions and rerun.")
    TOKEN.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_secret(TOKEN, json.dumps({"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                                 "refresh_token": r["refresh_token"], "access_token": r["access_token"],
                                 "expires_at": time.time() + int(r.get("expires_in", 3600))}, indent=2))
    me = http(f"{API}/profile", token=r["access_token"])
    print(f"✅ Gmail connected as {me.get('emailAddress')}. Token: {TOKEN}")


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", errors="replace")


def body_text(payload):
    plain, rich = [], []

    def walk(p):
        mt, data = p.get("mimeType", ""), (p.get("body") or {}).get("data")
        if data and mt == "text/plain":
            plain.append(b64d(data))
        elif data and mt == "text/html":
            rich.append(b64d(data))
        for sub in p.get("parts") or []:
            walk(sub)
    walk(payload)
    if plain:
        return "\n".join(plain)
    h = "\n".join(rich)
    links = " ".join(re.findall(r'href="([^"]+)"', h))  # keep link targets (LinkedIn application_rejected)
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    return html.unescape(re.sub(r"<[^>]+>", " ", h)) + "\n" + links


def parse_raw(raw_b64url):
    """One message from the cloud Gmail connector (format RAW: base64url MIME) → the same dict a live fetch makes.
    Keeps Authentication-Results, so the sender checks work the same as on the candidate's machine."""
    from email import policy
    from email.parser import BytesParser
    from email.utils import parsedate_to_datetime
    data = base64.urlsafe_b64decode(raw_b64url + "=" * (-len(raw_b64url) % 4))
    m = BytesParser(policy=policy.default).parsebytes(data)
    part = m.get_body(preferencelist=("plain", "html"))
    text = part.get_content() if part is not None else ""
    if part is not None and part.get_content_type() == "text/html":
        links = " ".join(re.findall(r'href="([^"]+)"', text))  # keep link targets (LinkedIn application_rejected)
        text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", text))) + "\n" + links
    try:
        when = parsedate_to_datetime(str(m["Date"])).astimezone(ET)
    except (TypeError, ValueError):
        when = datetime.now(ET)
    return {"id": str(m.get("Message-ID", "")), "thread": "", "from": str(m.get("From", "")),
            "auth": " ".join(str(v) for v in m.get_all("Authentication-Results", [])),
            "subject": str(m.get("Subject", "")), "date": when.isoformat(), "body": text or ""}


def fetch(since, cap):
    tok = access_token()
    after = int(since.timestamp())
    ids = {}
    for kind, phrases in SEARCH.items():
        q = f"after:{after} -from:me ({' OR '.join(phrases)})"
        page = None
        while True:
            params = {"q": q, "maxResults": 100}
            if page:
                params["pageToken"] = page
            r = http(f"{API}/messages?" + urllib.parse.urlencode(params), token=tok)
            for m in r.get("messages", []):
                ids.setdefault(m["id"], kind)
            page = r.get("nextPageToken")
            if not page or len(ids) >= cap:
                break
    msgs = []
    for mid in list(ids)[:cap]:
        m = http(f"{API}/messages/{mid}?format=full", token=tok)
        h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
        when = datetime.fromtimestamp(int(m["internalDate"]) / 1000, tz=timezone.utc).astimezone(ET)
        msgs.append({"id": mid, "thread": m.get("threadId"), "from": h.get("from", ""),
                     "auth": h.get("authentication-results", ""),
                     "subject": h.get("subject", ""), "date": when.isoformat(), "body": body_text(m["payload"])})
    return msgs


# ---------------------------------------------------------------- sweep
def default_since(hours):
    if hours:
        return datetime.now(ET) - timedelta(hours=hours)
    last = load_json(APPS).get("last_gmail_sweep")
    if last:
        try:
            return datetime.fromisoformat(last) - timedelta(hours=1)  # 1h overlap: late-indexed mail
        except ValueError:
            pass  # not a parseable value: leave the default
    return datetime.now(ET) - timedelta(days=3)


def cmd_sweep(a):
    source = "fixture"
    if a.fixture:
        msgs = load_json(a.fixture)["messages"]
        since = None
    elif a.raw:  # cloud: messages the Gmail connector fetched in RAW format (see the sweep docstring)
        msgs = [parse_raw(x["raw"]) for x in load_json(a.raw)["messages"]]
        since = None
        source = "cloud raw file"
    else:
        since = default_since(a.hours)
        msgs = fetch(since, a.max)
    rows = load_json(APPS)["applications"]
    arch = load_json(ARCHIVE)["applications"] if ARCHIVE.exists() else []
    live_idx = [i for i, r in enumerate(rows) if r.get("status") in LIVE_STATUS]
    live_rows = [rows[i] for i in live_idx]

    results = []
    for m in sorted(msgs, key=lambda x: x["date"]):
        kind, why = classify(f"{m['subject']}\n{m['body']}")
        if not kind:
            continue
        hits = [live_idx[i] for i in find_rows(m, live_rows)]
        if not hits:  # a triaged/skipped row can still get mail; then archive
            hits = [i for i in find_rows(m, rows)]
        arch_hits = [] if hits else find_rows(m, arch)
        at = m["date"][:16].replace("T", " ")
        sysname = system_of(m["from"]) or system_of(m["body"][:4000])
        res = {"kind": kind, "date": at, "from": m["from"], "subject": m["subject"], "evidence": why,
               "id": m.get("id"), "system": sysname, "rows": [], "command": None, "auto": False, "flag": ""}
        if len(hits) == 1:
            r = rows[hits[0]]
            sender_ok, why_not = sender_check(m, r)
            res["rows"] = [f"{r.get('company')} - {r.get('role','')[:60]} [{r.get('status')}]"]
            ref = row_ref(r)
            sys_opt = ["--system", sysname] if sysname else []
            if kind == "confirmation":
                if r.get("confirmed") is True:
                    continue  # already logged
                res["command"] = ["note", ref, f"Confirmation email {at}", "--confirmed", "true"]
                res["auto"] = sender_ok
            elif kind == "rejection":
                if r.get("status") == "rejected":
                    continue
                checks, risk = assess_rejection(m, r, sender_ok)
                res["checks"], res["risk"] = checks, risk
                res["command"] = ["set", ref, "rejected", "--at", at, "--date", at[:10], "--note",
                                  f"Rejection email {at}" + (f" ({sysname})" if sysname else ""),
                                  "--checks", checks, "--risk", risk] + sys_opt
                res["auto"] = True  # every rejection updates the row; the risk flag says where to look
                if risk != "low":
                    res["flag"] = f"Risk {risk.upper()} ({checks}): review this row"
            elif kind == "interview":
                if r.get("status") in ("interview", "offer"):
                    res["flag"] = "Follow-up from an active interview: read it"
                res["command"] = ["set", ref, "interview", "--at", at, "--date", at[:10], "--note", "Recruiter screen: <who/when>"] + sys_opt
            elif kind == "offer":
                res["command"] = ["set", ref, "offer", "--at", at, "--date", at[:10], "--note", "Offer: <details>"] + sys_opt
            if kind == "confirmation" and not sender_ok:
                res["flag"] = f"Not auto-applied: {why_not}. Check the email is real before running the line"
        elif len(hits) > 1:
            res["rows"] = [f"#{i} {rows[i].get('company')} - {rows[i].get('role','')[:60]} [{rows[i].get('status')}]" for i in hits[:6]]
            res["flag"] = "Ambiguous: more than one row matches; pick with log.py find"
        elif arch_hits:
            r = arch[arch_hits[0]]
            res["rows"] = [f"(archive) {r.get('company')} - {r.get('role','')[:60]} [{r.get('status')}]"]
            res["flag"] = "Matches only an archived row: tell the candidate, never add a row"
        else:
            res["flag"] = "No row matches (applied outside the loop?)"
        results.append(res)

    report(results, since, len(msgs), source)
    if a.json:
        Path(a.json).write_text(json.dumps({"since": since.isoformat() if since else None,
                                            "scanned": len(msgs), "results": results}, indent=2), newline="\n")
    if a.apply:
        ran = 0
        for res in results:
            if res["auto"] and res["command"]:
                p = subprocess.run(LOG + res["command"], cwd=ROOT, capture_output=True, text=True)
                print(("  ✓ " if p.returncode == 0 else "  ✗ ") + (p.stdout.strip() or p.stderr.strip()).splitlines()[-1])
                ran += p.returncode == 0
        if not (a.fixture or a.raw):  # only a live sweep covers the whole window, so only it moves the stamp
            subprocess.run(LOG + ["sweep-stamp"], cwd=ROOT)
        print(f"Applied {ran} line(s). Interviews/offers above need the candidate; add a 'likely why' note to each rejection.")


def report(results, since, scanned, source="fixture"):
    label = {"offer": "🎉 OFFER", "interview": "📅 INTERVIEW", "rejection": "✖ Rejection", "confirmation": "✓ Confirmation"}
    print(f"Gmail sweep: {scanned} email(s) scanned" + (f" since {since:%Y-%m-%d %H:%M} ET" if since else f" ({source})"))
    if not results:
        print("Nothing new.")
        return
    for kind in ORDER:  # offers and interviews first: those go to the candidate right away
        for r in [x for x in results if x["kind"] == kind]:
            print(f"\n{label[kind]}  {r['date']}  {r['subject'][:80]}")
            print(f"   from: {r['from'][:80]}" + (f"  · {r['system']}" if r["system"] else ""))
            print(f"   says: …{r['evidence'][:150]}…")
            for row in r["rows"]:
                print(f"   row:  {row}")
            if r["flag"]:
                print(f"   ⚠ {r['flag']}")
            if r["command"]:
                cmd = " ".join(f'"{x}"' if " " in x or not x else x for x in r["command"])
                print(f"   {'auto' if r['auto'] else 'candidate OKs'}: python scripts/log.py {cmd}")


# ---------------------------------------------------------------- draft / send
def build_mime(a):
    me = http(f"{API}/profile", token=access_token()).get("emailAddress")
    msg = EmailMessage()
    msg["To"], msg["From"], msg["Subject"] = a.to, me, a.subject
    page = Path(a.html).read_text(encoding="utf-8")
    msg.set_content(re.sub(r"<[^>]+>", "", re.sub(r'<a href="([^"]+)">([^<]*)</a>', r"\2 (\1)", page)))
    msg.add_alternative(page, subtype="html")
    for f in a.attach or []:
        p = Path(f).resolve()
        roots = (ROOT, Path.home() / "Downloads")  # sent resumes are often saved to Downloads
        if not any(r in p.parents for r in roots) or ".git" in p.parts or p.suffix.lower() not in (".docx", ".pdf"):
            raise SystemExit(f"Refused attachment {f}: only .docx/.pdf files in the repo or ~/Downloads (never tokens or keys).")
        sub = "vnd.openxmlformats-officedocument.wordprocessingml.document" if p.suffix == ".docx" else \
              "pdf" if p.suffix == ".pdf" else "octet-stream"
        msg.add_attachment(p.read_bytes(), maintype="application", subtype=sub, filename=p.name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def cmd_draft(a):
    if "," in a.to or ";" in a.to:
        raise SystemExit("One recipient per message (never in bulk).")
    if a.cmd == "send":
        if a.yes and not sys.stdin.isatty():  # an agent can pass --yes; it can't type at the user's keyboard
            raise SystemExit("send needs a person at the keyboard (no TTY). Use `draft` and send it from Gmail.")
        if a.yes:
            try:
                a.yes = input(f"Type SEND to email {a.to}: ").strip() == "SEND"
            except EOFError:  # Windows reports NUL as a TTY; no answer = not sent
                a.yes = False
        if not a.yes:
            print(f"Not sent. Preview: to {a.to} · '{a.subject}' · attachments: {', '.join(a.attach or []) or 'none'}")
            print("Rerun with --yes only after the candidate approved this exact note.")
            return
    raw = build_mime(a)
    if a.cmd == "send":
        r = http(f"{API}/messages/send", {"raw": raw}, access_token(), method="JSON")
        print(f"✅ Sent to {a.to} (message {r.get('id')}). Log it: python scripts/log.py note <row> \"Referral ask sent to ...\"")
    else:
        r = http(f"{API}/drafts", {"message": {"raw": raw}}, access_token(), method="JSON")
        print(f"📝 Draft saved for {a.to} (draft {r.get('id')}). It's in Gmail > Drafts; nothing was sent.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    au = sub.add_parser("auth"); au.add_argument("--client", required=True)
    sw = sub.add_parser("sweep")
    sw.add_argument("--hours", type=float); sw.add_argument("--max", type=int, default=300)
    sw.add_argument("--apply", action="store_true"); sw.add_argument("--json")
    sw.add_argument("--fixture")
    sw.add_argument("--raw", help="cloud: JSON from the Gmail connector, {\"messages\": [{\"raw\": \"<base64url MIME>\"}]}")
    for name in ("draft", "send"):
        d = sub.add_parser(name)
        d.add_argument("--to", required=True); d.add_argument("--subject", required=True)
        d.add_argument("--html", required=True, help="HTML body file; links as explicit <a href>")
        d.add_argument("--attach", action="append")
        if name == "send":
            d.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    {"auth": cmd_auth, "sweep": cmd_sweep, "draft": cmd_draft, "send": cmd_draft}[a.cmd](a)


if __name__ == "__main__":
    main()
