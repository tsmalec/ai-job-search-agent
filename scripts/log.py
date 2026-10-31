"""log.py — update data/applications.json from the command line instead of hand-editing JSON.

Usage (from the repo root):
    python scripts/log.py find initech                         list matching rows (applications + leads)
    python scripts/log.py set initech applied                  change status (today's date)
    python scripts/log.py set "acme ops" interview --note "Recruiter screen" --date 2026-01-20
    python scripts/log.py set acme rejected --confirmed true
    python scripts/log.py note "acme" "Injection review: false positive"   append a note only
                                                                (status and status_date untouched)
    python scripts/log.py note acme "Confirmation email 10:02 AM" --confirmed true
                                                                log a confirmation email: sets confirmed,
                                                                status and status_date untouched
    python scripts/log.py sweep-stamp [--at "2026-01-15 10:30"]  stamp last_gmail_sweep (local time) after an email sweep
    python scripts/log.py add --company Acme --role "Sr Manager, BizOps" --url https://... --verdict yellow
    python scripts/log.py promote "partner development" --verdict yellow --status triaged
                                                                move a lead from leads.json into applications.json
    python scripts/log.py live "partner development" open      liveness only: sets live + live_checked,
                                                                status and status_date untouched
    python scripts/log.py archive                               move skipped/passed/dropped/closed rows to archive.json

Matching: a posting URL or 6+ digit posting/req ID matches by ID first; otherwise words must all appear
in "company role" (case-insensitive), or pass a row number from `find` (searches archive.json too).
If more than one row matches, nothing changes and the matches are listed.
Every save also rebuilds data/seen.txt (the compact dedupe list the scheduler reads).
Other fields for set/add/promote: --ats --resume --note --next --referral --url --verdict --confirmed --date
  --years "program|5+ years managing programs|short"   (years_req: type|asks|met/borderline/short)
  --screen "questionnaire 2026-01-08"   (first sign of progression past submission: assessment, questionnaire, recruiter contact)
Timing (for spotting response-speed patterns; your time zone, minute precision):
  --at "2026-01-08 17:00"   when it happened (the email's timestamp); default = now (local), or --date if given
  --system Jobvite          the hiring system/portal that sent it (Jobvite, Greenhouse, Workday, ...)
  `set ... applied` fills timing.applied_at; the first rejected/interview/offer fills timing.outcome_at
  and timing.outcome_type (later statuses don't overwrite it). `scoreboard.py --timing` shows the patterns.
Liveness: `live` (open|closed|uncertain) and `live_checked` (date) sit beside `status`, never
  replace it. Set with `log.py live <match> <state>` or `--live <state>` on add/promote/set.
  A closed posting you applied to stays `applied`; followups.py skips nudges on live=closed.
`set` with the row's current status (e.g. `set acme applied --confirmed true`) keeps status_date unless
  --date is given (it used to reset to today).
Standing rule: only log rejected/interview/offer when an email clearly says so.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from datetime import datetime  # noqa: E402
from common import url_key, APPS, ARCHIVE, DONE, LEADS, existing, job_ids, LOCAL_TZ, load_json, norm, save_json, today, write_seen  # noqa: E402

STATUSES = {"applied", "rejected", "interview", "offer", "triaged", "deciding", "fit_check", "resume_built",
            "skipped", "passed", "dropped", "closed"}
LIVE = {"open", "closed", "uncertain"}
VERDICTS = {"green", "yellow", "orange", "red", "green/yellow", None,
            "green?", "yellow?", "orange?", "red?", "green/yellow?"}  # "?" = prelim: sweep call from title/pay/years, not the full JD


def warn_verdict(r, new):
    """A verdict change used to be silent. Print it loudly so it gets told to the user."""
    old = r.get("verdict")
    if new is not None and old not in (None, "") and old != new:
        tag = " (old one was a prelim sweep call)" if str(old).endswith("?") else ""
        print(f"⚠ Verdict changed: {old} → {new}{tag}. Tell the user in the reply, with why.")
FIELDS = {"--ats": "ats", "--resume": "resume", "--note": "note", "--next": "next_step",
          "--referral": "referral", "--url": "url", "--verdict": "verdict", "--confirmed": "confirmed",
          "--company": "company", "--role": "role", "--status": "status", "--date": "status_date",
          "--years": "years_req", "--at": "_at", "--system": "_system", "--screen": "screen", "--live": "live",
          "--checks": "rejection_checks", "--risk": "risk_flag"}
OUTCOMES = {"rejected", "interview", "offer"}


def opts(argv):
    o, rest, i = {}, [], 0
    while i < len(argv):
        if argv[i] in FIELDS and i + 1 < len(argv):
            v = argv[i + 1]
            if FIELDS[argv[i]] == "confirmed":
                v = {"true": True, "false": False}.get(v.lower())
            if FIELDS[argv[i]] == "years_req":  # --years "program|5+ years managing programs|short"
                t, asks, st = (v.split("|") + ["", ""])[:3]
                v = {"type": t.strip(), "asks": asks.strip(), "status": st.strip()}
            if FIELDS[argv[i]] == "verdict" and v.lower() == "null":
                v = None
            o[FIELDS[argv[i]]] = v
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    return o, rest


def match(rows, q):
    if q.isdigit() and int(q) < len(rows) and len(q) < 6:
        return [int(q)]
    ids = job_ids(q)  # a posting URL or a 6+ digit posting/req ID
    if ids:
        hits = [i for i, r in enumerate(rows)
                if ids & (job_ids(r.get("url") or "") | job_ids(r.get("role") or "") | job_ids(r.get("note") or ""))]
        if hits:
            return hits
    if q.startswith("http"):
        u = q.split("?")[0].rstrip("/")
        hits = [i for i, r in enumerate(rows) if (r.get("url") or "").split("?")[0].rstrip("/") == u]
        if hits:
            return hits
    words = norm(q).split()
    return [i for i, r in enumerate(rows) if all(w in norm(f"{r.get('company','')} {r.get('role','')}") for w in words)]


def line(i, r):
    return f"  #{i:<3} {r.get('company')} - {r.get('role','')[:70]}  [{r.get('status')} {r.get('status_date') or r.get('source','')}]"


def one(rows, q, label):
    hits = match(rows, q)
    if len(hits) == 1:
        return hits[0]
    print(f"{'No' if not hits else len(hits)} {label} match '{q}'." + (" Be more specific or use the #number:" if hits else ""))
    for i in hits[:15]:
        print(line(i, rows[i]))
    sys.exit(1)


def now_local():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(LOCAL_TZ)).strftime("%Y-%m-%dT%H:%M")
    except Exception:  # no tz database (some Windows Pythons): use the machine's local clock
        return datetime.now().strftime("%Y-%m-%dT%H:%M")


def stamp(at, d):
    """--at wins (accepts 'YYYY-MM-DD HH:MM' or a date); else --date (date only); else now (local)."""
    if at:
        return at.strip().replace(" ", "T")[:16]
    return d or now_local()


def apply_timing(r, status, at, d, system):
    """Record when the application went in and when the employer first answered."""
    t = r.setdefault("timing", {})
    if system:
        t["system"] = system
    if status == "applied" and (at or not t.get("applied_at")):
        t["applied_at"] = stamp(at, d)
    if status in OUTCOMES and (at or not t.get("outcome_at")):
        t["outcome_at"], t["outcome_type"] = stamp(at, d), status
        if not at:
            print("  Tip: pass --at \"YYYY-MM-DD HH:MM\" with the email's time for exact timing.")
    if not t:
        del r["timing"]


def check(o):
    if "status" in o and o["status"] not in STATUSES:
        raise SystemExit(f"Unknown status '{o['status']}'. Use one of: {', '.join(sorted(STATUSES))}")
    if "verdict" in o and o["verdict"] not in VERDICTS:
        raise SystemExit("Verdict must be green, yellow, orange, red, green/yellow or null (add ? for a prelim sweep call)")
    if o.get("url") and bare_url(o["url"]):
        raise SystemExit(f"URL '{o['url']}' is a site home page, not a posting. Use the posting's own link "
                         "(or leave --url off): a bare home page dedupe-matches every other row.")


def bare_url(url):
    """True for a link with no path and no posting ID (e.g. https://www.indeed.com/): it would dedupe-match
    every other row with the same home page."""
    from urllib.parse import urlparse
    u = urlparse(url.strip())
    return u.path.strip("/") == "" and not job_ids(u.query)


def check_live_opt(o):
    if "live" in o:
        o["live"] = o["live"].lower()
        if o["live"] not in LIVE:
            raise SystemExit("--live must be open, closed or uncertain")
        o["live_checked"] = today()


def new_row(o):
    return {"added": today(), "company": o.get("company", ""), "role": o.get("role", ""),
            "verdict": o.get("verdict"), "ats": o.get("ats"), "resume": o.get("resume"),
            "status": o.get("status", "triaged"), "status_date": o.get("status_date", today()),
            "confirmed": o.get("confirmed"), "referral": o.get("referral"), "note": o.get("note", ""),
            "next_step": o.get("next_step", ""), "url": o.get("url"),
            **({"years_req": o["years_req"]} if o.get("years_req") else {}),
            **({"live": o["live"], "live_checked": o["live_checked"]} if o.get("live") else {})}


def merge_into(r, o, at, system):
    """Update an existing row with the new call's fields; notes stack instead of overwrite."""
    if "verdict" in o:
        warn_verdict(r, o["verdict"])
    for k, v in o.items():
        if v in (None, "") or k in ("company", "role", "note", "url"):
            continue
        r[k] = v
    if o.get("note"):
        r["note"] = (r.get("note") or "") + f" | {today()}: {o['note']}"
    if o.get("status"):
        apply_timing(r, o["status"], at, o.get("status_date"), system)


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    cmd, o_rest = argv[0], argv[1:]
    o, rest = opts(o_rest)
    at, system = o.pop("_at", None), o.pop("_system", None)
    check(o)
    check_live_opt(o)
    data = load_json(APPS)
    apps = data["applications"]

    if cmd == "find":
        q = " ".join(rest)
        hits = match(apps, q)
        print(f"applications: {len(hits)}")
        for i in hits:
            print(line(i, apps[i]))
        if ARCHIVE.exists():
            arch = load_json(ARCHIVE)["applications"]
            ah = match(arch, q)
            print(f"archive (read-only): {len(ah)}")
            for i in ah:
                print(line(i, arch[i]))
        leads = load_json(LEADS)["leads"]
        lh = match(leads, q)
        print(f"leads: {len(lh)}")
        for i in lh:
            print(line(i, leads[i]))
        return

    if cmd == "set":
        if len(rest) < 2:
            raise SystemExit("Usage: log.py set <match> <status> [--note ...]")
        status = rest[-1]
        q = " ".join(rest[:-1])
        o["status"] = status
        check(o)
        i = one(apps, q, "application")
        r = apps[i]
        old = r.get("status")
        explicit_date = o.get("status_date")
        if status == old and not explicit_date and r.get("status_date"):
            o["status_date"] = r["status_date"]  # same status: keep its date (confirmation-email fix)
        o.setdefault("status_date", today())
        if "note" in o and r.get("note"):
            o["note"] = f"{r['note']} | {o['status_date']}: {o['note']}"
        if "verdict" in o:
            warn_verdict(r, o["verdict"])
        r.update(o)
        apply_timing(r, status, at, explicit_date, system)
        if status in ("rejected", "skipped", "passed", "dropped", "closed") and "next_step" not in o:
            r["next_step"] = "Closed"
        print(f"{r['company']} - {r['role'][:60]}: {old} → {status} ({r['status_date']})")

    elif cmd == "note":
        if len(rest) < 2 and "note" not in o:
            raise SystemExit('Usage: log.py note <match> "note text"')
        text = o.pop("note", None) or rest[-1]
        q = " ".join(rest if "note" in o or text != rest[-1] else rest[:-1])
        i = one(apps, q, "application")
        r = apps[i]
        r["note"] = f"{r['note']} | {today()}: {text}" if r.get("note") else f"{today()}: {text}"
        extra = ""
        if "confirmed" in o:
            r["confirmed"] = o["confirmed"]
            extra = f", confirmed={o['confirmed']}"
        print(f"{r['company']} - {r['role'][:60]}: note added{extra} (status {r.get('status')} {r.get('status_date')} unchanged)")

    elif cmd == "sweep-stamp":
        ts = stamp(at, None)
        try:
            from zoneinfo import ZoneInfo
            off = datetime.fromisoformat(ts).replace(tzinfo=ZoneInfo(LOCAL_TZ)).strftime("%z")
            off = off[:3] + ":" + off[3:]
        except Exception:
            off = datetime.now().astimezone().strftime("%z"); off = off[:3] + ":" + off[3:]
        data["last_gmail_sweep"] = f"{ts}:00{off}"
        print(f"last_gmail_sweep → {data['last_gmail_sweep']}")

    elif cmd == "live":
        if len(rest) < 2:
            raise SystemExit("Usage: log.py live <match> <open|closed|uncertain> [--note ...]")
        o["live"] = rest[-1]
        check_live_opt(o)
        i = one(apps, " ".join(rest[:-1]), "application")
        r = apps[i]
        r["live"], r["live_checked"] = o["live"], o["live_checked"]
        if o.get("note"):
            r["note"] = f"{r['note']} | {today()}: {o['note']}" if r.get("note") else f"{today()}: {o['note']}"
        print(f"{r['company']} - {r['role'][:60]}: live={r['live']} ({r['live_checked']}); status {r.get('status')} unchanged")

    elif cmd == "add":
        if not o.get("company") or not o.get("role"):
            raise SystemExit("add needs --company and --role")
        j = existing(apps, o.get("url"))
        if j is not None:
            merge_into(apps[j], o, at, system)
            print(f"Already logged as #{j}; updated it instead of adding a duplicate: {line(j, apps[j])}")
        else:
            apps.append(new_row(o))
            apply_timing(apps[-1], apps[-1]["status"], at, o.get("status_date"), system)
            print(f"Added {o['company']} - {o['role']} as {apps[-1]['status']}")
        # an add used to leave the scheduled agent's matching lead in the inbox, so it got re-screened.
        if o.get("url"):
            ldata = load_json(LEADS)
            keep = [l for l in ldata["leads"] if existing([l], o["url"]) is None]
            n = len(ldata["leads"]) - len(keep)
            if n:
                ldata["leads"] = keep
                ldata["updated"] = today()
                save_json(LEADS, ldata)
                print(f"Closed {n} matching scheduled-agent lead(s)")

    elif cmd == "promote":
        ldata = load_json(LEADS)
        leads = ldata["leads"]
        i = one(leads, " ".join(rest), "lead")
        lead = leads.pop(i)
        j = existing(apps, lead.get("url"))
        if j is not None:
            merge_into(apps[j], {**o, "note": o.get("note") or f"Scheduled agent lead closed as duplicate ({lead.get('source','')})"},
                       at, system)
            ldata["updated"] = today()
            save_json(LEADS, ldata)
            print(f"Lead already logged as #{j}; closed the lead and updated that row: {line(j, apps[j])}")
        else:
            row = new_row({"company": lead["company"],
                           "role": f"{lead['role']} ({lead.get('where_pay','')})".replace(" ()", ""),
                           "url": lead.get("url"), "verdict": lead.get("verdict"),
                           **{k: v for k, v in o.items() if k != "note"},
                           "note": (f"Scheduled agent {lead.get('source','')}: {lead.get('note','')}"[:500]
                                    + (f" | {today()} Claude: {o['note']}" if o.get("note") else ""))})
            if lead.get("search"):  # carry the scheduled agent's search tag for the scoreboard (adjustments.md)
                row["search"] = lead["search"]
            apply_timing(row, row["status"], at, o.get("status_date"), system)
            apps.append(row)
            ldata["updated"] = today()
            save_json(LEADS, ldata)
            print(f"Moved lead → applications: {row['company']} - {row['role'][:60]} [{row['status']}]")
    elif cmd == "dedupe":
        # Rows sharing one posting URL merge into one.
        # Keeper = furthest-along status (ties: earliest row); the others' notes fold in, then they go.
        rank = {s_: n for n, s_ in enumerate(["offer", "interview", "rejected", "applied", "resume_built",
                                              "deciding", "fit_check", "triaged", "passed", "skipped",
                                              "dropped", "closed"])}
        groups = {}
        for i, r in enumerate(apps):
            if r.get("url") and not bare_url(r["url"]):
                groups.setdefault(url_key(r["url"]), []).append(i)
        drop = set()
        for ix in groups.values():
            if len(ix) < 2:
                continue
            keep = min(ix, key=lambda i: (rank.get(apps[i].get("status"), 99), i))
            k = apps[keep]
            for i in ix:
                if i == keep:
                    continue
                r = apps[i]
                extra = f"Merged dup #{i} ({r.get('company')}, {r.get('status')} {r.get('status_date')})"
                if r.get("note") and r["note"] not in (k.get("note") or ""):
                    extra += f": {r['note'][:300]}"
                k["note"] = f"{k['note']} | {today()}: {extra}" if k.get("note") else f"{today()}: {extra}"
                for f in ("referral", "resume", "ats", "years_req", "confirmed"):
                    if r.get(f) and not k.get(f):
                        k[f] = r[f]
                drop.add(i)
            print(f"keep #{keep} {k['company']} [{k['status']}] · drop " + ", ".join(
                f"#{i} [{apps[i]['status']}]" for i in ix if i != keep))
        if "--dry-run" in o_rest:
            print(f"Dry run: {len(drop)} rows would merge away. Nothing saved.")
            return
        data["applications"] = [r for i, r in enumerate(apps) if i not in drop]
        print(f"Merged away {len(drop)} duplicate rows")
    elif cmd == "archive":
        adata = load_json(ARCHIVE) if ARCHIVE.exists() else {"note": "Closed rows moved out of applications.json "
                                                             "by `log.py archive`. Same schema. Read-only history.",
                                                             "applications": []}
        moved = [r for r in apps if r.get("status") in DONE]
        data["applications"] = [r for r in apps if r.get("status") not in DONE]
        adata["applications"] = moved + adata["applications"]
        adata["updated"] = today()
        save_json(ARCHIVE, adata)
        print(f"Archived {len(moved)} closed rows → data/archive.json ({len(data['applications'])} live rows left)")
    else:
        raise SystemExit(__doc__)

    data["updated"] = today()
    save_json(APPS, data)
    write_seen()
    journal([cmd] + o_rest)  # before any print: a closed pipe (| head) must not skip it
    print("Saved data/applications.json. Run `python scripts/sync.py \"<message>\"` to push.")


JOURNAL = Path(__file__).resolve().parent.parent / ".git" / "log_journal.jsonl"
MUTATING = {"dedupe", "set", "note", "live", "add", "promote", "archive", "sweep-stamp"}


def journal(argv):
    """Remember data-changing calls until sync.py pushes them, so a push conflict can be replayed
    on GitHub's latest data instead of hand-fixed. Lives in .git: never committed."""
    if argv and argv[0] in MUTATING and JOURNAL.parent.exists():
        with open(JOURNAL, "a", newline="\n") as f:
            f.write(json.dumps(argv) + "\n")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    main(sys.argv[1:])
