"""SupabaseStore.load() 的 TTL 快取行為測試（PLAN 效能優化第 4 項）。

純本地邏輯測試，monkeypatch _req() 計算呼叫次數，不依賴真實 Supabase。
驗證的是快取本身的正確性（命中/失效/department 邊界），不是驗證
「跨部門隔離」這件事本身在真實 PostgREST 語意下是否成立——那需要
sentinel_pack/verify_isolation.sh 對真實 Supabase 驗證（見該檔案新增
的連續查詢測試，以及 test_no_fake_isolation_claims.py 的說明）。
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import storage


def _make_store(cache_ttl=60):
    return storage.SupabaseStore(
        "alarms", pk="code",
        pk_fields=["department", "device_model", "code", "variant"],
        cache_ttl=cache_ttl,
    )


def _row(department, code="E001"):
    return {"department": department, "device_model": "M1", "code": code, "variant": ""}


def test_second_load_within_ttl_does_not_requery(monkeypatch):
    store = _make_store()
    calls = []

    def fake_req(method, path, body=None, extra_headers=None):
        calls.append(path)
        return [_row("mf4d")]

    monkeypatch.setattr(store, "_req", fake_req)
    store.load("mf4d")
    store.load("mf4d")
    assert len(calls) == 1


def test_cache_dict_uses_separate_key_per_department(monkeypatch):
    """快取鍵必須包含 department——這裡驗證的是 _cache dict 本身用不同
    department 當 key 存放各自結果的純邏輯，不是「真實 Supabase 環境下
    跨部門隔離是否生效」那個更高層級的保證（那需要 sentinel_pack/
    verify_isolation.sh 對真實 Supabase 連續查詢驗證，見該檔案 T-21）。"""
    store = _make_store()

    def fake_req(method, path, body=None, extra_headers=None):
        assert "department=eq." in path
        dept = "mf4d" if "mf4d" in path else "zztest"
        return [_row(dept)]

    monkeypatch.setattr(store, "_req", fake_req)
    mf4d_result = store.load("mf4d")
    zztest_result = store.load("zztest")
    assert mf4d_result[0]["department"] == "mf4d"
    assert zztest_result[0]["department"] == "zztest"


def test_department_none_bypasses_cache(monkeypatch):
    """department=None（總管跨部門查詢）不進快取——範圍不固定，快取鍵
    不該用特殊值代表「沒有邊界」，直接跳過快取最單純。"""
    store = _make_store()
    calls = []

    def fake_req(method, path, body=None, extra_headers=None):
        calls.append(path)
        return [_row("mf4d")]

    monkeypatch.setattr(store, "_req", fake_req)
    store.load(None)
    store.load(None)
    assert len(calls) == 2


def test_upsert_one_invalidates_only_its_own_department(monkeypatch):
    store = _make_store()
    calls = {"get": 0}

    def fake_req(method, path, body=None, extra_headers=None):
        if method == "GET":
            calls["get"] += 1
            dept = "mf4d" if "mf4d" in path else "zztest"
            return [_row(dept)]
        return [{**body[0]}]

    monkeypatch.setattr(store, "_req", fake_req)
    store.load("mf4d")
    store.load("zztest")
    assert calls["get"] == 2

    store.upsert_one(_row("mf4d", "E002"), department="mf4d", on_conflict="department,device_model,code,variant")

    store.load("mf4d")   # mf4d 快取已失效，要重查
    store.load("zztest")  # zztest 快取應仍有效，不重查
    assert calls["get"] == 3


def test_delete_one_invalidates_cache(monkeypatch):
    store = _make_store()
    calls = {"get": 0}

    def fake_req(method, path, body=None, extra_headers=None):
        if method == "GET":
            calls["get"] += 1
            return [_row("mf4d")]
        return []

    monkeypatch.setattr(store, "_req", fake_req)
    store.load("mf4d")
    store.delete_one(department="mf4d", match={"device_model": "M1", "code": "E001", "variant": ""})
    store.load("mf4d")
    assert calls["get"] == 2


def test_patch_one_invalidates_cache(monkeypatch):
    store = _make_store()
    calls = {"get": 0}

    def fake_req(method, path, body=None, extra_headers=None):
        if method == "GET":
            calls["get"] += 1
            return [_row("mf4d")]
        return [_row("mf4d")]

    monkeypatch.setattr(store, "_req", fake_req)
    store.load("mf4d")
    store.patch_one(department="mf4d", match={"device_model": "M1", "code": "E001", "variant": ""},
                     patch={"severity": "警告"})
    store.load("mf4d")
    assert calls["get"] == 2


def test_expired_cache_requeries(monkeypatch):
    store = _make_store(cache_ttl=1)
    calls = []

    def fake_req(method, path, body=None, extra_headers=None):
        calls.append(path)
        return [_row("mf4d")]

    monkeypatch.setattr(store, "_req", fake_req)
    store.load("mf4d")
    store._cache["mf4d"] = (0, store._cache["mf4d"][1])  # 模擬已過期
    store.load("mf4d")
    assert len(calls) == 2


def test_zero_ttl_disables_cache_entirely(monkeypatch):
    """cache_ttl=0（devices_store 等其他表的預設值）完全不啟用快取——
    這次改動不該影響其他表的行為。"""
    store = _make_store(cache_ttl=0)
    calls = []

    def fake_req(method, path, body=None, extra_headers=None):
        calls.append(path)
        return [_row("mf4d")]

    monkeypatch.setattr(store, "_req", fake_req)
    store.load("mf4d")
    store.load("mf4d")
    assert len(calls) == 2
