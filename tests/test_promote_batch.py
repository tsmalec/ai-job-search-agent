"""promote_batch.py plan checks and apply loop. Fictional data only."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import promote_batch as pb  # noqa: E402


def leads(*urls):
    return [{"company": "Acme", "role": f"Role {i}", "url": u, "status": "not_triaged"} for i, u in enumerate(urls)]


def entry(u, **kw):
    return {"url": u, "status": "triaged", "live": "open", "note": "read from saved text", **kw}


A, B = "https://jobs.acme.example/1", "https://jobs.acme.example/2"


def test_good_plan_has_no_errors():
    assert pb.check([entry(A), entry(B + "/")], leads(A, B)) == []


def test_missing_lead_refused():
    assert any("missing" in e for e in pb.check([entry(A)], leads(A, B)))


def test_unknown_repeated_and_blank_refused():
    errs = pb.check([entry(A), entry(A), entry("https://x.example/9"), entry(B, note=" "), entry(B, live="")], leads(A, B))
    text = " | ".join(errs)
    assert "repeated" in text and "not an open lead" in text and "note is empty" in text and "live must" in text


def test_closed_cannot_be_triaged():
    assert any("closed" in e for e in pb.check([entry(A, live="closed")], leads(A)))


def test_apply_promotes_each_and_reports_refusals(tmp_path, monkeypatch, capsys):
    lf = tmp_path / "leads.json"
    lf.write_text(json.dumps({"leads": leads(A, B)}), encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([entry(A), entry(B)]), encoding="utf-8")
    monkeypatch.setattr(pb, "LEADS", lf)
    done = []
    def fake(e):
        done.append(e["url"])
        return "refused for test" if e["url"] == B else ""
    assert pb.main([str(plan), "--apply"], apply=fake) == 1
    assert done == [A, B]
    assert "refused for test" in capsys.readouterr().out


def test_check_only_changes_nothing(tmp_path, monkeypatch, capsys):
    lf = tmp_path / "leads.json"
    lf.write_text(json.dumps({"leads": leads(A)}), encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([entry(A)]), encoding="utf-8")
    monkeypatch.setattr(pb, "LEADS", lf)
    called = []
    code = pb.main([str(plan)], apply=lambda e: called.append(e))
    assert code == 0
    assert called == [] and "Plan OK" in capsys.readouterr().out


def test_preliminary_verdict_allowed_final_refused():
    assert pb.check([entry(A, verdict="yellow?")], leads(A)) == []
    assert pb.check([entry(A, verdict="")], leads(A)) == []
    assert any("verdict" in e for e in pb.check([entry(A, verdict="yellow")], leads(A)))


def test_template_has_a_verdict_field(tmp_path):
    t = pb.template(leads(A), table_path=tmp_path / "none.json")
    assert t[0]["verdict"] == ""
