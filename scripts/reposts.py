"""reposts.py — flag reposted / possible ghost postings (idea from career-ops, our own code).

Usage (from the repo root):
    python scripts/reposts.py              every repost group in applications.json + archive.json
    python scripts/reposts.py --leads      only groups that include an untriaged lead in leads.json
    python scripts/reposts.py "acme"       only groups whose company or title contains the words
    python scripts/reposts.py --days 60    window (default 90)

A repost = same company + same title (posting IDs, req numbers and parentheticals ignored) under
2+ different URLs/posting IDs, first seen on 2+ different dates within the window.
Flag only: nothing is changed, and a repost is never a skip by itself (it can be a real re-open).
"""
import re, sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import LEADS, all_apps, job_ids, load_json, norm  # noqa: E402

LEVEL_NOISE = re.compile(r"\b(remote|hybrid|us|usa|united states|r\d+|req|jr\d+|p\d+)\b")


def title_key(company, role):
    r = re.sub(r"\([^)]*\)|\[[^]]*\]|#?\d{4,}", " ", role or "")
    r = re.sub(r"\s[-–|]\s.*(remote|hybrid|us|usa|united states).*$", " ", r, flags=re.I)
    c = re.sub(r"\b(inc|llc|corp|corporation|co|ltd|the)\b", " ", norm(company))
    return (" ".join(c.split()), " ".join(LEVEL_NOISE.sub(" ", norm(r)).split()))


def posting_key(row):
    ids = job_ids(row.get("url") or "") | job_ids(row.get("role") or "")
    return ",".join(sorted(ids)) or (row.get("url") or "").split("?")[0].rstrip("/").lower() or None


def as_date(s):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def groups(days=90, extra=()):
    g = defaultdict(list)
    for src, rows in (("log", all_apps()), ("lead", extra)):
        for r in rows:
            k = title_key(r.get("company"), r.get("role"))
            if k[0] and k[1]:
                g[k].append((src, r))
    out = []
    for k, rows in g.items():
        keys = {posting_key(r) for _, r in rows if posting_key(r)}
        dates = sorted({d for d in (as_date(r.get("added") or r.get("date_found") or r.get("source", "")[:10])
                                    for _, r in rows) if d})
        if len(keys) < 2 or len(dates) < 2:
            continue
        span = (dates[-1] - dates[0]).days
        if span > days:
            dates = [d for d in dates if (dates[-1] - d).days <= days]
            if len(dates) < 2:
                continue
            span = (dates[-1] - dates[0]).days
        out.append((k, rows, dates, span))
    out.sort(key=lambda x: (-len(x[2]), x[0]))
    return out


def check(company, role, url="", days=90):
    """For triage: return a one-line repost flag for one posting, or ''. Never a skip."""
    probe = {"company": company, "role": role, "url": url, "added": date.today().isoformat()}
    for k, rows, dates, span in groups(days, [probe]):
        if any(r is probe for _, r in rows):
            return f"🔁 possible repost: seen {len(rows) - 1}x before ({', '.join(d.isoformat() for d in dates[:-1])})"
    return ""


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    a = sys.argv[1:]
    days = 90
    if "--days" in a:
        i = a.index("--days")
        days = int(a[i + 1])
        del a[i:i + 2]
    leads_only = "--leads" in a
    a = [x for x in a if x != "--leads"]
    want = norm(" ".join(a)).split()
    leads = []
    if LEADS.exists():
        leads = [l for l in load_json(LEADS).get("leads", []) if l.get("status") == "not_triaged"]
    res = groups(days, leads)
    if leads_only:
        res = [x for x in res if any(s == "lead" for s, _ in x[1])]
    if want:
        res = [x for x in res if all(w in " ".join(x[0]) for w in want)]
    if not res:
        print(f"No reposts found (window {days} days).")
    else:
        print(f"Possible reposts / ghost postings (same company + title, 2+ posting IDs, 2+ dates within {days} days)."
              " Flag only, never a skip.\n")
    for (co, ti), rows, dates, span in res:
        print(f"🔁 {co} | {ti}  · {len(rows)} postings over {span} days ({', '.join(d.strftime('%b %d') for d in dates)})")
        for src, r in rows:
            tag = "LEAD " if src == "lead" else ""
            print(f"     {tag}{(r.get('added') or '')[:10]:10}  {r.get('status', ''):11} {r.get('url') or '(no url)'}")
