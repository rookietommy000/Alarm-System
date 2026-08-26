# 設備警報代碼查詢系統

> 製造四部內部使用 — 設備警報知識庫，快速查詢警報代碼、原因分析與解決方案。

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask)](https://flask.palletsprojects.com)
[![Vue 3](https://img.shields.io/badge/Vue-3.x-4FC08D?logo=vue.js&logoColor=white)](https://vuejs.org)
[![Supabase](https://img.shields.io/badge/Supabase-Database-3ECF8E?logo=supabase&logoColor=white)](https://supabase.com)

## 線上網址

| 介面 | 連結 |
|---|---|
| 🏠 入口頁 | https://alarm-system-1.onrender.com/ |
| 🔍 前台查詢 | https://alarm-system-1.onrender.com/app |
| ⚙️ 後台管理 | https://alarm-system-1.onrender.com/admin |
| 📊 回饋儀表板 | https://alarm-system-1.onrender.com/admin/dashboard |

> 使用 [cron-job.org](https://cron-job.org) 每 5 分鐘自動 ping `https://alarm-system-1.onrender.com/ping`，防止 Render 免費方案休眠。

## 資料庫

| 服務 | 連結 |
|---|---|
| 🗄️ Supabase 專案 | https://supabase.com/dashboard/project/yphzobfsvlvenfrnyelg |
| 📋 資料表編輯器 | https://supabase.com/dashboard/project/yphzobfsvlvenfrnyelg/editor |
| 🔑 API 設定 | https://supabase.com/dashboard/project/yphzobfsvlvenfrnyelg/settings/api |

---

## 多部門隔離架構

系統以單一部署同時服務多個部門，部門身分綁定在登入 session 上，不是各部門各自部署。`/` 直接導向 `/app`（前台查詢），登入後所有資料存取都限定在自己的部門範圍內。

- **部門識別**：登入時選擇部門，之後所有讀寫 API 的 URL 都帶著 `<department>` 路徑段（例如 `/api/alarms/<department>/...`），伺服器端一律以登入 session 記錄的部門為準，不信任前端傳入值
- **新增部門**：後台「部門管理」（`/api/admin/departments` 系列端點）直接建立，不需要修改程式碼或新增前端頁面
- **總管視角**：另有總管（superadmin）身分可跨部門查看與管理，一般部門帳號看不到其他部門資料
- **隔離驗證**：`sentinel_pack/verify_isolation.sh` 針對正式 Supabase 環境跑一組跨部門洩漏檢查（讀取、寫入、統計端點、搜尋等），本機 pytest 環境因採單租戶 JsonStore 無法測到隔離本身

> 完全獨立部署（各部門各自一份服務）仍然可行：Fork 此 repo 後接自己的 Supabase 專案、只建立一個部門即可，但多部門場景下建議直接用本系統內建的部門管理，不需要 Fork。

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
│   ├── app.py           # Flask API + 靜態檔伺服器
│   ├── storage.py       # 儲存層抽象（JsonStore / SupabaseStore）
│   ├── alarm_ingest/    # 批次匯入（欄位偵測、切分、驗證、寫入）
│   ├── ai/              # AI 拍照辨識與分析
│   └── migrations/      # Supabase schema 異動腳本
├── frontend/
│   ├── index.html       # 前台查詢介面（Vue 3，含入口重導）
│   ├── dashboard.html   # 後台管理介面（/admin 實際指向這支，不是 admin.html）
│   ├── login.html       # 使用者登入
│   ├── admin-login.html # 管理員登入
│   └── style.css        # 全站深色主題樣式
├── data/                # 本地開發 JSON 資料（生產用 Supabase）
├── tools/variant/       # 離線資料整理 CLI（語意品質掃描、variant 欄位解析）
├── sentinel_pack/       # 跨部門隔離驗證用的哨兵測試資料與腳本
└── tests/               # pytest 整合測試
```

**技術特點：**
- Flask 單一進程同時 serve API 與前端靜態檔，無需額外 Web Server
- Vue 3 CDN 載入，無需 npm / build step，改檔即生效
- 儲存層透過環境變數自動切換：有 Supabase 憑證用 Supabase，否則用本地 JSON
- Supabase 複合主鍵設計：`(department, device_model, code, variant)` 支援跨部門、跨機種、同機種多變體使用相同代碼

---

## 頁面路徑

| 路徑 | 說明 | 權限 |
|---|---|---|
| `/` | 導向 `/app` | 公開 |
| `/login` | 使用者登入 | 公開 |
| `/app` | 前台查詢主介面 | 登入後 |
| `/admin/login` | 管理員登入 | 公開 |
| `/admin` | 後台警報管理 | 管理員 |
| `/admin/dashboard` | 回饋儀表板 | 管理員 |
| `/logout` | 登出 | — |
| `/admin/logout` | 管理員登出 | — |

---

## API 端點

所有 `/api/*` 端點都需要登入（`/api/departments/public`、`/api/whoami`、`/ping` 除外），讀寫端點的 URL 一律帶 `<department>` 路徑段，伺服器只信任 session 裡的部門、不信任前端傳入值（見上方「多部門隔離架構」）。完整清單以 `backend/app.py` 的路由裝飾器為準，`tests/test_route_auth_registry.py` 強制每個 `/api/*` 路由都要登記權限層級，兩者不會脫節。以下按功能分類列出主要端點：

| 分類 | 說明 | 代表端點 |
|---|---|---|
| 警報 CRUD | 查詢、新增、修改、刪除警報代碼 | `/api/alarms/<department>[/<device_model>/<code>]` |
| 機種管理 | 機種清單與 CRUD | `/api/devices/<department>[/<device_model>]` |
| 現場處置 | 不覆寫原廠 `solution` 的現場自訂方案，含變更歷史 | `/api/alarms/<department>/<device_model>/<code>/local`、`.../history` |
| 回饋 / 瀏覽統計 | 使用者標記方案有效性、熱門查詢排行 | `/api/feedback`、`/api/feedback/stats`、`/api/view`、`/api/view/stats` |
| 操作歷史 | 所有寫入操作的完整 diff 稽核軌跡 | `/api/audit` |
| 批次匯入 | 上傳範本檔預覽、切分、確認寫入、整批復原 | `/api/admin/bulk-import/<department>/{preview,commit}`、`/api/admin/import/<department>/{inspect,split,snapshots}` |
| 語意審核 | AI 掃描出的翻譯疑慮，人工逐筆確認採用 | `/api/admin/semantic-review/<department>[/<index>]` |
| AI 拍照分析 | 拍照辨識警報並提出建議，需人工確認/修正 | `/api/analyze`、`/api/confirm`、`/api/correct` |
| AI 稽核 | AI 使用量與判斷記錄查詢 | `/api/admin/scan-stats`、`/api/admin/scan-recent`、`/api/admin/scan-ranking`、`/api/admin/ai-logs` |
| 部門管理（總管限定） | 建立/停用/重設密碼/刪除部門 | `/api/admin/departments[/<dept_id>]` |
| 身分 | 目前登入狀態、公開部門清單 | `/api/whoami`、`/api/departments/public` |

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

完整 schema 異動歷史見 `backend/migrations/`（依序執行的編號 SQL 檔），這裡只說明核心設計概念：

- **`alarms`**：主資料表，`(department, device_model, code, variant)` 複合鍵。`solution` 是原廠欄位、任何路徑都不得覆寫；現場自訂的處置方式走獨立的 `local_solution`/`local_reason` 欄位，兩者並存、互不污染
- **`departments`**：部門清單，含密碼雜湊、啟用狀態、`session_version`（用於強制舊 session 失效）
- **`devices`**：機種清單，`(department, model)` 複合唯一約束（同機種名可能分屬不同部門）
- **variant（機種多變體）**：同一機種型號在不同產線可能有細微差異，`variant` 欄位區分同 `device_model` 下的不同版本，非必要時留空字串即可
- **`import_snapshots` / `import_snapshot_rows`**：批次匯入的整批復原保底機制，commit 前記錄每筆寫入前的值，支援整批 undo。僅 Supabase 模式生效，本機 JsonStore fallback 不支援
- **`feedback` / `alarm_views` / `alarm_history`**：使用者回饋、查詢瀏覽紀錄、操作歷史稽核軌跡，皆帶 `department` 欄位供隔離查詢
- **`ai_scans` / `ai_corrections` / `ai_logs`**：AI 拍照辨識與分析的使用記錄

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

本專案部署至 [Render](https://render.com)，透過 GitHub 自動部署，設定見 `render.yaml`：

1. 在 Render 建立 **Web Service**，連接此 GitHub Repo
2. **Build Command：** `pip install -r backend/requirements.txt`
3. **Start Command：** `gunicorn --chdir backend --bind 0.0.0.0:$PORT app:app`
4. **Health Check Path：** `/api/server-url`
5. 在 Render Environment 設定環境變數（同上方說明，另需 `GEMINI_API_KEY`、`SUPERADMIN_PASSWORD`——見 `render.yaml` 完整清單）

每次 push 到 `main` 分支即自動重新部署。免費層閒置會休眠，搭配 [cron-job.org](https://cron-job.org) 每 5 分鐘 ping `/ping` 端點防止休眠（見上方「線上網址」一節）。

---

## 授權

內部使用系統，未授權不得對外公開或商業使用。
