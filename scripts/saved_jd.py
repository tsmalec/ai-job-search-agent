"""saved_jd.py — reuse a posting text the screen already saved, instead of fetching it again.

    python3 scripts/saved_jd.py <posting url>                 report: full | partial | none (and the file)
    python3 scripts/saved_jd.py <posting url> --out jd.txt    also copy a FULL text to jd.txt

The screen (attach_jds.py) saves each lead's text to jds/leads/<company>_<role>_<urlhash>.txt. A triage chat checks
here first. Exit 0 = full text found (copied when --out is given); exit 1 = partial or none: use the next capture step.
A partial text is never copied, because a verdict must not rest on half a posting.
"""
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, jd_state  # noqa: E402

DIR = ROOT / "jds" / "leads"


def find(url, directory=None):
    """Saved files for this url: the name ends in the url's sha1 prefix (attach_jds.fname)."""
    directory = Path(directory or DIR)
    tag = hashlib.sha1((url or "").strip().encode("utf-8")).hexdigest()[:8]
    return sorted(directory.glob(f"*_{tag}.txt")) if directory.is_dir() else []


def check(url, directory=None):
    """(state, why, path) for the best saved text of this url; state is full | partial | none."""
    best = ("none", "no saved text for this link", None)
    for p in find(url, directory):
        st, why = jd_state(p)
        if st == "full":
            return st, why, p
        if st == "partial" and best[0] == "none":
            best = (st, why, p)
    return best


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1] if i + 1 < len(argv) else None
        args = [a for a in args if a != out]
    if len(args) != 1:
        print(__doc__)
        return 2
    st, why, p = check(args[0])
    if st == "full":
        print(f"saved_jd: full text saved by the screen: {p.relative_to(ROOT).as_posix()}")
        if out:
            shutil.copyfile(p, out)
            print(f"saved_jd: copied to {out}")
        return 0
    print(f"saved_jd: {st} ({why})" + (f": {p.relative_to(ROOT).as_posix()}" if p else "") + " — use the next capture step")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
