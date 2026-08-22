"""
守住「/api/server-url 在正式環境漏設 RENDER_EXTERNAL_URL/PUBLIC_URL 時
不會把內網 IP 洩漏給任何未驗證的呼叫端」。

背景：這個端點是 Render 的 healthCheckPath（render.yaml），不能加
login_required。正式環境下若忘記設定這兩個環境變數，原本會 fallback
到回傳內網 IP——這個端點是 public，等於把內網拓樸資訊公開給任何人。
改為 production 環境下缺這兩個變數就回 500（拒絕洩漏比默默照常運作
更安全，Render 健康檢查失敗是合理的警訊，代表環境配置確實有問題）。

本機/內網開發模式（非 production）維持原行為，回內網 IP 是刻意功能。
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def test_dev_mode_returns_lan_ip(client):
    """本機模式（測試 fixture 預設不設 RENDER_EXTERNAL_URL）維持回內網 IP。"""
    r = client.get("/api/server-url")
    assert r.status_code == 200
    assert "url" in r.get_json()


def test_production_without_public_url_returns_500(monkeypatch):
    """production 環境（FLASK_ENV=production）若沒設 PUBLIC_URL/
    RENDER_EXTERNAL_URL，也不該 fallback 到內網 IP——直接 500。

    啟動期的 fail-fast（`is_production and not _use_supabase()`）要求
    production 環境必須設定 SUPABASE_URL/SUPABASE_KEY 才能開機，這裡
    給假網址讓 create_app() 通過檢查——create_app() 本身不會在啟動時
    真的發送請求驗證連線，只判斷環境變數是否存在，所以不需要真的連
    得上（也不能設 ALARM_DATA_DIR，那會強制 _use_supabase() 回
    False，繞過 fail-fast，反而測不到這個情境）。
    """
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.delenv("ALARM_DATA_DIR", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")

    for mod in ("app", "storage"):
        sys.modules.pop(mod, None)
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    r = c.get("/api/server-url")
    assert r.status_code == 500
