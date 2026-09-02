"""空路徑段的路由安全驗收（外部審查 2026-09-02，取代舊版
test_route_empty_segment_trap.py）。

## 背景：這個坑比 CLAUDE.md 原本的「已知陷阱」描述更曲折

CLAUDE.md 原本記載：「Flask/Werkzeug 的 `<string>` 路由段不匹配空
字串——URL 該段為空時會直接塌陷成路由層 404」。QA 2026-09-02 對正式
環境一筆 device_model 為空字串的資料執行 DELETE 時，實測發現真正
命中的不是單純的「路由層 404」，而是兩種不同機制、依路徑形狀而異：

1. **落入靜態檔案 catch-all**：`<string>` converter 對空字串匹配失敗
   時，Werkzeug 的 url_map 會往下掉進 `static_folder`/
   `static_url_path=""` 那條萬用靜態檔案規則（frontend/ 被掛成靜態檔
   根目錄，見 CLAUDE.md「架構」一節）。這條規則只允許 GET/HEAD/
   OPTIONS，結果：GET 因為找不到那個檔名的靜態檔案 → 404；PUT/POST/
   DELETE 因為方法不允許 → 405（不是 404）。

2. **`merge_slashes` 重導向到錯誤端點（更嚴重）**：當空字串段後面
   還接著一段「字面量」路徑（不是動態段，例如 `/reset-password`、
   `/active`、`/local`），Werkzeug 預設開啟的 `merge_slashes` 會把
   連續的 `//` 合併成 `/`，讓路徑「參數位移」後剛好匹配另一條完全
   不同、通常更短的路由——這不是「安全地擋下」，是**端點混淆**。
   實測重現（老師 2026-09-02 獨立複查確認機制屬實）：
   `PUT /api/admin/departments/<空dept_id>/reset-password`
   → 308 重導向 → `PUT /api/admin/departments/reset-password`
   → 這條路徑匹配的其實是 `rename_department()`（改名端點），
   `dept_id` 的值變成字面字串 `"reset-password"`，body 只要剛好帶
   `name` 欄位就會 200 成功執行——執行的是完全不同的業務邏輯，不是
   呼叫端原本要打的 reset-password 端點。

   （這個具體案例現狀更新：`reset-password`/`active` 已經因為 URL
   重構被移除，合併成 `PUT /api/admin/department-actions/<dept_id>`
   ——dept_id 挪到路徑最後一段、前綴字面量改用不跟 `departments` 重疊
   的 `department-actions`，從根本消除這種「動態段+字面量後綴」路徑
   形狀天生會跟其他單一動態段路由同構碰撞的問題，不是加白名單。第一版
   合併方案（`/api/admin/departments/<dept_id>/actions`）仍未解決
   根因——`<dept_id>` 消失後路徑 collapse 成 `/api/admin/departments/
   actions`，跟 `rename_department()` 的路由格式同構，只是換了碰撞
   對象。這段歷史保留在這裡是因為「動態段+字面量後綴」這個路徑形狀
   本身是可重複發生的結構性教訓，不是只跟這兩個端點有關。）

   同一次調查也意外揭露一個獨立的測試安全缺口：`DepartmentStore`
   （storage.py）是全庫唯一零次呼叫 `_use_supabase()` 的 store 類別，
   任何測試腳本只要沒有明確覆寫 `SUPABASE_URL`/`SUPABASE_KEY`，呼叫
   到部門相關功能就會打真正的正式環境（`app.py` 的 `load_dotenv()`
   無條件載入專案根目錄 `.env` 的真實憑證）——這個缺口跟路由混淆本身
   是兩個獨立問題，一併送交專家評估，見「第4項」相關测试/修復。

## 這裡驗證什麼

外部專家方案：merge_slashes 關閉 + 新增明確攔截（in progress，
「第1項」，員工尚未完成）。本檔案先釘住修復完成後的目標行為——
任何 `<string>` 動態段為空，一律回 404，不能是 3xx 重導向，也不能
落入 405（405 代表又掉進靜態檔案 catch-all，不是我們自己的防護在
生效）。目前（修復前）多數案例會是 404/405 混雜，重導向的幾個案例
（`reset-password`/`active`/`local`）會是 308——這些預期會 FAIL，直到
員工完成「merge_slashes 關閉 + 攔截」後才會全綠，這是刻意的、釘住
目標行為的寫法，不是誤判。
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def app(client):
    return client.application


def test_merge_slashes_disabled(app):
    """merge_slashes 保持開啟就是端點混淆重導向的根因（見檔案開頭
    reset-password 案例），必須關閉——這是外部專家方案的核心一步。"""
    assert app.url_map.merge_slashes is False


# 每個 tuple：(url 樣板以 {} 標記字串型別的動態段, HTTP 方法, 要清空的段索引)
# 涵蓋全部 /api/* 路由裡用預設 <string> converter 的動態段（<int:...>
# 型別本來就無法用空字串或非數字字串匹配，不受這個陷阱影響，不重複測）。
ROUTE_CASES = [
    ("/api/alarms/{}/{}/{}", "GET", 0),
    ("/api/alarms/{}/{}/{}", "GET", 1),
    ("/api/alarms/{}/{}/{}", "GET", 2),
    ("/api/alarms/{}/{}/{}", "PUT", 0),
    ("/api/alarms/{}/{}/{}", "PUT", 1),
    ("/api/alarms/{}/{}/{}", "PUT", 2),
    ("/api/alarms/{}/{}/{}", "DELETE", 0),
    ("/api/alarms/{}/{}/{}", "DELETE", 1),
    ("/api/alarms/{}/{}/{}", "DELETE", 2),
    ("/api/alarms/{}", "POST", 0),
    ("/api/devices/{}", "POST", 0),
    ("/api/devices/{}/{}", "GET", 0),
    ("/api/devices/{}/{}", "GET", 1),
    ("/api/devices/{}/{}", "PUT", 0),
    ("/api/devices/{}/{}", "PUT", 1),
    ("/api/devices/{}/{}", "DELETE", 0),
    ("/api/devices/{}/{}", "DELETE", 1),
    ("/api/admin/bulk-import/{}/preview", "POST", 0),
    ("/api/admin/bulk-import/{}/commit", "POST", 0),
    ("/api/admin/import/{}/inspect", "POST", 0),
    ("/api/admin/import/{}/split", "POST", 0),
    ("/api/admin/import/{}/snapshots", "GET", 0),
    ("/api/admin/semantic-review/{}", "GET", 0),
    ("/api/alarms/{}/{}/{}/local", "PUT", 0),
    ("/api/alarms/{}/{}/{}/local", "PUT", 1),
    ("/api/alarms/{}/{}/{}/local", "PUT", 2),
    ("/api/alarms/{}/{}/{}/history", "GET", 0),
    ("/api/alarms/{}/{}/{}/history", "GET", 1),
    ("/api/alarms/{}/{}/{}/history", "GET", 2),
    ("/api/alarms/{}/{}/{}/suggestions", "POST", 0),
    ("/api/alarms/{}/{}/{}/suggestions", "POST", 1),
    ("/api/alarms/{}/{}/{}/suggestions", "POST", 2),
    ("/api/admin/departments/{}", "PUT", 0),
    ("/api/admin/departments/{}", "DELETE", 0),
    ("/api/admin/department-actions/{}", "PUT", 0),
    ("/api/admin/departments/{}/impact", "GET", 0),
]


def _build_path(template: str, empty_index: int) -> str:
    n = template.count("{}")
    values = ["dummy"] * n
    values[empty_index] = ""
    return template.format(*values)


@pytest.mark.parametrize("template,method,empty_index", ROUTE_CASES,
                          ids=[f"{m}:{t}:seg{i}" for t, m, i in ROUTE_CASES])
def test_empty_segment_returns_404_not_redirect(client, template, method, empty_index):
    path = _build_path(template, empty_index)
    resp = client.open(path, method=method, follow_redirects=False)
    assert resp.status_code not in (301, 302, 307, 308), (
        f"{method} {path} 回了 {resp.status_code}——空字串路徑段被 "
        f"merge_slashes 重導向了，可能落到參數位移後的錯誤端點（見檔案"
        f"開頭 reset-password 案例），不能是任何 3xx"
    )
    assert resp.status_code == 404, (
        f"{method} {path} 回了 {resp.status_code}（預期 404）——405 代表"
        f"又落入了 static_url_path=\"\" 的靜態檔案 catch-all，不是我們"
        f"自己的防護在生效"
    )


# 老師 2026-09-02 指定要釘進去的具體案例：這是唯一已經對正式環境實測
# 重現過重導向行為的真實路徑（QA 用 device_model=ACM002、code 缺值
# 重現 DELETE 405），跟上面的泛化 template 案例分開列，確保這個真實
# 案例本身不會因為未來調整 ROUTE_CASES 而不小心被移除。
def test_reproduced_case_alarms_local_code_empty_returns_404(client):
    resp = client.put("/api/alarms/mf4d/ACM002//local", follow_redirects=False)
    assert resp.status_code not in (301, 302, 307, 308)
    assert resp.status_code == 404


def test_reproduced_case_department_actions_dept_id_empty_returns_404(client):
    """外部審查最初實測重現的端點混淆案例是舊路徑
    `/api/admin/departments/<dept_id>/reset-password`（修復前這裡是
    308 重導向到 rename_department()，body 帶 name 欄位甚至會 200
    執行改名）——那條路徑本身已經因為 URL 重構被移除（員工方案：
    dept_id 挪到路徑最後一段、前綴改用不跟 departments 重疊的字面量
    `department-actions`，從根本消除這種「動態段+字面量後綴」路徑
    形狀的碰撞可能，不是加白名單），繼續測舊路徑只會測到「路由不存在
    回 404」，跟原本要驗證的「空段不會被重導向到別的端點」語意不同，
    改測新端點本身在 dept_id 為空時的行為（新端點同樣受
    _reject_empty_path_segments() 保護，這裡驗證的是防護對新端點依然
    生效，不是重新驗證新的 URL 設計本身沒有碰撞——那是
    test_no_route_collapse_collisions 的責任）。"""
    resp = client.put("/api/admin/department-actions/", follow_redirects=False)
    assert resp.status_code not in (301, 302, 307, 308)
    assert resp.status_code == 404


def test_no_route_collapse_collisions(app):
    """碰撞掃描腳本（scripts/check_route_collisions.py）：靜態掃描
    url_map，找出「清空某個動態段後，路徑字面上會 collapse 成另一條
    真實存在的路由」這種組合，不依賴實際發送請求逐一撞——這條測試的
    意義是防止未來新增路由時，沒人意識到又製造出一組新的 collapse
    collision。

    KNOWN_HARMLESS_COLLISIONS 是外部審查（2026-09-02）逐一實測判讀過
    的既有碰撞，全部確認不構成漏洞，白名單化而非要求空清單：
      - 命中 endpoint='static'：Flask 的 static_url_path="" 讓靜態檔案
        路由掛在根目錄，adapter.match() 的靜態比對邏輯會「匹配」到這個
        萬用規則，但實際請求會依檔案是否存在而 404，不會執行業務邏輯
        或洩漏資訊（已實測：直接打 collapse 後的路徑本身得到 404）。
      - 命中權限要求更高的端點（update_alarm/get_alarm 都是承接
        login_required 端點 collapse 後撞到的 admin_required 本體）：
        方向是變嚴格不是變寬鬆，一般帳號打畸形路徑會被高權限要求擋下
        （已實測：login_required session 直接打 collapse 路徑得 403）。
      - /<path:filename> 命中 root_redirect：前台靜態檔案路由本身，
        跟 /api/* 業務邏輯無關。

    白名單外新出現的碰撞會讓這條測試 fail——新增路由時如果不小心跟
    既有路由撞在一起，這裡會抓到，不能默默加進白名單解決，要先判讀
    是不是真的無害（見上面三類）才能列入。"""
    from scripts.check_route_collisions import find_collapse_collisions

    KNOWN_HARMLESS_COLLISIONS = {
        ("GET", "/<path:filename>", "root_redirect"),
        ("GET", "/api/alarms/<department>/<device_model>/<code>", "static"),
        ("GET", "/api/devices/<department>/<device_model>", "static"),
        ("GET", "/api/admin/semantic-review/<department>", "static"),
        ("GET", "/api/admin/import/<department>/snapshots", "static"),
        ("GET", "/api/admin/departments/<dept_id>/impact", "static"),
        ("PUT", "/api/alarms/<department>/<device_model>/<code>/local", "update_alarm"),
        ("GET", "/api/alarms/<department>/<device_model>/<code>/history", "get_alarm"),
    }

    collisions = find_collapse_collisions(app)
    unexpected = [
        c for c in collisions
        if (c[1], c[0], c[3]) not in KNOWN_HARMLESS_COLLISIONS
    ]
    assert unexpected == [], f"發現未經判讀的新路由碰撞: {unexpected}"
