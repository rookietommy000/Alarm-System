"""部門寫入端點必須清除 _dept_cached() 查詢快取的不變量測試。

背景：create_department()/rename_department() 原本漏了呼叫
_invalidate_dept_cache()（其餘 3 個部門寫入端點：reset-password/
active/purge 都已經有）——修好後這裡釘住「這 5 個端點都必須呼叫
_invalidate_dept_cache()」這件事，避免之後有人加新的部門寫入端點時
又忘記補上。

_DEPT_CACHE 原本是 create_app() 函式內部的閉包變數，測試從外部拿不到
引用。改為在 create_app() 裡多一行 app.config["_DEPT_CACHE"] = _DEPT_CACHE
（同一份 dict 的引用，不是複製）——比照 SESSION_COOKIE_SECURE 等既有
create_app() 內部值掛 app.config 的寫法，純資料結構暴露，不改變任何
生產行為。有了這個管道，測試才能真正驗證「呼叫端點後快取項目確實被
清除」，而不是只驗證「端點呼叫沒有出錯」——後者曾經被證實是假陽性：
拿掉 _invalidate_dept_cache() 那一行後，用舊寫法的測試依然 PASSED，
完全沒有偵測到 bug。這裡改寫後有做反向驗證（見開發過程記錄），拿掉
修法確實會讓測試 FAILED。

DepartmentStore 沒有 JsonStore fallback（跟 AlarmSuggestionStore/
LoginAttemptStore 同款，只服務 Supabase），create()/update_name() 等
方法一律直接打真實 HTTP，這裡用 monkeypatch 讓它們不真的發網路請求。
"""
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def _superadmin_client(anon_client):
    """本機/測試模式下 /admin/login 只能拿到 admin=True（見 app.py
    admin_login_submit() 的本機 fallback 分支），無法拿到
    superadmin=True——要測 superadmin_required 保護的端點，必須用
    session_transaction() 手動設定 session，這是本專案第一次需要測試
    這個權限層級的端點，沒有既有先例可循，採 Flask 測試的標準做法。
    assert_session_valid() 在 _use_supabase()=False 時直接 return（見
    app.py 該函式 docstring），不會因為缺少真實部門資料而擋下這個
    手動構造的 session。"""
    with anon_client.session_transaction() as sess:
        sess["auth"] = True
        sess["admin"] = True
        sess["superadmin"] = True
        sess["department"] = None
    return anon_client


def _seed_cache(client, dept_id: str) -> None:
    """手動塞一筆假快取值進去，模擬「這個部門 id 之前被查過、快取裡
    還留著（可能已過期或未過期的）舊資料」的情境——(row, timestamp)
    的 tuple 格式對應 app.py _dept_cached() 的實際存放格式。"""
    cache = client.application.config["_DEPT_CACHE"]
    cache[dept_id] = ({"id": dept_id, "name": "舊快取值", "active": True}, time.monotonic())


def test_create_department_invalidates_cache(anon_client, monkeypatch):
    import storage as storage_mod

    monkeypatch.setattr(
        storage_mod.department_store, "create",
        lambda dept_id, name, pw_hash, admin_pw_hash, **kw: {
            "id": dept_id, "name": name, "pw_hash": pw_hash, "admin_pw_hash": admin_pw_hash,
        },
    )
    client = _superadmin_client(anon_client)
    _seed_cache(client, "newdept")
    assert "newdept" in client.application.config["_DEPT_CACHE"]

    r = client.post("/api/admin/departments", json={
        "id": "newdept", "name": "新部門",
        "password": "pw12345678", "admin_password": "adminpw12345678",
    })

    assert r.status_code == 201
    assert "newdept" not in client.application.config["_DEPT_CACHE"]


def test_rename_department_invalidates_cache(anon_client, monkeypatch):
    import storage as storage_mod

    monkeypatch.setattr(
        storage_mod.department_store, "update_name",
        lambda dept_id, name: None,
    )
    client = _superadmin_client(anon_client)
    _seed_cache(client, "existing-dept")
    assert "existing-dept" in client.application.config["_DEPT_CACHE"]

    r = client.put("/api/admin/departments/existing-dept", json={"name": "改名後"})

    assert r.status_code == 200
    assert "existing-dept" not in client.application.config["_DEPT_CACHE"]
