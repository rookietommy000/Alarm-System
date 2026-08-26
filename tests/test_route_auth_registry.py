"""
路由權限白名單自動測試（PLAN 8.1 節）。

驗證兩件事：
1. 每個 /api/* 的 (rule, method) 組合都在 ROUTE_AUTH_REGISTRY 登記
2. view function 上實際掛著對應的 _auth_level 標記（不只是字典裡有登記）

只驗字典不驗執行的話，忘記掛裝飾器的路由測試照樣會綠——這裡兩者都要對上。
"""
import ast
import inspect
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

# key 為 (rule, method) 元組——權限層級是按 HTTP method 分的，不是按路徑分的
# （PLAN 4.4/8.1 節第七輪審查修正）。
ROUTE_AUTH_REGISTRY = {
    ("/api/alarms", "GET"): "login",
    ("/api/alarms/<department>/<device_model>/<code>", "GET"): "login",
    ("/api/alarms/<department>", "POST"): "admin",
    ("/api/alarms/<department>/<device_model>/<code>", "PUT"): "admin",
    ("/api/alarms/<department>/<device_model>/<code>", "DELETE"): "admin",

    ("/api/admin/bulk-import/<department>/preview", "POST"): "admin",
    ("/api/admin/bulk-import/<department>/commit", "POST"): "admin",
    ("/api/admin/import/<department>/inspect", "POST"): "admin",
    ("/api/admin/import/<department>/split", "POST"): "admin",
    ("/api/admin/import/<department>/snapshots", "GET"): "admin",
    ("/api/admin/import/<department>/snapshots/<int:snapshot_id>/undo", "POST"): "admin",
    ("/api/admin/semantic-review/<department>", "GET"): "admin",
    ("/api/admin/semantic-review/<department>/<int:index>", "PUT"): "admin",

    # 現場處置做法（PLAN_local_solution.md）——審核路徑停用決策後，
    # local 端點改為任何登入者皆可編輯，不再限管理員
    ("/api/alarms/<department>/<device_model>/<code>/local", "PUT"): "login",
    ("/api/alarms/<department>/<device_model>/<code>/suggestions", "POST"): "login",
    ("/api/admin/suggestions", "GET"): "admin",
    ("/api/admin/suggestions/<int:suggestion_id>", "PUT"): "admin",
    ("/api/alarms/<department>/<device_model>/<code>/history", "GET"): "login",

    ("/api/devices", "GET"): "login",
    ("/api/devices/<department>", "POST"): "admin",
    ("/api/devices/<department>/<device_model>", "GET"): "login",
    ("/api/devices/<department>/<device_model>", "PUT"): "admin",
    ("/api/devices/<department>/<device_model>", "DELETE"): "admin",

    ("/api/server-url", "GET"): "public",

    ("/api/feedback", "POST"): "login",
    ("/api/feedback/stats", "GET"): "login",
    ("/api/view", "POST"): "login",
    ("/api/view/stats", "GET"): "login",

    ("/api/analyze", "POST"): "login",
    ("/api/confirm", "POST"): "login",
    ("/api/correct", "POST"): "login",
    ("/api/ai-usage-summary", "GET"): "login",

    ("/api/audit", "GET"): "admin",

    ("/api/admin/scan-stats", "GET"): "admin",
    ("/api/admin/scan-recent", "GET"): "admin",
    ("/api/admin/scan-ranking", "GET"): "admin",
    ("/api/admin/ai-logs", "GET"): "admin",
    ("/api/admin/ai-usage-stats", "GET"): "admin",
    ("/api/admin/cleanup-expired", "POST"): "superadmin",

    ("/api/admin/departments", "GET"): "superadmin",
    ("/api/admin/departments", "POST"): "superadmin",
    ("/api/admin/departments/<dept_id>", "PUT"): "superadmin",
    ("/api/admin/departments/<dept_id>/reset-password", "PUT"): "superadmin",
    ("/api/admin/departments/<dept_id>/active", "PUT"): "superadmin",
    ("/api/admin/departments/<dept_id>/impact", "GET"): "superadmin",
    ("/api/admin/departments/<dept_id>", "DELETE"): "superadmin",

    ("/api/whoami", "GET"): "public",
    ("/api/departments/public", "GET"): "public",
}


@pytest.fixture
def app_instance(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "devices.json").write_text("[]", encoding="utf-8")
    (data_dir / "alarms.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("ALARM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LOGIN_PASSWORD", "test-pw")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")

    for mod in ("app", "storage"):
        sys.modules.pop(mod, None)
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


def test_all_api_routes_registered_and_decorated(app_instance):
    seen = set()
    for rule in app_instance.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            key = (rule.rule, method)
            seen.add(key)
            assert key in ROUTE_AUTH_REGISTRY, f"{method} {rule.rule} 未登記權限層級"

            view_func = app_instance.view_functions[rule.endpoint]
            declared_level = ROUTE_AUTH_REGISTRY[key]
            actual_level = getattr(view_func, "_auth_level", None)
            assert actual_level == declared_level, (
                f"{method} {rule.rule} 宣告層級為 {declared_level!r}，"
                f"但實際裝飾器標記為 {actual_level!r}"
                f"（可能忘記掛裝飾器或掛錯層級，若確實要公開必須明確加上 @public_endpoint）"
            )

    stale = set(ROUTE_AUTH_REGISTRY) - seen
    assert not stale, f"白名單裡有已不存在於 app.py 的路由，需要清理：{stale}"


def test_resolve_target_department_does_not_read_request_args():
    """確保 resolve_target_department() 的目標部門只來自 URL path，
    不會被重構時 fallback 到 request.args（那樣會讓超管的讀寫來源再度分岔）。

    用 AST 解析並排除 docstring 後才檢查函式本體——函式自己的 docstring 裡
    就寫著「不讀 request.args」這句說明文字，naive 字串比對會被這行誤判
    （第二十一輪審查發現的陷阱）。
    """
    import app as app_module

    source = inspect.getsource(app_module.create_app)
    tree = ast.parse(source)

    target_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_target_department":
            target_func = node
            break
    assert target_func is not None, "找不到 resolve_target_department 函式定義"

    body = target_func.body
    # 跳過開頭的 docstring 節點（Expr(Constant(str))），只檢查真正執行的敘述
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]

    body_source = "\n".join(ast.unparse(stmt) for stmt in body)
    assert "request.args" not in body_source, (
        "resolve_target_department() 不得讀取 request.args —— "
        "目標部門必須只來自 URL path 參數（見 PLAN 4.1 節配套規則 a）"
    )
