"""PendingAlarmImportStore.create() 的 payload 組裝邏輯（見 migration
010_add_pending_alarm_imports.sql）。

用 monkeypatch 假造 urllib.request.urlopen，不依賴真實 Supabase 連線
（同 CLAUDE.md「測試的能力邊界」：這裡只測「程式碼邏輯對假造回應的
處理是否正確」，不是「對真實 Supabase 是否真的生效」）。這支方法目前
還沒有呼叫端（批次匯入/AI 辨識的寫入邏輯待補），這裡先釘住 store 層
本身的組裝邏輯是正確的。
"""
import io
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import storage as storage_mod


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_create_not_use_supabase_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: False)
    store = storage_mod.PendingAlarmImportStore()
    result = store.create(
        department="local", device_model="CNC-A100", code="0099", variant="",
        description="測試描述", source="bulk_import", flagged_reason="格式不符",
    )
    assert result == {}


def test_create_sends_required_fields_and_omits_none_optional_fields(monkeypatch):
    """必填欄位（department/device_model/code/variant/description/
    source/flagged_reason）一律要送；選填欄位缺席（None）時不該送進
    payload——跟 AlarmSuggestionStore.create() 的既有慣例一致，也避免
    呼叫端誤以為送 None 等於「明確清空」而非「沒提供」。"""
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    captured = {}

    def fake_urlopen(req, *a, **kw):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return FakeResponse(json.dumps(captured["body"]).encode())

    monkeypatch.setattr(storage_mod.urllib.request, "urlopen", fake_urlopen)
    store = storage_mod.PendingAlarmImportStore()
    result = store.create(
        department="local", device_model="CNC-A100", code="0099", variant="",
        description="測試描述", source="bulk_import", flagged_reason="格式不符",
    )

    assert "pending_alarm_imports" in captured["url"]
    sent = captured["body"][0]
    assert sent == {
        "department": "local", "device_model": "CNC-A100", "code": "0099",
        "variant": "", "description": "測試描述", "source": "bulk_import",
        "flagged_reason": "格式不符",
    }
    # status 完全不送——固定用 DB default 'pending'，不讓呼叫端指定初始狀態
    assert "status" not in sent
    assert result == sent


def test_create_includes_optional_fields_when_provided(monkeypatch):
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    captured = {}

    def fake_urlopen(req, *a, **kw):
        captured["body"] = json.loads(req.data.decode())
        return FakeResponse(json.dumps(captured["body"]).encode())

    monkeypatch.setattr(storage_mod.urllib.request, "urlopen", fake_urlopen)
    store = storage_mod.PendingAlarmImportStore()
    store.create(
        department="local", device_model="CNC-A100", code="0099", variant="A",
        description="測試描述", source="ai_recognition", flagged_reason="信心度過低",
        severity="警告", cause="測試原因", solution="測試處置",
        keywords=["kw1", "kw2"], sol_steps={"step1": "說明"},
        raw_source_text="0099.raw", confidence=42.5, submitted_by="tester",
    )

    sent = captured["body"][0]
    assert sent["severity"] == "警告"
    assert sent["cause"] == "測試原因"
    assert sent["solution"] == "測試處置"
    assert sent["keywords"] == ["kw1", "kw2"]
    assert sent["sol_steps"] == {"step1": "說明"}
    assert sent["raw_source_text"] == "0099.raw"
    assert sent["confidence"] == 42.5
    assert sent["submitted_by"] == "tester"


@pytest.fixture(params=[
    ("AlarmSuggestionStore", "alarm_suggestions", "suggestion_id",
     {"suggestion": "建議", "reason": None, "submitted_by": "tester"},
     "*,alarms(solution,local_solution,local_reason,description)"),
    ("PendingAlarmImportStore", "pending_alarm_imports", "import_id",
     {"description": "描述", "source": "bulk_import", "flagged_reason": "格式",
      "confidence": 0, "keywords": [], "cause": None}, "*"),
])
def crud_case(request, monkeypatch):
    name, table, id_arg, fields, select = request.param
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid/")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
    calls = []
    response = {"raw": b'[{"id": 7}]'}

    def fake_urlopen(req):
        calls.append(req)
        return FakeResponse(response["raw"])

    monkeypatch.setattr(storage_mod, "_urlopen", fake_urlopen)
    return (getattr(storage_mod, name)(), table, id_arg,
            dict(department="local", device_model="M1", code="001", variant="", **fields),
            select, calls, response)


def test_crud_noop_without_supabase(crud_case, monkeypatch):
    store, _, id_arg, fields, _, calls, _ = crud_case
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: False)
    assert store.create(**fields) == {}
    assert store.list_pending(department=None) == []
    assert store.get_by_id(**{id_arg: 7}) is None
    assert store.review(**{id_arg: 7}, status="rejected", reviewed_by="admin", review_note=None) is None
    assert store.claim(7, from_status="pending", to_status="rejected", actor="admin") is None
    assert store.release(7) is None
    assert calls == []


@pytest.mark.parametrize("raw, expected", [(b'[{"id": 7}]', {"id": 7}), (b'[]', {}), (b' ', {})])
def test_crud_create_payload_and_response(crud_case, raw, expected):
    store, table, _, fields, _, calls, response = crud_case
    response["raw"] = raw
    assert store.create(**fields) == expected
    req, = calls
    assert req.full_url == f"https://example.invalid/rest/v1/{table}"
    assert req.get_method() == "POST"
    assert json.loads(req.data) == [{k: v for k, v in fields.items() if v is not None}]
    assert req.get_header("Prefer") == "return=representation"
    assert req.get_header("Apikey") == "test-key"
    assert req.get_header("Authorization") == "Bearer test-key"
    assert req.get_header("Content-type") == "application/json"


@pytest.mark.parametrize("department, suffix", [(None, ""), ("", "&department=eq."),
    ("部門/a b", "&department=eq.%E9%83%A8%E9%96%80%2Fa%20b")])
def test_crud_list_query(crud_case, department, suffix):
    store, table, _, _, select, calls, _ = crud_case
    assert store.list_pending(department=department) == [{"id": 7}]
    req, = calls
    assert req.get_method() == "GET"
    assert req.data is None
    assert req.full_url == (f"https://example.invalid/rest/v1/{table}?select={select}"
                            f"&status=eq.pending&order=submitted_at.desc{suffix}")


@pytest.mark.parametrize("raw, expected", [(b'[]', None), (b'', None), (b'[{"id": 7}]', {"id": 7})])
def test_crud_get_by_id_contract(crud_case, raw, expected):
    store, table, id_arg, _, _, calls, response = crud_case
    response["raw"] = raw
    assert store.get_by_id(**{id_arg: 7}) == expected
    req, = calls
    assert req.get_method() == "GET"
    assert req.full_url == f"https://example.invalid/rest/v1/{table}?id=eq.7&select=*"


@pytest.mark.parametrize("note", [None, "", "審核說明"])
def test_crud_review_claim_release_contract(crud_case, note):
    store, table, id_arg, _, _, calls, response = crud_case
    base = f"https://example.invalid/rest/v1/{table}?id=eq.7"
    assert store.review(**{id_arg: 7}, status="rejected", reviewed_by="admin", review_note=note) == {"id": 7}
    assert calls[-1].full_url == base  # review 保留既有的無條件 PATCH。
    review_body = json.loads(calls[-1].data)
    assert review_body.pop("reviewed_at")
    expected = {"status": "rejected", "reviewed_by": "admin"}
    if note is not None:
        expected["review_note"] = note
    assert review_body == expected
    assert store.claim(7, from_status="pending", to_status="rejected", actor="admin", review_note=note) == {"id": 7}
    assert calls[-1].full_url == base + "&status=eq.pending"
    claim_body = json.loads(calls[-1].data)
    assert claim_body.pop("reviewed_at")
    assert claim_body == expected
    for req in calls:
        assert req.get_method() == "PATCH"
        assert req.get_header("Prefer") == "return=representation"
    response["raw"] = b'[]'
    assert store.claim(7, from_status="pending", to_status="rejected", actor="admin") is None
    assert store.release(7) is None
    assert calls[-1].full_url == base
    assert calls[-1].get_method() == "PATCH"
    assert json.loads(calls[-1].data) == {"status": "pending", "reviewed_by": None, "reviewed_at": None}
