# 多部門隔離 — 執行清單

依 `PLAN_department_isolation.md`（第八輪審查後版本）拆解的可執行任務，依實作順序排列。對照計畫書章節編號方便交叉查閱。狀態會隨開發進度更新。

**本清單於第七輪審查後整份重新生成**，不是逐條補丁——先前版本落後計畫書約三輪，尤其「密碼與登入安全」「前端改動」兩個階段內容嚴重過時（含一條與現行設計直接矛盾的項目），故不沿用舊版逐條修補。第八輪新增階段 -1、-0.5（前置階段）並調整維護窗口分段，其餘階段內容未變。

---

## 階段 -1：建立乾淨的版本控制基準點（對應計畫「尚未做」項，第八輪發現／第九輪定案）

- [x] 確認專案已是 git repo：`main` 分支，有 `origin` 遠端
- [x] **【第九輪定案】採選項甲**：先 commit 這批舊變更當基準點，打 tag `pre-multitenancy`，之後所有 diff 以此為基準
- [x] **實測現況可跑**（commit 前）：搜尋、後台四個分頁、清理過期資料皆正常。**拍照分析因本機非 HTTPS 環境無法測相機權限，順延至階段 -0.5 Render 部署（HTTPS）後補測**——這是刻意延後，不是遺漏，順便也符合 5.5 節「SW 快取行為需在真實 HTTPS 環境驗證」的既有要求
- [x] **寫 `.gitignore`（必須在 `git add` 之前）**：
  ```
  # 祕密（最重要）
  .env
  .env.local

  # 本機資料
  data/
  data/backup/

  # 雜項
  __pycache__/
  *.pyc
  .DS_Store
  venv/
  ```
  `.env` 含 Supabase key、Gemini API key、`LOGIN_PASSWORD`、`ADMIN_PASSWORD`（之後還會加 `SUPERADMIN_PASSWORD`）——即使不打算推上 GitHub 也不該進版本庫，Render 部署很可能會接 GitHub 遠端，屆時就晚了。`data/backup/` 內有原廠文件 PDF，進了 git 歷史就永遠在裡面
- [x] 建立並 commit `.env.example`（只列 key 名稱、無值），供換機器或交接時知道要設哪些變數
- [x] `git add -A` 後，**先看再 commit**：`git status` 逐行確認沒有 `.env`、沒有大檔；`git diff --cached --stat` 複核（確認乾淨，另外發現 `color.html` 為 0 bytes 空檔案，判斷為廢棄草稿，已刪除不進 commit）
- [x] 視情況輕度拆分（路徑好拆就拆，例如 `backend/ai/` 一批、`frontend/dashboard.html` 一批、RWD 一批），但不超過 15 分鐘——中間狀態多半啟動不了，`git bisect` 在這批交織開發的變更上沒有意義，拆分價值只剩「文件性質」，commit message 寫清楚就達成
- [x] Commit：實際拆成 4 個 commit（`585821e` AI 管線、`84d4b50` 前台/後台/PWA、`cf13647` 後端 API＋資料清理、`8f50f33` 規劃文件＋`.gitignore`/`.env.example`）
- [x] `git tag pre-multitenancy`（已打在 `8f50f33`）
- [ ] **【關鍵，連動階段 10/11 設計】確認 `storage.py` 的改動與 `app.py` 的改動落在不同 commit**——階段 10/11 刻意分兩步部署（先只上 storage.py 驗證行為不變，再上 app.py），前提是兩者能分別部署到「只有 storage.py 生效」的中間點，若同一個 commit 混著兩者的改動，這個分階段部署的安全設計會失效
- [ ] **往後 commit 紀律**：每階段結束 commit 一次（訊息帶階段編號，如 `stage 5: storage.py 部門過濾與 DepartmentStore`）；每個部署點打 tag（`deploy-stage10-storage`、`deploy-stage11-app`），部署時指定 tag 不用「當前 main」；不開 branch，單人+階段中途要部署，branch 只會多一道 merge 手續，直接在 main 上做，靠 tag 標記部署點——階段 11 若出問題，回滾就是部署 `deploy-stage10-storage` tag，一行指令

**階段 -1 已完成（除上方兩條往後才會用到的紀律提醒外）。** Baseline 建立於 2026-08-11。

## 階段 -0.5：Render 正式部署（對應計畫「尚未做」項，多部門改造的前置）— 【第十二輪：Railway→Render，沿用免費層+cron-job.org防休眠】

- [x] 建立 Render Web Service，連接 GitHub repo `rookietommy000/Alarm-System`；已建立並套用 `render.yaml`（`gunicorn --chdir backend --bind 0.0.0.0:$PORT app:app`）
- [x] 環境變數搬遷：`SUPABASE_URL`/`SUPABASE_KEY`/`GEMINI_API_KEY`/`FLASK_SECRET_KEY`/`LOGIN_PASSWORD`/`ADMIN_PASSWORD`（過程中發現並修正 `SUPABASE_KEY` 誤貼含變數名前綴的問題，見第十三輪）
- [x] **【第十三輪新增】確認 `requirements.txt` 的 `google-genai` 修正已部署並生效**——`curl`/Python 直測空白圖確認 503 消失、回 200 且 Gemini 真的被呼叫（`ERR_MODEL_UNKNOWN` 是測試圖無內容的正常回應，非套件問題）；使用者實機拍攝真實警報畫面測試，AI 辨識正常運作，確認修復完整生效
- [x] **決策：沿用免費層 + `cron-job.org` 防休眠**（既有機制，`app.py` 的 `/ping` 端點已存在）——已確認排程指向新的 Render 網域 `alarm-system-1.onrender.com/ping`；已知偶爾仍可能遇到喚醒延遲，先上線觀察，非本次必須解決項目
- [x] Cookie 設定 `Secure`／`HttpOnly`／`SameSite=Lax`——`app.py` `create_app()` 新增 `SESSION_COOKIE_SECURE`（依 `RENDER_EXTERNAL_URL` 是否存在動態開關，本機 HTTP 開發環境維持 `False` 避免登入失效）、`SESSION_COOKIE_HTTPONLY=True`、`SESSION_COOKIE_SAMESITE="Lax"`，57 個既有 pytest 測試全綠
- [ ] **確認 Service Worker 在 HTTPS 下的實際行為**——Service Worker 只在 HTTPS（或 `localhost`）下運作，本機一直是 HTTP 環境，代表 PWA 快取行為（含 5.4 節當作安全機制在處理的 `/api/*` 排除快取、`activate` 清除舊快取）從未在真實環境跑過。**此項不阻塞階段 -0.5 結束**——真正有意義的驗證要等階段 13（前端改動含 sw.js 修改）完成後才有東西可測，屆時階段 14 的 SW 手動驗證步驟會在真正的 HTTPS 環境（現在已具備）下執行，此處僅確認 Render 已提供必要的 HTTPS 前提
- [x] 確認 Render 實際配置的 worker 數量：**`WEB_CONCURRENCY=1`**（免費層預設，見 Render 部署 log）——這是 2.2.3 節粗細節流設計「共用 IP 不會被誤傷」推論的前提假設，本機測試環境通常也是單一 worker，數量差異不大，暫不需重新校準門檻，但正式上線後應持續觀察

## 階段 0：資料庫前置確認（對應計畫 1.2、1.3、1.7）— 【第十一輪：已用 00_preflight_check.sql 完成，結果如下】

- [x] 確認 `devices` 現行主鍵/唯一約束的實際型態：**已確認為混合型**——主鍵 `id`（獨立代理鍵，如 `M-201`）不動，另有獨立 `UNIQUE(model)` 約束（注意：型號欄位實際叫 `model`，不是 `device_model`，見 PLAN 1.2 節命名決策）需切換為 `(department, model)`
- [x] 確認 `alarms` 現行主鍵是否確實是 `(device_model, code)`：**已確認**，`alarms_pkey` 就是 `PRIMARY KEY (device_model, code)`，欄位命名與 PLAN 假設一致
- [ ] Supabase 遷移前備份（快照或關鍵表 CSV 匯出），記錄回滾方式
- [x] 執行重複值預檢：`00_preflight_check.sql` 已含此檢查，結果 **0 筆重複**，`alarms`/`devices` 均可安全繼續
- [x] **【第十一輪額外查明】無外鍵指向 `alarms`/`devices` 舊主鍵**（0 筆），主鍵切換不用擔心連動；基準筆數 `alarms` 1759、`devices` 14、`ai_scans` 5，與既有記錄一致
- [x] **【第十一輪額外查明】`devices.id` 是否為型號本身**：已確認不是（`id` 與 `model` 完全脫鉤），新機種安全建立不需要額外設計 id 產生規則
- [x] **【第十一輪額外查明】`devices.line` 是否隱含跨部門資料**：已與使用者確認，`line`（2.1/2.2）只是同一部門內的產線分類標記，非隱藏的部門邊界，不影響現行「只有一個部門」的前提

## 階段 1：建表與加欄位（對應計畫 1.1、1.2、1.3、1.4、2.2.1）— 全部 nullable，零行為變更

- [ ] 建立 `departments` 表（`id`/`name`/`pw_hash`/`admin_pw_hash`/`session_version`/`active`/`hidden`/`purgeable`/`created_at`）
- [ ] 建立 `login_attempts` 表（`id`/`ip`/`department`（無 FK）/`success`/`attempted_at`），SQL 註記中明確標註 `department` 對不到 `departments` 表是刻意設計、不可被清理腳本誤刪
- [ ] `devices` 加 `department` 欄位＋索引
- [ ] `alarms` 加 `department` 欄位＋索引（先建 `(department, device_model, code)` unique index，暫不切主鍵）
- [ ] `ai_scans`／`ai_corrections`／`ai_logs`／`alarm_history`／`feedback`／`alarm_views` 加 `department` 欄位＋索引（`feedback`/`alarm_views` 之後不加 `NOT NULL`，見 1.5、3.7）

## 階段 2：遷移腳本（對應計畫 1.7）

- [ ] 撰寫 `backend/migrate_add_departments.py`：重複值預檢 → 用現有 `.env` 密碼雜湊建立預設部門（`hidden=false, purgeable=false`）→ `devices`/`alarms` 回填 → `feedback`/`alarm_views` best-effort 回填 → 印出處理筆數，可重複執行
- [ ] 執行遷移腳本，驗證 `devices`/`alarms` 無 NULL
- [ ] 驗證通過後，`devices.department`／`alarms.department` 加 `NOT NULL` 約束

## 階段 3：主鍵/唯一約束切換（對應計畫 1.2、1.3、第 7 節步驟 3）— 獨立階段，必須在階段 5（storage.py 含 `upsert_one()`）之前完成

- [ ] **【第十一輪：已確認為混合型，工作量比原假設輕】** `devices` 主鍵 `id` 不動，只需 `alter table devices drop constraint devices_model_key;` + `alter table devices add constraint devices_dept_model_key unique (department, model);`（注意欄位名是 `model`）
- [ ] `alarms` 主鍵從 `(device_model, code)` 切換為 `(department, device_model, code)`，執行並驗證（欄位名確認無誤，直接照 PLAN 1.3 節做）
- [ ] 確認無其他表以外鍵指向這兩張表的舊主鍵（若有，先 drop 再重建）

## 階段 4：後端 — 密碼與登入安全（對應計畫第 2 節，本清單風險最高的階段）

- [ ] `requirements.txt` 加 `werkzeug>=3.0`
- [ ] 密碼雜湊／驗證改用 `generate_password_hash`/`check_password_hash`
- [ ] **實作登入路徑入口分岔**：表單選 `__super__` 只比對 `SUPERADMIN_PASSWORD`（`hmac.compare_digest`），選真實部門只比對該部門 `admin_pw_hash`，**兩條路互不 fallthrough**——這是整份規劃權限提升風險最高的一項，優先處理
- [ ] `session.clear()` 於登入成功時執行，避免舊 session 殘留鍵帶入新登入
- [ ] `POST /api/admin/departments`、`reset-password` 拒絕與 `SUPERADMIN_PASSWORD` 明文相同的密碼（400）
- [ ] 實作 `assert_session_valid()`：部門存在性＋`active`＋`session_version` 三態合取校驗，`None`（不存在）也要快取，TTL 30~60 秒行程內快取
- [ ] `set_active()`／`update_password()`／`purge()` 成功後主動 pop 快取，讓操作者所在 worker 立即生效
- [ ] 實作部門不存在時的 dummy hash 比對（`_DUMMY_HASH` 模組載入時算好），回應與密碼錯誤完全一致（枚舉防護）
- [ ] 實作登入節流細網：`(ip, department)` 組合，15 分鐘窗口內最近成功登入之後的連續失敗數 `N_ip_dept`
- [ ] 實作登入節流粗網：只看 `ip`（不看 department），門檻 20 次，`N_ip`
- [ ] `delay = min(max(2**N_ip_dept if N_ip_dept>=1 else 0, 2**(N_ip-19) if N_ip>=20 else 0), 60)`，429 + `Retry-After`，**不用 `sleep()`**
- [ ] `login_attempts` 寫入三段式判斷：格式不合法（不符 `DEPT_ID_RE`）不查 DB 不寫入；格式合法但部門不存在照常寫入 `success=false`；已在節流窗口內的請求只回 429 不再寫入
- [ ] `cleanup-expired` 端點新增 `delete from login_attempts where attempted_at < now() - interval '90 days'`

## 階段 5：後端 — `storage.py`（對應計畫第 3 節）

- [ ] 修正 `SupabaseStore.save()` 刪除掃描地雷：`devices_store` 加 `department=eq.<dept>` 過濾
- [ ] 新增 `upsert_one()`／`delete_one()`，**明確指定 `on_conflict`**（`alarms`: `department,device_model,code`；`devices`: `department,device_model`），`alarms`/`devices` 單筆 CRUD 改用這兩個方法
- [ ] `JsonStore.load()`/`save()` 加 `department=None` 參數（忽略，維持單租戶相容，僅服務 pytest）
- [ ] 另建獨立 Supabase 開發專案（schema 相同），本機開發連線資訊放 `.env.local` 並加入 `.gitignore`——**不要**讓 `JsonStore` 支援多部門過濾
- [ ] 新增 `DepartmentStore` 類別：`list()`（含 `hidden`/`purgeable`，絕不回傳密碼雜湊）、`get_by_id()`（含 `session_version`/`active`）、`check_login()`、`create()`、`update_name()`、`update_password()`（連帶 `session_version += 1`）、`set_active()`、`purge(dept_id, confirm_id)`
- [ ] `purge()` 保護機制：僅 `purgeable=true` 可執行（建立時固定，不由時間/登入紀錄推斷），需 `confirm_id` 與 `dept_id` 相符二次確認
- [ ] 3.6 節：`AuditLogger`/`FeedbackStore`/`ViewStore`/`AiScanStore`/`ai_memory` 所有涉及部門的方法，`department` 參數改必填（遷移期暫留預設值 `None`，階段 10 上線後同一次 commit 內移除）
- [ ] 3.7 節：確認 `DeptScope.ALL` 路徑的 query builder 不加任何 `department` 相關過濾條件（含容易被誤加的 `.not.is.null`），程式碼加註解說明約束

## 階段 6：後端 — AI 管線部門隔離（對應計畫 3.5）

- [ ] `ai_memory.py` 所有查詢函式（歷史記錄、候選建議）加 `department` 參數並套用於查詢條件，不可省略
- [ ] `ai_pipeline.py` 把 `session["department"]` 一路往下傳，不遺漏

## 階段 7：後端 — `app.py` 權限框架（對應計畫第 4 節）

- [ ] 實作 `scope_department()`：讀取過濾用，只讀 `session`／（超管時）`?dept=`，`DeptScope.ALL`/`DeptScope.DEPT`，取不到部門直接 401
- [ ] 實作 `resolve_target_department()`：寫入專用，**只讀 URL path，禁止讀 `request.args`**（配套規則 a，最容易被重構破壞的地方）；body 帶的 `department` 與 path 不符 → 400（規則 b）；無部門路徑段的端點超管呼叫一律 400（規則 c）
- [ ] 新增 `superadmin_required`、`@public_endpoint` 裝飾器；`login_required`/`admin_required`/`superadmin_required`/`public_endpoint` 四者各自設 `_auth_level` 標記屬性（`"login"`/`"admin"`/`"superadmin"`/`"public"`）
- [ ] **【第七輪關鍵】確認同一 rule 依 HTTP method 拆成獨立 view function**（例如 `GET /api/alarms/<department>/<device_model>/<code>` 用 `login_required`，同路徑 `PUT`/`DELETE` 用 `admin_required`），不可用單一函式處理多個 method 又想要不同權限層級——此決定務必在動手寫其餘端點前先確認，事後拆分成本高
- [ ] `create_app()` 啟動時 fail-fast 檢查：生產環境未設 Supabase 直接中止，不悄悄降級
- [ ] 更換 `FLASK_SECRET_KEY` 並確認寫死在 Render 環境變數（非隨機產生，否則每次重啟/擴容全體被登出）

## 階段 8：後端 — 各端點改動（對應計畫 4.4~4.7）

**警報（`alarms`）**：
- [ ] `GET /api/alarms`（列表）→ `login`，`scope_department()` 過濾
- [ ] `POST /api/alarms/<department>` → `admin`，`resolve_target_department(department)`
- [ ] `GET /api/alarms/<department>/<device_model>/<code>` → `login`
- [ ] `PUT /api/alarms/<department>/<device_model>/<code>` → `admin`
- [ ] `DELETE /api/alarms/<department>/<device_model>/<code>` → `admin`

**機種（`devices`）**：
- [ ] `GET /api/devices`（列表）→ `login`，`scope_department()` 過濾
- [ ] `POST /api/devices/<department>` → `admin`，`resolve_target_department(department)`（不再從 body 解析）
- [ ] `GET /api/devices/<department>/<device_model>` → `login`
- [ ] `PUT /api/devices/<department>/<device_model>` → `admin`
- [ ] `DELETE /api/devices/<department>/<device_model>` → `admin`

**其他端點**：
- [ ] `POST /api/feedback`、`POST /api/view` 加 `login_required`（原無驗證），寫入帶 `department`（session 取），超管呼叫 400
- [ ] `GET /api/feedback/stats`、`GET /api/view/stats` 加驗證＋`scope_department()` 過濾（讀取端點，超管可用 `?dept=`）
- [ ] `POST /api/analyze` 加驗證，部門傳入 `run_pipeline()`，超管呼叫 400
- [ ] `POST /api/confirm`、`POST /api/correct` 加驗證，超管呼叫 400；`confirmed_by` 改用 `f"{target}/{role}"`（`target` 來自 `resolve_target_department()`，`role` 三態含 `superadmin`）
- [ ] `GET /api/audit` 依 `scope_department()` 過濾
- [ ] `GET /api/admin/scan-stats`、`scan-recent`、`scan-ranking` 依部門過濾
- [ ] 確認 `ai_logs` 讀取端點（若 Dashboard「AI 辨識失敗率」有讀）也套用部門過濾
- [ ] `POST /api/admin/cleanup-expired` 改 `superadmin_required`
- [ ] 新增部門管理端點：`GET/POST/PUT /api/admin/departments*`、`PUT .../active`、`DELETE .../<id>`（purge，body 需 `confirm_id`）
- [ ] 新增 `GET /api/whoami`（`@public_endpoint`）
- [ ] 新增 `GET /api/departments/public`（`@public_endpoint`，只列 `active=true` 且 `hidden=false`）

## 階段 9：自動化測試（對應計畫 8.1）— 【第七輪調整：移到部署 app.py 之前】

**此階段的把關意義是在部署前擋下漏掛裝飾器/掛錯層級的路由，必須排在階段 10（部署 app.py）之前執行，全綠才可進入階段 10。**

- [ ] 撰寫 `tests/test_route_auth_registry.py`：`ROUTE_AUTH_REGISTRY` 的 key 為 `(rule, method)` 元組（不可只用 rule 字串，因為同路徑不同 method 權限層級不同，見階段 7）
- [ ] 測試遍歷 `app.url_map`，對每個 `(rule, method)` 斷言：白名單已登記＋view function 實際 `_auth_level` 與宣告層級一致
- [ ] 新增 `resolve_target_department()` 原始碼檢查：`inspect.getsource` 斷言函式體不含 `request.args`
- [ ] 針對本機（`JsonStore`）能跑的部分先確認全綠

## ⚠️ 階段 11～14 屬於同一個維護窗口（對應計畫「第八輪審查」發現）

**開始階段 11 之前，先確認當天有足夠時間把階段 11、12、13、14 一次做完，不要中途收工。**

原因：`app.py` 部署（階段 11）之後、前端部署（階段 13）之前，存在一段系統對使用者不可用的空窗——`/login` 改成需要 `department` 欄位，舊版 `login.html` 不會送，登入直接失敗；`/api/alarms/<device_model>/<code>` 舊路由已不存在（改三段式），舊版前台警報詳情、後台編輯全部 404。這段期間唯一的部門完全無法使用系統。

**不要**為了「先讓登入能用」而把 5.1/5.2 節的前端改動提前到階段 11 一起上——半殘可用（能登入但功能全 404）比乾脆離線更容易引發誤操作或困惑，且會讓部署批次拆分變得複雜。**維護窗口內完整走完階段 11→14 才收工**，比分批上線更誠實、更不容易在某個週五晚上部署完 `app.py` 就意外收工過夜。

## 階段 10：部署 — storage.py 先行（對應計畫第 7 節步驟 4）

- [ ] 確認 `storage.py` 改動已獨立 commit（不與 `app.py` 改動混在同一個 commit，見階段 -1 commit 紀律）
- [ ] 部署新版 `storage.py`（`department` 參數暫留預設 `None`，`app.py` 未改前過濾視同不啟用）
- [ ] 驗證行為與改動前一致
- [ ] 打 tag `deploy-stage10-storage`，部署以此 tag 為準

## 階段 11：部署 — app.py 正式套用過濾（對應計畫第 7 節步驟 5）— 需階段 9 全綠才可執行，**維護窗口開始**

- [ ] 各 Store 方法的 `department` 參數拿掉預設值改必填（3.6 節，同一次 commit 完成）
- [ ] 部署新版 `app.py`
- [ ] 確認 `FLASK_SECRET_KEY` 已更換，既有 session 全部失效（全公司此時仍只有一個部門，任何 bug 表現成「本部門看不到自己資料」而非「跨部門洩漏」，這是刻意的安全部署順序）
- [ ] 打 tag `deploy-stage11-app`，若出問題回滾即部署 `deploy-stage10-storage` tag
- [ ] **此刻起系統對使用者不可用，直到階段 14 完成**

## 階段 12：哨兵驗證（對應計畫第 7 節步驟 6、8.2，採用 `sentinel_pack`）— 不需前端，維護窗口期間可執行

- [x] **【第十輪→已完成】`sentinel_pack` 已於第十一輪換成 v3**（`/Applications/My Project/sentinel_pack/`，內含 `00_preflight_check.sql`、`tests/`），舊版備份於 `sentinel_pack_old_v1/`
- [ ] 讀取 `sentinel_pack/README.md` v3 內容，確認驗證項目與資料筆數（T-00~T-14、50 筆哨兵＋2 筆孤兒列）
- [ ] **【第十一輪關鍵】`01_seed_sentinel.sql` 的 `devices` INSERT 段落，執行前務必核對是否已依實際 schema 更新**——正式 `devices` 表欄位是 `id`（PK，需明確給值）/`model`（非 `device_model`）/`category`/`line`，若 seed 腳本仍寫 `device_model` 或未給 `id`，INSERT 會直接失敗，需先手動修正
- [ ] `pip install werkzeug>=3.0 && python gen_hashes.py` 產生密碼雜湊（隨機產生，不寫死進檔案）
- [ ] 替換 `01_seed_sentinel.sql` 三個佔位字串（`__PW_HASH__`/`__ADMIN_PW_HASH__`、`__HOME_DEPT__`、`__COLLIDE_CODE__`）
- [ ] 對照 Supabase 實際 schema 檢查各段 INSERT 假設欄位是否吻合（`alarm_views` 是計數表還是事件表要特別確認；`devices` 段落見上一條）
- [ ] 記錄 Dashboard 基準值（今日掃描數、機種數、Top10）
- [ ] 執行 `01_seed_sentinel.sql`，確認自我檢查印出的合計筆數與 `null_dept_devices=0`（實際數字以套件當下版本為準）
- [ ] `export` 密碼環境變數，執行 `verify_isolation.sh`，全數項目 exit 0
- [ ] 特別確認撞名機種測試（`ACM001` 過濾是否正確帶 `department`，而非只比對 `device_model`）與哨兵資料反向確認（防止資料沒載入成功卻誤判全過）
- [ ] **補充驗證**（若 `sentinel_pack` 尚未涵蓋）：一般帳號帶 `?dept=別的部門` 打 `/api/alarms`，回應必須仍只有自己部門資料
- [ ] **補充驗證**：超管登入路徑分岔——用等於部門管理員密碼的字串打 `__super__` 選項應失敗，反之亦然（確認無 fallthrough）

## 階段 13：前端改動（對應計畫第 5 節）— 維護窗口內

- [ ] 撰寫 `frontend/js/api.js`：`apiFetch()` 統一封裝、401 全域攔截導回登入、429 讀 `Retry-After` 倒數、`whoami` 結果快取；寫入呼叫時自動把當前部門值放進 URL path（不進 body/query）
- [ ] `login.html`、`admin-login.html` 新增部門下拉選單（串接 `/api/departments/public`，含 `__super__` 選項），改用 `api.js`
- [ ] `dashboard.html` 新增超管部門切換器：URL query param `?dept=` 為唯一權威，`sessionStorage` 僅重載補位，`__all__` 為明確選項非預設值
- [ ] `dashboard.html`：`?dept=__all__` 時「新增機種／新增警報」按鈕停用並提示「請先選擇部門」
- [ ] `dashboard.html` 新增部門管理頁籤（`isSuperadmin` 條件顯示，含新增/重設密碼/停用/purge 對話框）
- [ ] **確認移除**「超管新增機種/警報時加部門選擇欄位」這類舊構想——已被切換檢視部門模式取代，不應出現在既有畫面裡
- [ ] `index.html` 移除四處 `confirmed_by: 'operator'` 字面值，topbar 加上目前登入部門名稱顯示（共用平板防呆）
- [ ] `sw.js`：`STATIC_SHELL` 加入 `js/api.js`；`activate` 事件清除舊版 cache＋當前 cache 裡殘留的 `/api/*` 回應；`skipWaiting()`+`clients.claim()`；`/api/*` 排除快取；登出流程主動 `caches.delete(CACHE)`；快取版本號遞增
- [ ] 確認 `<script src="/js/api.js">` 對應的 Flask static 路由能正確載入（非 404）

## 階段 14：SW 快取行為手動驗證（對應計畫 5.5，無法自動化，必須人工執行）— **維護窗口結束點**

- [ ] 在共用平板（或模擬環境）用改造前舊版 PWA 登入 A 部門，瀏覽讓 API 回應被快取
- [ ] 部署新版前端與 `sw.js`，不手動清瀏覽器快取
- [ ] 開發者工具確認新版 SW 已 `activate`、舊版已被替換
- [ ] 檢查 Cache Storage 只剩新版本 cache 名稱
- [ ] **【第十輪補強】跨帳號測試的 B 帳號來源**：哨兵部門 `hidden=true`（見 1.1、4.7），不會出現在 `/api/departments/public` 回傳的登入頁下拉選單，無法直接選取登入 → 測試開始前先跑 `UPDATE departments SET hidden=false WHERE id='<哨兵部門id>'` 暫時讓它出現，測完這一步立刻改回 `hidden=true`（同一 SQL 語句，`true`/`false` 對調）；不影響維護窗口節奏，也不需要動登入頁機制或延後階段 14
- [ ] 登出 A、登入 B（哨兵部門，前一步驟已臨時 `hidden=false`），模擬弱網/離線，確認 `/api/*` 請求失敗時回錯誤而非回舊快取
- [ ] 測試完成，立即執行 `UPDATE departments SET hidden=true WHERE id='<哨兵部門id>'` 改回隱藏，**不要遺漏這步**——忘記改回去會讓哨兵部門一直出現在正式登入頁的下拉選單裡
- [ ] 確認 `js/api.js`、`manifest.webmanifest`、圖示離線模式下仍可載入
- [ ] **本步驟完成後，系統恢復對使用者可用——維護窗口結束**

## 階段 15：批次匯入工具（對應計畫第 6 節）

- [ ] 撰寫 `backend/import_alarms.py`：CSV/Excel 讀取，`--department <id>` 必填
- [ ] **前置驗證**：取出 CSV 所有相異 `device_model`，比對該部門 `devices` 表是否都存在，缺任何一個整批中止並列出清單與各自警報筆數
- [ ] `--create-missing-devices` opt-in 旗標（預設關閉，防止錯字建立新機種）
- [ ] 交易語意全有全無：任何一筆失敗整批回滾
- [ ] `--mode append`（預設，走 `upsert_one()`）/`upsert`（同義）/`replace`（需 `--yes-i-mean-replace`，走 `save()`）
- [ ] `--dry-run`：印出將寫入筆數＋缺少機種清單（`ERROR` 標示）；`replace` 模式額外印出將刪除筆數
- [ ] 機種批次建立比照辦理或整合進同一腳本

## 階段 16：第一個真實部門上線（對應計畫第 7 節步驟 7~9）

- [ ] 先建立機種（`import_devices.py` 或同腳本 `--devices-only`），驗證無誤
- [ ] 用批次匯入工具建立第一個真實第二部門的機種與警報
- [ ] 建立第二個真實部門的登入帳號
- [ ] 重跑 `verify_isolation.sh`（真實部門取代哨兵部門位置）＋階段 12 的補充驗證，確認結果與階段 12 一致

## 階段 17：收尾（對應計畫第 7 節步驟 10、「purge 時機的判斷」）

依當下狀況判斷，不預設固定選項：
- [ ] **選項 A（清除）**：確認往後不需頻繁改動隔離邏輯，或稽核規範要求正式庫不留測試資料 → 執行 `02_teardown_sentinel.sql`
- [ ] **選項 B（保留但清空）**：預期近期還會頻繁修改隔離邏輯 → 只清資料列，保留 `departments` 表裡 `hidden=true` 的哨兵部門記錄本身，供日後重新灌資料當靶場

---

## 待確認事項（需先決定才能排入階段 2）

- [ ] 你目前這個部門（ACM001、TFM001 等機種）要取什麼正式名稱？（`migrate_add_departments.py`（階段 2）建立第一筆 `departments` 資料列需要；`import_alarms.py`（階段 15）的 `--department` 是另一支工具的必填參數，兩者依賴同一名稱但屬不同工具，可先用暫定 slug，之後改名不影響 id）

---

## 附註

- **【第九輪定案】階段 -1 已從「待專家決策」轉為具體步驟**：採選項甲（先 commit 舊變更當基準），`.gitignore` 必須先於 `git add` 寫好（防祕密進歷史），commit 後打 `pre-multitenancy` tag。往後每階段一個 commit、每個部署點一個 tag，不開 branch
- **【第八輪新增】階段 -1（git 基準點）與階段 -0.5（Render 部署）必須在階段 0 之前完成**——這兩個是先前版本清單完全缺漏的前置階段，PLAN 明確要求 Render 在多部門改造前完成，git 基準點則是階段 10/11 分兩步部署設計能不能真正回滾的前提
- **【第八輪新增】階段 11～14 是同一個維護窗口，開始前先確認當天做得完，不要中途收工**——這段期間唯一部門的系統完全無法使用（登入頁與 API 路由形狀同時變動），細節見階段 11 前的警示區塊
- 每個階段完成後才進入下一階段，尤其：
  - 階段 3（主鍵切換）必須在階段 5（`storage.py` 含 `upsert_one()`）之前完成
  - 階段 9（路由權限測試）必須全綠才可進入階段 11（部署 `app.py`）——測試的把關意義在部署前生效才有意義
- 階段 10、11 之間刻意分兩步部署，讓過濾邏輯上線時全公司仍只有一個部門，任何 bug 表現成「本部門看不到自己資料」而非「跨部門洩漏」；**兩者改動必須落在不同 commit，否則無法真正部署到「只有 storage.py 生效」的中間點**（見階段 -1、階段 10）
- **【第十輪新增】階段 14 跨帳號測試前，記得臨時把哨兵部門 `hidden` 改 `false`，測完立刻改回 `true`**——否則登入頁選不到第二個部門帳號，這個測試步驟做不了（見階段 14）
- **【第十輪提醒】階段 12 執行前，先確認本機 `sentinel_pack` 是不是作者已交付的 v3**（T-00~T-14、含 `00_preflight_check.sql`）——本機截至第十輪仍是舊版 T-01~T-10，換成 v3 後清單裡「以 README 當下版本為準」的寫法會自動對上新內容，不需要再改文字（見階段 12）
- **【第十一輪新增】`devices` 表實際欄位是 `model`，不是 `device_model`**（`alarms` 才是 `device_model`）——PLAN 全文 `devices` 相關 SQL 已改用 `model`，路由/API 層維持 `device_model` 名稱但經由 `storage.py` 的 `_row_to_device()` 做唯一轉換點；`devices` 主鍵切換（階段 3）比原假設輕，只需 drop/add unique 約束兩行，不動主鍵（見 PLAN 1.2、3.1.1 節）
- **【第十二輪新增】部署平台由 Railway 改回 Render**（成本考量；`git log` 顯示這本來就是專案原本的臨時方案，這次是轉正）——全文所有 Railway 字樣已置換為 Render，階段 -0.5 執行時需另外建立 Render 的部署設定（`render.yaml` 或後台手動設定），並確認是否要用免費層＋防休眠 cron（見 PLAN 第十二輪附錄）
- **【第十三輪新增】`backend/requirements.txt` 的 `google-generativeai` 已改為 `google-genai`**（程式碼實際用新版 SDK 語法，本機 `.venv` 因新舊套件並存長期掩蓋此落差，Render 乾淨環境部署後才首次曝光）——修正後尚未部署驗證，見階段 -0.5 補充項
- 完整審查對照見 `PLAN_department_isolation.md` 附錄（共十三輪審查）
