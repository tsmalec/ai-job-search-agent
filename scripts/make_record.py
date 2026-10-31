"""make_record.py — write the records/05_ file for an application from what was actually built.

Usage (from the repo root):
    python scripts/ats_check.py OUT.docx jd.json > ats.txt
    python scripts/make_record.py content.json --company "Acme" --role "Senior Project Manager, Business Transformation" \
        --url https://... --verdict green --ats ats.txt --file Alex_Example_Acme.docx [--applied 2026-01-15] \
        [--gaps "Six Sigma"] [--notes "Interview: Story 3"]

What it writes (records/05_Approved_Resume_<Company>_<Role>.md):
date and posting, ATS summary lines, pages, summary opener and each bullet with its closest B item ID
(matched by wording; marked ≈ so you know it's a best guess), the exact wording sent, skills line, gaps.
content.json carries "summary_id" and per-job "bullet_ids" (guard.py requires them), so IDs are exact.
≈ guessing is only a fallback for old content files. --verdict defaults to the verdict already logged
in applications.json for this posting (matched by --url, else company + role).

JD archive: pass --jd jd.txt (the fetch_jd.py file or pasted posting text) and the full
posting is saved to jds/<company>_<role>_<posting-id>.txt with the URL and date on top, linked from the
record ("JD archive" line) and from the applications.json row (`jd` field), so interview prep can read it
after the live posting is gone. Never overwrites a different saved JD (a dated copy is written instead).
Archive only, no record (backfill):  python scripts/make_record.py --jd-only jd.txt --company X --role Y --url U
"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import APPS, RECORDS, ROOT, all_apps, b_items, job_ids, load_json, norm, save_json, today  # noqa: E402

JDS = ROOT / "jds"

STOP = set("a an and the of to for in on with by as at from into its their his her our was were is are be "
           "that this these those over under vs via per each every all than then who which".split())


def words(s):
    return {w for w in norm(s).split() if w not in STOP and len(w) > 2}


def best_ids(text, items, prefix=None, n=2):
    tw = words(text)
    scored = []
    for it in items:
        if prefix and not it["id"].startswith(prefix):
            continue
        if not prefix and it["id"].startswith("SUM"):
            continue
        iw = words(" ".join([it["full"], it["short"], it["facts"]]))
        if not iw:
            continue
        score = len(tw & iw) / (len(tw) or 1)
        scored.append((score, it["id"]))
    scored.sort(reverse=True)
    picks = [i for s, i in scored[:n] if s >= 0.35]
    if len(picks) == 2 and scored[1][0] < scored[0][0] * 0.75:
        picks = picks[:1]
    return picks


def logged_verdict(o):
    ids = job_ids(o.get("url", ""))
    for r in all_apps():
        if ids and ids & (job_ids(r.get("url") or "") | job_ids(r.get("role") or "")):
            return r.get("verdict")
    want = norm(f"{o.get('company','')} {o.get('role','')}").split()
    for r in all_apps():
        if want and all(w in norm(f"{r.get('company','')} {r.get('role','')}") for w in want):
            return r.get("verdict")
    return None


def ats_lines(path):
    if not path:
        return []
    keep = ("Parse:", "Est. grade", "Title:", "Keywords", "  Required missing", "  Preferred missing", "  Missing:")
    return [l.rstrip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.startswith(keep)]


def slug(s):
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", s)).strip("_")[:50]


def archive_jd(src, company, role, url=""):
    """Save the posting text under jds/ and link it from the matching applications.json row. Returns the path."""
    text = Path(src).read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise SystemExit(f"{src} is empty; nothing archived.")
    ids = sorted(job_ids(url) | job_ids(role))
    name = f"{slug(company)}_{slug(role)[:40]}" + (f"_{ids[0]}" if ids else "")
    JDS.mkdir(exist_ok=True)
    out = JDS / f"{name}.txt"
    body = f"URL: {url or 'n/a'}\nSAVED: {today()}\nCOMPANY: {company}\nROLE: {role}\n\n{text}\n"
    if out.exists():
        old = out.read_text(encoding="utf-8")
        if old.split("\n\n", 1)[-1].strip() == text:
            body = None
        else:
            out = JDS / f"{name}_{today()}.txt"
    if body:
        out.write_text(body, encoding="utf-8", newline="\n")
    rel = out.relative_to(ROOT).as_posix()
    data = load_json(APPS)
    want_ids = job_ids(url)
    hits = [r for r in data["applications"] if want_ids and want_ids & job_ids(r.get("url") or "")]
    if not hits:
        w = norm(f"{company} {role}").split()
        hits = [r for r in data["applications"] if w and all(x in norm(f"{r.get('company','')} {r.get('role','')}") for x in w)]
    if len(hits) == 1:
        if hits[0].get("jd") != rel:
            hits[0]["jd"] = rel
            save_json(APPS, data)
        print(f"JD archived: {rel} (linked from the {hits[0]['company']} row)")
    else:
        print(f"JD archived: {rel} ({len(hits)} rows matched, so no row link was set; add it with the row's URL)")
    return rel


def main(argv):
    if argv and argv[0] == "--jd-only":
        o = {argv[i][2:]: argv[i + 1] for i in range(2, len(argv) - 1) if argv[i].startswith("--")}
        if len(argv) < 2 or "company" not in o or "role" not in o:
            raise SystemExit(__doc__)
        archive_jd(argv[1], o["company"], o["role"], o.get("url", ""))
        return
    if not argv or argv[0].startswith("--"):
        raise SystemExit(__doc__)
    o = {}
    i = 1
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            o[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    for need in ("company", "role"):
        if need not in o:
            raise SystemExit(f"--{need} is required")
    c = load_json(argv[0])
    items = b_items()
    applied = o.get("applied")
    lines = [f"# Record — {o['company']}, {o['role']} ({applied or today()})", ""]
    lines.append(f"**Applied {applied}** (final resume)." if applied else "*Built, not yet applied.*")
    lines += ["", ("*Short record of what was sent (not a starting point; rebuild from `B`). Written by make_record.py; "
              "IDs come from content.json; any ≈ ID is a best guess by wording.*"), ""]
    lines.append(f"- **Posting:** {o.get('url', 'n/a')}")
    if o.get("jd"):
        lines.append(f"- **JD archive:** `{archive_jd(o['jd'], o['company'], o['role'], o.get('url', ''))}`")
    ats = ats_lines(o.get("ats"))
    verdict = o.get("verdict") or logged_verdict(o) or "?"
    lines.append(f"- **Verdict:** {verdict}" + (" · " + " · ".join(l.strip() for l in ats if not l.startswith("  ")) if ats else ""))
    misses = [l.strip() for l in ats if l.startswith("  ")]
    if misses:
        lines.append("- **ATS misses:** " + " | ".join(misses))
    if o.get("file"):
        pages = ""
        try:
            from build_resume import page_count  # needs LibreOffice
            pages = f", {page_count(o['file'])[0]} page(s)"
        except Exception:
            pass  # best effort: this step is optional, carry on without it
        lines.append(f"- **File:** `{Path(o['file']).name}`{pages}")
    sid = [c["summary_id"]] if c.get("summary_id") else ["≈" + x for x in best_ids(c.get("summary", ""), items, prefix="SUM", n=1)]
    lines += [f"- **Summary:** {sid[0] if sid else 'one-off'} adapted. Exact text:", "", "> " + c.get("summary", ""), ""]
    lines.append("## Bullets in order")
    for job in c.get("jobs", []):
        bl = job.get("bullets") or []
        if not bl:
            lines.append(f"**{job['title']} | {job.get('dates','')}:** title-only")
            continue
        lines.append(f"**{job['title']} | {job.get('dates','')}**")
        given = job.get("bullet_ids") or []
        for k, b in enumerate(bl, 1):
            ids = [given[k - 1]] if k <= len(given) and given[k - 1] else ["≈" + x for x in best_ids(b, items)] or ["new wording"]
            lines.append(f"{k}. {' + '.join(ids)}: \"{b}\"")
    for ex in c.get("extra_sections", []):
        lines.append(f"**{ex.get('heading','')}:** {ex.get('text','')}")
    lines += ["", "## Skills line", c.get("skills", ""), ""]
    if o.get("gaps"):
        lines += ["## Gaps left off", o["gaps"], ""]
    if o.get("notes"):
        lines += ["## Interview notes", o["notes"], ""]
    RECORDS.mkdir(exist_ok=True)
    out = RECORDS / f"05_Approved_Resume_{slug(o['company'])}_{slug(o['role'])}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {out.relative_to(RECORDS.parent)}")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    main(sys.argv[1:])
