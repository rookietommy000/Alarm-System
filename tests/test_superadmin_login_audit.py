"""超管登入告警（GET /api/admin/superadmin-login-log，外部審查 2026-09-22）。

背景：login_attempts 表本來就在記錄超管登入嘗試（_do_login() 裡
login_attempt_store.record(ip, SUPER_DEPT_SENTINEL, ok)），缺的是讀取
端點跟保留期不被既有 90 天節流清除策略誤清。這個端點用 superadmin_required
而非 admin_required——執行門檻是 superadmin，查看門檻不能只要 admin，
否則權限不對稱（同 DepartmentAuditLogStore 的既有教訓）。

**能力邊界**：pytest 環境用 JsonStore（_use_supabase()=False），
list_superadmin_attempts() 在這個模式下直接回空 list，不會真的對
Supabase 發送查詢——這裡測的是路由權限層級（403 檢查）、參數驗證
（limit/success 格式），以及 mock 攔截的查詢與清除請求組成。
不驗證真實資料庫回傳或刪除效果（CLAUDE.md「測試的能力邊界」，test_no_fake_isolation_claims.py
會擋住宣稱測到這類機制的測試名稱）。
"""
import sys
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import Mock

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def _superadmin_client(anon_client):
    """本機/測試模式下 /admin/login 只能拿到 admin=True，測
    superadmin_required 保護的端點必須用 session_transaction() 手動設定
    （同 test_department_cache_invalidation.py 的既有 helper）。"""
    with anon_client.session_transaction() as sess:
        sess["auth"] = True
        sess["admin"] = True
        sess["superadmin"] = True
        sess["department"] = None
    return anon_client


def test_requires_superadmin_not_just_admin(client):
    """client fixture 只有 admin=True（見 conftest.py），沒有
    superadmin=True——驗證這個端點的權限層級確實是 superadmin，不是
    admin 就能看，這是規格文件驗收清單第 3 條的核心要求。"""
    r = client.get("/api/admin/superadmin-login-log")
    assert r.status_code == 403


def test_anonymous_gets_403_not_redirect(anon_client):
    """完全未登入時，/api/* 路徑的裝飾器回 403 JSON，不重導（同既有
    admin_required/superadmin_required 對 /api/* 的既有行為）。"""
    r = anon_client.get("/api/admin/superadmin-login-log")
    assert r.status_code == 403


def test_superadmin_can_access_returns_200(anon_client):
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log")
    assert r.status_code == 200
    body = r.get_json()
    assert "items" in body
    assert "limit" in body


def test_default_limit_is_100(anon_client):
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log")
    assert r.get_json()["limit"] == 100


def test_limit_param_respected(anon_client):
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log?limit=50")
    assert r.get_json()["limit"] == 50


@pytest.mark.parametrize("limit", ["0", "-1", "501", "9999", "", "1.5"])
def test_invalid_limit_returns_400_without_query(anon_client, monkeypatch, limit):
    query = Mock()
    monkeypatch.setattr(sys.modules["app"].login_attempt_store, "list_superadmin_attempts", query)
    r = _superadmin_client(anon_client).get(
        "/api/admin/superadmin-login-log", query_string={"limit": limit}
    )
    assert r.status_code == 400
    query.assert_not_called()


def test_non_numeric_limit_returns_400(anon_client):
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log?limit=abc")
    assert r.status_code == 400


def test_success_true_accepted(anon_client):
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log?success=true")
    assert r.status_code == 200


def test_success_false_accepted(anon_client):
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log?success=false")
    assert r.status_code == 200


def test_invalid_success_value_returns_400(anon_client):
    """success 只接受字面值 true/false，其他值（含常見的誤用如 1/0/yes）
    都應該明確拒絕，不是靜默忽略或當成 truthy 判斷。"""
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log?success=1")
    assert r.status_code == 400


def test_items_empty_list_in_local_mode(anon_client):
    """_use_supabase()=False 時 list_superadmin_attempts() 回空 list，
    不是真的查詢結果——這裡驗證的是「本機模式下端點不會報錯、回傳空
    結果」這個介面契約，不是驗證真實查詢邏輯（見檔案開頭能力邊界說明）。"""
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log")
    assert r.get_json()["items"] == []


@pytest.mark.parametrize("success, expected", [(None, None), ("true", True), ("false", False)])
@pytest.mark.parametrize("limit", [1, 100, 500])
def test_route_passes_query_arguments_and_returns_mock_items(anon_client, monkeypatch, success, expected, limit):
    """只驗證參數傳遞與 JSON 回應，資料由 mock 提供。"""
    items = [{"ip": "1.2.3.4", "success": False, "attempted_at": "2026-09-22T10:00:00Z"}]
    query = Mock(return_value=items)
    monkeypatch.setattr(sys.modules["app"].login_attempt_store, "list_superadmin_attempts", query)
    params = {} if limit == 100 else {"limit": limit}
    if success is not None:
        params["success"] = success
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log", query_string=params)
    assert r.status_code == 200
    assert r.get_json() == {"items": items, "limit": limit}
    query.assert_called_once_with(limit, expected)


@pytest.mark.parametrize("success", [None, True, False])
def test_list_request_query_parameters(monkeypatch, success):
    """攔截 _req，只檢查查詢組成，不驗證資料庫篩選效果。"""
    import storage

    store = storage.LoginAttemptStore()
    req = Mock(return_value=[])
    monkeypatch.setattr(store, "_req", req)
    monkeypatch.setattr(storage, "_use_supabase", lambda: True)
    assert store.list_superadmin_attempts(37, success) == []
    req.assert_called_once()
    method, path = req.call_args.args
    table, query = path.split("?", 1)
    assert method == "GET"
    assert table == "login_attempts"
    expected = {
        "select": ["ip,success,attempted_at"],
        "department": ["eq.__super__"],
        "order": ["attempted_at.desc"],
        "limit": ["37"],
    }
    if success is not None:
        expected["success"] = ["eq.true" if success else "eq.false"]
    assert parse_qs(query) == expected


def test_cleanup_request_includes_superadmin_exclusion(monkeypatch):
    """攔截 DELETE 請求，不執行或宣稱驗證真實資料刪除。"""
    from datetime import datetime, timedelta, timezone
    import storage

    store = storage.LoginAttemptStore()
    req = Mock(return_value=[{}, {}])
    monkeypatch.setattr(store, "_req", req)
    monkeypatch.setattr(storage, "_use_supabase", lambda: True)
    before = datetime.now(timezone.utc) - timedelta(days=90)
    assert store.cleanup_expired() == 2
    after = datetime.now(timezone.utc) - timedelta(days=90)
    req.assert_called_once()
    method, path = req.call_args.args
    table, query = path.split("?", 1)
    assert method == "DELETE"
    assert table == "login_attempts"
    params = parse_qs(query)
    assert set(params) == {"attempted_at", "department"}
    assert params["department"] == ["neq.__super__"]
    operator, cutoff = params["attempted_at"][0].split(".", 1)
    assert operator == "lt"
    assert before <= datetime.fromisoformat(cutoff) <= after
    assert req.call_args.kwargs == {"extra_headers": {"Prefer": "return=representation"}}
