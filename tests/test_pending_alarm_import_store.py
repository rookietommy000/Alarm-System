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
