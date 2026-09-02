"""DepartmentStore.purge() 的筆數對帳：呼叫端傳入的 acknowledge_counts
（使用者在確認畫面看過的筆數快照）必須跟動手刪除前重新現算的 actual
一致，不一致要擋下來，不能信任前端傳來的數字當真值。

背景（顧問裁決，2026-09-02）：/impact 拿到筆數後，使用者猶豫期間如果
資料又變動（例如另一個人同時在匯入），這正是最該停下來的時刻，不能
假設「使用者按過確認了就代表可以刪」。純邏輯測試，monkeypatch
count_impact()/get_by_id()/_req()，不依賴真實 Supabase 連線。
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import pytest

import storage as storage_mod


_TABLES = ("alarms", "ai_scans", "ai_corrections", "ai_logs",
           "feedback", "alarm_views", "alarm_history", "devices")


@pytest.fixture
def store(monkeypatch):
    s = storage_mod.DepartmentStore()
    monkeypatch.setattr(s, "get_by_id", lambda dept_id: {"id": dept_id, "purgeable": True})
    return s


def _zero_counts() -> dict:
    return {t: 0 for t in _TABLES}


def test_matching_counts_permits_deletion(store, monkeypatch):
    """確認的筆數跟現算的一致時，正常往下刪除。"""
    counts = {**_zero_counts(), "alarms": 5}
    monkeypatch.setattr(store, "count_impact", lambda dept_id: dict(counts))
    monkeypatch.setattr(store, "_req", lambda *a, **kw: [])

    removed = store.purge("dept1", "dept1", acknowledge_counts=counts)

    assert isinstance(removed, dict)


def test_mismatched_counts_blocks_deletion_and_reports_actual(store, monkeypatch):
    """使用者確認畫面看到的是 alarms=5，但動手刪除前重新現算變成
    alarms=8（確認後資料又變動）——必須擋下來，不能刪，錯誤訊息要帶
    實際筆數讓呼叫端能顯示清楚的重新確認訊息。"""
    confirmed = {**_zero_counts(), "alarms": 5}
    actual = {**_zero_counts(), "alarms": 8}
    monkeypatch.setattr(store, "count_impact", lambda dept_id: dict(actual))
    delete_calls = []
    monkeypatch.setattr(store, "_req", lambda method, *a, **kw: delete_calls.append(method))

    with pytest.raises(ValueError) as exc_info:
        store.purge("dept1", "dept1", acknowledge_counts=confirmed)

    assert "5" in str(exc_info.value)
    assert "8" in str(exc_info.value)
    # 對帳失敗時完全不該打任何刪除請求——不是部分刪除、不是先刪一半
    # 再中斷，是整個 purge 動作在真正刪除任何一筆資料之前就先擋下來。
    assert delete_calls == []


def test_actual_is_recomputed_not_trusted_from_caller(store, monkeypatch):
    """就算呼叫端傳來的 acknowledge_counts 剛好跟某個舊快照吻合，比對
    基準必須是「呼叫 purge() 當下」現算的 count_impact()，不是接受
    呼叫端聲稱的任何東西當真值——這裡驗證 count_impact() 確實被重新
    呼叫過，不是被繞過。"""
    calls = []
    counts = {**_zero_counts(), "devices": 2}

    def _count_impact(dept_id):
        calls.append(dept_id)
        return dict(counts)

    monkeypatch.setattr(store, "count_impact", _count_impact)
    monkeypatch.setattr(store, "_req", lambda *a, **kw: [])

    store.purge("dept1", "dept1", acknowledge_counts=counts)

    assert calls == ["dept1"]


def test_missing_table_in_acknowledge_counts_is_treated_as_mismatch(store, monkeypatch):
    """acknowledge_counts 缺某張表的 key（例如前端版本不同步、少送一個
    欄位）不該被當成「這張表是 0 筆」悄悄放行——跟其他不符情況一樣擋下
    來，不用特殊處理，dict 直接比較本來就會讓缺 key 視為不相等。"""
    actual = {**_zero_counts(), "alarms": 3}
    incomplete = {k: v for k, v in actual.items() if k != "devices"}
    monkeypatch.setattr(store, "count_impact", lambda dept_id: dict(actual))
    monkeypatch.setattr(store, "_req", lambda *a, **kw: [])

    with pytest.raises(ValueError):
        store.purge("dept1", "dept1", acknowledge_counts=incomplete)


# ── /api/admin/departments/<dept_id> DELETE 端點層驗證 ─────────────────

def _superadmin_client(anon_client):
    """同 test_department_cache_invalidation.py 的 helper——本機/測試
    模式下 /admin/login 只能拿到 admin=True，測 superadmin_required
    保護的端點必須用 session_transaction() 手動設定。這裡各自複製一份
    （非共用），只有兩處用到，第三次重複時再收斂進 conftest.py。"""
    with anon_client.session_transaction() as sess:
        sess["auth"] = True
        sess["admin"] = True
        sess["superadmin"] = True
        sess["department"] = None
    return anon_client


def test_deletion_endpoint_requires_acknowledge_counts(anon_client):
    """body 完全不帶 acknowledge_counts（或不是 dict）要在進入
    department_store.purge() 之前就被端點層擋下 400，不能讓
    acknowledge_counts=None 一路傳進 purge() 內部才出錯。"""
    client = _superadmin_client(anon_client)

    r = client.delete("/api/admin/departments/some-dept", json={"confirm_id": "some-dept"})

    assert r.status_code == 400
    assert "acknowledge_counts" in r.get_json()["error"]


def test_deletion_endpoint_converts_value_error_to_400(anon_client, monkeypatch):
    """department_store.purge() 因對帳不符拋 ValueError 時，端點層要
    轉成 400 並把訊息（含 confirmed/actual 明確數字）帶給呼叫端，不是
    讓例外冒到未處理變成 500。"""
    import storage as storage_mod

    def _fake_purge(dept_id, confirm_id, acknowledge_counts):
        raise ValueError("確認的筆數與目前實際資料不符（確認後資料已變動），請重新確認：{'alarms': {'confirmed': 5, 'actual': 8}}")

    monkeypatch.setattr(storage_mod.department_store, "purge", _fake_purge)
    client = _superadmin_client(anon_client)

    r = client.delete("/api/admin/departments/some-dept", json={
        "confirm_id": "some-dept", "acknowledge_counts": {"alarms": 5},
    })

    assert r.status_code == 400
    body = r.get_json()["error"]
    assert "5" in body and "8" in body
