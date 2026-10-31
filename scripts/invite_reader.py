"""invite_reader.py — read an interview invite (.ics or pasted email text) into a proposed log line.

Usage (from the repo root):
    python scripts/invite_reader.py invite.ics [--company "Acme"]
    python scripts/invite_reader.py email.txt  [--company "Acme"]
    python scripts/invite_reader.py - < email.txt

Prints: date, start/end time, time zone (and the time in your zone), meeting link, organizer, the panelists
sorted by type (recruiter / hiring manager / peer / exec) so prep in 07_Interview_Story_Bank.md matches
who's asking, a schedule-conflict check, and the proposed `log.py set <company> interview ...` line.
Nothing is logged, added to a calendar or sent: Claude shows it to the candidate, the candidate OKs, then Claude runs the
line and adds the event (calendar connector) itself.

Schedule check: if data/busy.json exists ({"weekly": [{"day": "Tue", "start": "17:00", "end": "19:00",
"what": "..."}]}) the invite is checked against it; otherwise it says to check the candidate's recurring
commitments (check your calendar) before accepting.
"""
import re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import LOCAL_TZ, MONTHS, ROOT, load_json  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

TZ = LOCAL_TZ  # your time zone (config.json search.timezone)
_EAST = "America/New_York"
TZ_ABBR = {"et": _EAST, "est": _EAST, "edt": _EAST, "eastern": _EAST, "ct": "America/Chicago", "cst": "America/Chicago",
           "cdt": "America/Chicago", "central": "America/Chicago", "mt": "America/Denver", "mst": "America/Denver",
           "mdt": "America/Denver", "mountain": "America/Denver", "pt": "America/Los_Angeles", "pst": "America/Los_Angeles",
           "pdt": "America/Los_Angeles", "pacific": "America/Los_Angeles", "utc": "UTC", "gmt": "UTC"}
LINK = re.compile(r"https?://[^\s<>\"')]*(?:zoom\.us|teams\.microsoft|teams\.live|meet\.google|webex|chime\.aws|bluejeans|gotomeeting|whereby)[^\s<>\"')]*", re.I)
TYPES = [("recruiter", r"recruit|talent|sourc|people partner|hr\b|human resources|coordinator"),
         ("exec", r"\b(?:chief|ceo|coo|cfo|cro|cto|svp|evp|vp|vice president|head of|general manager|managing director)\b"),
         ("hiring manager", r"hiring manager|\bdirector\b|manager of|\b(?:team|engineering|operations|sales|general|senior) manager\b(?!.*\b(?:program|project|product)\b)"),
         ("peer", r".")]


def tzinfo(name):
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(TZ_ABBR.get(name.lower(), name))
    except Exception:
        return None


def unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text)


def ics_dt(line):
    m = re.match(r"DT(?:START|END)(?:;TZID=([^:;]+))?(?:;[^:]*)?:(\d{8})(?:T(\d{4,6})(Z)?)?", line)
    if not m:
        return None, None
    d, t = m.group(2), (m.group(3) or "0000")[:4]
    dt = datetime.strptime(d + t, "%Y%m%d%H%M")
    if m.group(4):
        return dt.replace(tzinfo=timezone.utc), "UTC"
    tz = tzinfo(m.group(1))
    return (dt.replace(tzinfo=tz) if tz else dt), m.group(1)


def parse_ics(text):
    t = unfold(text)
    ev = {"people": []}
    for line in t.splitlines():
        if line.startswith("DTSTART"):
            ev["start"], ev["tz"] = ics_dt(line)
        elif line.startswith("DTEND"):
            ev["end"], _ = ics_dt(line)
        elif line.startswith("SUMMARY"):
            ev["summary"] = line.split(":", 1)[1].strip()
        elif line.startswith("ORGANIZER"):
            cn = re.search(r"CN=\"?([^\";:]+)", line)
            ev["organizer"] = (cn.group(1) if cn else line.split("mailto:")[-1]).strip()
        elif line.startswith("ATTENDEE"):
            cn = re.search(r"CN=\"?([^\";:]+)", line)
            mail = line.split("mailto:")[-1].strip()
            ev["people"].append((cn.group(1).strip() if cn else mail, ""))
    links = LINK.findall(t.replace("\\n", " ").replace("\\,", ","))
    ev["link"] = links[0] if links else ""
    desc = re.search(r"DESCRIPTION:(.*)", t)
    if desc:
        ev["people"] += people_from_text(desc.group(1).replace("\\n", "\n").replace("\\,", ","))
    return ev


DATE_TXT = re.compile(r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", re.I)
DATE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
TZW = r"\s*\(?\s*([A-Z][A-Za-z]{1,9}|[A-Za-z]+/[A-Za-z_]+)?"
TIME_RANGE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?m\.?)?\s*(?:-|–|to)\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?" + TZW, re.I)
TIME_ONE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?()()()" + TZW, re.I)


def people_from_text(text):
    out = []
    for line in text.splitlines():
        line = line.strip(" -•*\t")
        m = re.match(r"([A-Z][a-z'’\-]+(?: [A-Z][a-z'’\-.]+){1,2})\s*(?:[,–\-|(:]\s*)([A-Za-z][^)\n]{2,70})\)?$", line)
        if m and not re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d", line, re.I):
            out.append((m.group(1), m.group(2).strip()))
    return out


def parse_email(text):
    ev = {"people": people_from_text(text)}
    now = datetime.now()
    d = DATE_TXT.search(text)
    day = None
    if d:
        y = int(d.group(3)) if d.group(3) else now.year
        day = datetime(y, MONTHS[d.group(1).lower()[:3]], int(d.group(2)))
    else:
        n = DATE_NUM.search(text)
        if n:
            y = int(n.group(3)) if n.group(3) else now.year
            day = datetime(y + 2000 if y < 100 else y, int(n.group(1)), int(n.group(2)))
    rest = text[d.end():] if d else text
    tm = TIME_RANGE.search(rest) or TIME_ONE.search(rest) or TIME_RANGE.search(text) or TIME_ONE.search(text)
    tzname = None
    if day and tm:
        ap0 = (tm.group(3) or tm.group(6) or "a").lower()
        h = int(tm.group(1)) % 12 + (12 if ap0 == "p" else 0)
        start = day.replace(hour=h, minute=int(tm.group(2) or 0))
        tzname = tm.group(7) if tm.group(7) and tzinfo(tm.group(7)) else None
        if not tzname:
            z = re.search(r"\b(ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT|Eastern|Central|Mountain|Pacific|UTC|GMT)\b", text)
            tzname = z.group(1) if z else None
        tz = tzinfo(tzname)
        ev["start"] = start.replace(tzinfo=tz) if tz else start
        if tm.group(4):
            ap = (tm.group(6) or ap0).lower()
            eh = int(tm.group(4)) % 12 + (12 if ap == "p" else 0)
            ev["end"] = ev["start"].replace(hour=eh, minute=int(tm.group(5) or 0))
        else:
            dur = re.search(r"(\d{2,3})[- ]?min", text, re.I)
            ev["end"] = ev["start"] + timedelta(minutes=int(dur.group(1)) if dur else 30)
    elif day:
        ev["start"] = day
    ev["tz"] = tzname
    links = LINK.findall(text)
    ev["link"] = links[0] if links else ""
    subj = re.search(r"^subject:\s*(.+)$", text, re.I | re.M)
    ev["summary"] = subj.group(1).strip() if subj else ""
    return ev


def clean_people(people):
    """Drop link lines, merge duplicates (keep the entry that has a title)."""
    out = {}
    for name, title in people:
        if re.search(r"https?://|zoom|teams|meet|webex|join", f"{name} {title}", re.I):
            continue
        if name not in out or (title and not out[name]):
            out[name] = title
    return list(out.items())


def kind(title):
    t = (title or "").lower()
    for k, pat in TYPES:
        if re.search(pat, t):
            return k
    return "peer"


def in_et(dt):
    if dt and dt.tzinfo and ZoneInfo:
        return dt.astimezone(ZoneInfo(TZ))
    return None


def conflicts(start, end):
    busy = ROOT / "data" / "busy.json"
    if not busy.exists():
        return None
    s = in_et(start) or start
    e = in_et(end) or end
    hits = []
    for b in load_json(busy).get("weekly", []):
        if s.strftime("%a").lower()[:3] != b["day"].lower()[:3]:
            continue
        bs = s.replace(hour=int(b["start"][:2]), minute=int(b["start"][3:5]))
        be = s.replace(hour=int(b["end"][:2]), minute=int(b["end"][3:5]))
        if s < be and e > bs:
            hits.append(f"{b['day']} {b['start']}-{b['end']} {b.get('what', '')}")
    return hits


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    company = a[a.index("--company") + 1] if "--company" in a else None
    src = a[0]
    text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8", errors="replace")
    ev = parse_ics(text) if "BEGIN:VCALENDAR" in text else parse_email(text)
    ev["people"] = clean_people(ev["people"])
    st, en = ev.get("start"), ev.get("end")
    print(f"Invite: {ev.get('summary') or '(no subject)'}")
    if st:
        et = in_et(st)
        print(f"  When: {st.strftime('%a %b %d, %Y %I:%M %p')}" + (f"-{en.strftime('%I:%M %p')}" if en else "") +
              f" {ev.get('tz') or '(time zone not stated: confirm)'}" +
              (f"  = {et.strftime('%I:%M %p')} local" if et and ev.get("tz") not in (None, TZ) and not str(ev.get('tz')).lower().startswith(('et', 'est', 'edt', 'eastern')) else ""))
    else:
        print("  When: not found: read the invite and confirm the date/time")
    print(f"  Link: {ev.get('link') or 'none found (phone? on-site? confirm)'}")
    if ev.get("organizer"):
        print(f"  Organizer: {ev['organizer']}")
    if ev["people"]:
        print("  Panel (type guessed from title, for 07 prep):")
        seen = set()
        for k in ("recruiter", "hiring manager", "exec", "peer"):
            for name, title in ev["people"]:
                if name in seen or kind(title) != k:
                    continue
                seen.add(name)
                print(f"    {k:15} {name}" + (f", {title}" if title else ""))
    if st and en:
        c = conflicts(st, en)
        if c is None:
            print("  Schedule: check the candidate's recurring commitments (check your calendar) before accepting.")
        else:
            print("  Schedule: " + ("CONFLICT with " + "; ".join(c) if c else "no conflict with data/busy.json"))
    at = (in_et(st) or st).strftime("%Y-%m-%d %H:%M") if st else ""
    who = "; ".join(f"{n} ({kind(t)})" for n, t in ev["people"])[:200]
    when_txt = ((in_et(st) or st).strftime('%b %d %I:%M %p') + (' local' if in_et(st) or ev.get('tz') else ' (tz?)')) if st else '(time TBD)'
    note = f"Interview {when_txt}" + (f"; panel: {who}" if who else "") + \
           ("; link in invite" if ev.get("link") else "")
    print("\nProposed (run only after the candidate OKs):")
    print(f'  python scripts/log.py set "{company or "<company role>"}" interview --note "{note}"' + (f' --at "{at}"' if at else ""))
    print("  + add the event to the calendar (connector). Nothing has been logged or sent.")
