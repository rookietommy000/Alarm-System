# 交接文件 — 現場處置做法（local_solution）

給下一個接手的人（或下一個 session）快速上手用。完整脈絡在 `PLAN_local_solution.md`，這份只挑「不看完整文件也要知道」的部分。

## 一分鐘版本

一個警報代碼可以有兩種處置方式並存：原廠的 `solution`（唯讀，不可覆寫）和現場自己的 `local_solution`（任何登入者都能直接編輯）。編輯不經審核，靠「最後修改是誰、什麼時候」+ 完整變更歷史來追溯可信度，不是靠審核把關。

## 現在在哪

階段 1-5 全部完成、上線、驗證過。**下一步是階段 6：缺處置清單**（哪些警報還沒有 `local_solution`，用 `GET /api/alarms?missing_local=true` 篩選參數即可，不用排序，PLAN 4.5 節已經定案）。

階段 7（AI 分級審核）排在階段 6 之後，**不要提前做**——PLAN 裡明確警告過，會變成「有審核機制但沒東西可審」。

## 最重要的一件事：審核機制被停用了

這是最容易讓人誤判的地方。原始設計是「一般使用者提交建議、管理員審核」，這套機制**完整做出來、對正式環境端到端驗證成功過**，然後才決定停用——不是半途而廢，是驗證完才發現前提不成立：

部門用共用密碼、沒有個人帳號，提交者跟審核者根本無法區分，審核形同蓋橡皮圖章。改成所有登入者直接編輯，用「最後修改」+ 完整變更歷史取代審核。

`alarm_suggestions` 表跟四支相關端點**還在，沒刪**，加了註解說明停用原因，等以後有個人帳號功能再評估重新啟用。如果你看到這些程式碼還在，不代表沒做完，是刻意保留。

完整決策記錄在 `PLAN_local_solution.md` 的「審核路徑停用（決策記錄）」那段。

## 兩個檔案，別搞混

- **`frontend/dashboard.html`** ← `/admin` 路由實際服務的檔案，後台真正在用的
- ~~`frontend/admin.html`~~ ← 已刪除。這是 `dashboard.html` 的前身，早就沒被任何路由引用了，但外觀完整到會讓人誤判是正式後台。上一輪就是因為誤判在這個死檔案上做了一整輪功能才發現白做。

改後台前，先確認 `/admin` 路由（`backend/app.py`）現在指向哪個檔案，不要用「看起來像不像後台」判斷。`tests/test_no_orphan_frontend_html.py` 會擋住同類事再發生，但別靠測試補救，先查一次比較快。

## 登入身分速查

不要在這裡重新推導，直接看 `AUTH_FLOW.md`——那是逐條對照程式碼驗證過的權威文件。只提一個最常踩的坑：

**超管的 `whoami.department` 是 `null`。** 前端任何用 `whoami.department` 組 URL 路徑的地方，超管都會踩雷（`encodeURIComponent(null)` 變成字面字串 `"null"`，或用 `|| ''` fallback 會塌陷成空字串），兩種都導致 404 但成因不同。這輪修了兩處（`toggleLocalHistory`、`openLocalSolutionEditor`），都改用「資料本身帶的 `department`」優先於 `whoami.department`。新加任何要組部門路徑的功能，記得比照辦理。

## 除錯時如何分辨 404 的種類

三層 404 現在用不同措辭區分：

| 看到的錯誤訊息 | 代表什麼 |
|---|---|
| 英文（Werkzeug 預設，例如 "The requested URL was not found on the server"） | **路由層**沒對上任何規則，通常是前端組錯 URL（塌陷、多餘斜線、`undefined`/`null`） |
| 「找不到指定資源」（`NOT_FOUND_MSG`） | 部門解析失敗（不存在或無權存取，刻意不區分是哪一種） |
| 其他中文訊息（例如「找不到此警報代碼」） | 業務邏輯層，部門對了但資料本身不存在 |

看使用者截圖時先看是英文還是中文，能省很多來回猜測的時間。

## 跑測試 / 部署

```bash
cd backend && ../.venv/bin/pytest ../tests/ -q   # 84 項應該全過
```

`tests/conftest.py` 提供共用的 `client`/`anon_client` fixture。改動任何跟登入、部門解析、靜態檔案存取有關的東西，記得看 `test_route_auth_registry.py`、`test_no_orphan_frontend_html.py`、`test_static_html_blocked.py`、`test_department_404_messages.py` 這幾支有沒有要跟著更新。

部署是 push 到 `main` 就自動上 Render（`alarm-system-1.onrender.com`）。**不要相信第一次 curl 驗證的結果**——Render 部署切換有幾秒到幾十秒的過渡期，舊版本會短暫殘留，寫個等待迴圈或多等一下再驗證，不要一次 200/404 就下結論（這輪至少踩過一次）。

## 測試帳號

`zztest`（隔離驗證用測試部門，不是真實產線）的一般使用者密碼和管理員密碼已經重設過，密碼只在對話紀錄裡，沒有寫進任何檔案——需要的話找回那次對話，或直接重設一組新的（`backend/gen_department_hashes.py` 或直接 PATCH Supabase 的 `departments` 表，記得只改 `pw_hash`/`admin_pw_hash` 其中你要測的那一組，另一組留著）。

**絕對不要**在 `mf4d`（製造四部包裝組）這類真實產線部門上重設密碼測試——會把現場所有登入中的裝置登出。

## 這輪意外處理掉的東西（跟 local_solution 無關，但值得知道）

- 刪除了一個公開的重複 Render 部署（`alarm-system-j9dl`），它一直在同步跑最新程式碼，跟正式環境用同一份 Supabase，是個沒人管理的公開入口
- 修了 `frontend/*.html` 能被靜態路徑直接繞過登入存取的問題（`backend/app.py` 的 `_block_direct_html_access`）
- `docs/index.html`（GitHub Pages）跟 `README.md` 的網址從舊的 `alarm-system-j9dl` 改成 `alarm-system-1`

---

## 三份規劃的現況（逐項核對過程式碼，不是憑印象）

### 規劃一：`variant` 欄位（方案 C）—— ⬜ 完全未動工

`backend/app.py`、`backend/storage.py` 都沒有任何 `variant` 相關程式碼。`frontend/dashboard.html` 裡出現的 13 處 `variant` 字串全部是 CSS 屬性 `font-variant-numeric`，跟這份規劃無關，純巧合。

這份規劃本身完整（schema 遷移步驟、rollback、`storage.py` 改動點、端點語意變更、前台多變體選擇 UI、哨兵資料擴充），要開工時直接照文件的八個階段走即可，不需要重新設計。

### 規劃二：審核路徑停用與變更歷史 —— ✅ 已全部完成並上線

這是這輪 session 實際做的事，逐項核對：

- [x] `PUT .../local` 改 `login_required`，`ROUTE_AUTH_REGISTRY` 同步
- [x] 前台編輯按鈕對所有登入者顯示，超管例外（`whoami.department` 存在才顯示）
- [x] 新增 `GET .../history` 端點，`login_required`，唯讀，只回 `local_update`
- [x] `AuditLogger.list_for_alarm()`，`department` 必填
- [x] 前台顯示「最後修改」一行 + 展開才呼叫的變更紀錄
- [x] 後台 `dashboard.html` 的 `auditText()` 加 `local_update` 友善顯示
- [x] 不移植待審清單、不新增待審分頁（原本做了一版又移除了）
- [x] `alarm_suggestions` 表與端點保留不刪，加註解說明停用原因
- [x] PLAN 文件記錄決策

2.6 節（推廣到新部門時的評估提醒）是文字性提醒，不是實作項，維持記錄狀態即可，不用動工。

### 規劃三：批次匯入與 AI 解析 —— ⬜ 完全未動工

`backend/app.py` 沒有 `/bulk` 或 `/parse` 端點。符合預期——這份規劃本來就明確標註「阻塞於 `variant`」，`variant` 沒做，這份自然沒開始。

---

## 待辦逐項核對結果

| 編號 | 項目 | 狀態 | 備註 |
|---|---|---|---|
| 4.1 | 前台 `department` 取得收成單一入口 | 🟡 部分完成 | 兩個踩雷點都已修（各自加了 fallback），但沒有收成建議的單一 `_deptPath()` helper，也沒有「取不到就明確報錯中止」的防呆。第三處出現前建議補上 |
| 4.2 | 四處 `abort(404)` 統一 `NOT_FOUND_MSG` | ✅ 完成 | 四處都已改。**額外發現**：`department_impact()`（超管專用部門管理端點）也有一處 `abort(404, "部門不存在")`，但語意相反（那裡故意要明確告知部門不存在），不屬於這次範圍，不用動 |
| 4.3 | `conftest.py` 收斂測試 fixture | ✅ 完成 | `client`/`anon_client` 已建立，既有測試檔案的本地 fixture 照建議暫未刪除 |
| 4.4 | 孤兒檔案刪除（`admin.html`/`portal.html`） | ✅ 完成 | 兩個都已刪除，`test_no_orphan_frontend_html.py` 已存在 |
| 4.5 | `create_alarm`/`update_alarm` 的死檢查 | ⬜ **未完成，待修** | 確認死碼成立：`normalize()` 濾掉 `department` 後才呼叫 `_check_body_department_conflict`，永遠不會觸發。**`create_device`/`update_device` 兩處是有效的**（用未過濾的原始 body），不要誤改。只需要調整 `create_alarm`/`update_alarm` 這兩處，在 `normalize()` 之前用原始 body 檢查 |
| 4.6 | `GET /api/server-url` 公開回傳內網 IP | ⬜ **未完成，待修** | 仍是 `@public_endpoint`，仍會在沒有 `RENDER_EXTERNAL_URL`/`PUBLIC_URL` 時 fallback 到 LAN IP |
| 4.7 | 靜態路徑保護殘留檢查 | ✅ 完成 | `before_request` 已上線並實測；`frontend/` 目錄確認只有 `.DS_Store`（已 gitignore、未被追蹤，非風險），沒有備份檔或洩漏資料 |
| 4.8 | `docs/index.html` 死連結 | 🟡 已處理，但走了不同方案 | 文件建議「傾向關掉 Pages」，實際做的是「改連結」（`alarm-system-j9dl` → `alarm-system-1`）。目前連結有效，非阻塞，但跟原始建議方向不同，如果之後要重新考慮「乾脆關掉 Pages」，這是背景 |

**這次沒有處理，下次接手可以直接做的兩項（都不大）**：4.5（死碼修正）、4.6（內網 IP 洩漏）。

## 第五節環境約束 —— ⬜ 未加進 `CLAUDE.md`

建議的六條約束目前都還沒寫進 `CLAUDE.md`。且順帶發現 `CLAUDE.md` 現有內容裡還提到「`frontend/index.html` 與 `admin.html`」——`admin.html` 已經刪除，這句話本身就過期了，一併更新會比較好：

```markdown
- 所有 department 參數必填無預設值（漏傳要 TypeError，不可靜默不過濾）
- 新增 /api/* 路由必須同步更新 ROUTE_AUTH_REGISTRY，鍵為 (rule, method)
- alarms.solution 為原廠欄位，任何路徑都不得覆寫；現場做法寫 local_solution
- 寫入端點的目標部門只從 URL path 取，不讀 request.args、不讀 body
- 例外分支不得回傳看似合理的預設值（回 0/[]/None 會造成靜默失敗）
- 隔離只能用 sentinel_pack/verify_isolation.sh 對真實 Supabase 驗證，
  pytest 環境測不到（JsonStore 單租戶）
```

## 建議的下一步順序

1. **規劃二已經做完**，不用再碰
2. 兩個小待辦（4.5、4.6）隨時可以順手修，各自獨立、風險低
3. 4.1 的架構收斂（`_deptPath()` helper）不急，但下次要加任何新的部門相關前端功能時，先做這個會省事
4. 把第五節六條約束補進 `CLAUDE.md`（連帶修正過期的 `admin.html` 提及）
5. **規劃一（`variant`）是下一個大工程**，規劃三（批次匯入）卡在它後面，兩者都還沒開始
6. 階段 6（缺處置清單）跟 `variant`/批次匯入是三條獨立的線，看要先做哪個都可以
