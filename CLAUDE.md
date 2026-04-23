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
`backend/app.py` 用 `static_folder=FRONTEND` 把 `frontend/` 掛成靜態檔根目錄，並用兩個顯式路由 `/` 與 `/admin` 回 HTML。前後端同源，無需 CORS 設定，但仍啟用 `flask-cors` 以便日後分離。

### 儲存層抽象：JsonStore
`backend/storage.py` 的 `JsonStore` 類別封裝讀寫 JSON 檔，暴露 `load()` / `save()` 兩個方法。模組級別 singleton `alarms_store` 與 `devices_store` 被 `app.py` 直接 import 使用。

- 寫入採 **tmp-file + atomic replace** 並加 `threading.Lock`
- 資料目錄透過 `ALARM_DATA_DIR` 環境變數覆寫（預設 `<repo>/data/`），**這是測試隔離的關鍵機制**
- BLOCK 6 擴充方向：新增 `SqliteStore` 類別即可替換，不動 `app.py`

### 測試通過「重載模組」切換資料目錄
`tests/test_api.py` 的 `client` fixture 做了三件事：
1. 在 `tmp_path` 寫入測試用 `alarms.json` / `devices.json`
2. `monkeypatch.setenv("ALARM_DATA_DIR", ...)`
3. `sys.modules.pop("app")` + `sys.modules.pop("storage")` 後重新 import

若新增需要讀 env 的模組，記得加進 fixture 的 pop 清單，否則舊的 `_data_dir()` 結果可能被快取。

### 欄位驗證集中在 `normalize()`
`create_alarm` / `update_alarm` 都走同一個 `normalize()` 函式：
- `severity` 白名單：`{"嚴重", "警告", "資訊"}`
- `keywords` 支援字串（逗號分隔）或陣列，**統一正規化為 list**
- `update_alarm` 呼叫時 `require_code=False`，URL 上的 code 會覆蓋 body

### 前端：Vue 3 CDN，無 build step
`frontend/index.html` 與 `admin.html` 透過 `unpkg.com/vue@3` 的 global build 運行，直接瀏覽器載入。改前端不需要 npm / bundler，存檔即生效。

## 資料模型

警報欄位（`data/alarms.json`）：
`code`、`device_model`、`severity`、`description`、`cause`、`solution`、`keywords`（陣列）

機種欄位（`data/devices.json`）：
`id`、`model`、`category`
