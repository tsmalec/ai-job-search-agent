"""capture_jd.py: save a posting you read in the browser, in full, as a file the screens can run on.

Cloud chats can't use fetch_jd.py (every session sits behind a proxy), so a posting read in the built-in browser
has no other way into a file. Page text goes in on stdin:

    python3 scripts/capture_jd.py "https://boards.example.com/acme/jobs/123" < page.txt
    python3 scripts/capture_jd.py URL --company Acme --title "Operations Manager" --out jd.txt [--min 1200]

If URL is a lead in data/leads.json, company and title come from the lead and the file goes to
work/<company>_<role>.txt, the same name `screen_leads.py --leads --fetch` uses, so screen_done.py can see it.
Otherwise pass --company, --title and --out.

Refuses text shorter than --min characters (default 1200): a posting is longer than an excerpt or a summary.
This can't prove the text wasn't retyped; it makes a short excerpt impossible to screen. The file's SOURCE line
says "browser capture", never fetch, so a reader can tell where it came from. Text that is thin or has no
required-qualifications section is saved with a READ: partial header (and a warning), so it is never counted as a full read.
Then run inject_check.py --strip, screen_leads.py <file>, knockouts.py <file>, in that order.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import LEADS, load_json, url_key  # noqa: E402


def lead_for(url):
    key = url_key(url)
    for l in load_json(LEADS).get("leads", []):
        if url_key(l.get("url") or "") == key:
            return l
    return None


def take(a, name, default=None):
    if name in a:
        i = a.index(name)
        if i + 1 >= len(a):
            raise SystemExit(f"{name} needs a value.\n{__doc__}")
        val = a[i + 1]
        del a[i:i + 2]
        return val
    return default


def main(a):
    if not a or any(x in ("-h", "--help") for x in a):
        print((__doc__ or "").strip())
        return 0
    out, company, title = take(a, "--out"), take(a, "--company"), take(a, "--title")
    floor = int(take(a, "--min", "1200"))
    bad = [x for x in a if x.startswith("--")]
    if bad or len(a) != 1:
        raise SystemExit(f"Give one posting URL (unknown option(s): {', '.join(bad) or 'none'}).\n{__doc__}")
    url = a[0]
    lead = lead_for(url)
    if lead:
        company = company or lead.get("company")
        title = title or lead.get("role")
        out = out or "work/" + re.sub(r"\W+", "_", f"{lead['company']}_{lead['role']}")[:60] + ".txt"
    if not (company and title and out):
        raise SystemExit("Not a lead in data/leads.json: pass --company, --title and --out.")
    if sys.stdin.isatty():
        raise SystemExit("Pipe the page's full text in on stdin (< page.txt).")
    text = sys.stdin.read().strip()
    if len(text) < floor:
        raise SystemExit(f"Only {len(text)} characters: that reads as an excerpt or a summary, not the posting. "
                         f"Capture the full page text (or pass --min lower for a genuinely short posting).")
    from fetch_jd import write  # noqa: E402  (imported here so --help works without it)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    write({"title": title, "company": company, "url": url, "text": text,
           "source": f"browser capture, full text ({len(text)} chars)"}, url, out)
    from common import partial_reason  # noqa: E402
    why = partial_reason(text)
    if why:
        print(f"⚠️ PARTIAL READ: {why}. The file is marked READ: partial; report this lead as a partial read, "
              "not a full one, and capture the rest of the page if you can.")
    print(f"Saved {out} ({len(text)} chars). Next: inject_check.py {out} --strip, screen_leads.py {out}, knockouts.py {out}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
