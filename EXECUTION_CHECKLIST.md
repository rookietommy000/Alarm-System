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
- [ ] **【第十五輪：展開為七步驟，第十七輪再加第7步】Supabase 遷移前備份**，詳見 PLAN 1.6 節：
  - [x] 1. 查清 `alarms`/`devices` 主鍵與約束實際名稱——`alarms_pkey PRIMARY KEY (device_model, code)`、`devices_pkey PRIMARY KEY (id)`、`devices_model_key UNIQUE (model)`（用 `\d alarms`/`\d devices` 實查確認）
  - [x] 2. 撰寫 `rollback_stage3.sql`（DROP COLUMN 新欄位、還原 1.3 節主鍵切換，用上面查到的真實約束名稱），已 commit（`9ade50b`）
  - [x] 3. `pg_dump -Fc --no-owner --no-privileges` 完整備份，並用 `pg_restore -l` 驗證可讀——已執行，415 TOC entries，`alarms`/`devices`/`feedback`/`ai_scans`/`ai_corrections`/`ai_logs`/`alarm_history`/`alarm_views` 8 張目標表均涵蓋 TABLE+DATA+約束+索引（**此步驟已取代 CSV 匯出，不需另外再做**）。檔案存於 `data/backup/`（已在 `.gitignore` 排除，不進版控；**提醒：應再搬到 Supabase 帳號體系以外的地方，如外接硬碟/雲端硬碟，避免只留在本機專案目錄**）
  - [x] 4. Supabase Dashboard 確認內建備份/保留天數——**【第十六輪查證，第十七輪校正解讀】免費方案沒有備份功能**（Scheduled backups/Point in Time Restore/Restore to new project 皆需 Pro 方案）。**這不是「兩層防護少一層」**——正確理解是：第一層＝交易包裹＋`rollback_stage3.sql`（防遷移失敗，已就緒），第二層＝`pg_dump`（防專案損毀，已就緒且已驗證），Dashboard 備份只是第二層的備援副本；專家確認「夠，可以往下走」，不需升級付費方案，見 PLAN 1.6/第十六~十七輪
  - [x] 5. 確認 `002`/`003` 的 SQL 全部包在 `BEGIN`/`COMMIT` 交易內——兩支腳本皆已包裹並成功 `COMMIT`
  - [x] 6. 遷移執行後重跑驗收——`\d devices`/`\d alarms` 確認新約束正確生效，計數驗證 `alarms` 1759 筆、`devices` 14 筆不變，無 NULL、無重複
  - [x] 7. 階段 2 回填完成、驗證無 NULL 之後執行一次 `pg_dump`——`post_backfill_20260812_0912.dump`（437 TOC entries，已驗證可讀）

**【階段 1/2/3 全部執行完成】**
- `001_add_department_columns.sql`：9張表加 `department` 欄位，`departments`/`login_attempts` 建表完成
- `002_migrate_add_departments.sql`：建立 `mf4d` 部門，回填 `devices` 14筆／`alarms` 1759筆／`feedback` 76筆／`alarm_views` 3筆，`feedback` 剩 1 筆測試殘留資料留 NULL（符合 PLAN 1.5 節設計）；`line` 交叉驗證通過（`mf4d`／`2.1`=7、`mf4d`／`2.2`=7）
- `003_switch_constraints.sql`：`devices` 主鍵 `id` 不動＋新增 `devices_dept_model_key UNIQUE(department, model)`；`alarms` 主鍵切換為 `PRIMARY KEY(department, device_model, code)`；兩表 `department` 皆已收緊 `NOT NULL`
- [x] 執行重複值預檢：`00_preflight_check.sql` 已含此檢查，結果 **0 筆重複**，`alarms`/`devices` 均可安全繼續
- [x] **【第十一輪額外查明】無外鍵指向 `alarms`/`devices` 舊主鍵**（0 筆），主鍵切換不用擔心連動；基準筆數 `alarms` 1759、`devices` 14、`ai_scans` 5，與既有記錄一致
- [x] **【第十一輪額外查明】`devices.id` 是否為型號本身**：已確認不是（`id` 與 `model` 完全脫鉤），新機種安全建立不需要額外設計 id 產生規則
- [x] **【第十一輪額外查明】`devices.line` 是否隱含跨部門資料**：已與使用者確認，`line`（2.1/2.2）只是同一部門內的產線分類標記，非隱藏的部門邊界，不影響現行「只有一個部門」的前提

## 階段 1：建表與加欄位（對應計畫 1.1、1.2、1.3、1.4、2.2.1）— 全部 nullable，零行為變更 — 【已完成，第十八輪】

- [x] 建立 `departments` 表（`id`/`name`/`pw_hash`/`admin_pw_hash`/`session_version`/`active`/`hidden`/`purgeable`/`created_at`）——`backend/migrations/001_add_department_columns.sql`
- [x] 建立 `login_attempts` 表（`id`/`ip`/`department`（無 FK）/`success`/`attempted_at`），SQL 註記中明確標註 `department` 對不到 `departments` 表是刻意設計、不可被清理腳本誤刪
- [x] `devices` 加 `department` 欄位＋索引
- [x] `alarms` 加 `department` 欄位＋索引
- [x] `ai_scans`／`ai_corrections`／`ai_logs`／`alarm_history`／`feedback`／`alarm_views` 加 `department` 欄位＋索引
- [x] 驗證：9 張表皆已取得 `department` 欄位，外鍵 `alarms_department_fkey`/`devices_department_fkey` 正確指向 `departments(id)`

## 階段 2：遷移腳本（對應計畫 1.7）— 【已完成，第十九輪】

- [x] 撰寫 `backend/migrations/002_migrate_add_departments.sql`：重複值預檢（0 筆）→ 用現有 `.env` 密碼雜湊（`pbkdf2:sha256`，見 `gen_department_hashes.py`）建立 `mf4d` 部門（`hidden=false, purgeable=false`）→ `devices`/`alarms` 回填 → `feedback`/`alarm_views` best-effort 回填
- [x] 執行遷移腳本，驗證 `devices`/`alarms` 無 NULL——`devices_null_dept=0`、`alarms_null_dept=0`；`feedback` 76 筆回填成功，剩 1 筆測試殘留資料（`code=TEST`）留 NULL 符合預期；`alarm_views` 3 筆全數回填
- [x] `line` 交叉驗證：`mf4d`／`2.1`=7、`mf4d`／`2.2`=7，合計 14 筆，無非預期組合
- [x] 回填後補一次 `pg_dump`（`post_backfill_20260812_0912.dump`，437 TOC entries，已驗證可讀）

## 階段 3：主鍵/唯一約束切換（對應計畫 1.2、1.3、第 7 節步驟 3）— 【已完成，第十九輪】— 獨立階段，已在階段 5（storage.py 含 `upsert_one()`）之前完成

- [x] `devices` 主鍵 `id` 不動，`alter table devices drop constraint devices_model_key;` + `alter table devices add constraint devices_dept_model_key unique (department, model);` 已執行——`backend/migrations/003_switch_constraints.sql`
- [x] `alarms` 主鍵從 `(device_model, code)` 切換為 `(department, device_model, code)`，已執行並用 `\d alarms` 驗證生效
- [x] 確認無其他表以外鍵指向這兩張表的舊主鍵（第十一輪已查明 0 筆，此次未發現連動問題）
- [x] `devices`/`alarms` 的 `department` 皆已收緊 `NOT NULL`
- [x] 最終計數驗證：`alarms` 1759 筆、`devices` 14 筆，數量與遷移前一致，無 NULL、無重複

## 階段 4：後端 — 密碼與登入安全（對應計畫第 2 節，本清單風險最高的階段）— 【核心登入分岔已完成並實測，第二十一輪；節流機制尚未實作】

- [x] `requirements.txt` 加 `werkzeug>=3.0`
- [x] 密碼雜湊／驗證改用 `generate_password_hash`/`check_password_hash`（`method="pbkdf2:sha256"`，Python 3.9 無 `hashlib.scrypt`，見第十八輪）
- [x] **實作登入路徑入口分岔**：表單選 `__super__` 只比對 `SUPERADMIN_PASSWORD`（`hmac.compare_digest`），選真實部門只比對該部門 `admin_pw_hash`，**兩條路互不 fallthrough**——已用真實 Supabase 連線實測「超管密碼登入部門帳號」與「部門密碼登入 `__super__`」皆被正確拒絕
- [x] `session.clear()` 於登入成功時執行，避免舊 session 殘留鍵帶入新登入
- [x] `POST /api/admin/departments`、`reset-password` 拒絕與 `SUPERADMIN_PASSWORD` 明文相同的密碼（400）——已實測驗證
- [x] 實作 `assert_session_valid()`：部門存在性＋`active`＋`session_version` 三態合取校驗，`None`（不存在）也要快取，TTL 60 秒行程內快取——已用真實 API（停用/重設密碼/purge）實測三種情況皆讓 session 立即失效（purge 後回乾淨 401，非撞外鍵 500）
- [x] `set_active()`／`update_password()`／`purge()` 成功後主動 pop 快取，讓操作者所在 worker 立即生效——已實測確認同一 process 內立即生效
- [x] 實作部門不存在時的 dummy hash 比對（`_DUMMY_HASH` 模組載入時算好），回應與密碼錯誤完全一致（枚舉防護）——已寫入程式碼（`_do_login()` 內），格式不合法與部門不存在兩種情況皆會消耗 dummy hash 比對時間
- [ ] **【尚未實作】登入節流細網**：`(ip, department)` 組合，15 分鐘窗口內最近成功登入之後的連續失敗數 `N_ip_dept`
- [x] 實作登入節流粗網：只看 `ip`（不看 department），門檻 20 次，`N_ip`（`LoginAttemptStore.count_coarse()`）
- [x] `delay = min(max(2**N_ip_dept, 2**(N_ip-19)), 60)`，429 + `Retry-After`，**不用 `sleep()`**——已用真實 Supabase 連線實測：立即重試回 429、等待 delay 秒後可重試、成功登入後計數歸零
- [x] `login_attempts` 寫入三段式判斷：格式不合法（不符 `DEPT_ID_RE`）不查 DB 不寫入；格式合法但部門不存在照常寫入 `success=false`；已在節流窗口內的請求只回 429 不再寫入——三種情況皆已實測驗證
- [x] `cleanup-expired` 端點新增 90 天清理（`login_attempt_store.cleanup_expired(days=90)`），回應含 `login_attempts_removed` 欄位

**【第二十二輪重要發現】第一版實作有邏輯缺陷，已修正**：最初把 `delay` 理解成「N 達門檻就永久節流」，導致成功登入前必須先通過節流檢查、節流又必須靠成功登入才能歸零，形成死鎖（任何人一旦觸發一次失敗就永久被鎖）。修正為 `delay` 是相對「最後一次失敗時間」的倒數計時，過了窗口即可重試。詳見 PLAN 第二十二輪。

**⚠️ 上線前提醒**：`SUPERADMIN_PASSWORD` 目前本機測試值為弱密碼（純數字），節流機制已完成，但正式上線前仍需要換成高強度密碼。

## 階段 5：後端 — `storage.py`（對應計畫第 3 節）— 【核心改寫已完成，第二十輪】

- [x] 修正 `SupabaseStore.save()` 刪除掃描地雷：`department` 有值時刪除掃描比對加 `department=eq.<dept>` 過濾（`devices`/`alarms` 皆適用），實測驗證：`save([], department='zztest')` 只刪 `zztest` 部門資料，`mf4d` 14 筆完全不受影響
- [x] 新增 `upsert_one()`／`delete_one()`，**明確指定 `on_conflict`**（`alarms`: `department,device_model,code`；`devices`: `department,model`），已用真實 Supabase 連線測試寫入/刪除，過程無殘留資料
- [x] **【第十九輪定案】`_row_to_device()` 採永久雙 key**（不是過渡期）：回傳同時含 `model` 與 `device_model` 兩個 key 指向同一個值；已實測驗證 14 筆 `devices` 資料雙 key 值一致；`_device_payload_to_row()` 做寫入方向對稱轉換（兩個 key 都接受，統一轉 `model` 寫入 DB）
- [x] `JsonStore.load()`/`save()` 加 `department=None` 參數（忽略，維持單租戶相容，僅服務 pytest）——57 個既有測試全綠
- [ ] 另建獨立 Supabase 開發專案（schema 相同），本機開發連線資訊放 `.env.local` 並加入 `.gitignore`——**不要**讓 `JsonStore` 支援多部門過濾（尚未執行，非阻塞項，可延後）
- [x] 新增 `DepartmentStore` 類別：`list()`（含 `hidden`/`purgeable`，已實測確認絕不回傳密碼雜湊）、`get_by_id()`（含 `session_version`/`active`，已驗證）、`list_public()`（供 `/api/departments/public` 用）、`create()`、`update_name()`、`update_password()`（連帶 `session_version += 1`）、`set_active()`、`purge(dept_id, confirm_id)`
- [x] `purge()` 保護機制：僅 `purgeable=true` 可執行，需 `confirm_id` 與 `dept_id` 相符二次確認——已建立臨時測試部門 `zztest` 實測整套流程（建立→寫入→purge），確認 `mf4d` 資料全程未受影響
- [ ] 3.6 節：`AuditLogger`/`FeedbackStore`/`ViewStore`/`AiScanStore`/`ai_memory` 所有涉及部門的方法，`department` 參數改必填——**目前處於過渡期，全部保留預設值 `None`**（PLAN 4.8 節），待階段 11 部署 `app.py` 時在同一次 commit 內移除預設值
- [ ] 3.7 節：確認 `DeptScope.ALL` 路徑的 query builder 不加任何 `department` 相關過濾條件（含容易被誤加的 `.not.is.null`）——`load()`/`stats()` 系列方法已支援 `department=None` 時不加過濾，符合此節要求，但正式的 `scope_department()` 呼叫邏輯要到階段 7（`app.py`）才會寫

**驗證方式**：未透過 pytest（Supabase 連線非測試環境覆蓋範圍），改用 `.venv/bin/python3` 直接呼叫 real Supabase 逐項手動驗證（`load()`、`upsert_one()`/`delete_one()`、`save()` 部門過濾、`DepartmentStore` 讀取/`purge()`），過程中建立的所有臨時測試資料（`zztest` 部門、`TEST-UPSERT-*`/`TEST-MODEL`/`TEST-CODE`）皆已清除，最終確認 `alarms` 1759 筆、`devices` 14 筆、`departments` 僅 `mf4d` 一筆，與階段 3 完成時一致。

## 階段 6：後端 — AI 管線部門隔離（對應計畫 3.5）

- [ ] `ai_memory.py` 所有查詢函式（歷史記錄、候選建議）加 `department` 參數並套用於查詢條件，不可省略
- [ ] `ai_pipeline.py` 把 `session["department"]` 一路往下傳，不遺漏

## 階段 7：後端 — `app.py` 權限框架（對應計畫第 4 節）— 【已完成並實測，第二十一輪】

- [x] 實作 `scope_department()`：讀取過濾用，只讀 `session`／（超管時）`?dept=`，`DeptScope.ALL`/`DeptScope.DEPT`，取不到部門直接 401——已實測一般帳號帶 `?dept=其他部門` 完全不影響查詢範圍
- [x] 實作 `resolve_target_department()`：寫入專用，**只讀 URL path，禁止讀 `request.args`**（配套規則 a）；body 帶的 `department` 與 path 不符 → 400（規則 b，`_check_body_department_conflict()`）；無部門路徑段的端點超管呼叫一律 400（規則 c，`feedback`/`view`/`analyze`/`confirm`/`correct` 皆已實作）——已用 AST 解析（排除 docstring）驗證函式本體不含 `request.args`，**發現簡單字串比對會被 docstring 說明文字誤判，寫測試時需注意（見第二十一輪）**
- [x] 新增 `superadmin_required`、`@public_endpoint` 裝飾器；四個裝飾器皆設 `_auth_level` 標記——已逐一檢查全部 30 個 `/api/*` 路由確認標記完整且與規格一致
- [x] **同一 rule 依 HTTP method 拆成獨立 view function**（`GET`=`login`、`PUT`/`DELETE`=`admin`）——已在 `alarms`/`devices` 的 `<department>/<device_model>[/<code>]` 路由套用
- [x] `create_app()` 啟動時 fail-fast 檢查：生產環境未設 Supabase 直接中止，不悄悄降級
- [x] `FLASK_SECRET_KEY` 讀取環境變數固定值（本機 `.env` 已有），Render 環境變數維持既有設定，尚未在部署時重新產生換新（見階段 11 維護窗口）

## 階段 8：後端 — 各端點改動（對應計畫 4.4~4.7）— 【已完成並實測，第二十一輪】

**警報（`alarms`）**：
- [x] `GET /api/alarms`（列表）→ `login`，`scope_department()` 過濾——已實測 mf4d 帳號登入後只看到自己 1759 筆
- [x] `POST /api/alarms/<department>` → `admin`，`resolve_target_department(department)`
- [x] `GET /api/alarms/<department>/<device_model>/<code>` → `login`
- [x] `PUT /api/alarms/<department>/<device_model>/<code>` → `admin`
- [x] `DELETE /api/alarms/<department>/<device_model>/<code>` → `admin`

**機種（`devices`）**：
- [x] `GET /api/devices`（列表）→ `login`，`scope_department()` 過濾
- [x] `POST /api/devices/<department>` → `admin`，`resolve_target_department(department)`（不再從 body 解析）
- [x] `GET /api/devices/<department>/<device_model>` → `login`
- [x] `PUT /api/devices/<department>/<device_model>` → `admin`
- [x] `DELETE /api/devices/<department>/<device_model>` → `admin`

**其他端點**：
- [x] `POST /api/feedback`、`POST /api/view` 加 `login_required`（原無驗證），寫入帶 `department`（session 取），超管呼叫 400
- [x] `GET /api/feedback/stats`、`GET /api/view/stats` 加驗證＋`scope_department()` 過濾（讀取端點，超管可用 `?dept=`）
- [x] `POST /api/analyze` 加驗證，超管呼叫 400（`department` 傳入 `run_pipeline()` 待階段 6 AI 管線隔離完成後串接）
- [x] `POST /api/confirm`、`POST /api/correct` 加驗證，超管呼叫 400；`confirmed_by` 改用 `_confirmed_by()`（`f"{target}/{role}"`，`role` 三態含 `superadmin`）
- [x] `GET /api/audit` 依 `scope_department()` 過濾
- [x] `GET /api/admin/scan-stats`、`scan-recent`、`scan-ranking` 依部門過濾
- [x] 新增 `GET /api/admin/ai-logs` 讀取端點，套用部門過濾
- [x] `POST /api/admin/cleanup-expired` 改 `superadmin_required`
- [x] 新增部門管理端點：`GET/POST/PUT /api/admin/departments*`、`PUT .../active`、`DELETE .../<id>`（purge，body 需 `confirm_id`）——已用真實 API 逐一實測（建立/改名/停用/重設密碼/purge）
- [x] 新增 `GET /api/whoami`（`@public_endpoint`）——已驗證回應格式正確
- [x] 新增 `GET /api/departments/public`（`@public_endpoint`，只列 `active=true` 且 `hidden=false`）

**驗證方式**：`tests/test_api.py` 已同步更新反映新路由結構，57 個測試全數通過；核心安全機制（登入分岔、越權防護、`assert_session_valid()` 三態）額外用真實 Supabase 連線手動驗證（詳見 PLAN 第二十一輪），過程建立的臨時部門（`zztest2`~`zztest6`）皆已清除，正式資料（`mf4d`）全程未受影響。

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

- [x] **【第十四輪定案】部門正式名稱**：`id = "mf4d"`（小寫 ASCII slug，符合 2.2.4 節 `DEPT_ID_RE` 格式驗證，永久不可改）、`name = "製造四部包裝組"`（顯示名稱，可改）。過程中發現一個原本要踩的坑：使用者一開始提出大寫 `MF4D` 或直接用中文當 `id`，兩者都會讓 2.2.4 節的登入節流格式驗證判定為「格式不合法」、導致真實部門完全無法登入——已說明 `id`（URL/DB 安全 slug）與 `name`（人類可讀顯示名稱，可放中文）本來就是刻意分開的兩個欄位，中文/大寫一律放 `name`，`id` 維持小寫 ASCII

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
- **【第十四輪定案】部門正式名稱**：`id="mf4d"`、`name="製造四部包裝組"`——待確認事項已全部解決，可正式進入階段 0/2
- **【第十五輪定案】遷移前備份策略確定為六步驟流程**（查主鍵真名 → 寫 `rollback_stage3.sql` → `pg_dump -Fc` 並驗證可還原 → Dashboard 備份確認 → 遷移腳本全包 `BEGIN`/`COMMIT` → 事後重跑 `00_preflight_check.sql`）——取代原本「快照或CSV擇一」的簡化版本，CSV 匯出已被 `pg_dump` 完全取代不需另做（見 PLAN 1.6 節、階段 0）
- **【第十六~十七輪校正】免費方案無 Dashboard 備份，但專家確認現有防護足夠**——正確分層是「交易包裹+`rollback_stage3.sql`」（防遷移失敗）vs「`pg_dump`」（防專案損毀，Dashboard 備份只是這層的備援副本），階段1-3幾乎無資料遺失風險，不需升級付費方案；新增備份步驟7：階段2回填後再做一次 `pg_dump`（見 PLAN 1.6 節）
- **【第十八輪新增】本機與 Render 皆為 Python 3.9，`hashlib` 無 `scrypt` 支援**——`generate_password_hash()` 一律要明確指定 `method="pbkdf2:sha256"`，不能用預設演算法，否則 `AttributeError`；`backend/migrations/001~003_*.sql`、`backend/gen_department_hashes.py` 已撰寫完成，001 已執行並驗證通過（9張表皆有 `department` 欄位，`departments`/`login_attempts` 結構與外鍵正確）（見 PLAN 第十八輪）
- **【第十九輪定案，取代第十一輪過渡期構想】`_row_to_device()` 改為永久雙 key，前端 76 處不動**——`devices.line` 排除邏輯經專家確認合理（業務語意問題資料推不出答案，需問系統擁有者），並補上正式定義句；`_row_to_device()` 同時回傳 `model`/`device_model` 兩個 key，理由是階段13維護窗口已滿、混用錯誤是沉默的、收斂前端收益為零——三者皆非本次必要（見 PLAN 3.1.1、4.4 節）
- 完整審查對照見 `PLAN_department_isolation.md` 附錄（共十九輪審查）
