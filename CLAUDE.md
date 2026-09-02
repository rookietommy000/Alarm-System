# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概述

**警報查詢系統** — 設備警報代碼知識庫，整合 GMP Audit Trail 與設備維護歷史資料。提供前台查詢與後台管理兩個介面（對應「資源人員」操作）。

## 常用指令

```bash
# 安裝依賴（建議使用 venv）
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# 啟動服務（同時 serve API 與前端靜態檔）
python backend/app.py
# 前台：http://localhost:5001/
# 後台：http://localhost:5001/admin

# 執行所有測試
pytest tests/

# 執行單一測試
pytest tests/test_api.py::test_create_alarm -v
```

> **Port 注意**：README 寫 5000，但實際使用 **5001**。macOS 的 AirPlay Receiver 會佔用 5000 並回 403，已在 `backend/app.py` 固定為 5001。

## 架構

### 單一 Flask 進程同時負責 API + 前端
`backend/app.py` 用 `static_folder=FRONTEND, static_url_path=""` 把 `frontend/` 掛成靜態檔根目錄，並用顯式路由（`/app` 前台、`/admin` 後台，`/` 重導向到 `/app`）回 HTML。前後端同源，無需 CORS 設定，但仍啟用 `flask-cors` 以便日後分離。

⚠️ `static_url_path=""` 代表 `frontend/*.html` 理論上可被靜態路徑直接存取、繞過所有 `@app.route` 裝飾器（含登入檢查）。已用 `before_request` 的 `_block_direct_html_access()` 擋掉 `.html` 直接存取，新增任何 `frontend/*.html` 都會自動受這個保護，不用額外處理；但如果要在 `frontend/` 放非 HTML 但不該公開的檔案（設定檔、備份），這層保護擋不到，不要放進去。

### 儲存層抽象：JsonStore
`backend/storage.py` 的 `JsonStore` 類別封裝讀寫 JSON 檔，暴露 `load()` / `save()` 兩個方法。模組級別 singleton `alarms_store` 與 `devices_store` 被 `app.py` 直接 import 使用。

- 寫入採 **tmp-file + atomic replace** 並加 `threading.Lock`
- 資料目錄透過 `ALARM_DATA_DIR` 環境變數覆寫（預設 `<repo>/data/`），**這是測試隔離的關鍵機制**
- BLOCK 6 擴充方向：新增 `SqliteStore` 類別即可替換，不動 `app.py`

### 測試通過「重載模組」切換資料目錄
`tests/conftest.py` 的 `anon_client`/`client` fixture（`client` 建構在 `anon_client` 之上，多一個已登入的 admin session）做了三件事：
1. 在 `tmp_path` 寫入測試用 `alarms.json` / `devices.json`
2. `monkeypatch.setenv("ALARM_DATA_DIR", ...)`
3. `sys.modules.pop("app")` + `sys.modules.pop("storage")` 後重新 import

若新增需要讀 env 的模組，記得加進 fixture 的 pop 清單，否則舊的 `_data_dir()` 結果可能被快取。這個 fixture 曾經散在多個測試檔案各自複製定義，第三次重複時收斂進 `conftest.py`——新增測試優先用 `conftest.py` 提供的版本，不要再複製一份。

### 欄位驗證集中在 `normalize()`
`create_alarm` / `update_alarm` 都走同一個 `normalize()` 函式：
- `severity` 白名單：`{"嚴重", "警告", "資訊"}`
- `keywords` 支援字串（逗號分隔）或陣列，**統一正規化為 list**
- `update_alarm` 呼叫時 `require_code=False`，URL 上的 code 會覆蓋 body

### 前端：Vue 3 CDN，無 build step
`frontend/index.html`（前台，`/app` 路由）與 `frontend/dashboard.html`（後台，`/admin` 路由）透過 `unpkg.com/vue@3` 的 global build 運行，直接瀏覽器載入。改前端不需要 npm / bundler，存檔即生效。

⚠️ **`/admin` 服務的是 `dashboard.html`，不是 `admin.html`**——`admin.html` 是 `dashboard.html` 的前身，早已被取代並刪除。改後台前先確認 `backend/app.py` 的 `/admin` 路由實際指向哪個檔案，不要用「看起來像不像後台」判斷；`tests/test_no_orphan_frontend_html.py` 會擋住類似的孤兒檔案再次出現。

## 多部門相關的硬約束

這些是踩過真實坑之後定下來的規則，改動涉及部門／權限的程式碼時務必遵守：

- 所有 `department` 參數必填無預設值（漏傳要讓程式明確報錯，不可靜默不過濾——那會讓查詢意外跨部門）
- 新增 `/api/*` 路由必須同步更新 `ROUTE_AUTH_REGISTRY`（`tests/test_route_auth_registry.py`），鍵為 `(rule, method)`
- `alarms.solution` 為原廠欄位，任何路徑都不得覆寫；現場處置寫法走 `local_solution`（見 `PLAN_local_solution.md`）
- 寫入端點的目標部門只從 URL path 取（`resolve_target_department()`），不讀 `request.args`、不讀 body 的 `department` 欄位
- 例外分支不得回傳看似合理的預設值（`abort` 失敗時回 `0`/`[]`/`None` 會造成靜默失敗，難以察覺）
- 跨部門隔離只能用 `sentinel_pack/verify_isolation.sh` 對真實 Supabase 驗證，pytest 環境（JsonStore 單租戶）測不到這件事——`tests/test_no_fake_isolation_claims.py` 會擋住宣稱驗證這類機制但實際測不到的測試名稱
- `variant` 是 `alarms` 主鍵的一部分（`(department, device_model, code, variant)`），單筆定位（`get_one`/`patch_one`/`delete_one`）務必帶齊，否則 PostgREST 條件不足時會靜默取任意一筆——`storage.py` 的 `_require_full_pk_match()` 會擋，但呼叫端仍要記得傳
- 批次匯入走 upsert 語意，`alarm_ingest/parse.py` 的 `OPTIONAL_FIELDS`（`severity`/`keywords`/`sol_steps`）欄位若這次沒填，不會送出覆蓋，避免整列取代把既有值清空（見 `commit.py`）

前台任何需要把 `department` 組進 URL 的地方，走 `frontend/index.html` 的 `deptOf(a)` helper，不要各自寫 fallback——超管的 `whoami.department` 是 `null`，直接塞進字串會產生看起來合理但無效的路徑（`"null"` 字面字串或塌陷成空字串），這個坑在同一輪修過兩次才收斂成單一入口。

## 例外處理判準

全庫系統性盤點過 `except Exception` 的靜默失敗模式後，修一個之前先問：**這個機制失效時，有沒有次一級的備援接住？**

- 有備援（降級後系統仍可用，只是保護力下降）→ fail-open，但降級狀態要留痕（至少一行 log；統計/管控用途的資料要能被外部查詢到，例：`count_fine()` 登入節流查詢失敗時降級成行程內記憶體計數，見 commit `8e4c805`）
- 無備援（失效後沒有次一級手段）→ fail-closed，讓例外往上拋、附明確錯誤訊息，不要用 `[]`/`0`/`None` 蒙混成「這是真實的業務狀態」（例：`load_valid_models()` 白名單讀取失敗若回空 list，會被 `apply_post_rules()` 誤判成「所有機種都不在白名單」，見 commit `6940019`，改為拋 `ValidModelsUnavailable` + TTL 快取降級）
- 安全邊界（`scope_department()`/`resolve_target_department()`/`assert_session_valid()`）→ 一律 fail-closed，不允許查詢異常時預設放行，沒有「優雅降級」這回事
- 不管哪一種，「例外發生過」這件事本身必須可追溯，禁止完全空白的 `except Exception: pass`/`return []`

已知因為這個模式踩過的真實案例：`ImportSnapshotStore.list_snapshots()` 曾經對「資料表不存在」的 404 靜默回空清單，被誤判成「表存在只是沒資料」；`suggest_semantic_fixes.py` 曾因合併時漏掉 `import re`，`_parse_response()` 對任何輸入都拋 `NameError`，被外層 `except Exception` 吞掉、誤記為批次資料本身有問題。完整判準與更多案例見 `DRAFT_error_handling_policy.md`（尚未定案併入本檔）。

## 狀態轉換鐵則

任何「一筆資料的狀態只能被處理一次」的流程（審核通過/退回、核准/駁回這類 pending→終態的轉換），**狀態的條件式更新（CAS）必須是流程的第一步，不是最後一步**。

錯誤的順序（PostgREST 沒有跨請求交易，這個模式在這裡格外危險）：
```
讀取狀態確認是 pending（僅檢查一次，之後沒有再次確認）
  → 執行實際動作（寫入 alarms 等正式表）
  → 最後才更新狀態
```
這個順序的漏洞：兩個請求幾乎同時進來，都會通過最前面那次讀取檢查（讀到的都還是舊狀態），都會執行到「實際動作」那一步——最後才做的狀態更新頂多能防住「狀態欄位本身被錯誤覆蓋」，防不住「實際動作被執行兩次」。已知真實案例：`review_suggestion()`（app.py:1426 起）跟原始設計的 `review_pending_alarm_import()` 都是這個順序，外部審查抓到（2026-09-02），修復見 commit `2237191`（`StatusTransitionMixin.claim()`/`release()`，storage.py:1386 起）。

正確的順序：
```
CAS 更新狀態（PATCH 帶 status=eq.<原狀態> 條件，僅當狀態仍是預期值才成功）
  → 失敗（PostgREST 回空陣列）代表已被別人搶走，直接 abort(409)，不執行任何後續動作
  → 成功才執行實際動作
  → 實際動作若失敗（網路中斷等），要把狀態改回原狀態（release），不能留下「已標記完成但實際動作沒發生」的孤兒狀態
```
**這個 release 步驟不是可省的細節**——把 CAS 提到最前面本身會製造一個新的失敗模式（狀態已轉換但後續動作失敗），比原本的競態更容易發生，不能只做搶佔（claim）不做釋放（release）。

**release 只有「終態之後還有可能失敗的後續動作」時才需要**：accept 分支 claim 成功後還要接著寫 alarms（`patch_one()`/`upsert_one()`），這一步可能失敗，所以 claim 成功要進一步判斷寫入結果、失敗要 release 回原狀態；reject 分支 claim 成功（`to_status="rejected"`）本身就是終態，沒有後續會失敗的動作，不需要 release。**但這不代表 reject 可以跳過 claim 本身**——QA 驗收 accept 修復時另外抓到（2026-09-02）：`review_suggestion()`/`review_pending_alarm_import()` 最初只把 claim-first 套用在 accept 分支，reject 分支仍是無條件 PATCH，繞過了 CAS 保護，會讓已經 accept 成功的一筆資料被稍晚才到、仍讀著舊快照的 reject 請求覆蓋成 rejected 狀態——不影響已寫入的 alarms 資料本身，但稽核記錄失真（狀態顯示 rejected，事實是已核准並寫入）。**同一組 accept/reject 之類的互斥終態，只要有一邊會被非同步覆蓋，就要兩邊都走 claim，不能只顧其中一邊**，修復見 commit `bb6679a`。

架構上：這個模式（claim + 對應的 release）要抽成共用方法讓多個 store 共用，不要在每個需要狀態轉換的 store 各自複製一份順序邏輯——跟本檔其他「把靠人記得換成做不到」的原則一致，讓錯的寫法比對的寫法麻煩，比事後補測試更可靠。

## 測試的能力邊界

pytest 環境用的是 `JsonStore`（單租戶本機檔案），測不到：跨部門過濾、session 三態檢查、登入節流、PostgREST 分頁/upsert 語意。這些只能用 `sentinel_pack/verify_isolation.sh` 對真實 Supabase 驗證。任何 pytest 測試如果宣稱驗證了這類機制，那個宣稱本身就是假的——`tests/test_no_fake_isolation_claims.py` 會擋住這類測試名稱/docstring 再次出現。「程式碼裡有這段防護邏輯」跟「這段邏輯經黑箱測試證實真的擋得住」是兩個不同等級的結論，報告/commit message 裡不要混用。

## 已知陷阱

- Flask/Werkzeug 的 `<string>` 路由段不匹配空字串——URL 該段為空時會直接塌陷成路由層 404，不會進到你寫的視圖函式
- `abort(404)` 不帶訊息時 Werkzeug 會填英文預設值，跟路由層 404 沒有訊息上的區別，容易混淆兩種不同成因的 404——一律用 `app.py` 的 `NOT_FOUND_MSG` 常數
- `openpyxl` 讀出的儲存格值可能是 `int`/`float` 而非 `str`（例如警報代碼被 Excel 存成數字），涉及 code 欄位一律先過 `alarm_ingest/detect.py` 的 `_cell_to_str()`
- 前端動態插入的 DOM（例如清單裡的按鈕）要用事件委派（綁在容器上），逐一綁 listener 在重繪後會失效——見 `login.html`/`admin-login.html` 的 `deptList.addEventListener`
- Supabase 執行 DDL（新增欄位/約束）後，PostgREST 的 schema cache 不會自動刷新，migration 檔案結尾要下 `notify pgrst, 'reload schema';`（見 `006_add_variant.sql`/`007_add_import_snapshots.sql`）

## 可用 Skill

- `llm-council`（`.claude/skills/llm-council/`）：重大決策要多角度壓力測試時使用（例如「要不要做X還是Y」「這個方向對不對」）。用 5 個不同思考角度的 sub-agent 各自分析、互相匿名審閱，最後彙整成一份裁決。觸發詞見該 skill 檔案開頭的 description，不用於單一正解的事實查詢或單純的建立任務。

## 資料模型

警報欄位（`data/alarms.json`）：
`code`、`device_model`、`severity`、`description`、`cause`、`solution`、`keywords`（陣列）

機種欄位（`data/devices.json`）：
`id`、`model`、`category`
