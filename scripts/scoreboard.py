"""Print the job-search scoreboard from data/applications.json (+ archive.json) and data/leads.json.

Usage: python3 scripts/scoreboard.py            (run from the repo root)
       python3 scripts/scoreboard.py --timing   time from applying to the first employer response
       python3 scripts/scoreboard.py --fit      core vs. stretch: screen and rejection rates by years_req status and verdict, split by age
"""
import sys
from collections import Counter, defaultdict
from datetime import datetime
from statistics import median
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import LEADS, ROOT, all_apps, load_json  # noqa: E402

apps = all_apps()  # live rows + archive.json


def when(v):
    return datetime.fromisoformat(v) if v and "T" in v else (datetime.fromisoformat(v + "T12:00") if v else None)


def timing_view():
    """Response speed: hours when both times are known, else days (date-only stamps count as noon)."""
    rows = []
    for a in apps:
        t = a.get("timing") or {}
        s, e = when(t.get("applied_at")), when(t.get("outcome_at"))
        if s and e:
            exact = "T" in t["applied_at"] and "T" in t["outcome_at"]
            rows.append(((e - s).total_seconds() / 86400, exact, a, t))
    rows.sort(key=lambda x: x[0])
    print(f"Time to first response: {len(rows)} outcomes (date-only stamps are approximate, marked ~;"
          " a send at exactly :00 may be an ATS scheduled/batch email, so it shows send timing, not review depth)")
    for d, exact, a, t in rows:
        span = f"{d * 24:.0f} hrs" if exact and d < 2 else f"{d:.1f} days"
        sent = t["outcome_at"][11:] if "T" in t["outcome_at"] else ""
        sched = ("  (possible scheduled send)" if sent.endswith(":00")
                 and "approximate" not in (t.get("source") or "") else "")
        print(f"  {'' if exact else '~'}{span:>10}  {t.get('outcome_type',''):<9} {a['company'][:22]:<22}"
              f" {(t.get('system') or '?'):<15} {a.get('verdict') or '-':<7} sent {sent or '?'}{sched}")
    for label, key in (("By system", lambda a, t: t.get("system") or "?"),
                       ("By verdict", lambda a, t: a.get("verdict") or "-")):
        g = defaultdict(list)
        for d, _, a, t in rows:
            g[key(a, t)].append(d)
        print(f"{label}: " + " · ".join(f"{k} n={len(v)} median {median(v):.1f}d" for k, v in sorted(g.items())))
    open_ = [a for a in apps if a["status"] == "applied" and (a.get("timing") or {}).get("applied_at")]
    print(f"Still waiting (applied, no response): {len(open_)}")
    if len(rows) < 20:
        print(f"Only {len(rows)} outcomes: a log, not a pattern. Draw conclusions at ~20+.")


def fit_view():
    """Core vs. stretch test. Screen = row has a `screen` field (set with log.py --screen) or status interview/offer.
    Pending = still applied, no screen. Split by age so new applications aren't read as failures."""
    now = datetime.now()
    rows = [a for a in apps if a["status"] in ("applied", "rejected", "interview", "offer")]

    def age(a):
        d = when(a.get("status_date") if a["status"] == "applied" else a.get("added")) or when(a.get("added"))
        return (now - d).days if d else 0

    def screened(a):
        return bool(a.get("screen")) or a["status"] in ("interview", "offer")

    def line(label, grp):
        n = len(grp)
        if not n:
            return
        sc = sum(screened(a) for a in grp)
        rj = sum(a["status"] == "rejected" for a in grp)
        pend = [a for a in grp if a["status"] == "applied" and not screened(a)]
        old = sum(age(a) >= 14 for a in pend)
        print(f"  {label:<12} n={n:<3} screened {sc} ({sc / n:.0%}) · rejected {rj} ({rj / n:.0%})"
              f" · pending {len(pend)} ({old} are 14+ days old)")

    print("Core vs. stretch (years_req.status; core = met, stretch = borderline/short):")
    g = defaultdict(list)
    for a in rows:
        g[(a.get("years_req") or {}).get("status") or "no years line"].append(a)
    for k in ("met", "borderline", "short", "no years line"):
        line(k, g.get(k, []))
    print("By verdict:")
    g = defaultdict(list)
    for a in rows:
        g[a.get("verdict") or "-"].append(a)
    for k in sorted(g):
        line(k, g[k])
    total_out = sum(screened(a) or a["status"] == "rejected" for a in rows)
    if total_out < 20:
        print(f"Only {total_out} outcomes so far: a log, not a pattern. Compare again once most rows are 14+ days old.")


if "--fit" in sys.argv:
    fit_view()
    sys.exit()
if "--timing" in sys.argv:
    timing_view()
    sys.exit()
leads = load_json(LEADS)["leads"]

st = Counter(a["status"] for a in apps)
ever_applied = [a for a in apps if a["status"] in ("applied", "rejected", "interview", "offer")]
rejected = [a["company"] for a in apps if a["status"] == "rejected"]

print(f"Applied: {len(ever_applied)} · Rejected: {len(rejected)} ({', '.join(rejected)})"
      f" · Open: {st['applied'] + st['interview']} · Interviews: {st['interview']} · Offers: {st['offer']}")
today = datetime.now().strftime("%Y-%m-%d")
SEARCH_START = load_json(ROOT / "config.json").get("search_start", "") if (ROOT / "config.json").exists() else ""
if SEARCH_START:  # optional: "search_start": "YYYY-MM-DD" in config.json turns on the pace line
    _days = (datetime.now().date() - datetime.strptime(SEARCH_START, "%Y-%m-%d").date()).days
    if _days > 0:
        print(f"Search: {_days} days since {SEARCH_START} · {len(ever_applied) / _days:.1f} applications/day")
def _applied_on(a):
    t = (a.get("timing") or {}).get("applied_at") or ""
    return t[:10] == today or (a["status"] == "applied" and a.get("status_date") == today)
t_app = [a for a in apps if _applied_on(a)]
t_rej = [a["company"] for a in apps if a["status"] == "rejected" and a.get("status_date") == today]
t_int = [a["company"] for a in apps if a["status"] == "interview" and a.get("status_date") == today]
print(f"Today ({today}): Applied {len(t_app)} · Rejected {len(t_rej)}"
      f"{' (' + ', '.join(t_rej) + ')' if t_rej else ''} · Interviews {len(t_int)}"
      f"{' (' + ', '.join(t_int) + ')' if t_int else ''}")
not_started = [f"{a['company']} - {a['role'][:60]}" for a in apps if a["status"] in ("triaged", "deciding")]
print(f"Triaged, not started: {len(not_started)}")
for s in not_started:
    print("  -", s)
ls = Counter(l["status"] for l in leads)
print(f"the scheduler backlog: {ls['not_triaged'] + ls['tier1_next']} not triaged"
      f" ({ls['tier1_next']} tier 1 next) · rated, not picked: {ls['rated']} · not checked: {ls['not_checked']}")
