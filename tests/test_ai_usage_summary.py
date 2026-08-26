"""GET /api/ai-usage-summary 的權限層級與回應格式測試。

只驗證 login 層級即可存取（不需要 admin）、回應只含 month_count 這一個
低風險欄位（不洩漏 token/金額等留在 /api/admin/ai-usage-stats 的敏感
細節）。真正的跨部門查詢隔離只在真實 Supabase 才測得到（JsonStore
fallback 下 usage_stats() 直接回 0，見 storage.py），此處不宣稱驗證
那件事。
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def test_requires_login(anon_client):
    r = anon_client.get("/api/ai-usage-summary")
    assert r.status_code == 401


def test_login_only_user_can_access_without_admin(anon_client):
    """一般使用者（非管理員）也能存取——這支端點是 login 層級，不是
    admin_required，跟後台的 /api/admin/ai-usage-stats 權限邊界不同。"""
    anon_client.post("/login", data={"department": "local", "password": "test-pw"})
    r = anon_client.get("/api/ai-usage-summary")
    assert r.status_code == 200


def test_response_only_contains_month_count(anon_client):
    """只回 month_count 一個欄位，不含 by_department/token 數字這類
    留在 admin 層級的敏感細節。"""
    anon_client.post("/login", data={"department": "local", "password": "test-pw"})
    r = anon_client.get("/api/ai-usage-summary")
    data = r.get_json()
    assert set(data.keys()) == {"month_count"}
    assert isinstance(data["month_count"], int)
