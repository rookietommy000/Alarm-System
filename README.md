# 設備警報代碼查詢系統

> 製造四部內部使用 — 設備警報知識庫，快速查詢警報代碼、原因分析與解決方案。

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask)](https://flask.palletsprojects.com)
[![Vue 3](https://img.shields.io/badge/Vue-3.x-4FC08D?logo=vue.js&logoColor=white)](https://vuejs.org)
[![Supabase](https://img.shields.io/badge/Supabase-Database-3ECF8E?logo=supabase&logoColor=white)](https://supabase.com)

## 線上網址

| 介面 | 連結 |
|---|---|
| 🏠 入口頁 | https://alarm-system-j9dl.onrender.com/ |
| 🔍 前台查詢 | https://alarm-system-j9dl.onrender.com/app |
| ⚙️ 後台管理 | https://alarm-system-j9dl.onrender.com/admin |
| 📊 回饋儀表板 | https://alarm-system-j9dl.onrender.com/admin/dashboard |

> 使用 [cron-job.org](https://cron-job.org) 每 5 分鐘自動 ping `https://alarm-system-j9dl.onrender.com/ping`，防止 Render 免費方案休眠。

## 資料庫

| 服務 | 連結 |
|---|---|
| 🗄️ Supabase 專案 | https://supabase.com/dashboard/project/yphzobfsvlvenfrnyelg |
| 📋 資料表編輯器 | https://supabase.com/dashboard/project/yphzobfsvlvenfrnyelg/editor |
| 🔑 API 設定 | https://supabase.com/dashboard/project/yphzobfsvlvenfrnyelg/settings/api |

---

## 入口頁設計

入口頁（`/`）是整個系統的組別選擇起點，目前規劃三個部門，各自對應獨立的查詢系統。

| 組別 | 狀態 | 說明 |
|---|---|---|
| 📦 包裝組 | ✅ 系統運行中 | 已上線，連結至 `/app` |
| ⚗️ 調劑組 | 🔜 即將上線 | 待建置 |
| 💊 充填組 | 🔜 即將上線 | 待建置 |

### 其他部門如何效仿

每個組別的系統架構完全相同，只有**資料內容不同**。新增一個部門的步驟：

1. **建立獨立的 Supabase 資料表**（或沿用同一個，用 `device_model` 區分）
2. **匯入該部門的警報代碼資料**（CSV 或 JSON 格式）
3. **在入口頁 `portal.html` 新增一張卡片**，`href` 指向對應的查詢路徑
4. **設定獨立的登入密碼**（透過環境變數 `LOGIN_PASSWORD`）

> 若要完全隔離（各部門各自部署），Fork 此 repo 後修改資料與密碼即可獨立運作，整體不超過 2 小時可完成初始建置。

---

## 功能總覽

### 前台 — 警報查詢

| 功能 | 說明 |
|---|---|
| 🔍 全文搜尋 | 依代碼、關鍵字、描述即時搜尋 |
| 🏭 產線 / 機種導覽 | 依產線選機種，逐層縮小範圍 |
| 🏷️ 嚴重度篩選 | 嚴重 / 警告 / 資訊 三級分類 |
| 📋 詳情 Modal | 顯示原因分析、四段式解決步驟、關鍵字 |
| ✅ 使用者回饋 | 標記解決方案「有效 / 無效」 |
| 📊 成功率顯示 | 列表直接顯示每筆警報解決成功率 |
| 🔥 熱門警報標示 | 最常查詢 Top 10 以橙色邊框高亮顯示 |

### 後台 — 管理員介面（需密碼登入）

| 功能 | 說明 |
|---|---|
| ➕ 新增 / 編輯 / 刪除 | 完整 CRUD 警報代碼管理 |
| 📝 四段式解決方案 | ① 檢查步驟 ② 更換零件 ③ 復歸動作 ④ 安全注意 |
| 📊 回饋儀表板 | 成功率統計、良好 / 中等 / 需改善分類 |
| 🔥 最常查詢排行 | Top 10 熱門警報排行 |
| 📋 操作歷史紀錄 | 所有新增 / 編輯 / 刪除的完整 Diff，點擊展開查看 |
| 🔎 機種篩選 + 搜尋 | 快速定位特定機種或代碼 |

---

## 系統架構

```
.
├── backend/
│   ├── app.py          # Flask API + 靜態檔伺服器
│   └── storage.py      # 儲存層抽象（JsonStore / SupabaseStore）
├── frontend/
│   ├── portal.html     # 入口選擇頁（各組別）
│   ├── index.html      # 前台查詢介面（Vue 3）
│   ├── admin.html      # 後台管理介面（Vue 3 + Bootstrap 5）
│   ├── dashboard.html  # 回饋儀表板
│   ├── login.html      # 使用者登入
│   ├── admin-login.html# 管理員登入
│   └── style.css       # 全站深色主題樣式
├── data/               # 本地開發 JSON 資料（生產用 Supabase）
└── tests/
    └── test_api.py     # pytest 整合測試
```

**技術特點：**
- Flask 單一進程同時 serve API 與前端靜態檔，無需額外 Web Server
- Vue 3 CDN 載入，無需 npm / build step，改檔即生效
- 儲存層透過環境變數自動切換：有 Supabase 憑證用 Supabase，否則用本地 JSON
- Supabase 複合主鍵設計：`(device_model, code)` 支援不同機種使用相同代碼

---

## 頁面路徑

| 路徑 | 說明 | 權限 |
|---|---|---|
| `/` | 入口頁（組別選擇） | 公開 |
| `/login` | 使用者登入 | 公開 |
| `/app` | 前台查詢主介面 | 登入後 |
| `/admin/login` | 管理員登入 | 公開 |
| `/admin` | 後台警報管理 | 管理員 |
| `/admin/dashboard` | 回饋儀表板 | 管理員 |
| `/logout` | 登出 | — |
| `/admin/logout` | 管理員登出 | — |

---

## API 端點

### 讀取（一般登入即可）

| Method | 路徑 | 說明 |
|---|---|---|
| GET | `/api/alarms` | 查詢警報（支援 `?q=&device=&severity=`） |
| GET | `/api/alarms/<device>/<code>` | 取得單筆警報 |
| GET | `/api/devices` | 機種清單 |
| GET | `/api/feedback/stats` | 回饋成功率統計 |
| POST | `/api/feedback` | 提交回饋（effective / ineffective） |
| POST | `/api/view` | 記錄查詢瀏覽事件 |
| GET | `/api/view/stats` | 查詢次數統計（熱門排行） |

### 寫入（需管理員權限）

| Method | 路徑 | 說明 |
|---|---|---|
| POST | `/api/alarms` | 新增警報代碼 |
| PUT | `/api/alarms/<device>/<code>` | 更新警報代碼 |
| DELETE | `/api/alarms/<device>/<code>` | 刪除警報代碼 |
| GET | `/api/audit` | 操作歷史紀錄（最新 100 筆） |

---

## 本地開發

### 環境需求

- Python 3.11+
- （選用）Supabase 帳號（不設定則使用本地 JSON 儲存）

### 快速啟動

```bash
# 1. Clone 專案
git clone https://github.com/rookietommy000/Alarm-System.git
cd Alarm-System

# 2. 建立虛擬環境
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. 安裝依賴
pip install -r backend/requirements.txt

# 4. 設定環境變數
cp .env.example .env        # 填入密碼與 Supabase 設定（見下方說明）

# 5. 啟動服務
python backend/app.py
```

開啟瀏覽器：
- 前台：http://localhost:5001/
- 後台：http://localhost:5001/admin

> ⚠️ macOS 注意：port 5000 被 AirPlay 佔用，本專案固定使用 **5001**。

### 環境變數說明

建立 `.env` 檔案並填入以下設定：

```env
FLASK_SECRET_KEY=your-random-secret-key
LOGIN_PASSWORD=使用者密碼
ADMIN_PASSWORD=管理員密碼

# 選填：設定後自動使用 Supabase，否則使用本地 JSON
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-anon-key
```

### 執行測試

```bash
pytest tests/
```

---

## 資料庫 Schema（Supabase）

```sql
-- 警報代碼（主資料表）
create table alarms (
  device_model text not null,
  code         text not null,
  severity     text,
  description  text,
  cause        text,
  solution     text,
  keywords     text[],
  sol_steps    jsonb,
  primary key (device_model, code)
);

-- 設備清單
create table devices (
  id       text primary key,
  model    text,
  category text,
  line     text
);

-- 使用者回饋
create table feedback (
  id           bigint generated always as identity primary key,
  code         text,
  device_model text,
  result       text,
  created_at   timestamptz default now()
);

-- 查詢瀏覽紀錄
create table alarm_views (
  id           bigint generated always as identity primary key,
  code         text,
  device_model text,
  viewed_at    timestamptz default now()
);

-- 操作歷史
create table alarm_history (
  id         bigint generated always as identity primary key,
  operation  text,
  code       text,
  old_data   jsonb,
  new_data   jsonb,
  changed_at timestamptz default now()
);
```

### 四段式解決方案欄位（`sol_steps` JSONB）

```json
{
  "check":  "先確認主電源開關狀態…",
  "parts":  "需更換保險絲 F1…",
  "reset":  "完成後按下 Reset 按鈕…",
  "safety": "操作前務必確認機器停機…"
}
```

---

## 部署（Render）

本專案部署至 [Render](https://render.com)，透過 GitHub 自動部署：

1. 在 Render 建立 **Web Service**，連接此 GitHub Repo
2. **Build Command：** `pip install -r backend/requirements.txt`
3. **Start Command：** `gunicorn -w 2 -b 0.0.0.0:$PORT backend.app:app`
4. 在 Render Environment 設定環境變數（同上方說明）

每次 push 到 `main` 分支即自動重新部署。

---

## 授權

內部使用系統，未授權不得對外公開或商業使用。
