"""500/503 errorhandler 測試（PLAN 拍照辨識故障修復 PR-1）。

背景：使用者反映拍照辨識「有回應但沒資料」，追查後這只是連鎖問題的
一部分——後端未捕捉例外時 Flask 預設回一頁 HTML 錯誤頁，前端
`.then(r => r.json()).catch(() => null)` 這種寫法會讓 HTML 解析失敗、
被 catch 吞掉變成 null，UI 因此統一顯示成「未偵測到警報」，使用者
完全看不出來是伺服器出錯還是 AI 真的沒看到東西。這裡的 handler
讓 /api/* 路徑至少能穩定回一個 JSON 錯誤物件，PR-1 前端那邊再改成
先檢查 r.ok（見 frontend/index.html 的修改）。

兩個必須守住的邊界：
1. 訊息來源要分兩種——明確 abort(503, "安全文案") 的訊息要原樣保留
   （開發者已經寫好安全字串），不是一路全部替換成籠統文案；只有
   完全未捕捉的原始例外（可能夾帶內部堆疊細節）才替換成固定文案。
2. 只有 /api/* 路徑接手；非 API 路徑（/login、/app、/admin 等）維持
   Flask 預設的 HTML 錯誤頁，不然使用者會在瀏覽器看到裸 JSON。
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def test_explicit_abort_503_preserves_curated_message(client, monkeypatch):
    """abort(503, "...") 是開發者已經寫好的安全文案，handler 不該蓋掉。
    /api/analyze 的 ValidModelsUnavailable 分支正是這種情況
    （app.py: except ValidModelsUnavailable as e: abort(503, str(e))）——
    monkeypatch run_pipeline 讓它拋出這個例外，走最貼近真實情境的路徑。
    """
    import ai as ai_pkg

    def _boom(*a, **k):
        raise ai_pkg.ValidModelsUnavailable("機種清單暫時無法讀取，請稍後再試（若持續發生請聯絡管理員）：測試模擬")

    monkeypatch.setattr(ai_pkg, "run_pipeline", _boom)

    r = client.post("/api/analyze", json={"image": "Zm9v"})
    assert r.status_code == 503
    body = r.get_json()
    assert body["error"] == "機種清單暫時無法讀取，請稍後再試（若持續發生請聯絡管理員）：測試模擬"


def test_unhandled_exception_on_api_path_returns_generic_json_message(client, monkeypatch):
    """完全未捕捉的原始例外（不是透過 abort()）落到 /api/* 路徑時，
    回應是固定的籠統文案，不含原始例外訊息內容（避免內部細節外洩），
    但狀態碼仍是 500，且是合法 JSON（前端 r.json() 不會再解析失敗）。

    client.application.config["PROPAGATE_EXCEPTIONS"] = False：Flask 在
    TESTING=True 時預設會讓未捕捉例外直接往外冒（方便測試看到完整
    traceback），不會走 errorhandler(500)——這裡要驗證的正是 errorhandler
    本身的行為，必須明確關掉這個預設，否則測試測到的是「pytest 底下
    的例外傳遞機制」而不是「正式環境使用者真正會看到的回應」。
    """
    import storage

    client.application.config["PROPAGATE_EXCEPTIONS"] = False

    def _boom(*a, **k):
        raise RuntimeError("內部關鍵細節：資料庫連線字串包含敏感資訊 xyz")

    monkeypatch.setattr(storage.alarms_store, "load", _boom)

    r = client.get("/api/alarms")
    assert r.status_code == 500
    body = r.get_json()
    assert body is not None, "回應必須是合法 JSON，前端才能穩定用 r.json() 解析"
    assert body["error"] == "伺服器錯誤，請稍後再試"
    assert "資料庫連線字串" not in body["error"]


def test_unhandled_exception_on_non_api_path_does_not_return_json(client, monkeypatch):
    """非 /api/* 路徑（例如 /admin）若發生未捕捉例外，維持 Flask 預設
    HTML 錯誤頁行為，不接手轉成 JSON——否則使用者在瀏覽器看到的會是
    一坨裸 JSON 而不是任何看得懂的頁面。"""
    import app as app_module

    client.application.config["PROPAGATE_EXCEPTIONS"] = False

    def _boom(*a, **k):
        raise RuntimeError("模擬非 API 路徑的未預期例外")

    monkeypatch.setattr(app_module, "send_from_directory", _boom)

    r = client.get("/admin")
    assert r.status_code == 500
    assert r.content_type != "application/json"
