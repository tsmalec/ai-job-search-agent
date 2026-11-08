"""check_extract.py — checks a local model's jd.json before Claude builds on it. Stdlib only.

    python scripts/check_extract.py work/acme/jd.json [--ref reference_jd.json]

FAIL (exit 1): no title, or no required items.
WARN (exit 0): a thin required list, a long entry that is probably several requirements bundled together,
a phrase listed as both preferred and keyword, or (with --ref) a reference item with no matching entry.
Each run writes extract_warnings.md next to jd.json for Claude to read, and appends a line to
data/logs/extract_checks.jsonl. The word-overlap check against --ref is rough: it matches any shared word
longer than three letters.
"""
import argparse, datetime, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from model import setting  # noqa: E402

LOG = ROOT / "data" / "logs" / "extract_checks.jsonl"
MAX_WORDS = 6
MIN_REQUIRED = 3
MAX_REQUIRED = 25


def covered(item, keys):
    words = {w for w in item.split() if len(w) > 3}
    return any(words & set(k.split()) for k in keys)


def check(jd, ref=None):
    fails, warns = [], []
    req = list(jd.get("required", {}))
    pref = list(jd.get("preferred", {}))
    kws = list(jd.get("keywords", {}))
    title = jd.get("title", "").strip()
    if not title:
        fails.append("no title")
    elif len(title.split()) < 2:
        warns.append(f"title is one word, maybe the company name: '{title}'")
    if not req:
        fails.append("required list is empty")
    elif len(req) < MIN_REQUIRED:
        warns.append(f"only {len(req)} required item(s); postings usually list more")
    elif len(req) > MAX_REQUIRED:
        warns.append(f"{len(req)} required items; probably over-extracted")
    for k in req + pref:
        if len(k.split()) > MAX_WORDS:
            warns.append(f"long entry, probably bundled: '{k}'")
    overlap = sorted(set(pref) & set(kws))
    if overlap:
        warns.append(f"{len(overlap)} phrase(s) listed as both preferred and keyword: {', '.join(overlap[:5])}")
    if ref:
        for section in ("required", "preferred"):
            have = list(jd.get(section, {})) + req + pref
            for item in ref.get(section, {}):
                if not covered(item, have):
                    warns.append(f"reference {section} item not found: '{item}'")
    return fails, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jd")
    ap.add_argument("--ref")
    a = ap.parse_args()
    jd_path = Path(a.jd)
    jd = json.loads(jd_path.read_text(encoding="utf-8"))
    ref = json.loads(Path(a.ref).read_text(encoding="utf-8")) if a.ref else None
    fails, warns = check(jd, ref)
    status = "FAIL" if fails else ("WARN" if warns else "OK")
    md = [f"# Extraction check: {status}", "", f"Model: {setting()}", f"File: {jd_path}", ""]
    md += [f"- FAIL: {f}" for f in fails] + [f"- WARN: {w}" for w in warns]
    if status == "OK":
        md.append("- No problems found. Still check the required list by eye.")
    (jd_path.with_name("extract_warnings.md")).write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"ts": datetime.datetime.now().isoformat(timespec="seconds"), "jd": str(jd_path),
                            "model": setting(), "status": status, "fails": fails, "warnings": warns},
                           ensure_ascii=False) + "\n")
    print("\n".join(md))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
