"""save_json writes back in the layout the file already has. Fictional data only."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import common  # noqa: E402

DATA = {"rows": [{"name": "Café Acme", "n": 1}]}


def roundtrip(tmp_path, newline="", **kw):
    f = tmp_path / "x.json"
    text = json.dumps(DATA, **kw) + newline
    f.write_text(text, encoding="utf-8", newline="\n")
    common.save_json(f, DATA)
    return text, f.read_text(encoding="utf-8")


def test_one_space_ascii_no_newline_is_kept(tmp_path):
    before, after = roundtrip(tmp_path, indent=1, ensure_ascii=True)
    assert after == before and "\\u00e9" in after and not after.endswith("\n")


def test_house_style_is_kept(tmp_path):
    before, after = roundtrip(tmp_path, newline="\n", indent=2, ensure_ascii=False)
    assert after == before and "Café" in after


def test_four_space_is_kept(tmp_path):
    before, after = roundtrip(tmp_path, indent=4, ensure_ascii=False)
    assert after.splitlines()[1].startswith("    \"rows\"")


def test_new_file_gets_house_style(tmp_path):
    f = tmp_path / "new.json"
    common.save_json(f, DATA)
    assert f.read_text(encoding="utf-8") == json.dumps(DATA, indent=2, ensure_ascii=False) + "\n"


def test_unreadable_existing_file_falls_back_to_house_style(tmp_path):
    f = tmp_path / "bad.json"
    f.write_bytes(b"\xff\xfe not utf-8")
    common.save_json(f, DATA)
    assert f.read_text(encoding="utf-8").endswith("\n")
