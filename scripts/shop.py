"""shop.py — pick bullets from B_Bullet_Catalog.md for a job description.

Usage (from the repo root):
  python3 scripts/shop.py B_Bullet_Catalog.md C_Gaps.md keywords.txt --basket basket.md
keywords.txt: one JD keyword or phrase per line, in the order the JD lists them.

Matching: each keyword word is matched by its first 6 letters (short words like "AI"
must match exactly) against item tags and full/short text. The `facts` line is ignored
on purpose, since it holds limits like "not Kaizen." Tested on TE-style
keywords: 14 of 15 covered, Kaizen correctly flagged as a known gap.

Output:
  1. Ranked catalog items (ID, name, which JD keywords each covers)
  2. A suggested basket: fewest items that cover the most keywords, in JD order
  3. Uncovered keywords, split into "known gap" (in C_Gaps) and "ask the candidate" (Q&A list)
"""
import re, sys
from pathlib import Path


def _lines(path):
    """A file's lines (newlines kept), with the file closed afterwards."""
    return Path(path).read_text(encoding="utf-8").splitlines(True)


def norm(s):
    return re.sub(r"[^a-z0-9$+&/ ]", " ", s.lower())


def stem_hit(kw, text):
    # match each word of the keyword by its first 6 letters, so "forecasting" ~ "forecast"
    words = [w for w in norm(kw).split() if w]
    pats = [r"\b" + re.escape(w) + r"\b" if len(w) <= 3 else r"\b" + re.escape(w[:6]) for w in words]
    return all(re.search(p, text) for p in pats)


def load_items(path):
    items, cur = [], None
    for line in _lines(path):
        m = re.match(r"### (\S+) · (.+)", line)
        if m:
            cur = {"id": m.group(1), "name": m.group(2).strip(), "tags": "", "text": ""}
            items.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("tags:"):
            cur["tags"] = norm(line[5:])
        elif line.startswith(("full:", "short:")):  # facts hold limits ("not Kaizen"), so they don't count
            cur["text"] += " " + norm(line.split(":", 1)[1])
        elif line.startswith("## "):
            cur = None
    return items


def d_opener(cat, kws, items):
    """Summary opener from D's 'Summary opener by role type' table (shop picked SUM-09 Finance for a
    sales ops role). Scores each row's role-type words against the first keywords (title first)."""
    import os
    dfile = os.path.join(os.path.dirname(os.path.abspath(cat)), "D_Fit_Calibration.md")
    if not os.path.exists(dfile):
        return None
    rows = []
    for line in _lines(dfile):
        m = re.match(r"\|\s*([^|]+?)\s*\|\s*(SUM-\d+)\s*\|", line)
        if m:
            rows.append((m.group(1), m.group(2)))
    head = norm(" ".join(kws[:6]))
    best, score = None, 0
    for types, sid in rows:
        sc = sum(3 if i < 2 else 1 for i, k in enumerate(kws[:6])
                 for t in re.split(r"[/;,]", types) if t.strip() and stem_hit(t.strip(), norm(k)))
        sc += sum(1 for t in re.split(r"[/;,]", types) if t.strip() and stem_hit(t.strip(), head))
        if sc > score:
            best, score = sid, sc
    return next((i for i in items if i["id"] == best), None) if best else None


def main(cat, gaps, kwfile, out=None):
    items = load_items(cat)
    kws = [k.strip() for k in _lines(kwfile) if k.strip()]
    # gap names = first two cells of each table row in C_Gaps ("Gap" and "Never write")
    gap_text = " | ".join(norm(" ".join(r.split("|")[1:3])) for r in _lines(gaps)
                          if r.startswith("|") and not r.startswith("|---") and not r.startswith("| Gap"))
    for it in items:
        it["hits"] = [k for k in kws if stem_hit(k, it["tags"]) or stem_hit(k, it["text"])]
        it["score"] = sum(2 if stem_hit(k, it["tags"]) else 1 for k in it["hits"])
    ranked = sorted([i for i in items if i["hits"]], key=lambda i: -i["score"])

    print("== RANKED ITEMS ==")
    for it in ranked[:25]:
        print(f"{it['id']:9} {it['score']:>3}  {it['name'][:45]:45}  {', '.join(it['hits'][:8])}")

    # greedy basket: keep adding the item that covers the most still-uncovered keywords
    covered, basket = set(), []
    pool = [i for i in ranked if not i["id"].startswith("SUM")]
    while True:
        best = max(pool, key=lambda i: len(set(i["hits"]) - covered), default=None)
        if not best or not (set(best["hits"]) - covered):
            break
        basket.append(best); covered |= set(best["hits"]); pool.remove(best)
    first_kw = lambda it: min(kws.index(k) for k in it["hits"])
    basket.sort(key=first_kw)
    print("\n== SUGGESTED BASKET (JD order) ==")
    for it in basket:
        print(f"{it['id']:9} {it['name']}")
    best_sum = d_opener(cat, kws, items) or max([i for i in items if i["id"].startswith("SUM")],
                                                 key=lambda i: i["score"], default=None)
    if best_sum:
        print(f"Summary opener: {best_sum['id']} {best_sum['name']} (from D's role-type table; override if the "
              "JD's job family says otherwise)")

    missing = [k for k in kws if k not in covered]
    known = [k for k in missing if stem_hit(k, gap_text)]
    ask = [k for k in missing if k not in known]
    print(f"\nCoverage: {len(covered)}/{len(kws)} keywords")
    print("Known gaps (don't claim):", ", ".join(known) or "none")
    print("Ask the candidate (Q&A list):", ", ".join(ask) or "none")
    partial = {}
    for k in ask:  # multi-word phrases B covers in pieces: check these items' facts before asking the candidate
        words = [w for w in norm(k).split() if len(w) > 3]
        if len(words) < 2:
            continue
        scored = sorted(((sum(stem_hit(w, i["tags"] + " " + i["text"]) for w in words), i["id"]) for i in items
                         if not i["id"].startswith("SUM")), reverse=True)
        top = [iid for n, iid in scored if n >= max(1, len(words) - 1)][:4]
        if top:
            partial[k] = top
    for k, ids in partial.items():
        print(f"  partial '{k}': {', '.join(ids)} (check facts before asking)")

    if out:
        write_basket(cat, out, basket, best_sum, ranked, kws, known, ask, partial)
        print(f"\nWrote {out}: read THIS instead of all of B (header, picks, alternates, skills vocab).")


def write_basket(cat, out, basket, best_sum, ranked, kws, known, ask, partial=None, n_alt=10):
    """basket.md = B's header block + skills vocabulary + the full text of every pick and the next-best
    alternates. Lets a build read ~5K tokens instead of the whole ~25K-token catalog. Need another item?
    `grep -n -A6 '^### ID ' B_Bullet_Catalog.md` for just that one."""
    lines = Path(cat).read_text(encoding="utf-8").splitlines()
    blocks, cur, header, vocab, section = {}, None, [], [], "header"
    for line in lines:
        m = re.match(r"### (\S+) · ", line)
        if line.startswith("## "):
            section = "vocab" if line.startswith("## Skills vocabulary") else "aisle"
            cur = None
        elif m and section == "aisle":
            cur = m.group(1)
            blocks[cur] = []
        if section == "header":
            header.append(line)
        elif section == "vocab":
            vocab.append(line)
        if cur and section == "aisle":
            blocks[cur].append(line)
    picked = [i["id"] for i in basket]
    alts = [i["id"] for i in ranked if i["id"] not in picked and not i["id"].startswith("SUM")][:n_alt]
    for ids in (partial or {}).values():  # dedupe; partial lists repeat IDs across keywords
        for x in ids:
            if x not in picked and x not in alts:
                alts.append(x)
    hits = {i["id"]: i["hits"] for i in ranked}
    o = ["# Basket (generated by shop.py; read this, not all of B)", "",
         f"Keywords in JD order: {'; '.join(kws)}",
         f"Known gaps (C): {', '.join(known) or 'none'}  |  Ask the candidate (3b candidates): {', '.join(ask) or 'none'}",
         "Partial phrase matches (check these items' facts before asking): "
         + ("; ".join(f"{k} -> {', '.join(v)}" for k, v in (partial or {}).items()) or "none"), ""]
    o += header + [""]
    if best_sum:
        o += ["## Summary opener"] + blocks.get(best_sum["id"], []) + [""]
    o += ["## Picks (JD order)"]
    for i in picked:
        o += [f"<!-- covers: {', '.join(hits.get(i, []))} -->"] + blocks.get(i, []) + [""]
    o += ["## Alternates (next best; swap in if a pick reads wrong)"]
    for i in alts:
        o += [f"<!-- covers: {', '.join(hits.get(i, []))} -->"] + blocks.get(i, []) + [""]
    o += vocab
    Path(out).write_text("\n".join(o) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):  # never run on --help
        print((__doc__ or "").strip() or "No usage notes; see the script header.")
        sys.exit(0)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = sys.argv[sys.argv.index("--basket") + 1] if "--basket" in sys.argv else None
    if out:
        args.remove(out)
    main(*args[:3], out=out)
