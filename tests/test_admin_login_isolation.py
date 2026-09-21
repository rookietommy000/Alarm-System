"""後台登入入口隔離（login isolation v3）：`/admin/login/<dept_id>` 新路由。

背景：`/admin/login`（不帶部門）原本會列出全部部門+系統管理員（`__super__`）
分岔選項，任何知道網址的人都能看到完整部門清單、甚至一路點到超管登入。
改成部門專屬 URL 後，後端只負責格式檢查（不合法直接 404）與回傳同一份
靜態 HTML，實際顯示邏輯與內容組裝交給前端解析 URL 路徑——見
`frontend/admin-login.html`。前台 `/login`、`login_page()`、`login_submit()`
完全不受影響，不在本檔案測試範圍內。

**能力邊界**：pytest 環境用 `JsonStore`（`ALARM_DATA_DIR` 生效時
`_use_supabase()=False`），`admin_login_submit()` 走的是 `.env` 明文比對
的本機 fallback 分支，不會執行 `_do_login()`/`_check_login_throttle()`/
`_fetch_login_precheck()` 這條走 Supabase 部門查詢與節流的路徑——那條
路徑（密碼錯誤重導回原部門頁、節流重導帶 dept_id）只能在正式環境用
`sentinel_pack/verify_isolation.sh` 或黑箱驗收確認，這裡測的是路由格式
檢查與靜態 HTML 回傳這個層級（CLAUDE.md「測試的能力邊界」）。
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def test_admin_login_no_dept_id_still_works(anon_client):
    """既有行為不能因為新增路徑參數而回歸：不帶 dept_id 的 /admin/login
    要維持原本可以造訪、回傳 admin-login.html 的行為。"""
    r = anon_client.get("/admin/login")
    assert r.status_code == 200
    assert b"admin" in r.data.lower() or "管理員" in r.data.decode("utf-8")


def test_admin_login_with_valid_dept_id_returns_200(anon_client):
    r = anon_client.get("/admin/login/mf4c")
    assert r.status_code == 200


def test_admin_login_with_super_sentinel_returns_200(anon_client):
    r = anon_client.get("/admin/login/__super__")
    assert r.status_code == 200


def test_admin_login_with_invalid_dept_id_format_returns_404(anon_client):
    """DEPT_ID_RE 不過的路徑段（含大寫字母）直接 404，不落入「未知部門」
    的 fallback 語意——這是刻意設計，避免枚舉探測有效部門 ID。"""
    r = anon_client.get("/admin/login/MF4C")
    assert r.status_code == 404


def test_admin_login_with_special_chars_returns_404(anon_client):
    """`..` 這類路徑本身在路由比對前就會被正規化掉、不會命中
    `<dept_id>` 單一路徑段，不是這裡要驗證的格式檢查邏輯生效——換一個
    會真正走進 admin_login_page() view function、但不符合 DEPT_ID_RE
    的字元（連字號）當反例，才是真正測到 abort(404) 這段程式碼。"""
    r = anon_client.get("/admin/login/mf-4c")
    assert r.status_code == 404


def test_admin_login_dept_id_page_content_matches_no_dept_id_page(anon_client):
    """後端不組裝任何 dept_id 相關內容，兩個路徑回傳的是同一份靜態
    HTML——差異完全在前端 JS 解析路徑後才產生，這裡驗證後端這一半。"""
    r_no_dept = anon_client.get("/admin/login")
    r_with_dept = anon_client.get("/admin/login/mf4c")
    assert r_no_dept.data == r_with_dept.data


def test_admin_login_page_redirects_to_admin_when_already_logged_in(client):
    """client fixture 已建立 admin session（見 conftest.py）——is_admin()
    為 True 時應直接重導 /admin，不管有沒有帶 dept_id，這個既有行為不因
    新增路徑參數而改變。"""
    r = client.get("/admin/login/mf4c", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"] == "/admin"


def test_admin_login_submit_still_works_in_local_fallback_mode(anon_client):
    """本機模式（_use_supabase()=False）的 .env 明文比對 fallback 完全
    不涉及 dept_id 路徑機制，這條既有登入路徑不能因為本次改動回歸。"""
    r = anon_client.post("/admin/login", data={"password": "test-admin-pw"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"] == "/admin"


def test_login_page_unaffected_by_admin_login_changes(anon_client):
    """前台 /login 完全不動，這裡做最基本的迴歸確認——不是本次改動的
    測試重點，只是確保沒有不小心牽動到共用邏輯（_check_login_throttle
    新增了 dept_id 參數但帶預設值，login_submit() 呼叫時不傳這個參數）。"""
    r = anon_client.get("/login")
    assert r.status_code == 200
