---
name: security-audit
description: 對整個警報查詢系統專案做安全稽核，輸出依嚴重度分類的完整報告（含檔案行號與修正建議）。適用於上線前、定期檢查、或接手維護時的基線建立。
---

# security-audit — 全專案安全稽核

## 目標

對這個 Flask + Vue 3 CDN 專案做**全面**的靜態安全掃描並產出報告。不僅僅是跑 hook 的 regex，要結合對本專案架構的理解做上下文判斷。

## 掃描範圍

- `backend/**/*.py` — Flask API、儲存層
- `frontend/**/*.{html,js,vue,css}` — 前端（Vue 3 CDN 運行）
- `data/**/*.json` — 資料檔（檢查是否混入敏感資料）
- `tests/**/*.py` — 測試（低風險但仍掃）
- 根目錄 `*.md`、`*.toml`、`*.cfg`、`*.env*`

**略過**：`.venv/`、`.pytest_cache/`、`node_modules/`、`.git/`、`.claude/hooks/`

## 檢查清單（依類別）

### 1. 敏感資料
- 硬編碼 API key / password / token / secret（抓字面量指派）
- 私鑰片段（`-----BEGIN ... PRIVATE KEY-----`）
- AWS / GitHub / Slack / OpenAI token 格式
- `.env` 類檔案是否被 commit

### 2. 危險 Python 寫法
- `eval`、`exec`、`os.system`、`os.popen`
- `subprocess(..., shell=True)`
- `yaml.load` 未指定 `SafeLoader`
- `pickle.load` / `marshal.load` 處理外部輸入
- `app.run(debug=True)`（本專案已知在 [backend/app.py:131](backend/app.py#L131)，需判斷是否只用於 dev）
- SQL f-string / `%` 格式化 / 字串相加拼接
- `hashlib.md5` / `sha1` 用於安全用途
- `random` 模組用於產 token / session id（應用 `secrets`）

### 3. 前端 XSS
- `v-html`、`innerHTML`、`outerHTML`、`document.write`
- `eval`、`new Function`、`setTimeout`/`setInterval` 傳字串
- Vue template 中把使用者輸入直接插入 `href="javascript:..."` 類連結
- `admin.html` 的 form 欄位是否反射式顯示使用者輸入

### 4. 路徑 / 檔案
- `open()` / `send_file` / `send_from_directory` 直接吃 request 輸入
- `os.path.join` / `Path()` 拼接使用者輸入（traversal）
- `JsonStore` 的 `filename` 是否有被使用者輸入污染的路徑

### 5. API / 架構層級（需讀 `backend/app.py` 判斷）
- 後台 `/admin`、`/api/alarms` 的寫入端點**是否有驗證機制**（目前應為無）
- CORS 是否過度開放（`CORS(app)` 預設允許任何 origin）
- 是否有 rate limit / CSRF 防護（Flask-WTF / Flask-Limiter）
- 錯誤訊息是否外洩堆疊（debug=True 時會）
- JSON 寫入的 atomic replace 是否有 TOCTOU 風險

### 6. 依賴
- `backend/requirements.txt` 的版本範圍是否固定（`>=` 而非 `==` 在可複現性上較弱）
- 是否有已知 CVE 的套件版本

### 7. 設定 / 秘密管理
- Flask 是否設 `SECRET_KEY`（目前未設 — 若加 session 會掉預設值）
- 環境變數使用是否得當（`ALARM_DATA_DIR` 屬無害）

## 執行流程

1. 列出範圍內所有檔案（`Glob` 或 `Bash find`）
2. 批次 `Read` 或用 `Grep` 針對上述每類 pattern 跑
3. 針對每個命中，**打開前後文 ±10 行**判斷是否為真問題（避開測試的假陽性）
4. 對照 `backend/app.py` 的架構做 API / 架構層級判斷（該層無法單純由 regex 決定）
5. 分類整理為報告

## 報告格式

```
# Security Audit Report — 警報查詢系統
Scanned: <N> files, <M> issues found
Severity: 🔴 critical / 🟠 high / 🟡 medium / 🔵 low / ℹ️ info

## 🔴 Critical
### [category] 標題
**File:** [path:line](path#Lline)
**Issue:** …
**Evidence:**
```code snippet```
**Fix:** 建議修法（含具體程式片段）

## 🟠 High
…

## Summary
- 🔴 N，🟠 N，🟡 N，🔵 N，ℹ️ N
- 建議優先處理：…
```

## 規則 / 撰寫守則

- **每個 finding 都要引用 `file:line`**（使用 Markdown 連結語法 `[name](path#Lline)`）
- **不列假陽性**：命中的字面字串若明顯是測試固定值（`tests/` 下），只列於 ℹ️ info 或略過
- **提供可行的修正建議**，不要只說 "fix this"
- 若發現本專案**架構層級**問題（例：admin 無驗證），列在 high/critical
- 報告結尾給**優先處理順序**（3–5 項）

## 預期常見 findings（此專案已知 baseline）

先打勾確認，才能偵測後續新增問題：
- `backend/app.py` `app.run(debug=True)` — 🟡 medium（dev 常態，但請確認部署策略）
- `CORS(app)` 預設全開 — 🟡 medium（前後端同源可收斂 origin）
- `/admin` 無驗證 — 🔴 critical（資源人員介面不應公開）
- 無 CSRF、無 rate limit — 🟠 high
- JSON 寫入採 tmp + atomic replace — ✅ 良好
- `storage.py` 已有 threading.Lock — ✅ 良好

若發現不在上列的新問題，代表有回歸需立刻處理。
