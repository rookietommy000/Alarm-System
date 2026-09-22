"""超管登入告警（GET /api/admin/superadmin-login-log，外部審查 2026-09-22）。

背景：login_attempts 表本來就在記錄超管登入嘗試（_do_login() 裡
login_attempt_store.record(ip, SUPER_DEPT_SENTINEL, ok)），缺的是讀取
端點跟保留期不被既有 90 天節流清除策略誤清。這個端點用 superadmin_required
而非 admin_required——執行門檻是 superadmin，查看門檻不能只要 admin，
否則權限不對稱（同 DepartmentAuditLogStore 的既有教訓）。

**能力邊界**：pytest 環境用 JsonStore（_use_supabase()=False），
list_superadmin_attempts() 在這個模式下直接回空 list，不會真的對
Supabase 發送查詢——這裡測的是路由權限層級（403 檢查）跟參數驗證
（limit/success 格式），不是真實查詢邏輯本身，那部分只能在正式環境
用黑箱驗收確認（CLAUDE.md「測試的能力邊界」，test_no_fake_isolation_claims.py
會擋住宣稱測到這類機制的測試名稱）。
"""
import sys
from pathlib import Path

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


def test_limit_clamped_to_500_max(anon_client):
    """比照 /api/audit 既有模式：limit 夾在 1-500 之間，不是直接拒絕。"""
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log?limit=9999")
    assert r.get_json()["limit"] == 500


def test_limit_clamped_to_1_min(anon_client):
    r = _superadmin_client(anon_client).get("/api/admin/superadmin-login-log?limit=0")
    assert r.get_json()["limit"] == 1


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
