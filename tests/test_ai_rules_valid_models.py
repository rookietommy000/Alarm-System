"""load_valid_models() 的 fail-closed + TTL 快取行為（拍照辨識故障修復
PR-3：改用 devices_store 讀取，不再自己直接讀 devices.json 檔案，跟
app.py 讀 devices 資料走同一個入口，避免正式環境資料實際存在 Supabase，
這裡卻讀本機檔案系統看到空的白名單）。

純本地邏輯測試，monkeypatch devices_store.load() 模擬各種情境，不依賴
真實 Supabase。
"""
import pytest

from ai import ai_rules
from ai.ai_rules import ValidModelsUnavailable, load_valid_models


@pytest.fixture(autouse=True)
def _reset_cache():
    """每個測試前後清掉模組級快取，避免測試之間互相污染。"""
    ai_rules._valid_models_cache["models"] = None
    ai_rules._valid_models_cache["expires_at"] = 0.0
    yield
    ai_rules._valid_models_cache["models"] = None
    ai_rules._valid_models_cache["expires_at"] = 0.0


def _device(model, active=True):
    """devices_store.load() 回傳的列已經過 _row_to_device() 轉換，
    同時含 model/device_model 兩個 key（見 storage.py 的說明）。"""
    return {"id": f"id-{model}", "model": model, "device_model": model, "active": active}


def _patch_devices_load(monkeypatch, fn):
    import storage
    monkeypatch.setattr(storage.devices_store, "load", fn)


def test_loads_active_models_only(monkeypatch):
    _patch_devices_load(monkeypatch, lambda department: [
        _device("CNC-A100", active=True),
        _device("CNC-B200", active=False),
        _device("CNC-C300"),  # 無 active 欄位視為有效（_device 預設 True）
    ])
    result = load_valid_models()
    assert result == {"CNC-A100", "CNC-C300"}


def test_department_none_means_no_filter_not_null_query(monkeypatch):
    """department=None 呼叫 devices_store.load() 代表「不加過濾，取全部
    部門」，不是「查 department 為 NULL」——確認呼叫時確實傳的是 None。"""
    captured = {}

    def _fake_load(department):
        captured["department"] = department
        return [_device("CNC-A100")]

    _patch_devices_load(monkeypatch, _fake_load)
    load_valid_models()
    assert captured["department"] is None


def test_returns_set_not_list(monkeypatch):
    _patch_devices_load(monkeypatch, lambda department: [_device("CNC-A100")])
    result = load_valid_models()
    assert isinstance(result, set)


def test_raises_when_cache_empty_and_load_fails(monkeypatch):
    """沒有裝過快取、devices_store.load() 也失敗時，必須 fail-closed，
    不能回空 set。"""
    def _boom(department):
        raise ConnectionError("模擬 Supabase 連線失敗")

    _patch_devices_load(monkeypatch, _boom)
    with pytest.raises(ValidModelsUnavailable):
        load_valid_models()


def test_cache_hits_avoid_repeated_load_call(monkeypatch):
    calls = []

    def _fake_load(department):
        calls.append(1)
        return [_device("CNC-A100")]

    _patch_devices_load(monkeypatch, _fake_load)
    first = load_valid_models()
    assert first == {"CNC-A100"}

    second = load_valid_models()
    assert second == {"CNC-A100"}
    assert len(calls) == 1, "快取命中時不該重複呼叫 devices_store.load()"


def test_stale_cache_used_as_fallback_on_transient_failure(monkeypatch):
    """已有成功讀過的快取時，之後查詢失敗要降級回傳舊值（即使已過期），
    不是直接掛掉——一次瞬斷不該讓正在使用中的辨識功能整個失效。"""
    _patch_devices_load(monkeypatch, lambda department: [_device("CNC-A100")])
    first = load_valid_models()
    assert first == {"CNC-A100"}

    ai_rules._valid_models_cache["expires_at"] = 0.0  # 強制視為過期

    def _boom(department):
        raise ConnectionError("模擬瞬斷")

    _patch_devices_load(monkeypatch, _boom)
    fallback = load_valid_models()
    assert fallback == {"CNC-A100"}


def test_recovers_after_transient_failure_fixed(monkeypatch):
    """快取過期後查詢已恢復正常，應該讀到新內容，不是永遠卡在舊快取。"""
    _patch_devices_load(monkeypatch, lambda department: [_device("CNC-A100")])
    load_valid_models()

    ai_rules._valid_models_cache["expires_at"] = 0.0
    _patch_devices_load(monkeypatch, lambda department: [_device("CNC-Z900")])
    result = load_valid_models()
    assert result == {"CNC-Z900"}
