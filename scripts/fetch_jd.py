"""fetch_jd.py — pull a job posting's text into a plain file, no browser and no Claude.

Usage (from the repo root):
    python scripts/fetch_jd.py <posting URL> [jd.txt]      (or --out jd.txt)

LinkedIn is browser only (SKILL.md): this script refuses LinkedIn links instead of using
the guest API. Unknown --flags are rejected (an unrecognized "--out" used to write nothing useful).
Knows: Greenhouse (job-boards / boards / my.greenhouse.io, ?gh_jid= with --board),
Lever, Ashby, Workday, plus any page that publishes schema.org JobPosting data.
Anything else falls back to the page's visible text.

Output file starts with a header (TITLE / COMPANY / LOCATION / PAY / REMOTE / URL / SOURCE),
a blank line, then the description. screen_leads.py and inject_check.py read this file.

Options:
    --board NAME   Greenhouse board name for company-site links that only carry ?gh_jid=
    --out FILE     where to save (same as the second positional argument)
    --quiet        don't print the summary
Workday pulls every location (additionalLocations); schema.org pages with list-valued addresses no longer crash; a dead Greenhouse job ID says to look the title up on the board.
"""
import html, json, os, re, sys, urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser

sys.path.insert(0, __import__("os").path.dirname(__file__))
__import__("common")  # side effect: sets UTF-8 output on Windows
from common import host_is  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}


def _public_ip(a):
    import ipaddress
    ip = ipaddress.ip_address(str(a).split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:  # ::ffff:127.0.0.1 is judged as 127.0.0.1
        ip = ip.ipv4_mapped
    # require globally routable (outside review): carrier-grade NAT 100.64/10, benchmarking 198.18/15 and other
    # special-purpose ranges aren't private, loopback or link-local, so the old blocklist let them through
    return ip.is_global and not ip.is_multicast


def _ip_literal(host):
    """The address a host string denotes when it is an IP literal in any spelling the OS accepts
    (dotted, decimal 2130706433, octal 0177.0.0.1, hex 0x7f.1, IPv6, IPv4-mapped), else None."""
    import ipaddress, socket
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass  # not a parseable value: leave the default
    try:
        return socket.inet_ntoa(socket.inet_aton(host))  # inet_aton accepts the short/decimal/octal/hex forms
    except OSError:
        return None


def _safe_url(url):
    """Only http(s) to public hosts: an agent may call this on a link from an untrusted posting, so block
    file:, localhost and private/link-local networks. Checked on the URL, on every redirect, and again on the
    address actually connected to (guards DNS rebinding). Unresolvable hosts are refused."""
    import socket
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError(f"refused: only http(s) URLs ({url[:80]})")
    host = u.hostname.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError(f"refused: {host} is a local name")
    lit = _ip_literal(host)
    if lit is not None and not _public_ip(lit):
        raise ValueError(f"refused: {host} is a non-public address")
    if _PROXIED:
        if os.environ.get("JOBSEARCH_ALLOW_PROXY") != "yes":
            raise ValueError("refused: an HTTP(S) proxy is set, so the connected address can't be checked. "
                             "Unset it, or set JOBSEARCH_ALLOW_PROXY=yes to accept that risk.")
        # Behind a proxy the proxy connects, so the connect-time peer check can't run. Still resolve the name here
        # when we can and refuse private answers; an unresolvable name is left to the proxy (outside review).
        try:
            for a in {x[4][0] for x in socket.getaddrinfo(u.hostname, None)}:
                if not _public_ip(a):
                    raise ValueError(f"refused: {u.hostname} resolves to a non-public address ({a})")
        except socket.gaierror:
            pass  # name doesn't resolve: the fetch itself will report it
        return url
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(u.hostname, None)}
    except socket.gaierror:
        raise ValueError(f"refused: {u.hostname} does not resolve")
    for a in addrs:
        if not _public_ip(a):
            raise ValueError(f"refused: {u.hostname} resolves to a non-public address ({a})")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _checked(conn_cls):
    class C(conn_cls):
        def connect(self):
            super().connect()
            peer = self.sock.getpeername()[0]
            if not _PROXIED and not _public_ip(peer):
                self.sock.close()
                raise ValueError(f"refused: connected to a non-public address ({peer})")
    return C


import http.client  # noqa: E402
_PROXIED = bool(urllib.request.getproxies().get("https") or urllib.request.getproxies().get("http"))


class _SafeHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_checked(http.client.HTTPConnection), req)


class _SafeHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_checked(http.client.HTTPSConnection), req, context=self._context)


_OPENER = urllib.request.build_opener(_SafeRedirect, _SafeHTTP, _SafeHTTPS)


TOTAL_SECONDS = 75  # whole fetch, every retry included: one slow site can't stall a sweep
MAX_BYTES = 5_000_000  # a job page is far smaller; anything bigger is refused, not read into memory


def get(url, as_json=False, tries=3):
    """Retry transient failures (Workday IncompleteRead, resets, timeouts, 429/5xx) with a growing pause; permanent
    ones (DNS, TLS, refused URL) fail at once. Responses over MAX_BYTES are refused."""
    import http.client
    import time
    last = None
    deadline = time.monotonic() + TOTAL_SECONDS
    for n in range(tries):
        left = deadline - time.monotonic()
        if left <= 1:
            break
        try:
            req = urllib.request.Request(_safe_url(url), headers=UA)
            with _OPENER.open(req, timeout=min(30, left)) as r:
                raw = r.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError(f"refused: response over {MAX_BYTES:,} bytes ({url[:80]})")
                body = raw.decode(r.headers.get_content_charset() or "utf-8", "replace")
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
            last = e
        except urllib.error.URLError as e:  # wraps DNS/TLS/refusals too: retry only timeouts and dropped connections
            if not isinstance(e.reason, (TimeoutError, ConnectionError)):
                raise
            last = e
        except (http.client.IncompleteRead, ConnectionError, TimeoutError) as e:
            last = e
        if n < tries - 1:
            time.sleep(min(2 * (n + 1), max(0, deadline - time.monotonic())))
    raise last or ValueError(f"no response within {TOTAL_SECONDS}s ({url[:80]})")


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "ul", "ol"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


class _PerThread:
    """A list per thread (board_sweep's vet pass fetches 8 postings at once; one shared list mixed them)."""
    _tl = __import__("threading").local()

    def _l(self):
        if not hasattr(self._tl, "x"):
            self._tl.x = []
        return self._tl.x

    def extend(self, xs): self._l().extend(xs)
    def clear(self): self._l().clear()
    def __bool__(self): return bool(self._l())
    def __iter__(self): return iter(self._l())


HIDDEN = _PerThread()  # invisible-styled text seen while fetching (inject_check.hidden_html)


def to_text(fragment):
    try:
        from inject_check import hidden_html
        HIDDEN.extend(hidden_html(html.unescape(fragment or "") if "&lt;" in (fragment or "") else (fragment or "")))
    except ImportError:
        pass  # optional helper missing: skip that check
    p = _Text()
    p.feed(html.unescape(fragment or "") if "&lt;" in (fragment or "") else (fragment or ""))
    text = "".join(p.out)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pay(obj):
    """schema.org baseSalary -> '$X–$Y per YEAR'."""
    try:
        v = obj.get("value", {})
        lo, hi = v.get("minValue"), v.get("maxValue")
        unit = v.get("unitText", "")
        cur = obj.get("currency", "USD")
        fmt = lambda n: f"${float(n):,.0f}" if cur == "USD" else f"{cur} {float(n):,.0f}"
        if lo and hi:
            return f"{fmt(lo)}–{fmt(hi)} {unit}".strip()
        if v.get("value"):
            return f"{fmt(v['value'])} {unit}".strip()
    except Exception:
        pass  # best effort: this step is optional, carry on without it
    return ""


def _as_list(x):
    return x if isinstance(x, list) else ([] if x is None else [x])


def _blocks(page, tag, need=""):
    """Contents of each <tag ...>...</tag> whose opening tag contains `need`. Linear: str.find, not a lazy regex
    (those go quadratic on a hostile page of unclosed tags; 5 MB took hours)."""
    low, out, i = page.lower(), [], 0
    while True:
        s = low.find(f"<{tag}", i)
        gt = low.find(">", s) if s >= 0 else -1
        e = low.find(f"</{tag}>", gt) if gt >= 0 else -1
        if e < 0:
            return out
        if need in low[s:gt]:
            out.append(page[gt + 1:e])
        i = e + len(tag) + 3


def json_ld(page):
    """Find a schema.org JobPosting in the page's ld+json blocks."""
    for block in _blocks(page, "script", "application/ld+json"):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        for d in (data if isinstance(data, list) else data.get("@graph", [data])):
            types = d.get("@type") if isinstance(d, dict) else None
            if isinstance(d, dict) and (types == "JobPosting" or isinstance(types, list) and "JobPosting" in types):
                where = []
                for loc in _as_list(d.get("jobLocation")):
                    for addr in _as_list(loc.get("address") if isinstance(loc, dict) else None):
                        if isinstance(addr, dict):
                            w = ", ".join(str(x) for x in [addr.get("addressLocality"), addr.get("addressRegion")] if x)
                            if w and w not in where:
                                where.append(w)
                where = "; ".join(where)
                org = next((o for o in _as_list(d.get("hiringOrganization")) if isinstance(o, dict)), {})
                return {"title": d.get("title", ""),
                        "company": org.get("name", ""),
                        "location": where,
                        "pay": _pay(next((b for b in _as_list(d.get("baseSalary")) if isinstance(b, dict)), {})),
                        "remote": "TELECOMMUTE" if d.get("jobLocationType") == "TELECOMMUTE" else "",
                        "text": to_text(d.get("description", ""))}
    return None


# ---------- site handlers: each returns dict(title, company, location, pay, remote, text) ----------

def linkedin(u):
    m = re.search(r"(?:/jobs/view/(?:[^/]*?-)?|currentJobId=|jobPosting/)(\d{6,})", u)
    if not m:
        return None
    page = get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}")
    grab = lambda pat: to_text((re.search(pat, page, re.S) or [None, ""])[1])
    return {"title": grab(r'<h2[^>]*top-card-layout__title[^>]*>(.*?)</h2>'),
            "company": grab(r'topcard__org-name-link[^>]*>(.*?)</a>'),
            "location": grab(r'topcard__flavor--bullet[^>]*>(.*?)</span>'),
            "pay": grab(r'compensation__salary[^>]*>(.*?)</div>'),
            "remote": "",
            "text": to_text((re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
                             or [None, page])[1])}


def greenhouse(u, board=None):
    m = re.search(r"greenhouse\.io/(?:jobs/)?([\w-]+)/jobs/(\d+)", u) or \
        re.search(r"my\.greenhouse\.io/jobs/([\w-]+)/(\d+)", u)
    if m:
        board, jid = m.group(1), m.group(2)
    else:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
        jid = (q.get("gh_jid") or [None])[0]
        if not (jid and board):
            return None
    try:
        d = get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}?pay_transparency=true", True)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # a posting ID changed, the job didn't close
            raise RuntimeError(f"job {jid} not on board '{board}'. Before calling it closed, look the title up in "
                               f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs (IDs get reissued)")
        raise
    pay = ""
    for r in d.get("pay_input_ranges") or []:
        lo, hi = r.get("min_cents"), r.get("max_cents")
        if lo and hi:
            pay += f"${lo/100:,.0f}–${hi/100:,.0f} {r.get('title','')}; "
    return {"title": d.get("title", ""), "company": d.get("company_name", board),
            "location": (d.get("location") or {}).get("name", ""), "pay": pay.strip("; "),
            "remote": "", "text": to_text(d.get("content", ""))}


def lever(u):
    m = re.search(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})", u)
    if not m:
        return None
    d = get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}", True)
    parts = [d.get("descriptionPlain", "")]
    for lst in d.get("lists", []):
        parts.append(lst.get("text", "") + "\n" + to_text(lst.get("content", "")))
    parts.append(d.get("additionalPlain", ""))
    cat = d.get("categories", {})
    sal = d.get("salaryRange") or {}
    pay = f"${sal['min']:,}–${sal['max']:,} {sal.get('interval','')}" if sal.get("min") else ""
    return {"title": d.get("text", ""), "company": m.group(1), "location": cat.get("location", ""),
            "pay": pay, "remote": d.get("workplaceType", ""), "text": "\n\n".join(p for p in parts if p)}


def ashby(u):
    m = re.search(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})", u)
    if not m:
        return None
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}?includeCompensation=true", True)
    for j in d.get("jobs", []):
        if j.get("id") == m.group(2):
            comp = (j.get("compensation") or {}).get("compensationTierSummary", "")
            return {"title": j.get("title", ""), "company": m.group(1), "location": j.get("location", ""),
                    "pay": comp, "remote": "remote" if j.get("isRemote") else (j.get("workplaceType") or ""),
                    "text": j.get("descriptionPlain") or to_text(j.get("descriptionHtml", ""))}
    return None


def workday(u):
    m = re.search(r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/(job/.+?)(?:\?|$)", u)
    if m:
        tenant, wd, site, path = m.groups()
        api = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/{path}"
    else:  # wd1.myworkdaysite.com/recruiting/<tenant>/<site>/job/...
        m = re.search(r"https://(wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/([\w-]+)/([\w-]+)/(job/.+?)(?:\?|$)", u)
        if not m:
            return None
        wd, tenant, site, path = m.groups()
        api = f"https://{wd}.myworkdaysite.com/wday/cxs/{tenant}/{site}/{path}"
    d = get(api, True)
    info = d.get("jobPostingInfo", {})
    locs = [info.get("location", "")] + [x for x in info.get("additionalLocations") or [] if x]
    return {"title": info.get("title", ""), "company": tenant, "location": "; ".join(x for x in locs if x),
            "pay": "", "remote": info.get("remoteType", ""), "text": to_text(info.get("jobDescription", ""))}


def generic(u):
    page = get(u)
    found = json_ld(page)
    if found and len(found["text"]) > 200:
        return found
    title = to_text((_blocks(page, "title") or [""])[0])
    body = (_blocks(page, "main") or [page])[0]
    return {"title": title, "company": urllib.parse.urlparse(u).netloc, "location": "", "pay": "",
            "remote": "", "text": to_text(body)}


def fetch(url, board=None):
    host = urllib.parse.urlparse(url).netloc.lower()
    order = []
    if host_is(host, "linkedin.com"):  # SKILL.md: LinkedIn = browser only, never WebFetch or the guest API
        raise SystemExit("LinkedIn is browser only (SKILL.md): open the posting in the browser, read 'About the job', "
                         "and save it to jd.txt. This script doesn't use LinkedIn's guest API.")
    if host_is(host, "greenhouse.io") or "gh_jid=" in url:
        order.append(("Greenhouse", lambda u: greenhouse(u, board)))
    if host_is(host, "lever.co"):
        order.append(("Lever", lever))
    if host_is(host, "ashbyhq.com"):
        order.append(("Ashby", ashby))
    if host_is(host, "myworkdayjobs.com", "myworkdaysite.com"):
        order.append(("Workday", workday))
    order.append(("page", generic))
    errors = []
    HIDDEN.clear()
    for name, fn in order:
        try:
            d = fn(url)
            if d and len(d.get("text", "")) > 200:
                d["source"] = name
                if HIDDEN:
                    d["hidden"] = " | ".join(HIDDEN)
                return d
        except Exception as e:  # keep going down the list
            errors.append(f"{name}: {e}")
    raise SystemExit("Couldn't read that posting. " + " | ".join(errors) +
                     "\nOpen it in a browser and paste the text into jd.txt instead.")


_STOP = {"and", "the", "for", "with", "job", "jobs", "senior", "sr", "lead", "remote", "usa", "united", "states",
         "careers", "career", "external", "details", "apply", "en", "us", "of", "to", "in", "at"}


def _words(t):
    return {w for w in re.findall(r"[a-z]{3,}", (t or "").lower()) if w not in _STOP}


def wrong_page(d, url, role=""):
    """Catches a careers landing page read in place of the posting:: '' when d looks like the
    posting, else why not. (1) A job-board link (Greenhouse/Lever/Ashby/Workday) that only the plain-page fallback
    could read = the posting API didn't answer for it (closed, moved, or a company-site link). (2) The page's title and
    opening text share under half the role's words (role given, or else the title slug in the URL)."""
    host = urllib.parse.urlparse(url).netloc.lower()
    ats = host_is(host, "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com") or "gh_jid=" in url
    exp = _words(role)
    if ats and d.get("source") == "page" and exp:
        # the board API didn't answer; a company page can still be the posting (some company sites): its TITLE must match
        if len(exp & _words(d.get("title", ""))) / len(exp) < 0.5:
            return (f"Read a careers page, not the posting (page title: \"{(d.get('title') or '?')[:80]}\"; "
                    "the job board's API didn't return it: closed or moved?)")
        return ""
    if not exp:  # no role given: use the title slug in the URL, e.g. .../job/Denver-CO/Director--Strategy_R123
        seg = [x for x in urllib.parse.urlparse(url).path.split("/") if x]
        slug = seg[-1] if seg else ""
        exp = _words(re.sub(r"[_-]+(?:r|req|jr)?-?\d[\w-]*$", "", slug, flags=re.I).replace("-", " ").replace("_", " "))
    if len(exp) < 2:
        return ""
    got = _words(d.get("title", "")) | _words((d.get("text") or "")[:1500])
    if len(exp & got) / len(exp) < 0.5:
        return f"Page doesn't match the role (page title: \"{(d.get('title') or '?')[:80]}\"): likely a careers page, not the posting"
    return ""


def _one_line(v, cap=300):
    """Header values come from the posting (untrusted): a newline in a title could end the header early and push
    the real PAY/REMOTE lines into the body, so each value is collapsed to one capped line."""
    return " ".join(str(v or "").split())[:cap]


def write(d, url, out):
    head = [f"TITLE: {_one_line(d.get('title'))}", f"COMPANY: {_one_line(d.get('company'))}",
            f"LOCATION: {_one_line(d.get('location'))}", f"PAY: {_one_line(d.get('pay'))}",
            f"REMOTE: {_one_line(d.get('remote'))}", f"URL: {_one_line(d.get('url') or url, 2000)}",
            f"SOURCE: {_one_line(d.get('source'))}"]
    if d.get("hidden"):
        head.append("HIDDEN: " + " ".join(d["hidden"].split())[:600])
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(head) + "\n\n" + d["text"].strip() + "\n")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    bad = [a for a in sys.argv[1:] if a.startswith("--") and a not in ("--board", "--out", "--quiet")]
    if bad:
        raise SystemExit(f"Unknown option(s): {', '.join(bad)}\n" + __doc__)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    board = out = None
    for flag in ("--board", "--out"):
        if flag in sys.argv:
            i = sys.argv.index(flag)
            if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("--"):
                raise SystemExit(f"{flag} needs a value\n" + __doc__)
            val = sys.argv[i + 1]
            args = [a for a in args if a != val]
            board, out = (val, out) if flag == "--board" else (board, val)
    if not args:
        raise SystemExit(__doc__)
    url, out = args[0], (out or (args[1] if len(args) > 1 else "jd.txt"))
    d = fetch(url, board)
    write(d, url, out)
    if "--quiet" not in sys.argv:
        print(f"Saved {out} ({len(d['text'].split())} words) from {d['source']}")
        print(f"  {d.get('title','')} | {d.get('company','')} | {d.get('location','')}")
        if d.get("pay"):
            print(f"  Pay: {d['pay']}")
