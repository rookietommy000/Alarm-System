"""DepartmentStore 的測試環境隔離（外部審查 2026-09-18）：這是全系統
唯一沒有走 _use_supabase() 分支判斷的 Store 類別——任何呼叫都直接打
`.env` 載入的真實 SUPABASE_URL，本機/pytest 環境完全沒有保護。這個
缺口是路由重導向調查（2026-09-02）時發現的，_guard_production_write()
護欄只擋寫入方法，讀取方法（get_by_id() 等）在 pytest 環境下依然會
真的對正式 Supabase 發送 GET 請求（因為 _guard_production_write() 對
pytest 環境整層豁免，跟這裡要補的隔離是兩個獨立問題）。

這裡直接測 DepartmentStore 本身在 _use_supabase()=False 時的行為，
不透過 app.py 端點層——既有的 test_department_cache_invalidation.py/
test_department_purge_count_reconciliation.py 都用 monkeypatch 直接
替換掉要測的方法本體，完全不會執行到這裡要驗證的 _use_supabase()
判斷分支，無法證明這個修法真的生效，這是本檔案存在的理由。
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import storage as storage_mod


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: False)
    return storage_mod.DepartmentStore()


def test_list_returns_empty_list_when_not_using_supabase(store):
    assert store.list() == []


def test_list_public_returns_empty_list_when_not_using_supabase(store):
    assert store.list_public() == []


def test_get_by_id_returns_none_when_not_using_supabase(store):
    assert store.get_by_id("any-dept") is None


def test_count_impact_returns_zero_counts_when_not_using_supabase(store):
    counts = store.count_impact("any-dept")
    assert counts == {
        "alarms": 0, "ai_scans": 0, "ai_corrections": 0, "ai_logs": 0,
        "feedback": 0, "alarm_views": 0, "alarm_history": 0, "devices": 0,
    }


def test_create_raises_runtime_error_when_not_using_supabase(store):
    with pytest.raises(RuntimeError, match="不支援建立部門"):
        store.create("dept1", "部門一", "pwhash", "adminpwhash")


def test_update_name_raises_runtime_error_when_not_using_supabase(store):
    with pytest.raises(RuntimeError, match="不支援改部門名稱"):
        store.update_name("dept1", "新名稱")


def test_update_password_raises_runtime_error_when_not_using_supabase(store):
    with pytest.raises(RuntimeError, match="不支援重設部門密碼"):
        store.update_password("dept1", pw_hash="newhash")


def test_set_active_raises_runtime_error_when_not_using_supabase(store):
    with pytest.raises(RuntimeError, match="不支援啟用/停用部門"):
        store.set_active("dept1", False)


def test_deletion_precondition_rejects_when_get_by_id_returns_none(store):
    """purge() 沒有獨立的 _use_supabase() 判斷——依賴 get_by_id() 已經
    回 None，落入既有的「部門不存在」前置條件判斷分支明確拒絕，驗證的
    是這個純邏輯分支（部門不存在時擋下），不是真實 Supabase 的硬刪除
    機制本身，不碰資料庫。"""
    with pytest.raises(PermissionError, match="不可硬刪除"):
        store.purge("dept1", "dept1", acknowledge_counts={})


def test_none_writes_reach_real_urlopen_when_not_using_supabase(store, monkeypatch):
    """反向驗證用的探針：確認上面幾個寫入方法在真正拋錯之前，完全沒有
    呼叫到 _urlopen()——不是「打了正式環境但剛好失敗」，是根本沒發送
    請求。這裡直接 monkeypatch storage_mod._urlopen 確認沒被呼叫到。"""
    calls = []
    monkeypatch.setattr(storage_mod, "_urlopen", lambda *a, **kw: calls.append(1))

    with pytest.raises(RuntimeError):
        store.create("dept1", "部門一", "pwhash", "adminpwhash")
    with pytest.raises(RuntimeError):
        store.update_name("dept1", "新名稱")
    with pytest.raises(RuntimeError):
        store.update_password("dept1", pw_hash="newhash")
    with pytest.raises(RuntimeError):
        store.set_active("dept1", False)
    store.get_by_id("dept1")
    store.list()
    store.list_public()
    store.count_impact("dept1")

    assert calls == []
