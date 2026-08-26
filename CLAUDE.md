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

前台任何需要把 `department` 組進 URL 的地方，走 `frontend/index.html` 的 `deptOf(a)` helper，不要各自寫 fallback——超管的 `whoami.department` 是 `null`，直接塞進字串會產生看起來合理但無效的路徑（`"null"` 字面字串或塌陷成空字串），這個坑在同一輪修過兩次才收斂成單一入口。

## 可用 Skill

- `llm-council`（`.claude/skills/llm-council/`）：重大決策要多角度壓力測試時使用（例如「要不要做X還是Y」「這個方向對不對」）。用 5 個不同思考角度的 sub-agent 各自分析、互相匿名審閱，最後彙整成一份裁決。觸發詞見該 skill 檔案開頭的 description，不用於單一正解的事實查詢或單純的建立任務。

## 資料模型

警報欄位（`data/alarms.json`）：
`code`、`device_model`、`severity`、`description`、`cause`、`solution`、`keywords`（陣列）

機種欄位（`data/devices.json`）：
`id`、`model`、`category`
