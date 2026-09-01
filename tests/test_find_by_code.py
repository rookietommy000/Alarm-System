"""find_by_code() 測試（拍照辨識故障修復：variant 混淆問題）。

背景：AI 辨識完全不知道 variant 概念，只認得 code 文字。get_one()/
delete_one()/patch_one() 都要求完整主鍵（含 variant），無法用來
「不知道 variant、反查 DB 有哪些 variant」這個情境。find_by_code()
補這個缺口：只用 (department, device_model, code) 查，回傳可能
0/1/多筆列（多筆代表同一 code 有多個 variant）。

JsonStore 走 tmp 檔案；SupabaseStore 用 monkeypatch _req() 驗證
查詢條件，不依賴真實 Supabase。
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import storage


# ── JsonStore ────────────────────────────────────────────────────────────────

@pytest.fixture
def json_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ALARM_DATA_DIR", str(tmp_path))
    return storage.JsonStore("alarms.json")


def _write_alarms(tmp_path, rows):
    (tmp_path / "alarms.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def test_json_store_no_match_returns_empty_list(json_store, tmp_path):
    _write_alarms(tmp_path, [
        {"department": "mf4d", "device_model": "PILM004", "code": "0001", "variant": ""},
    ])
    result = json_store.find_by_code("mf4d", "PILM004", "9999")
    assert result == []


def test_json_store_single_match_returns_one_row(json_store, tmp_path):
    _write_alarms(tmp_path, [
        {"department": "mf4d", "device_model": "PILM004", "code": "0001", "variant": ""},
        {"department": "mf4d", "device_model": "PILM004", "code": "0002", "variant": ""},
    ])
    result = json_store.find_by_code("mf4d", "PILM004", "0001")
    assert len(result) == 1
    assert result[0]["code"] == "0001"


def test_json_store_multiple_variants_returns_all(json_store, tmp_path):
    _write_alarms(tmp_path, [
        {"department": "mf4d", "device_model": "PILM004", "code": "0001", "variant": "A"},
        {"department": "mf4d", "device_model": "PILM004", "code": "0001", "variant": "B"},
        {"department": "mf4d", "device_model": "PILM004", "code": "0002", "variant": ""},
    ])
    result = json_store.find_by_code("mf4d", "PILM004", "0001")
    assert len(result) == 2
    assert {r["variant"] for r in result} == {"A", "B"}


def test_json_store_does_not_cross_device_model(json_store, tmp_path):
    """同 code 但不同 device_model 不該被誤配對——find_by_code 是
    (device_model, code) 複合條件，不是只比對 code。"""
    _write_alarms(tmp_path, [
        {"department": "mf4d", "device_model": "PILM004", "code": "0001", "variant": ""},
        {"department": "mf4d", "device_model": "PILM003", "code": "0001", "variant": ""},
    ])
    result = json_store.find_by_code("mf4d", "PILM004", "0001")
    assert len(result) == 1
    assert result[0]["device_model"] == "PILM004"


def test_json_store_find_by_code_accepts_department_param_without_error(json_store, tmp_path):
    """department 參數：JsonStore 本身不做多租戶過濾（PLAN 3.2 節既有
    限制），這裡只驗證傳入不同 department 值呼叫時不會拋錯——不是驗證
    真正的跨部門隔離，那需要 SupabaseStore/真實 Supabase 環境
    （見 sentinel_pack/verify_isolation.sh）。"""
    _write_alarms(tmp_path, [
        {"department": "mf4d", "device_model": "PILM004", "code": "0001", "variant": ""},
    ])
    # JsonStore.load() 忽略 department 參數（單租戶），這裡只確認呼叫
    # 不會因為傳了不同 department 就拋錯或行為異常。
    result = json_store.find_by_code("other_dept", "PILM004", "0001")
    assert isinstance(result, list)


# ── SupabaseStore ────────────────────────────────────────────────────────────

def _make_supabase_store():
    return storage.SupabaseStore(
        "alarms", pk="code",
        pk_fields=["department", "device_model", "code", "variant"],
    )


def test_supabase_store_queries_with_department_device_model_code(monkeypatch):
    """find_by_code() 用目標查詢（同 get_one() 的理由），不是 load()
    整個部門撈下來——驗證查詢 URL 確實帶了三個過濾條件，不需要
    variant（那正是這個方法存在的理由：不知道 variant 才要反查）。"""
    store = _make_supabase_store()
    captured = {}

    def fake_req(method, path, body=None, extra_headers=None):
        captured["method"] = method
        captured["path"] = path
        return []

    monkeypatch.setattr(store, "_req", fake_req)
    store.find_by_code("mf4d", "PILM004", "0001")

    assert captured["method"] == "GET"
    assert "department=eq.mf4d" in captured["path"]
    assert "device_model=eq.PILM004" in captured["path"]
    assert "code=eq.0001" in captured["path"]
    assert "variant" not in captured["path"].split("?")[1] if "?" in captured["path"] else True


def test_supabase_store_no_match_returns_empty_list(monkeypatch):
    store = _make_supabase_store()
    monkeypatch.setattr(store, "_req", lambda *a, **k: [])
    result = store.find_by_code("mf4d", "PILM004", "9999")
    assert result == []


def test_supabase_store_multiple_variants_returns_all(monkeypatch):
    store = _make_supabase_store()
    rows = [
        {"department": "mf4d", "device_model": "PILM004", "code": "0001", "variant": "A"},
        {"department": "mf4d", "device_model": "PILM004", "code": "0001", "variant": "B"},
    ]
    monkeypatch.setattr(store, "_req", lambda *a, **k: rows)
    result = store.find_by_code("mf4d", "PILM004", "0001")
    assert len(result) == 2


def test_supabase_store_does_not_require_full_pk(monkeypatch):
    """跟 get_one()/delete_one()/patch_one() 不同，find_by_code() 不呼叫
    _require_full_pk_match()——這正是它存在的理由（不知道 variant 才
    要用這個方法反查），不該被那個要求完整主鍵的檢查擋下來。"""
    store = _make_supabase_store()
    monkeypatch.setattr(store, "_req", lambda *a, **k: [])
    # 不帶 variant 呼叫，不該拋 ValueError
    store.find_by_code("mf4d", "PILM004", "0001")
