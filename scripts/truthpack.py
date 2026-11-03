"""truthpack.py — pack the final resume next to its B sources for a fresh-eyes truth check.

Usage (from the repo root):
    python scripts/truthpack.py content.json [--out review.md]
    (one-page builds: pass content_1page.json, the version that actually went into the .docx)

Writes review.md: reviewer instructions, then every line on the resume (summary, each bullet, the
skills line) paired with the B text it claims to come from: full, short, facts, plus any
interview-only / context-only notes, labeled as NOT ALLOWED ON PAPER so the reviewer can catch hints.
"new" lines get the two B items that share the most words, as leads to check against.
Skills terms that appear nowhere in B are pre-flagged.
review.md also carries B's header block (roles, title forms, years wording, core identity,
education, certifications), the skills
vocabulary, B's "Not for paper" list, every facts*/full-N/section variant of an item, and the standard
summary opener wherever a SUM item abbreviates it with "…". Merged IDs ("SUM-01+SUM-02", "LEAD-01+LEAD-02")
are paired with every component. extra_sections take an "id" (e.g. {"heading": ..., "text": ..., "id": "OPS-01"});
without one, the section is matched to the B item whose section text it carries.

A separate agent (one that never saw the JD or the build) reads only review.md and answers
PASS / FLAG per line. guard.py checks wording; this checks claims: years, scope, authority, results.
Standard library only.
"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import b_items, load_json, norm, stem_hit  # noqa: E402

PROMPT = """# Truth check — fresh eyes

You are checking a finished resume against its only allowed source, the candidate's master catalog (B).
You have NOT seen the job posting and that's on purpose: judge truth only, not fit or style.

For every numbered line below, compare RESUME against B and answer one line each:
`#n PASS` or `#n FLAG — <what overreaches> → <fix using only B's words>`

Flag when the resume line:
1. States a number, year count, dollar figure, headcount or result B doesn't support for that claim.
2. Raises scope or authority past B: "directed", "managed", "owned", "approved", "decided", titles,
   or "led" where B says the candidate informed, supported, built reporting for, or coordinated.
3. Moves a fact to the wrong context (a result from one role or program credited to another;
   a total-career year count attached to leadership or programs when B gives a smaller leadership number).
4. Uses or hints at anything under NOT ALLOWED ON PAPER (interview-only or context-only), even reworded.
5. Names a tool, method, certification or domain B doesn't show the candidate using.
6. Is marked "new" and nothing in B backs it up.
7. Merges two B items so a metric reads as caused by the other item's work (a '+' bullet must keep each
   number attached to its own item's work: e.g. training work + hours saved by automation must
   not read as "training saved those hours").

The "B header rules", "B skills vocabulary" and every item block below all count as B: a fact or label
that appears there is supported (e.g. growth figures in the core identity, the approved title forms,
an in-progress certification line, approved skills labels). "B NOT FOR PAPER" is the opposite: never allowed on the resume.

Do not flag JD-style rewording, reordering, or shortening when the claim itself stays inside B.
End with one line: `TOTAL: <lines checked> checked, <n> flagged`.
"""


def words(s):
    return {w for w in norm(s).split() if len(w) > 3}


OPENER = ""  # SUM-01's opening sentence: what SUM items abbreviate with "…"


def b_block(it):
    out = [f"  **{it['id']} · {it['name']}**"]
    for k in ("full", "short"):
        if it[k].strip():
            out.append(f"  - {k}: {it[k].strip()}")
    for k, v in it.get("extra", []):
        if not k.startswith(("interview", "context", "facts")):
            out.append(f"  - {k}: {v}")
    if it["id"].startswith("SUM") and "…" in it["full"] and OPENER:
        out.append(f"  - (the \"…\" stands for the standard opener, also approved: {OPENER})")
    if it["facts"].strip():
        out.append(f"  - facts (all facts lines): {it['facts'].strip()}")
    for k in ("interview", "context"):
        if it[k].strip():
            out.append(f"  - NOT ALLOWED ON PAPER ({k}): {it[k].strip()}")
    for k, v in it.get("extra", []):
        if k.startswith(("interview", "context")):
            out.append(f"  - NOT ALLOWED ON PAPER ({k}): {v}")
    return "\n".join(out)


def item_text(it):
    return " ".join([it["full"], it["short"], it["facts"]] + [v for k, v in it.get("extra", [])
                                                            if not k.startswith(("interview", "context"))])


def nearest(text, items, n=2):
    w = words(text)
    return sorted(items, key=lambda it: -len(w & words(item_text(it))))[:n]


def b_sections():
    """(header rules, skills vocabulary, not-for-paper) as text blocks from B."""
    from common import B_FILE
    lines = B_FILE.read_text(encoding="utf-8").splitlines()
    header, vocab, nfp, sec = [], [], [], "intro"
    for line in lines:
        if line.startswith("**Roles"):
            sec = "header"
        if line.startswith("## "):
            sec = ("vocab" if line.startswith("## Skills vocabulary") else
                   "nfp" if line.startswith("## Not for paper") else "aisle")
            continue
        if line.startswith("### "):
            sec = "aisle"
        if line.strip() == "---":
            continue
        {"header": header, "vocab": vocab, "nfp": nfp}.get(sec, []).append(line)
    clean = lambda ls: "\n".join(ls).strip()
    return clean(header), clean(vocab), clean(nfp)


LABEL_RX = re.compile(r'\bskills?\s+(?:label|line)s?\s*(?:\([^)]*\)\s*)?[:,]?\s*["“]([^"”]{3,80})["”]'
                      r'(?:\s*(?:and|,)\s*["“]([^"”]{3,80})["”])?(?P<tail>[^.;]{0,25})', re.I)


def item_labels(items):
    """Skill labels approved inside one item's facts (e.g. 'Skill label "Contract Management (ToolX)" OK')
    would otherwise be invisible to the truth agent. Returns [(label, id)].
    Only a quoted label right after 'skill(s) label(s)/line' counts, and never one followed by 'never'/'not'."""
    out = []
    for it in items:
        for m in LABEL_RX.finditer(it.get("facts", "")):
            if re.search(r"\bnever\b|\bnot\b|\bdon't\b", m.group("tail"), re.I):
                continue
            for lab in (m.group(1), m.group(2)):
                if not lab:
                    continue
                lab, stem = lab.strip(), re.sub(r"\s*\(.*", "", lab.strip()).lower()
                # a later label with the same stem on the same item replaces the earlier one
                out = [(l, i) for l, i in out if not (i == it["id"] and re.sub(r"\s*\(.*", "", l).lower() == stem)]
                out.append((lab, it["id"]))
    return out


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("review.md")
    global OPENER
    c = load_json(argv[0])
    items = b_items()
    by_id = {it["id"]: it for it in items}
    if "SUM-01" in by_id:
        OPENER = re.split(r"(?<=\.)\s", by_id["SUM-01"]["full"].strip(), maxsplit=1)[0]
    header, vocab, nfp = b_sections()
    labels = item_labels(items)
    lab_block = "\n".join(f"- {l} ({i})" for l, i in labels)
    lines, n, missing = [PROMPT, "---\n", "# B header rules (approved facts and wording)\n", header, "",
                         "# B skills vocabulary (approved labels and tools)\n", vocab, "",
                         "# Item-approved skill labels (approved in that item's facts; count as B)\n", lab_block, "",
                         "# B NOT FOR PAPER (interview-only extras: never on the resume)\n", nfp, "", "---\n"], 0, []

    def entry(label, text, ids):
        nonlocal n
        n += 1
        lines.append(f"## #{n} · {label}\nRESUME: {text.strip()}\n\nB:")
        real = [i for i in ids if i and i.lower() != "new"]
        for i in real:
            if i in by_id:
                lines.append(b_block(by_id[i]))
            else:
                missing.append(i)
                lines.append(f"  (ID {i} not found in B)")
        if not real or any(i.lower() == "new" for i in ids):
            lines.append("  (marked \"new\": no B item named. Closest B items, check whether they back it up:)")
            lines.extend(b_block(it) for it in nearest(text, items))
        lines.append("")

    if c.get("summary"):
        sid = [x.strip() for x in str(c.get("summary_id") or "new").split("+") if x.strip()]
        entry("Summary", c["summary"], sid)
    for j in c.get("jobs", []):
        bullets, ids = j.get("bullets") or [], j.get("bullet_ids") or []
        for k, b in enumerate(bullets):
            bid = ids[k] if k < len(ids) else "new"
            entry(f"{j.get('title', '?')} ({j.get('dates', '')}), bullet {k + 1}", b,
                  [x.strip() for x in bid.split("+")])
    for sec in c.get("extra_sections") or []:
        sid = sec.get("id")
        if not sid:  # match the section to the B item whose "(section)" text it carries
            sw = words(sec.get("text", ""))
            best = max(items, key=lambda it: len(sw & words(" ".join(v for k, v in it.get("extra", [])
                                                                   if "section" in k))), default=None)
            if best and len(sw & words(" ".join(v for k, v in best.get("extra", []) if "section" in k))) >= 5:
                sid = best["id"]
        entry(f"Extra section: {sec.get('heading', '')}", sec.get("text", ""),
              [x.strip() for x in str(sid or "new").split("+")])

    if c.get("skills"):
        corpus = norm(" ".join(item_text(it) + " " + it["tags"] for it in items) + " " + header + " " + vocab
                      + " " + lab_block)
        terms = [t.strip() for t in re.split(r"[;,|]", c["skills"]) if t.strip()]
        unseen = [t for t in terms if not stem_hit(re.sub(r"\(.*?\)", "", t), corpus)]
        n += 1
        lines.append(f"## #{n} · Skills line\nRESUME: {c['skills'].strip()}\n")
        ok = [f"{t} ({i})" for t in terms for l, i in labels if norm(t) == norm(l)]
        if ok:
            lines.append("Item-approved labels on this line (PASS, approved in that item's facts): " + ", ".join(ok))
        lines.append("Terms found nowhere in B (FLAG unless clearly a synonym of something B shows): "
                     + (", ".join(unseen) if unseen else "none"))
        lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"truthpack: {n} lines → {out}" + (f" · unknown IDs: {', '.join(missing)}" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
