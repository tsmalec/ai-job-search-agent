"""dashboard.py — one self-contained HTML page: pipeline, follow-ups due, closing soon.

Usage (from the repo root):
    python scripts/dashboard.py out.html            standalone HTML page
    python scripts/dashboard.py --json DIR          row files for the Dashboard artifact (summary/pipeline/followups/closing)

Built from the same data as scoreboard.py / followups.py / closing_soon.py / calibrate.py; nothing is changed.
Rebuild on request or in the weekly chore. Light and dark themes via CSS variables; no external files.
"""
import html, sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import APPS, LEADS, all_apps, load_json  # noqa: E402
from closing_soon import scan  # noqa: E402

PIPE = [("triaged", "Triaged, not started"), ("deciding", "Deciding"), ("resume_built", "Resume built"),
        ("applied", "Applied, waiting"), ("interview", "Interview"), ("offer", "Offer"), ("rejected", "Rejected")]


def e(s):
    return html.escape(str(s or ""))


def age(d):
    try:
        return (date.today() - date.fromisoformat((d or "")[:10])).days
    except ValueError:
        return None


def link(r):
    name = f"{e(r.get('company'))} — {e((r.get('role') or '')[:70])}"
    url = str(r.get("url") or "")
    ok = url.lower().startswith(("http://", "https://"))  # no javascript:/data: links from untrusted lead rows
    return f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{name}</a>' if ok else name


def build():
    live = load_json(APPS)["applications"]
    every = all_apps()
    st = Counter(r.get("status") for r in every)
    applied_ever = sum(1 for r in every if r.get("status") in ("applied", "interview", "offer", "rejected"))
    today = date.today().isoformat()
    applied_today = sum(1 for r in live if ((r.get("timing") or {}).get("applied_at") or "")[:10] == today)
    leads = 0
    try:
        leads = sum(1 for l in load_json(LEADS).get("leads", []) if l.get("status") == "not_triaged")
    except Exception:
        pass  # best effort: this step is optional, carry on without it
    # follow-ups (same bands as followups.py)
    iv = [(age(r.get("status_date")), r) for r in live if r.get("status") == "interview"]
    quiet = sorted([(age(r.get("status_date")), r) for r in live if r.get("status") == "applied" and r.get("live") != "closed"
                    and (age(r.get("status_date")) or 0) >= 7], key=lambda x: -x[0])
    soon, past, unparsed, _ = scan(14, live)

    tiles = [("Applied (all time)", applied_ever), ("Open applications", st["applied"] + st["interview"]),
             ("Interviews", st["interview"]), ("Offers", st["offer"]), ("Applied today", applied_today),
             ("Leads to triage", leads)]
    tile_html = "".join(f'<div class="tile"><div class="n">{e(n)}</div><div class="l">{e(l)}</div></div>' for l, n in tiles)
    mx = max([st[k] for k, _ in PIPE] + [1])
    bars = "".join(
        f'<div class="row"><span class="lab">{e(lbl)}</span><span class="bar"><i style="width:{100 * st[k] / mx:.1f}%"></i></span>'
        f'<span class="cnt">{st[k]}</span></div>' for k, lbl in PIPE)

    def table(rows, cols):
        if not rows:
            return '<p class="empty">Nothing here.</p>'
        head = "".join(f"<th>{c}</th>" for c in cols)
        return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'

    band = lambda d: "30+ days: likely dead" if d >= 30 else ("14-29 days: second touch or let go" if d >= 14 else "7-13 days: light follow-up")
    fu_rows = [f"<tr><td>{d}d</td><td>{link(r)}</td><td>interview, no update</td></tr>" for d, r in iv if d is not None and d >= 5]
    fu_rows += [f"<tr><td>{d}d</td><td>{link(r)}{' · referral: ' + e(r['referral']) if r.get('referral') else ''}</td><td>{band(d)}</td></tr>"
                for d, r in quiet]
    cs_rows = [f"<tr><td>{d.strftime('%a %b %d')}</td><td>{'today' if n == 0 else f'{n}d'}</td><td>{link(r)}</td><td>{e(r.get('status'))}</td></tr>"
               for n, d, r in soon]
    cs_rows += [f"<tr class='muted'><td>{d.strftime('%b %d')}</td><td>passed</td><td>{link(r)}</td><td>{e(r.get('status'))}</td></tr>"
                for n, d, r in past]
    stamp = datetime.now().strftime("%b %d, %Y %I:%M %p")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Search Dashboard</title>
<style>
:root{{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e4e3de;--acc:#2f6f5e;--accs:#d7e9e3}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151614;--card:#1f201e;--ink:#ecebe6;--mute:#9a9992;--line:#30312e;--acc:#6fc1a6;--accs:#26392f}}}}
:root[data-theme="dark"]{{--bg:#151614;--card:#1f201e;--ink:#ecebe6;--mute:#9a9992;--line:#30312e;--acc:#6fc1a6;--accs:#26392f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:24px 16px 48px}}h1{{font-size:22px;margin:0 0 4px}}.sub{{color:var(--mute);margin:0 0 20px}}
h2{{font-size:16px;margin:28px 0 10px}}.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}.n{{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}}.l{{color:var(--mute);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;overflow-x:auto}}
.row{{display:grid;grid-template-columns:160px 1fr 48px;gap:10px;align-items:center;padding:4px 0}}.lab{{color:var(--mute);font-size:14px}}
.bar{{background:var(--accs);border-radius:4px;height:12px;overflow:hidden}}.bar i{{display:block;height:100%;background:var(--acc)}}.cnt{{text-align:right;font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--mute);font-weight:500}}
a{{color:var(--acc)}}.muted td{{color:var(--mute)}}.empty{{color:var(--mute);margin:4px 0}}
@media (max-width:560px){{.row{{grid-template-columns:110px 1fr 40px}}}}
</style></head><body><main>
<h1>Job Search Dashboard</h1><p class="sub">Built {e(stamp)} from the job-search repo. Read-only snapshot; ask Claude to rebuild.</p>
<div class="tiles">{tile_html}</div>
<h2>Pipeline by status</h2><div class="card">{bars}</div>
<h2>Follow-ups due ({len(fu_rows)})</h2><div class="card">{table(fu_rows, ["Quiet", "Application", "Suggested"])}</div>
<h2>Closing within 14 days ({len(soon)})</h2><div class="card">{table(cs_rows, ["Closes", "Left", "Posting", "Status"])}
{f'<p class="empty">{len(unparsed)} open rows mention a close date that could not be read.</p>' if unparsed else ''}</div>
</main></body></html>
"""


def export_json(outdir):
    """Row files for the Dashboard artifact: summary, pipeline, followups, closing."""
    import json
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    live = load_json(APPS)["applications"]
    every = all_apps()
    st = Counter(r.get("status") for r in every)
    today = date.today().isoformat()
    leads = 0
    try:
        leads = sum(1 for l in load_json(LEADS).get("leads", []) if l.get("status") == "not_triaged")
    except Exception:
        pass  # best effort: this step is optional, carry on without it
    summary = [
        {"metric": "Applied (all time)", "value": sum(1 for r in every if r.get("status") in ("applied", "interview", "offer", "rejected"))},
        {"metric": "Open applications", "value": st["applied"] + st["interview"]},
        {"metric": "Interviews", "value": st["interview"]},
        {"metric": "Offers", "value": st["offer"]},
        {"metric": "Applied today", "value": sum(1 for r in live if ((r.get("timing") or {}).get("applied_at") or "")[:10] == today)},
        {"metric": "Leads to triage", "value": leads}]
    pipeline = [{"stage": lbl, "count": st[k]} for k, lbl in PIPE]
    band = lambda d: "30+ days: likely dead" if d >= 30 else ("14-29 days: second touch or let go" if d >= 14 else "7-13 days: light follow-up")
    fu = []
    for r in live:
        d = age(r.get("status_date"))
        if d is None:
            continue
        if r.get("status") == "interview" and d >= 5:
            fu.append((d, r, "interview, no update"))
        elif r.get("status") == "applied" and r.get("live") != "closed" and d >= 7:
            fu.append((d, r, band(d)))
    followups = [{"id": str(r.get("id", i)), "company": r.get("company") or "", "role": (r.get("role") or "")[:80],
                  "days_quiet": d, "action": a, "referral": r.get("referral") or "", "url": r.get("url") or ""}
                 for i, (d, r, a) in enumerate(sorted(fu, key=lambda x: -x[0]))]
    soon, past, _, _ = scan(14, live)
    closing = [{"id": str(r.get("id", i)), "close_date": d.isoformat(), "days_left": n, "company": r.get("company") or "",
                "role": (r.get("role") or "")[:80], "status": r.get("status") or "", "url": r.get("url") or ""}
               for i, (n, d, r) in enumerate(past + soon)]
    for name, rows in (("summary", summary), ("pipeline", pipeline), ("followups", followups), ("closing", closing)):
        (out / f"{name}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8", newline="\n")
    print(f"Wrote 4 files to {out}")



if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    if sys.argv[1:2] == ["--json"]:
        export_json(sys.argv[2] if len(sys.argv) > 2 else "work/dashboard")
        raise SystemExit
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1])
    out.write_text(build(), encoding="utf-8", newline="\n")
    print(f"Wrote {out}")
