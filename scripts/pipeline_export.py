"""pipeline_export.py — one row per lead/application, for the Pipeline dashboard artifact (read-only).

Usage (from the repo root):
    python scripts/pipeline_export.py [--csv <path>] [--json <path>]

No AI judging and no new rules: reuses followups.py's age() and closing_soon.py's find_close()/
jd_text() — the same checks the terminal commands already run. Writes work/pipeline_table.csv by
default (header row always present, even with zero data rows, so an empty pipeline still has known
columns for the dashboard to read). Nothing in data/*.json is changed.
"""
import csv
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import APPS, LEADS, ROOT, all_apps, load_archive, load_json  # noqa: E402
from followups import age  # noqa: E402
from closing_soon import find_close, jd_text, OPEN as CLOSING_OPEN  # noqa: E402

TRIAGED_STATUSES = {"triaged", "fit_check", "deciding", "resume_built"}
OUTCOME_STATUSES = {"interview", "offer", "rejected", "skipped", "passed", "dropped", "closed"}

COLS = ["row_id", "company", "role", "url", "stage", "status", "status_date", "age_days", "live",
        "fit", "fit_prelim", "closing_date", "closing_days", "followup_band", "note", "jd_read",
        "pay", "req_covered", "req_total", "pref_covered", "pref_total", "scoreboard"]
SEARCH_START = ROOT / "data" / "search_start.json"  # {"start": "YYYY-MM-DD"}: day one of the search


def stage_of(status):
    if status == "applied":
        return "applied"
    if status in TRIAGED_STATUSES:
        return "triaged"
    if status in OUTCOME_STATUSES:
        return "outcome"
    return "other"


def followup_band(a, age_days):
    """Mirrors followups.py's own bands; nothing new judged here."""
    if a.get("status") == "interview":
        return "interview-stale" if (age_days or 0) >= 5 else ""
    if a.get("status") != "applied":
        return ""
    if a.get("live") == "closed":
        return "closed-no-nudge"
    if str(a.get("next_step") or "").startswith("No follow-up"):
        return ""
    if age_days is None:
        return ""
    if age_days >= 30:
        return "30+d"
    if age_days >= 14:
        return "14-29d"
    if age_days >= 7:
        return "7-13d"
    return ""


def closing_info(a):
    """Mirrors closing_soon.py's scan(): note first, then the archived JD text."""
    d, cue = find_close(a.get("note") or "")
    if not d:
        d2, cue2 = find_close(jd_text(a))
        d, cue = d2, (cue or cue2)
    if not d:
        return "", ""
    return d.isoformat(), str((d - date.today()).days)


_WORK_BY_URL = None


def posting_text(a):
    """Saved posting text for an application row: its committed jd path, else this chat's work/ capture
    whose URL header matches the row's url. '' when none (then no req/pref counts, never a guess)."""
    global _WORK_BY_URL
    from common import read_jd, url_key
    p = a.get("jd")
    if p and (ROOT / p).exists():
        return read_jd(ROOT / p)[1]
    if _WORK_BY_URL is None:
        _WORK_BY_URL = {}
        for f in (ROOT / "work").glob("*.txt") if (ROOT / "work").exists() else []:
            try:
                head, text = read_jd(f)
            except (OSError, UnicodeDecodeError):
                continue
            if head.get("url") and text.strip():
                _WORK_BY_URL[url_key(head["url"])] = text
    return _WORK_BY_URL.get(url_key(a.get("url") or ""), "")


def req_pref(a):
    """Rough lexical req/pref coverage (screen_leads.rough_req_pref); four empty strings when no posting text."""
    blank = {"req_covered": "", "req_total": "", "pref_covered": "", "pref_total": ""}
    text = posting_text(a)
    if not text:
        return blank
    try:
        from screen_leads import rough_req_pref
        req, pref = rough_req_pref(text)
    except Exception:  # a matcher problem must never break the export
        return blank
    return {"req_covered": req[0] if req else "", "req_total": req[1] if req else "",
            "pref_covered": pref[0] if pref else "", "pref_total": pref[1] if pref else ""}


_PAY_RE = __import__("re").compile(r"\$\s?[\d][\d,\.]*\s?[Kk]?(?:\s?(?:-|–|—|to)\s?\$?\s?[\d][\d,\.]*\s?[Kk]?)?")


def pay_of(a):
    """Pay as the row's own text shows it: the role's trailing '(... · $lo–$hi)' card first, then a 'Pay:' note
    from the screen. 'not listed' when the role says so and nothing else has a figure; '' otherwise. Nothing judged."""
    role, note = a.get("role") or "", a.get("note") or ""
    m = _PAY_RE.search(role)
    if m:
        return m.group(0).strip()
    n = __import__("re").search(r"Pay:\s*([^|]{0,60})", note)
    if n:
        m2 = _PAY_RE.search(n.group(1))
        if m2:
            return m2.group(0).strip()
    return "not listed" if "pay not listed" in role.lower() else ""


def role_clean(role):
    """The role as the dashboard shows it: log.py appends '(location · pay)' to the title, and pay has its own
    column, so drop the pay (and any 'posted <date>') pieces from that trailing parenthetical. Location stays."""
    import re
    r = str(role or "")
    r = re.sub(r"\s*·\s*(?:pay not listed|posted \d{4}-\d{2}-\d{2}|[^·()]*\$[^·()]*)", "", r)
    r = re.sub(r"\s*\((?:pay not listed)\)", "", r)
    # Older rows use '(source id; location; $pay; via <source>)': keep only the location-like pieces.
    m = re.search(r"\s*\(([^()]*;[^()]*)\)\s*$", r)
    if m:
        noise = re.compile(r"\$|pay|via |#|\b(?:linkedin|greenhouse|workday|lever|ashby|taleo|icims|pid|req|jr|r)\s*\d|posted|\d{6,}|bonus|equity|travel|band", re.I)
        keep = [p.strip() for p in m.group(1).split(";") if p.strip() and not noise.search(p)]
        r = r[:m.start()] + (f" ({'; '.join(keep)})" if keep else "")
    r = re.sub(r"\s*\(\s*\$[^()]*\)", "", r)  # a parenthetical that is only pay
    r = re.sub(r";\s*\$[^;()]*", "", r)         # a '; $pay' piece left inside nested parens
    r = re.sub(r"\s*\(\s*\)", "", r)
    return r.strip()


def latest_first(note):
    """Notes pile up as 'oldest | newer | newest'; the table shows two lines, so put the newest first."""
    parts = [p.strip() for p in (note or "").split(" | ") if p.strip()]
    return " | ".join(reversed(parts))[:600]


def row_for_app(a):
    verdict = a.get("verdict") or ""
    prelim = verdict.endswith("?")
    fit = verdict[:-1] if prelim else verdict
    d = age(a.get("status_date") or "")
    c_date, c_days = ("", "")
    if a.get("status") in CLOSING_OPEN and a.get("live") != "closed":
        c_date, c_days = closing_info(a)
    return {
        "company": a.get("company", ""), "role": role_clean(a.get("role", "")), "url": a.get("url", ""),
        "stage": stage_of(a.get("status")), "status": a.get("status", ""),
        "status_date": a.get("status_date", ""), "age_days": d if d is not None else "",
        "live": a.get("live", ""), "fit": fit, "fit_prelim": "true" if prelim else "false",
        "closing_date": c_date, "closing_days": c_days,
        "followup_band": followup_band(a, d), "note": latest_first(a.get("note")),
        "jd_read": a.get("jd_read", ""),
        "pay": pay_of(a),
        **(req_pref(a) if stage_of(a.get("status")) in ("triaged", "applied") else
           {"req_covered": "", "req_total": "", "pref_covered": "", "pref_total": ""}),
    }


def row_for_lead(l):
    return {
        "company": l.get("company", ""), "role": role_clean(l.get("role", "")), "url": l.get("url", ""),
        "stage": "leads", "status": l.get("status", ""), "status_date": "", "age_days": "",
        "live": "", "fit": "", "fit_prelim": "false", "closing_date": "", "closing_days": "",
        "followup_band": "", "note": latest_first(l.get("note")), "jd_read": "",
        "pay": pay_of(l),
        "req_covered": "", "req_total": "", "pref_covered": "", "pref_total": "",
    }


def build_rows():
    apps = load_json(APPS).get("applications", []) if APPS.exists() else []
    leads = [l for l in load_json(LEADS).get("leads", []) if l.get("status") == "not_triaged"] if LEADS.exists() else []
    lead_rows = [row_for_lead(l) for l in leads]
    for i, r in enumerate(lead_rows):
        r["row_id"] = f"L{i}"  # leads.json position; leads aren't in applications.json yet
    app_rows = [row_for_app(a) for a in apps]
    for i, r in enumerate(app_rows):
        r["row_id"] = str(i)  # = index in applications.json, so `log.py set <row_id> skipped` hits this row
    return lead_rows + app_rows


def scoreboard_row(today=None):
    """One extra row (stage "scoreboard") with the headline numbers scoreboard.py prints: days and week since the
    search started, applied, rejected, interviews, offers and open (totals and today's), the pace, and what is left to start."""
    today = today or date.today()
    apps = all_apps()
    ever = [a for a in apps if a.get("status") in ("applied", "rejected", "interview", "offer")]
    iso = today.isoformat()

    def applied_today(a):
        t = (a.get("timing") or {}).get("applied_at") or ""
        return t[:10] == iso or (a.get("status") == "applied" and a.get("status_date") == iso)

    rej_today = [a.get("company", "") for a in apps if a.get("status") == "rejected" and a.get("status_date") == iso]
    int_today = [a.get("company", "") for a in apps if a.get("status") == "interview" and a.get("status_date") == iso]
    not_started = [a.get("company", "") for a in apps if a.get("status") in ("triaged", "deciding")]
    sb = {"as_of": iso, "applied": len(ever),
          "rejected": sum(1 for a in apps if a.get("status") == "rejected"),
          "interviews": sum(1 for a in apps if a.get("status") == "interview"),
          "offers": sum(1 for a in apps if a.get("status") == "offer"),
          "open": sum(1 for a in apps if a.get("status") in ("applied", "interview")),
          "today_applied": sum(1 for a in apps if applied_today(a)),
          "today_rejected": len(rej_today), "today_rejected_names": rej_today,
          "today_interviews": len(int_today), "today_interviews_names": int_today,
          "left_to_start": len(not_started), "left_to_start_names": not_started[:5]}
    try:
        start = date.fromisoformat(load_json(SEARCH_START)["start"])
        days = (today - start).days
        if days >= 0:
            sb.update(start=start.isoformat(), days=days, week=days // 7 + 1)
            if days > 0:
                sb["pace"] = round(len(ever) / days, 1)
    except (OSError, KeyError, ValueError):
        pass  # no start date on file: the totals still show
    return {"row_id": "SCOREBOARD", "stage": "scoreboard", "status_date": iso, "scoreboard": json.dumps(sb)}


def stage_counts(rows):
    c = Counter(r["stage"] for r in rows)
    archived = len(load_archive())
    return {"leads": c.get("leads", 0), "triaged": c.get("triaged", 0), "applied": c.get("applied", 0),
            "outcome": c.get("outcome", 0), "other": c.get("other", 0), "archived": archived}


def write_csv(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def write_json(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


if __name__ == "__main__":
    args = sys.argv[1:]
    if any(a in ("-h", "--help") for a in args):  # never run on --help
        print((__doc__ or "").strip())
        sys.exit(0)
    rows = build_rows()
    rows.append(scoreboard_row())
    csv_path = Path(args[args.index("--csv") + 1]) if "--csv" in args else ROOT / "work" / "pipeline_table.csv"
    write_csv(rows, csv_path)
    print(f"Wrote {csv_path} ({len(rows)} rows)")
    if "--json" in args:
        i = args.index("--json")
        has_path = i + 1 < len(args) and not args[i + 1].startswith("--")
        json_path = Path(args[i + 1]) if has_path else ROOT / "work" / "pipeline_table.json"
        write_json(rows, json_path)
        print(f"Wrote {json_path} ({len(rows)} rows)")
    print("Counts:", stage_counts(rows))
