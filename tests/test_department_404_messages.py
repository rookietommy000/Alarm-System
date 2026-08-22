"""
守住「跨部門存取／部門不存在／無權存取」三種情況的錯誤訊息完全一致，
且與 Werkzeug 路由層 404 的預設英文訊息不同。

背景：resolve_target_department()/scope_department() 原本用裸的
abort(404)（不帶 description），Werkzeug 會自動填入它自己的預設英文
訊息。這個訊息剛好跟「URL 結構本身沒對上任何路由規則」的路由層 404
撞在一起——使用者截圖裡兩種完全不同層級的失敗長得一模一樣，除錯時
無法只憑錯誤訊息判斷問題出在哪一層。

改為統一用 NOT_FOUND_MSG 常數（backend/app.py）。這不是放寬安全設計：
「不透露部門存在與否／是否無權存取」這個安全需求約束的是訊息內容，
四種情況（部門不存在、無權存取、跨部門建議查詢等）的訊息完全相同，
只是把原本由 Werkzeug 決定的措辭換成自己控制的字串。

pytest 環境是 JsonStore（非 Supabase），超管分支（_dept_cached 存在性
驗證）測不到——這條路徑本來就依賴真實 Supabase 連線，跟
test_no_fake_isolation_claims.py 記錄的既有限制一致，本檔案不假裝
測到它。這裡只測不需要 Supabase、純邏輯就能觸發的分支：一般帳號
跨部門存取（resolve_target_department 的 path_department != dept）。
"""

def test_department_path_mismatch_returns_uniform_message(client):
    """resolve_target_department() 裡 path_department != dept 這個純字串
    比對分支（不碰資料庫）：一般帳號（session department="local"）打路徑
    段是別的部門的寫入端點，應該得到 NOT_FOUND_MSG，不是洩漏細節的訊息。
    這裡驗證的是訊息格式一致，不是隔離機制本身是否生效——真正的隔離
    驗證只在 sentinel_pack/verify_isolation.sh 對真實 Supabase 執行。"""
    from app import NOT_FOUND_MSG  # client fixture 已把 backend/ 加進 sys.path

    r = client.put(
        "/api/alarms/other-dept/CNC-A100/E001/local",
        json={"local_solution": "test"},
    )
    assert r.status_code == 404
    assert r.get_json()["error"] == NOT_FOUND_MSG


def test_not_found_message_differs_from_missing_alarm_message(client):
    """同部門但警報代碼真的不存在，走的是不同的錯誤分支（業務邏輯層
    的「找不到此警報代碼」，不是部門解析失敗），訊息不應該跟
    NOT_FOUND_MSG 混在一起——分層要分得出來，不是每個 404 都一樣。"""
    from app import NOT_FOUND_MSG

    r = client.put(
        "/api/alarms/local/CNC-A100/NONEXISTENT/local",
        json={"local_solution": "test"},
    )
    assert r.status_code == 404
    assert r.get_json()["error"] != NOT_FOUND_MSG


def test_not_found_message_differs_from_werkzeug_route_layer_404(client):
    """完全不存在的路由（URL 結構本身沒對上任何規則）應該得到 Werkzeug
    的預設訊息，不是 NOT_FOUND_MSG——這樣才能從訊息內容分辨失敗發生在
    路由層還是業務邏輯層。"""
    from app import NOT_FOUND_MSG

    r = client.get("/api/this-route-does-not-exist-at-all")
    assert r.status_code == 404
    assert r.get_json()["error"] != NOT_FOUND_MSG
