"""部門管理端點（rename_department()/department_action()）的稽核軌跡
（DepartmentAuditLogStore，見 migration 011_add_department_audit_log.sql）。

背景：這兩個端點原本完全沒有 audit log（外部審查 2026-09-02 路由重
導向調查時發現的獨立缺口，使用者裁決另開一輪處理）。這裡驗證的是
app.py 端點層的組裝邏輯（before/after 值是否記對、reset_password
是否確實不碰密碼相關內容、寫入失敗時是否還會誤記稽核），不是
DepartmentAuditLogStore 對真實 Supabase 是否真的生效——兩者是不同
等級的結論（CLAUDE.md「測試的能力邊界」）。

特別要驗證的交互：DepartmentStore（storage.py）已補上 _use_supabase()
判斷（commit 98f4e35），get_by_id() 在 _use_supabase()=False 時回
None、update_name()/update_password()/set_active() 則明確拋出
RuntimeError——這裡驗證 rename_department()/department_action() 的
before-name/before-active 查詢邏輯在 get_by_id() 回 None 時不會炸掉，
以及寫入方法失敗（RuntimeError）時不會誤記一筆「操作發生過」的稽核
記錄（順序：先寫入、成功才記稽核，不是反過來）。
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def _superadmin_client(anon_client):
    """同 test_department_cache_invalidation.py 的既有 helper——本機/
    測試模式下 /admin/login 只能拿到 admin=True，測 superadmin_required
    保護的端點必須用 session_transaction() 手動設定。"""
    with anon_client.session_transaction() as sess:
        sess["auth"] = True
        sess["admin"] = True
        sess["superadmin"] = True
        sess["department"] = None
    return anon_client


def test_rename_logs_before_and_after_name(anon_client, monkeypatch):
    import storage as storage_mod

    monkeypatch.setattr(
        storage_mod.department_store, "get_by_id",
        lambda dept_id: {"id": dept_id, "name": "舊名稱"},
    )
    monkeypatch.setattr(storage_mod.department_store, "update_name", lambda dept_id, name: None)
    log_calls = []
    monkeypatch.setattr(
        storage_mod.department_audit_log_store, "log",
        lambda *a, **kw: log_calls.append((a, kw)),
    )
    client = _superadmin_client(anon_client)

    r = client.put("/api/admin/departments/existing-dept", json={"name": "新名稱"})

    assert r.status_code == 200
    assert len(log_calls) == 1
    args, kwargs = log_calls[0]
    assert args[:2] == ("existing-dept", "rename")
    assert kwargs["before_value"] == "舊名稱"
    assert kwargs["after_value"] == "新名稱"


def test_rename_logs_before_value_none_when_get_by_id_returns_none(anon_client, monkeypatch):
    """get_by_id() 回 None（本機模式下的既有行為，或部門查詢當下剛好
    不存在）時，before_value 要傳 None，不能對 None.get() 炸掉
    AttributeError——這是這次要驗證的核心交互，確認 rename_department()
    的 before.get("name") if before else None 這行防呆確實有效。"""
    import storage as storage_mod

    monkeypatch.setattr(storage_mod.department_store, "get_by_id", lambda dept_id: None)
    monkeypatch.setattr(storage_mod.department_store, "update_name", lambda dept_id, name: None)
    log_calls = []
    monkeypatch.setattr(
        storage_mod.department_audit_log_store, "log",
        lambda *a, **kw: log_calls.append((a, kw)),
    )
    client = _superadmin_client(anon_client)

    r = client.put("/api/admin/departments/ghost-dept", json={"name": "新名稱"})

    assert r.status_code == 200
    assert len(log_calls) == 1
    args, kwargs = log_calls[0]
    assert kwargs["before_value"] is None
    assert kwargs["after_value"] == "新名稱"


def test_rename_does_not_log_when_update_name_fails(anon_client, monkeypatch):
    """update_name() 失敗（例如本機模式下 DepartmentStore 拋出的
    RuntimeError，或任何寫入層錯誤）時，不該誤記一筆稽核記錄——先
    寫入、成功才記稽核，順序不能顛倒，否則稽核記錄會顯示「改名成功」
    但實際上沒有真的改到。"""
    import storage as storage_mod

    monkeypatch.setattr(
        storage_mod.department_store, "get_by_id",
        lambda dept_id: {"id": dept_id, "name": "舊名稱"},
    )

    def _fail_update(dept_id, name):
        raise RuntimeError("模擬本機模式或網路失敗")

    monkeypatch.setattr(storage_mod.department_store, "update_name", _fail_update)
    log_calls = []
    monkeypatch.setattr(
        storage_mod.department_audit_log_store, "log",
        lambda *a, **kw: log_calls.append((a, kw)),
    )
    client = _superadmin_client(anon_client)

    # TESTING=True 讓 Flask 把例外原樣往外拋（不吞成 500 回應），這裡要
    # 驗證的是稽核記錄真的沒被寫入，例外本身有沒有被轉成 500 不是這個
    # 測試的重點（同 test_pending_alarm_imports.py 的既有處理方式）。
    with pytest.raises(RuntimeError, match="模擬本機模式或網路失敗"):
        client.put("/api/admin/departments/existing-dept", json={"name": "新名稱"})

    assert log_calls == []


def test_reset_password_logs_action_without_password_content(anon_client, monkeypatch):
    """reset_password 只記「動作發生過」——before_value/after_value
    皆為 None，絕對不能出現在呼叫參數裡的任何密碼相關內容（明文或
    雜湊值），這是 DepartmentAuditLogStore.log() docstring 的紅線。"""
    import storage as storage_mod

    monkeypatch.setattr(
        storage_mod.department_store, "update_password",
        lambda dept_id, pw_hash=None, admin_pw_hash=None: None,
    )
    log_calls = []
    monkeypatch.setattr(
        storage_mod.department_audit_log_store, "log",
        lambda *a, **kw: log_calls.append((a, kw)),
    )
    client = _superadmin_client(anon_client)

    r = client.put("/api/admin/department-actions/existing-dept",
                    json={"action": "reset_password", "password": "new-secret-password"})

    assert r.status_code == 200
    assert len(log_calls) == 1
    args, kwargs = log_calls[0]
    assert args[:2] == ("existing-dept", "reset_password")
    assert kwargs.get("before_value") is None
    assert kwargs.get("after_value") is None
    # 逐一確認整個呼叫參數（args + kwargs）裡完全不含測試送入的明文密碼
    all_values = [str(v) for v in args] + [str(v) for v in kwargs.values()]
    assert not any("new-secret-password" in v for v in all_values)


def test_reset_password_does_not_log_when_update_password_fails(anon_client, monkeypatch):
    import storage as storage_mod

    def _fail_update(dept_id, pw_hash=None, admin_pw_hash=None):
        raise RuntimeError("模擬本機模式或網路失敗")

    monkeypatch.setattr(storage_mod.department_store, "update_password", _fail_update)
    log_calls = []
    monkeypatch.setattr(
        storage_mod.department_audit_log_store, "log",
        lambda *a, **kw: log_calls.append((a, kw)),
    )
    client = _superadmin_client(anon_client)

    with pytest.raises(RuntimeError, match="模擬本機模式或網路失敗"):
        client.put("/api/admin/department-actions/existing-dept",
                    json={"action": "reset_password", "password": "new-secret-password"})

    assert log_calls == []


def test_active_logs_before_and_after_bool(anon_client, monkeypatch):
    import storage as storage_mod

    monkeypatch.setattr(
        storage_mod.department_store, "get_by_id",
        lambda dept_id: {"id": dept_id, "active": True},
    )
    monkeypatch.setattr(storage_mod.department_store, "set_active", lambda dept_id, active: None)
    log_calls = []
    monkeypatch.setattr(
        storage_mod.department_audit_log_store, "log",
        lambda *a, **kw: log_calls.append((a, kw)),
    )
    client = _superadmin_client(anon_client)

    r = client.put("/api/admin/department-actions/existing-dept",
                    json={"action": "active", "active": False})

    assert r.status_code == 200
    assert len(log_calls) == 1
    args, kwargs = log_calls[0]
    assert args[:2] == ("existing-dept", "active")
    assert kwargs["before_value"] == "True"
    assert kwargs["after_value"] == "False"


def test_active_logs_before_value_none_when_get_by_id_returns_none(anon_client, monkeypatch):
    """同 rename 的情境：get_by_id() 回 None 時 before_value 要傳
    None，不能對 None.get() 炸掉。"""
    import storage as storage_mod

    monkeypatch.setattr(storage_mod.department_store, "get_by_id", lambda dept_id: None)
    monkeypatch.setattr(storage_mod.department_store, "set_active", lambda dept_id, active: None)
    log_calls = []
    monkeypatch.setattr(
        storage_mod.department_audit_log_store, "log",
        lambda *a, **kw: log_calls.append((a, kw)),
    )
    client = _superadmin_client(anon_client)

    r = client.put("/api/admin/department-actions/ghost-dept",
                    json={"action": "active", "active": True})

    assert r.status_code == 200
    assert len(log_calls) == 1
    args, kwargs = log_calls[0]
    assert kwargs["before_value"] is None
    assert kwargs["after_value"] == "True"


def test_active_does_not_log_when_set_active_fails(anon_client, monkeypatch):
    import storage as storage_mod

    monkeypatch.setattr(
        storage_mod.department_store, "get_by_id",
        lambda dept_id: {"id": dept_id, "active": True},
    )

    def _fail_set_active(dept_id, active):
        raise RuntimeError("模擬本機模式或網路失敗")

    monkeypatch.setattr(storage_mod.department_store, "set_active", _fail_set_active)
    log_calls = []
    monkeypatch.setattr(
        storage_mod.department_audit_log_store, "log",
        lambda *a, **kw: log_calls.append((a, kw)),
    )
    client = _superadmin_client(anon_client)

    with pytest.raises(RuntimeError, match="模擬本機模式或網路失敗"):
        client.put("/api/admin/department-actions/existing-dept",
                    json={"action": "active", "active": False})

    assert log_calls == []
