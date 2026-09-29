"""CLI 防呆與假資料 dry-run；不代表真實 Supabase 查核。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "variant"))
import scan_semantic_quality as scan
import suggest_semantic_fixes as fixes


def alarm(code="1", variant=""):
    return dict(device_model="M", code=code, variant=variant, description="Alarm 錯譯")


@pytest.mark.parametrize("rows", [[alarm(variant="v2")], [alarm(), alarm()]])
def test_variant_guard(rows):
    with pytest.raises(AssertionError):
        scan.check_variants(rows)


@pytest.mark.parametrize("write", [False, True])
def test_cli_with_fake_store(tmp_path, monkeypatch, capsys, write):
    alarms = [alarm(str(i)) for i in range(1759)]
    findings = [dict(**alarm(str(i)), issue="錯譯", confidence="high") for i in range(5)]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(dict(department="mf4d", findings=findings)))
    writes = []
    def load(*, department):
        assert department == "mf4d"
        return alarms
    existing = [dict(device_model="M", code=str(i), status=status)
                for i, status in enumerate(["accepted", "rejected", "unknown", "pending"])]
    def load_findings(*, department):
        assert department == "mf4d"
        return existing
    def save_findings(rows, *, department):
        assert department == "mf4d"
        writes.append(rows)
    store = SimpleNamespace(load_all=load_findings, save_all=save_findings)
    monkeypatch.setattr(fixes, "load_storage", lambda: SimpleNamespace(
        alarms_store=SimpleNamespace(load=load, _invalidate_cache=lambda department: None), SemanticReviewStore=lambda: store))
    monkeypatch.setattr(fixes, "_load_client", lambda: None)
    monkeypatch.setattr(fixes, "suggest_batch", lambda *a: {i: "警報" for i in range(5)})
    monkeypatch.setattr(fixes.time, "sleep", lambda _: None)
    monkeypatch.setenv("ALLOW_LOCAL_PRODUCTION_WRITE", "1")
    monkeypatch.setattr(sys, "argv", ["suggest", "-i", str(path)] + (["--write"] if write else []))
    fixes.main()
    output = capsys.readouterr()
    rows = writes[0] if write else json.loads(output.out)
    assert [r["code"] for r in rows] == ["3", "4"]
    assert all(r["status"] == "pending" for r in rows)
    assert set(rows[0]) == {"device_model", "code", "description", "issue", "confidence",
                            "suggested_zh", "suggested_description", "status"}
    assert "1759" in output.err
    assert len(writes) == (1 if write else 0)


def test_write_requires_explicit_environment(monkeypatch):
    monkeypatch.delenv("ALLOW_LOCAL_PRODUCTION_WRITE", raising=False)
    monkeypatch.setattr(sys, "argv", ["suggest", "-i", "unused.json", "--write"])
    with pytest.raises(RuntimeError, match="ALLOW_LOCAL_PRODUCTION_WRITE"):
        fixes.main()


def test_existing_query_failure_is_not_empty_table():
    def failed(*, department):
        print("查詢失敗", file=sys.stderr)
        return []
    with pytest.raises(RuntimeError, match="load_all"):
        fixes.prepare_findings([], [], SimpleNamespace(load_all=failed))
