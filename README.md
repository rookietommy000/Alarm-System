# 設備警報代碼知識庫系統

專案任務規劃文件 v1.1 的實作版本。整合 GMP Audit Trail 與設備維護歷史資料，
提供前台查詢與後台管理兩個介面。

## 專案結構

```
.
├── backend/          Flask API（storage.py 抽象儲存層，之後可換 SQLite）
│   ├── app.py
│   ├── storage.py
│   └── requirements.txt
├── frontend/         Vue 3（CDN，免 build）
│   ├── index.html    使用者查詢介面
│   ├── admin.html    資源人員管理介面
│   └── style.css
├── data/             JSON 儲存（BLOCK 1 產出）
│   ├── alarms.json
│   └── devices.json
└── tests/            pytest 整合測試（BLOCK 5）
    └── test_api.py
```

## 快速開始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# 啟動 API（同時 serve 前端）
python backend/app.py
```

- 前台查詢：<http://localhost:5000/>
- 後台管理：<http://localhost:5000/admin>

## API 一覽

| Method | 路徑 | 說明 |
|--------|------|------|
| GET    | /api/alarms?q=&device=&severity= | 查詢警報（關鍵字 / 機種 / 嚴重度） |
| GET    | /api/alarms/&lt;code&gt; | 取得單筆 |
| POST   | /api/alarms | 新增 |
| PUT    | /api/alarms/&lt;code&gt; | 更新 |
| DELETE | /api/alarms/&lt;code&gt; | 刪除 |
| GET    | /api/devices | 機種清單 |

### 警報欄位

`code`（代碼）、`device_model`（機種）、`severity`（嚴重 / 警告 / 資訊）、
`description`（描述）、`cause`（原因）、`solution`（解決方案）、`keywords`（關鍵字陣列）

## 執行測試

```bash
pytest tests/
```

## 任務對照

| 區塊 | 對應檔案 |
|------|----------|
| BLOCK 1 資料準備與結構設計 | `data/alarms.json`、`data/devices.json` |
| BLOCK 2 後端服務建置       | `backend/app.py`、`backend/storage.py` |
| BLOCK 3 前台查詢介面       | `frontend/index.html` |
| BLOCK 4 後台管理介面       | `frontend/admin.html` |
| BLOCK 5 整合測試資料       | `tests/test_api.py` |
| BLOCK 6 未來擴充（選做）   | `storage.py` 已抽象化，後續可加 SQLite backend |

## BLOCK 6 擴充方向

- `backend/storage.py` 已抽離成介面，新增 `SqliteStore` 類別即可無痛切換
- 多欄位進階搜尋（範圍、時間）、版本化歷史紀錄
- 匯入 GMP Audit Trail 的歷史事件做關聯分析
