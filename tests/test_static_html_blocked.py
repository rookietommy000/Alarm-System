"""
守住「frontend/*.html 不能被靜態路徑直接存取」。

背景：backend/app.py 的 Flask(static_folder=FRONTEND, static_url_path="")
把 frontend/ 下所有檔案掛在根路徑，這是 Flask 靜態伺服器的行為，完全繞過
@app.route 上的 @admin_required/@login_required 裝飾器。實測正式環境
/dashboard.html、/admin.html 等 HTML 未登入即可直接讀取（HTTP 200）。

verify_isolation.sh 測不到這件事——它比對的是 API 回應裡有沒有哨兵標記，
HTML 模板本身不含任何資料，永遠不會命中，是工具能力邊界問題（跟部門隔離
測不到跨部門過濾是同一類：pytest 環境測不到、curl 對 API 測不到，都不
代表問題不存在，只代表現有工具的檢查範圍沒有涵蓋到）。

修法是 backend/app.py 的 _block_direct_html_access() before_request：
只擋 .html 直接存取（404，不透露檔案是否存在），不改 static_url_path
本身——那會牽動所有靜態資源路徑、sw.js 的 STATIC_SHELL、manifest 的
icons 路徑，且讓平板上既有的 Service Worker 快取全部失效。
"""

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """未登入的 client——這裡要測的正是「沒有 session 也能不能直接讀到
    HTML」，不能沿用 test_api.py 那個已建立 admin session 的 fixture。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "devices.json").write_text("[]", encoding="utf-8")
    (data_dir / "alarms.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("ALARM_DATA_DIR", str(data_dir))

    for mod in ("app", "storage"):
        sys.modules.pop(mod, None)
    from app import create_app

    monkeypatch.setenv("LOGIN_PASSWORD", "test-pw")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("path", [
    "/dashboard.html", "/index.html", "/login.html", "/admin-login.html",
])
def test_html_not_directly_servable(client, path):
    """frontend/ 掛在根路徑，HTML 必須只能透過 @app.route 進入。"""
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/app", "/admin", "/login", "/admin/login"])
def test_route_entrypoints_still_work(client, path):
    """確認 _block_direct_html_access() 沒有誤傷正常入口——這些路徑
    不以 .html 結尾，before_request 不該擋到它們。"""
    assert client.get(path).status_code in (200, 302)


def test_static_non_html_assets_still_servable(client):
    """CSS/JS/圖示等非 HTML 靜態資源不受這次修法影響，維持直接可存取
    （這些本來就該公開，改的只有 .html）。"""
    assert client.get("/style.css").status_code == 200
    assert client.get("/js/api.js").status_code == 200
