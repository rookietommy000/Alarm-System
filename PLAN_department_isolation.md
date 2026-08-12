# 警報查找系統 — 專案現況與多部門隔離規劃

## Context

這是一套工廠設備警報代碼查詢系統（Flask + Vue 3 CDN，Supabase 資料庫），從單一部門使用開始逐步擴充。系統已完成前台查詢/拍照分析、後台統一管理、資料庫串接、RWD、PWA 基礎建設。現在要推廣給其他部門使用，但各部門的機種與警報代碼完全不同，需要在**同一套程式碼、同一個資料庫**上做到「登入後資料完全隔離」的效果，並讓「新增部門」之後續擴充幾乎零成本。

本規劃經過一輪安全審查（見附錄「審查發現與修正」），修正了原版中會讓隔離機制形同虛設的三個結構性漏洞，並補上密碼安全、快取洩漏、測試策略等強化項目。**這一版才是實際要動工的版本。**

---

## 一、已完成部分（現況）

### ✅ 前台（`frontend/index.html`）
- ✅ 拍照分析流程：先選機種 → AI（Gemini）辨識 → 結果卡片可點開查看詳情，全程不跳頁
- ✅ 桌機測試用 mock 模式：避免開發時重複呼叫 Gemini API 產生費用
- ✅ 警報查詢頁：關鍵字搜尋 + 機種下拉篩選器
- ✅ 底部導覽列（手機）：拍照／搜尋／更多（後台入口＋登出，用 bottom sheet 選單呈現）
- ✅ 淺色系 UI（`#f5f4f0` 背景），RWD 已涵蓋主要斷點

### ✅ 後台（`frontend/dashboard.html`，取代舊 admin.html + dashboard.html）
統一介面，側邊欄／手機版改下拉選單導覽，四個分頁：
- ✅ **Dashboard 總覽**：今日/本週掃描數、AI 辨識失敗率、回饋統計、最常查詢 Top10、最近掃描記錄、機種掃描排行、各警報方案有效率
- ✅ **掃描紀錄管理**：日期/機種/代碼/是否修正篩選＋分頁
- ✅ **警報管理**：CRUD＋操作歷史紀錄
- ✅ **機種管理**：新增/刪除機種，含掃描量與失敗率
- ✅ 「清理過期資料」按鈕：依 AI 判讀信心分級（tier）保留天數，手動觸發清除 Supabase 過期掃描紀錄

### ✅ 後端 API（`backend/app.py`、`backend/storage.py`）
- ✅ 既有：`/api/alarms`、`/api/devices`、`/api/feedback*`、`/api/view*`、`/api/analyze`、`/api/confirm`、`/api/correct`、`/api/audit`
- ✅ 新增：`/api/admin/scan-stats`、`/api/admin/scan-recent`、`/api/admin/scan-ranking`、`/api/admin/cleanup-expired`、`POST/DELETE /api/devices`
- ✅ 資料層：`JsonStore`（本機/測試 fallback）、`SupabaseStore`（正式環境）、`AuditLogger`、`FeedbackStore`、`ViewStore`、`AiScanStore`
- ✅ AI 分析管線（`backend/ai/`）：`ai_pipeline.py`（Gemini 辨識＋POST 規則過濾＋二次驗證＋記憶＋警報＋日誌六層）、`ai_memory.py`（拍照歷史分級保留）、`ai_logger.py`（決策日誌）

### ✅ 資料庫（Supabase）
全面接通：`alarms`、`devices`、`feedback`、`alarm_views`、`alarm_history`（稽核）、`ai_scans`、`ai_corrections`、`ai_logs`，共 1759 筆警報、14 台機種

### ✅ 資料清理
- ✅ 3 筆髒翻譯資料修正（匯入時產生的亂碼/錯譯，已核對並修正）
- ✅ 17 個不再使用的舊本機檔案（過期備份快照、匯入中繼檔、原廠文件）歸檔至 `data/backup/`

### ✅ PWA
- ✅ `manifest.webmanifest`、`sw.js`（service worker，HTML always-network／API network-first／靜態 cache-first）、圖示、`apple-mobile-web-app-*` meta 標籤
- ✅ 修正：manifest/前台/後台三處主題色不一致問題、`sw.js` 缺漏 `/app`、`/admin/dashboard` 路徑、`dashboard.html` 原本完全沒有 PWA 設定（已補齊）

### ⏸ 已知決定不做 / 延後的項目
- ⏸ 掃描趨勢圖表（折線圖/熱力圖）— 等資料量累積
- ⏸ 掃描詳情頁含原始圖片 — 系統設計本來就不存照片，且需接 Supabase Storage
- ⏸ 回饋管理列表/處理 — 目前回饋只有按鈕計數，無文字內容可管理
- ⏸ APK 封裝 — 現有 RWD+PWA 已涵蓋主要需求，封裝維護成本高於效益，先不做
- ⏸ 義大利文機種 2 筆警報缺正確中文翻譯（`MANCANZA TENSIONE DI RETE`、`ABBINAMENTI ESCLUSI`）
- ⏸ **個人帳號層級稽核身份**（GMP `confirmed_by` 目前只做到部門層級，如 `"2.1線/admin"`；若日後稽核需要追蹤到個人，需另外設計使用者帳號系統——這是已知限制，非本次疏漏）

### ⬜ 尚未做，本次規劃前的待辦
- ⬜ **正式部署到 Render**（目前僅本機區網運作，電腦關機/換網路即斷線）— **確認在多部門改造前先完成**：理由不只是避免同時改兩件大事，更重要的是隔離驗證（第 8 節）需要對正式環境的 Supabase 跑才有意義，且部署後才會遇到 cookie Secure、反向代理等環境差異，這些跟登入機制改動同屬一塊，先踩完比較好
- ⬜ **【第八輪審查發現】專案已是 git repo（`main` 分支，有 `origin` 遠端），但有一大批從先前多次對話累積、從未 commit 過的變更**（Dashboard 重構、AI 管線、RWD 修正、`data/alarms.json`/`data/devices.json` 刪除等，`git status` 已確認）。在動工多部門隔離之前，這批舊變更**如何處理待專家決定**（先 commit 當基準點，或先確認內容後再說）——這件事必須先解決，否則階段 10/11「刻意分兩步部署以便精確回滾」的設計會失去意義（沒有乾淨的基準線，無法區隔「舊工作的問題」與「這次隔離改動的問題」）

---

## 二、⬜ 多部門隔離規劃

### 需求確認（含審查後修正）

三層權限：

| 角色 | Session | 能力 |
|---|---|---|
| 部門一般使用者 | `auth=True, department="X"` | 前台查詢/拍照，只看自己部門 |
| 部門管理員 | `auth=True, admin=True, department="X"` | 後台管理，只管自己部門 |
| 總管理員 | `auth=True, admin=True, superadmin=True` | 跨部門檢視全部 + 部門管理頁（新增部門、重設密碼） |

確認事項：
- **登入方式**：登入頁保留部門下拉選單 + 密碼欄位。**「完全看不到其他部門存在」降級為「登入後資料完全隔離」**——下拉選單本身會列出部門名稱清單，這是刻意的體驗權衡（見附錄），部門名稱本身不視為機密，資料才是。
- **`confirmed_by`（GMP 稽核欄位）**：這次做到部門層級（如 `"2.1線/admin"`），個人帳號層級稽核列為已知限制，寫入文件（見上方「已知決定不做」）
- **`cleanup-expired`（過期資料清理）**：只有總管能按，一次清全部部門
- **`feedback`/`alarm_views`**：都加部門欄位
- **部門管理員不能自己改密碼**，只有總管能設定/重置
- **新部門需要批次匯入警報**：後台目前只有單筆 CRUD，1000+ 筆規模需要專用匯入工具（見第 6 節）

---

### ⬜ 1. 資料庫改動（Supabase）

#### 1.1 新表 `departments`
```sql
create table departments (
  id             text primary key,       -- slug，例如 "line21"，建立後不可改
  name           text not null,          -- 顯示名稱，例如 "2.1線"，可改
  pw_hash        text not null,
  admin_pw_hash  text not null,
  session_version integer not null default 1,  -- 【審查修正】重設密碼時 +1，強制既有 session 失效
  active         boolean not null default true,  -- 能不能登入。停用 = 帳號整組凍結
  hidden         boolean not null default false, -- 【審查修正】要不要出現在公開登入下拉選單，與 active 是獨立的軸
  purgeable      boolean not null default false, -- 【審查修正】能否被硬刪除，建立當下就固定，不由時間/登入紀錄推斷
  created_at     timestamptz not null default now()
);
```

**【審查修正】`active` 與 `hidden` 是兩個獨立的軸**：原設計只有 `active`，公開登入清單（4.7 節）過濾 `active=true`。但哨兵/測試部門需要「能登入（`active=true`）同時不出現在下拉選單（`hidden=true`）」——這兩個需求無法用單一旗標同時滿足：設 `active=true` 會讓所有正式部門使用者在登入頁看到「這個陌生部門是什麼」，設 `active=false` 又根本登不進去、驗證做不了。因此拆成兩個獨立欄位，`sentinel_pack` 的 `01_seed_sentinel.sql` 已經是這樣設計（`hidden=true`），本計畫的表結構需要跟上。

#### 1.2 `devices` 表加欄位【第十一輪：已用 `00_preflight_check.sql` 確認實際 schema，取代先前的假設性描述】

**問題**：1.3 節決定「`device_model` 允許跨部門重複」，但如果 `devices` 表現在的唯一約束就綁在型號欄位上，這個約束不會因為 `alarms` 表怎麼改就自動跟著變。第二個部門要建立同名機種時，會在 `INSERT INTO devices` 這一步就直接被唯一約束擋下，整個 1.3 節「允許跨部門同名」的設計會在最前面就卡死。

**【第十一輪確認的實際現況，取代原本的情況 A/B 二選一假設】** 執行 `00_preflight_check.sql`（`sentinel_pack` v3）後，`\d devices` 顯示：
```
Table "public.devices"
  Column  | Type | Nullable | Default
----------+------+----------+----------
 id       | text | not null |
 model    | text | not null |
 category | text | not null | ''::text
 line     | text | not null | ''::text
Indexes:
    "devices_pkey" PRIMARY KEY, btree (id)
    "devices_model_key" UNIQUE CONSTRAINT, btree (model)
```

實際情況是**混合型**，比原先預想的情況 A（純代理鍵）或情況 B（型號本身是約束對象）都窄：**主鍵是獨立代理鍵 `id`（例如 `M-201`），另外有一個獨立的 `UNIQUE (model)` 約束**——`id` 完全不受這次改動影響，只需要把 `model` 的唯一範圍從全域收窄成 `(department, model)`：

```sql
alter table devices add column department text references departments(id);
-- 遷移驗證無 NULL 後才執行：
-- alter table devices alter column department set not null;
create index idx_devices_department on devices(department);

-- 主鍵 id 不動；只需 drop 舊的全域唯一約束、換成複合唯一約束
alter table devices drop constraint devices_model_key;
alter table devices add constraint devices_dept_model_key unique (department, model);
```

**額外查證：`devices.id` 是否為型號本身（會影響新機種能否安全建立）**——已查明 `id` 為獨立代理鍵（如 `M-201`、`M-501`），與 `model` 完全脫鉤，14 筆現有資料印證無誤。這代表第二部門建立同名機種時，只要給一個新的 `id`（沿用現行流水號風格，或改用 UUID 皆可），`(department, model)` 複合唯一約束就能正常運作，**不需要另外設計新機種的 id 產生規則**——這件事原本被列為「必須先查清楚」的風險項，查證後確認不成立，故不再需要額外處理。

**【第十一輪：欄位命名不一致，採用「PLAN 改用實際欄位名」原則】** `devices` 表實際欄位是 `model`，不是 `device_model`（`alarms` 表則確實是 `device_model`，兩表命名不一致，非統一疏漏）。**決策：不改資料庫欄位名**——現行系統已上線在跑，為了命名整齊去 rename 正式表欄位，是在安全邊界工程之外額外引入一個對現行功能有實際風險的改動，不符合這次工程「只動安全邊界，不做順手清理」的原則（呼應 5.1 節「不順便改前端架構」的同一條理由）。因此：
- 本計畫所有涉及 `devices` 表的 SQL，一律使用 `model`（而非 `device_model`）
- API／路由層的 `device_model` 這個名字維持不變（它是應用層的邏輯命名，不代表 DB 欄位名必須一致），但需在 `storage.py` 有一個唯一的轉換點做正規化，避免 `model` 這個名字擴散到十幾個呼叫點（見 3.1 節新增的 `_row_to_device()` 說明）
- 若日後決定要讓兩表命名徹底一致（`ALTER TABLE devices RENAME COLUMN model TO device_model`），列為多部門穩定運作後的獨立清理工作，不在本次範圍內

**`devices.line` 欄位確認與 department 無關**——`00_preflight_check.sql` 順帶查出 `devices` 還有一個 `line`（產線）欄位，14 筆資料分成 `2.1`／`2.2` 兩組各 7 筆，一度懷疑這兩組線代表隱藏的跨部門混合資料。**已與使用者確認：`2.1`／`2.2` 只是同一個使用單位內的產線分類標記，不代表不同的登入/權限邊界**，不需要當作部門拆分的依據。`department` 是權限與資料隔離的邊界，`line` 是部門內部的產線分類，一個部門可能有多條線——兩者關係在此明確記錄，避免日後被誤認為同一件事。

#### 1.3 `alarms` 表【審查修正：推翻原「不加欄位」設計】

**問題**：原設計靠 `device_model` 反查 `devices.department` 判斷歸屬，前提是 `device_model` 全域唯一。但不同部門很可能買同供應商同型號設備（例如都叫 `PILM003`），一旦撞名，A 部門的警報會直接混進 B 部門查詢結果，且完全不會報錯——這是會讓整個隔離機制形同虛設的漏洞。

**修正**：`alarms` 表**直接加 `department` 欄位**，複合主鍵從 `(device_model, code)` 改成 `(department, device_model, code)`：
```sql
alter table alarms add column department text references departments(id);
-- 主鍵改動：先加新複合唯一索引，驗證資料填齊後才真正切換主鍵
create unique index idx_alarms_dept_model_code on alarms(department, device_model, code);
create index idx_alarms_department on alarms(department);
```
`device_model` 允許跨部門重複（例如 A 部門和 B 部門都有一台 `PILM003`，各自有各自的警報代碼表），查詢/CRUD 一律先以 `department` 過濾再比對 `device_model+code`。

**【審查修正】路由歧義**：`device_model` 允許跨部門重複後，原本的 `/api/alarms/<device_model>/<code>` 對總管來說指向不明——`PUT /api/alarms/PILM003/E001` 若兩個部門都有 `PILM003`，要改哪一筆？路由本身無法唯一定位。改為 `/api/alarms/<department>/<device_model>/<code>`：
- 一般帳號／部門管理員：path 裡的 `department` 必須與 `session["department"]` 相符，不符一律 404（不是 403，避免洩漏其他部門存在）
- 總管：path 裡的 `department` 就是明確指定的目標部門，直接採用

這與 4.1 節的 `scope_department()` 是**兩套不同語意**，不可混用：`scope_department()` 是**讀取過濾**用（回傳「要看哪些部門」），寫入需要的是**明確的目標部門**（從 URL path 或 request body 解析，不是從 session 推導）——4 節內文會分開說明，避免實作時把兩者當同一件事處理。

**【審查修正】主鍵切換需要獨立部署階段 + 重複值預檢**：`create unique index` 這個動作若庫裡已存在重複的 `(device_model, code)` 組合（可能來自現有 `alarms` 表主鍵其實是別的代理鍵、而非目前假設的 `(device_model, code)`），建立會直接失敗；就算建立當下成功，遷移腳本 1.7 節第 3 步 `UPDATE alarms SET department=...` 把 1759 筆全部設成同一部門後，仍可能違反這個新 unique index。**遷移腳本執行前**必須先跑：
```sql
select device_model, code, count(*) from alarms group by device_model, code having count(*) > 1;
```
確認回傳 0 筆，才能繼續。若有重複，代表現行 `alarms` 表的實際約束跟本計畫假設不符，須先查清楚現況再往下走，不可略過這一步直接硬套新約束。

主鍵真正切換（`alter table alarms drop constraint ... , add primary key (department, device_model, code)`）**不屬於「零行為變更」的第一階段**，必須在第 7 節部署順序裡獨立列一個階段，且要確認：
- 若有其他表以外鍵指向 `alarms` 舊主鍵，需要先 drop 該外鍵再重建（目前查無此類 FK，但執行前應重新確認）
- `upsert_one()`（3.1 節新增方法）的 `ON CONFLICT` 目標欄位取決於當時生效的主鍵/unique index，若在主鍵切換完成前就上線呼叫 `upsert_one()`，會打到錯誤的約束——**兩者上線順序必須是：先完成主鍵切換並驗證，才部署會呼叫 `upsert_one()` 的新版 `app.py`**

#### 1.4 其他表加欄位（全部先 nullable，遷移完成後視情況收緊）
```sql
alter table ai_scans       add column department text;
alter table ai_corrections add column department text;
alter table ai_logs        add column department text;
alter table alarm_history  add column department text;
alter table feedback       add column department text;
alter table alarm_views    add column department text;

create index idx_ai_scans_department on ai_scans(department);
create index idx_ai_corrections_department on ai_corrections(department);
create index idx_alarm_history_department on alarm_history(department);
create index idx_feedback_department on feedback(department);
create index idx_alarm_views_department on alarm_views(department);
```
【審查補強】這些欄位 Dashboard 統計查詢會頻繁用到，全部加 index，避免上線後查詢變慢才回頭補。

#### 1.5 NULL 語意【審查補強】
PostgREST 的 `department=eq.X` 不會匹配 NULL，任何回填失敗的資料會變成所有人（含總管）都查不到的孤兒資料。遷移腳本執行後必須明確驗證：
- `devices.department` 無 NULL → 驗證通過後加 `NOT NULL` 約束
- `alarms.department` 無 NULL → 同上
- `feedback`/`alarm_views` 舊資料回填不到的（`device_model` 對不到現存 `devices`），**明確決定**：保留 NULL、標記為「僅總管可見的歷史雜訊」，不強制指派、不刪除（這類資料量小、時效性低，不值得為此增加遷移複雜度）

#### 1.6 遷移前備份【第十五輪定案，取代原先簡化版本】

**背景**：原版本只寫「Dashboard 快照或 CSV 匯出擇一」，使用者認為這個決定超出自己能判斷的範圍，交給外部專家評估後給出更完整的六步驟流程，直接採納，不再是二選一。

**執行順序（動工前必須照序完成）**：

1. **查清 `alarms` 主鍵實際名稱**——不能只憑本計畫文件假設（`(device_model, code)`），要用 `\d alarms` 或 `information_schema` 實查目前正式庫上生效的主鍵/約束名稱，回滾腳本要 `ALTER TABLE ... DROP CONSTRAINT <名稱>` 精確對應這個真實名稱，寫錯名稱會讓回滾腳本直接失敗
2. **先寫好 `rollback_stage3.sql`**——在動手改動之前，先把「如果這步做錯了要如何退回」寫成可執行的腳本並放進版本控制，不是出事才臨時想。內容至少涵蓋：`devices`/`alarms` 新增欄位的 `DROP COLUMN`、新建索引的 `DROP INDEX`、1.3 節主鍵切換的還原（`DROP CONSTRAINT` 新主鍵 + 重建舊主鍵，用步驟 1 查到的真實名稱）
3. **`pg_dump -Fc` 完整備份，並驗證可讀**——邏輯備份，涵蓋整個資料庫而不只是 `alarms`/`devices` 兩張表（1.6 節原版只考慮這兩張表，遺漏了 `feedback`/`ai_scans`/`ai_logs` 等）。**此步驟已完全取代原本考慮過的「CSV 匯出」選項**——`pg_dump` 涵蓋所有表、格式更完整，不需要額外再匯一份 CSV：
   ```bash
   pg_dump "$SUPABASE_DB_URL" -Fc --no-owner --no-privileges \
     -f pre_migration_$(date +%Y%m%d_%H%M).dump

   pg_restore -l pre_migration_*.dump | head   # 確認讀得出來，非空、非損毀
   ```
4. **到 Supabase Dashboard 確認內建備份能力**——確認免費/付費方案的自動備份保留天數與還原操作方式，記錄下來備查
   **【第十六輪查證結果】免費方案完全沒有內建備份**——Dashboard「Scheduled backups」頁面明確顯示 "Free Plan does not include project backups. Upgrade to the Pro Plan for up to 7 days of scheduled backups."，Point in Time Restore、Restore to new project 這些功能同樣是付費方案才有。

   **【第十七輪校正：「兩層」的正確定義】** 上面第十六輪一度把這件事解讀成「兩層安全網少了一層」，這是誤解，已由專家釐清並修正。實際分層不是「`rollback_stage3.sql` vs `pg_dump`」，也不是「`pg_dump` vs Dashboard 備份」，而是：
   - **第一層**：`BEGIN`/`COMMIT` 交易包裹 ＋ `rollback_stage3.sql`——防「遷移失敗、方向錯了要退回」，**已就緒**
   - **第二層**：`pg_dump`（Dashboard 備份只是同一層的第二份拷貝，不是獨立的一層）——防「整個 Supabase 專案損毀」，**已就緒且已驗證**（415 TOC entries，`pg_restore -l` 確認可讀）

   Dashboard 備份的缺席，只是第二層少了一份備援副本，不是少了一整層防護。第二層本來就不是階段 1~3 這批改動實際會用到的東西（見下方「為什麼足夠」）。
5. **正向腳本全部包在 `BEGIN`/`COMMIT` 裡執行**——`migrate_add_departments.py`（1.7 節）與階段 3 的約束切換 SQL，凡是會修改資料/結構的敘述都包在單一交易內，任何一步失敗即整體 `ROLLBACK`，不會留下「改了一半」的中間態
6. **執行後跑一次 `00_preflight_check.sql`**——確認新狀態符合預期（無 NULL、無重複、約束正確生效），這一步是遷移完成的驗收動作，不是可略過的多餘檢查
7. **【第十七輪新增】階段 2 回填完成、驗證無 NULL 之後，再做一次 `pg_dump`**：
   ```bash
   pg_dump "$SUPABASE_DB_URL" -Fc --no-owner --no-privileges \
     -f post_backfill_$(date +%Y%m%d_%H%M).dump
   pg_restore -l post_backfill_*.dump | head
   ```
   **為什麼加在這裡而不是升級付費方案**：階段 1（加欄位）與階段 3（主鍵/約束切換）都是純 DDL，不動任何既有資料列，`rollback_stage3.sql` 兩秒鐘就能還原。唯一真正「寫入資料」的動作是階段 2 的回填（`UPDATE devices/alarms SET department = ...`）。萬一回填時部門對應填錯（例如日後多部門並存時 slug 弄混），`rollback_stage3.sql` 救不了——它只還原 schema，不還原填錯的值。回填後立刻補一次 `pg_dump`，就有「遷移前」「回填後」兩個乾淨的檢查點，之後階段 3 若出事，可以精準退回「回填後」那個點，不用重跑整個遷移。三十秒的事，比升級 Pro 方案划算。

**分層防護的正確理解（第十七輪校正，取代第十六輪的誤解）**：不是「兩層各防一半」，而是：
- **第一層**（`rollback_stage3.sql` ＋ 步驟 5 交易包裹）：防「這一步做錯了、方向不對」，日常操作出錯走這裡，**已就緒**
- **第二層**（步驟 3、7 的 `pg_dump`；Dashboard 備份只是這一層的備援副本，不是獨立第三層）：防「整個 Supabase 專案損毀」，**已就緒且已驗證**

階段 1~3 這批改動幾乎不涉及「資料遺失」風險（見上方步驟 7 說明），第一層就已經是主要防線；第二層是應對「跟這次遷移本身無關」的災難情境（專案被刪、被暫停等）。Dashboard 備份的缺席不影響這次遷移的安全性，六步驟＋新增的第 7 步已經足夠，**不需要為此升級付費方案**。

**三個附帶提醒（第十七輪，操作面）**：
- **`pg_dump` 檔案應搬到 Supabase 以外的地方存放**（外接硬碟或雲端硬碟），放在專案資料夾內（即使被 `.gitignore` 排除）仍是常見疏忽——本機硬碟壞掉會連同專案資料夾一起遺失
- **免費層專案閒置會被自動暫停**（歷來約一週無活動即觸發）。若 `cron-job.org` 只打 `/ping`（見階段 -0.5）而該端點不觸及資料庫查詢，Supabase 這邊仍可能被判定為閒置——日後若遇到「Render 服務正常但所有查詢都失敗」，第一件事是去 Supabase Dashboard 確認專案是否被暫停，而不是先懷疑程式碼
- 415 個 TOC 項目、8 張表的資料/約束/索引都涵蓋在 `pg_dump` 內——這個驗證方式本身是對的，多數人執行完 `pg_dump` 就直接收工、不做 `pg_restore -l` 這一步查驗，這次有做到位

#### 1.7 遷移腳本 `backend/migrate_add_departments.py`（手動執行一次）

**【審查修正】執行前必須先做重複值預檢**，否則後續步驟可能因違反 1.3 節新增的 unique index 而失敗：
```sql
select device_model, code, count(*) from alarms group by device_model, code having count(*) > 1;
```
確認回傳 0 筆才能繼續。若有重複，代表現行 `alarms` 表實際約束跟本計畫假設（主鍵是 `(device_model, code)`）不符，需先查清楚現況再往下走。

步驟：
1. 執行上述預檢，確認 0 筆重複
2. 用現有 `.env` 的 `LOGIN_PASSWORD`/`ADMIN_PASSWORD` 雜湊後寫入 `departments` 一筆預設部門，讓現有使用者密碼繼續有效（`hidden=false, purgeable=false`）
3. `UPDATE devices SET department = '<預設部門id>' WHERE department IS NULL`（全部 14 台機種）
4. `UPDATE alarms SET department = '<預設部門id>' WHERE department IS NULL`（全部 1759 筆警報）
5. `feedback`/`alarm_views` 歷史資料 best-effort 透過 `device_model → devices.department` 反查回填，查不到的留 NULL
6. 印出處理筆數與驗證結果（NULL 計數），可重複執行（upsert 語意，不會重複插入）

**注意**：`alarms` 表的主鍵真正切換（見 1.3 節）不在這支腳本裡執行，是第 7 節部署順序的獨立階段，順序在這支遷移腳本之後。

---

### ⬜ 2. 密碼與登入安全

- 用 `werkzeug.security`（Flask 已內建依賴）：`generate_password_hash`/`check_password_hash`
- `requirements.txt` 補一行明確依賴 `werkzeug>=3.0`
- 登入頁：部門下拉選單（選項來自 `/api/departments/public`，只回傳 `id`+`name`，且**只列出 `active=true` 且 `hidden=false` 的部門**，見 1.1 節）+ 密碼欄位
- 超級管理員密碼：`.env` 環境變數 `SUPERADMIN_PASSWORD`，**比對改用 `hmac.compare_digest()`** 而非直接 `==`，避免 timing attack 洩漏密碼長度/前綴資訊

#### 【第四輪審查修正：權限提升風險】登入路徑在入口就分岔，不做 fallthrough

**問題**：原設計「先比對 `SUPERADMIN_PASSWORD` → 符合則總管；否則依表單選的部門查 `admin_pw_hash`」，加上需求明確允許各部門密碼可重複——只要任何部門的管理員密碼剛好等於 `SUPERADMIN_PASSWORD`，該管理員登入就會直接取得總管權限。沒有機制阻止，也不會留下任何跡象，是一條無聲的權限提升路徑。

**修正**：表單的部門選擇欄位決定走哪一條路，兩條路互不回退：
- 選擇 `__super__`（系統管理員特殊選項）→ 只比對 `SUPERADMIN_PASSWORD`，失敗即失敗，不再往下試任何部門密碼。成功則 `superadmin=True, department=None`
- 選擇任何真實部門 id → 只比對該部門的 `admin_pw_hash`，失敗即失敗，完全不碰 `SUPERADMIN_PASSWORD`

這樣「部門密碼恰好等於超管密碼」不再具有任何意義——兩者永遠不會在同一次比對裡互相取代。

`__super__` 不出現在 `/api/departments/public` 的回應裡（比照 `hidden` 的考量），由 `admin-login.html` 前端寫死一個額外選項，或走獨立的未公開登入路徑。

**第二道防線**：`POST /api/admin/departments` 與 `reset-password` 時，若明文密碼等於 `SUPERADMIN_PASSWORD`，一律拒絕並回 400。在上面的分岔設計下並非必要，但成本極低，能防止日後有人把登入邏輯改回 fallthrough 時重新開洞。

```python
SUPER_DEPT_SENTINEL = "__super__"

def do_admin_login(form_department: str, password: str):
    if form_department == SUPER_DEPT_SENTINEL:
        if not hmac.compare_digest(password, os.environ["SUPERADMIN_PASSWORD"]):
            return None                      # 不 fallthrough 到部門密碼
        session.clear()
        session.update(auth=True, admin=True, superadmin=True, department=None)
        return "superadmin"

    dept = department_store.get_by_id(form_department)
    if dept is None or not dept["active"]:
        return None                          # 不 fallthrough 到超管密碼
    if not check_password_hash(dept["admin_pw_hash"], password):
        return None
    session.clear()
    session.update(auth=True, admin=True, superadmin=False,
                   department=dept["id"],
                   dept_session_version=dept["session_version"])
    return "admin"
```
`session.clear()` 是刻意的：避免舊 session 的殘留鍵（例如上一個帳號的 `superadmin=True`）被帶進新登入。

#### 2.1 【第四輪審查修正：一般化】session 有效性檢查——不只檢查密碼重設

**問題**：原設計只檢查 `session_version`，但實際有三種情況都該讓既有 session 立刻失效，原設計只涵蓋第一種：

| 情況 | 觸發操作 | 原設計是否涵蓋 |
|---|---|---|
| 密碼被重設 | `reset-password` | ✅ 已涵蓋 |
| 部門被停用 | `set_active(false)` | ❌ 舊 cookie 繼續有效，「停用」只對新登入生效 |
| 部門被硬刪除 | `purge()` | ❌ 讀取回空、寫入撞外鍵 500 |

第三種特別值得處理：`purge()` 之後 session 仍帶著舊 id，讀取回空看起來只是「沒資料」，但 `resolve_target_department()` 會回傳一個已不存在的 id，撞上 `departments(id)` 外鍵變成 500 而不是乾淨的 401。

**修正**：每次請求都要確認 session 對應的部門目前仍然可用。要檢查的是三件事的合取：**部門仍存在（未被 `purge()`）、`active=true`（未被停用）、`session_version` 與資料庫一致（密碼未被重設）**。任一不成立即視同未登入（401）。三者共用同一個快取條目，成本與原本只查 `session_version` 完全相同：

```python
import time
from flask import session, abort

_DEPT_CACHE: dict[str, tuple[dict | None, float]] = {}
_DEPT_CACHE_TTL = 60  # 秒

def _dept_cached(dept_id: str) -> dict | None:
    """回傳部門資料列；None 代表部門不存在（含已被 purge）。"""
    now = time.monotonic()
    hit = _DEPT_CACHE.get(dept_id)
    if hit is not None and now - hit[1] < _DEPT_CACHE_TTL:
        return hit[0]
    row = department_store.get_by_id(dept_id)
    _DEPT_CACHE[dept_id] = (row, now)   # None 也要快取，避免被不存在的 id 打穿
    return row

def assert_session_valid() -> None:
    """在 login_required / admin_required / superadmin_required 內部呼叫。"""
    if session.get("superadmin"):
        return                            # 超管不綁部門
    dept_id = session.get("department")
    if not dept_id:
        abort(401)                        # 改造前簽發的舊 session
    dept = _dept_cached(dept_id)
    if dept is None:
        abort(401)                        # 部門已被 purge
    if not dept["active"]:
        abort(401)                        # 部門已被停用
    if session.get("dept_session_version") != dept["session_version"]:
        abort(401)                        # 密碼已被重設
```

**快取失效**：`set_active()`、`update_password()`、`purge()` 成功後，於同一 process 內主動 `_DEPT_CACHE.pop(dept_id, None)`，讓操作者自己那個 worker 立即生效。其他 worker 仍受 TTL 限制。

**明確的時效承諾**：停用帳號、重設密碼、刪除部門，最長 60 秒後全面生效。這是刻意的取捨。若日後出現需要即時生效的場景（例如資安事件），應對方式是輪替 `FLASK_SECRET_KEY` 一次清空所有 session，而不是把 TTL 調到 0。

**`None` 也要快取**：否則帶著不存在部門 id 的請求每次都會實際查一次 Supabase，變成免費的放大攻擊面。

#### 2.2 【審查補強：修正】登入防爆破，改漸進延遲取代硬鎖（且不佔用伺服器資源）
**問題**：工廠環境所有人共用同一個 NAT 出口 IP，加上部門密碼是共用的——原設計「同一 IP+部門短時間失敗超過 N 次就鎖定」在這個場景下，只要有人不小心連續打錯密碼，會讓**整個部門的所有人**同時被鎖住，且這個機制反而可能被拿來當阻斷手段（故意連續打錯就能讓一整條產線登不進去）。

**修正**：改用**漸進式延遲**而非硬鎖——第 N 次失敗後，該 IP+部門組合再次嘗試登入需要等待 `2^N` 秒（上限例如 60 秒封頂，不無限增長），成功登入後計數歸零。失敗紀錄照樣寫入（`alarm_history` 或新開 `login_attempts` 表），供總管查核異常嘗試模式。真正的硬鎖只保留給「單一 IP 極高頻請求」這種明顯自動化攻擊樣態（例如每秒數十次），用另一層更粗的節流規則處理，不與正常人為誤觸的節流邏輯混在一起。

**【審查修正】延遲不能用 `sleep()` 在請求處理中實作**——若在 Flask view function 裡真的 `time.sleep(60)`，會整整佔住一個 worker process/thread 60 秒不釋放。Render 上可用的 worker 數量有限，攻擊者只要開幾十個並行連線去觸發這個延遲，就能把所有 worker 佔滿、讓正常使用者完全連不上——這比原本要防的「硬鎖单一部門」更嚴重，等於自己做了一個更好用的阻斷服務工具。

正確做法：**伺服器不等待，直接回應**。還在延遲窗口內的請求，伺服器立即回 `429 Too Many Requests`，並帶上標準的 `Retry-After: <秒數>` 標頭告知客戶端該等多久再重試。等待這件事完全交給客戶端（登入頁的前端邏輯讀取 `Retry-After` 值，倒數計時後才允許使用者再次送出表單），伺服器端的請求處理本身是即時返回、不佔用資源的，不管失敗次數再多，都不會有 worker 被卡住。

#### 2.2.1 【第四輪審查補強】失敗狀態的儲存與清理

**問題**：2.2 定義了 `2^N` 退避與 429 回應，但沒有定義 N 存在哪裡——這使該節無法直接動工。

**決策：以資料庫為唯一真實來源，不使用行程內狀態。** 登入是低頻操作，多一次往返完全可接受；換來的是多 worker、重啟、擴容之下行為一致——用行程內 `dict` 的話，退避次數會被 worker 數量除掉，防護強度取決於部署規模，這不是可接受的設計。

```sql
create table login_attempts (
  id           bigserial primary key,
  ip           text not null,
  department   text,          -- 刻意不加 FK：要能記錄「嘗試登入不存在的部門」
                               -- 【第六輪審查補強】department 對不到 departments 表是刻意設計，不是資料品質問題——
                               -- 代表「有人嘗試登入不存在的部門」，是需要關注的訊號。日後任何清理腳本都不得
                               -- 把這些列當髒資料刪除，那是唯一能看出有人在探測部門 id 的證據。
  success      boolean not null,
  attempted_at timestamptz not null default now()
);

create index idx_login_attempts_lookup
  on login_attempts (ip, department, attempted_at desc);
```

**N 的定義**：該 `(ip, department)` 組合在最近一次成功登入之後的連續失敗次數，且只計算 15 分鐘窗口內。成功登入不需另外清除計數——這個定義本身就讓計數自然歸零，同時保留完整稽核軌跡：

```sql
select count(*) from login_attempts
where ip = :ip and department = :dept
  and attempted_at > now() - interval '15 minutes'
  and attempted_at > coalesce(
        (select max(attempted_at) from login_attempts
         where ip = :ip and department = :dept and success),
        '-infinity'::timestamptz);
```

`delay = min(2 ** N, 60)` 秒。仍在窗口內的請求立即回 `429` + `Retry-After: <剩餘秒數>`，伺服器不等待（見上方 2.2 主文）。

**清理策略**：併入現有的 `POST /api/admin/cleanup-expired`（已是全庫清理語意），新增 `delete from login_attempts where attempted_at < now() - interval '90 days'`。90 天讓稽核有足夠回溯範圍；每列極小，即使每天數千次登入，一年也只有數 MB。

**不做的事**：不在這張表上做即時的分散式鎖或計數快取。若日後登入量成長到讓這個查詢變成瓶頸，正確解法是在更前面一層（反向代理／Cloudflare）做粗粒度 IP 節流，而不是把狀態搬回行程內。

#### 2.2.2 【第六輪審查補強】部門枚舉防護，跟節流是兩件不同的事

**問題**：「部門不存在時節流要不要生效」看起來像節流的問題，實際上要先分開兩件事：**枚舉防護的真正防線是「回應必須完全一致」，不是節流。** 只要「部門不存在」和「密碼錯誤」回傳相同的狀態碼、相同的訊息、相近的耗時，攻擊者打幾次都問不出「這個部門 id 存不存在」——節流只是讓他慢一點問到同樣的「什麼都問不出來」，不能取代這一步。

**修正：不存在的部門要消耗與真實密碼比對相同的時間**，用一個模組載入時就算好的 dummy hash 墊底：
```python
import re
from werkzeug.security import generate_password_hash, check_password_hash

DEPT_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_DUMMY_HASH = generate_password_hash("__never_matches__")   # 模組載入時算一次

def do_admin_login(form_department: str, password: str):
    ...
    dept = department_store.get_by_id(form_department)
    if dept is None or not dept["active"]:
        check_password_hash(_DUMMY_HASH, password)   # 消耗與真實比對相同的時間
        return None                                   # 與密碼錯誤完全相同的回應
```
沒有這個 dummy hash，「部門不存在」會快很多（省掉一次 bcrypt/scrypt 雜湊比對），時間差足以區分——這跟 `SUPERADMIN_PASSWORD` 已採用 `hmac.compare_digest` 是同一類考量（見第 2 節）。

#### 2.2.3 【第六輪審查修正】細網會被「換部門」繞過，需要加一層只看 IP 的粗網

**問題**：2.2.1 節的 SQL 用 `where department = :dept`，`:dept` 是亂打的字串時不會出錯，只會回傳 0——這正是漏洞所在。攻擊者每次換一個新的假部門 id，`N` 永遠是 0，細網完全失效，等於節流形同虛設。

**修正**：加一層只看 IP 的粗網，取兩者最大值。**部門可以亂換，IP 不行**：
```sql
-- 細網：N_ip_dept（2.2.1 既有邏輯不變）

-- 粗網：N_ip（不看 department，攻擊者無法用換部門重置）
select count(*) from login_attempts
where ip = :ip
  and attempted_at > now() - interval '15 minutes'
  and attempted_at > coalesce(
        (select max(attempted_at) from login_attempts
         where ip = :ip and success),
        '-infinity'::timestamptz);
```
```python
delay_fine   = 2 ** N_ip_dept                    if N_ip_dept >= 1 else 0
delay_coarse = 2 ** (N_ip - 19)                  if N_ip >= 20    else 0
delay        = min(max(delay_fine, delay_coarse), 60)
```
粗網門檻設在 20 次，是為了不誤傷工廠共用出口 IP。

**為什麼共用 NAT 不會被粗網誤傷**：這點容易看起來像重蹈第三輪「硬鎖會鎖死整部門」的覆轍，實際上不會，關鍵在 `N` 的定義——**`N` 是最近一次成功登入之後的連續失敗次數**。工廠一整條產線共用出口 IP，代表成功登入非常頻繁——只要有任何一個人登入成功，`N_ip` 就歸零。正常營運下 `N_ip` 幾乎不可能累積到 20。反過來，一個什麼都猜不中的攻擊者沒有任何成功事件可以幫他歸零，計數會一路累積。**共用 IP 這件事在這個設計裡從缺點變成優點：使用者密度越高，正常流量越不可能觸發粗網。** 再加上它是延遲不是硬鎖、上限 60 秒、且用 429 + `Retry-After` 回應不佔 worker（見 2.2 主文），即使真的誤觸，代價也只是等一分鐘。

#### 2.2.4 【第六輪審查補強】寫入 `login_attempts` 前要先擋垃圾，三段式判斷

要記錄，但先擋掉垃圾，避免表本身被拿來當放大攻擊的目標：

1. **格式不合法**（不符 `DEPT_ID_RE`，即 `^[a-z0-9_]{1,32}$`）→ 直接回與密碼錯誤相同的 401，**不查 DB、不寫入 `login_attempts`**。這防止攻擊者用超長字串或無限多的相異值把表撐大。量的防護交給更前面一層（與 2.2.1 已寫的「粗粒度 IP 節流屬於反向代理」一致）
2. **格式合法但部門不存在** → 照常寫入一筆 `success=false`。這是 2.2.1 節「不加 FK」的用意所在：這種嘗試本身就是有價值的稽核訊號（有人在猜部門 id），且**必須計入 `N_ip`**，否則粗網就漏了這一類攻擊模式
3. **已經處於節流窗口內的請求** → 回 429，**不再寫入新列**。否則攻擊者能靠持續請求無限增加寫入量，讓 `login_attempts` 本身變成放大攻擊的目標。計數只在「實際做了一次密碼比對」時才增加

---

### ⬜ 3. `storage.py` 改動

**設計決策**：`department` 當作 `load()`/`save()` 的參數傳入，不綁進 singleton 實例（避免多執行緒下 instance 狀態互相污染）。

#### 3.1 `SupabaseStore.save()` 的刪除邏輯地雷（原審查發現，維持修正）
現有 `save()` 的「刪除不在新清單裡的舊資料」邏輯，是對整張表做 GET 再比對差異。若只在 `load()` 加部門過濾、`save()` 沒同步處理，會導致存 A 部門資料時把 B 部門資料誤刪。

修正做法：
- `devices_store`（有 `department` 欄位）：`save()` 的刪除掃描比對加上 `department=eq.<dept>` 過濾
- `alarms_store`（現在也有 `department` 欄位，見 1.3）：**兩種情境都要處理**——單筆 CRUD（`create_alarm`/`update_alarm`/`delete_alarm`）改用新增的 `upsert_one()`/`delete_one()` 方法，繞開整批比對；批次匯入（第 6 節新工具）走 `save()` 時，刪除掃描比對加上 `department=eq.<dept>` 過濾，比照 `devices_store`

**【第四輪審查補強】`upsert_one()` 必須明確指定衝突目標**：規劃已正確要求「主鍵切換完成後才部署會呼叫 `upsert_one()` 的 `app.py`」（見 1.3 節），但少了一個 Supabase 特有細節——透過 PostgREST 做 upsert 時，衝突目標**預設取主鍵，不會自動使用新建的 unique index**。在「unique index 已建立、主鍵尚未切換」這個中間狀態下，upsert 會安靜地打到舊主鍵，行為與預期不符且不會報錯。

因此一律在 URL 明確帶上衝突目標：
```
POST /rest/v1/alarms?on_conflict=department,device_model,code
Prefer: resolution=merge-duplicates
```
`devices` 表【第十一輪修正：欄位實際叫 `model`，見 1.2 節命名不一致決策】：`?on_conflict=department,model`

若改走直連 Postgres 下原生 SQL，一樣寫死 `ON CONFLICT (department, device_model, code)`（`alarms`）／`ON CONFLICT (department, model)`（`devices`）欄位組合，不使用 `ON CONFLICT ON CONSTRAINT <名稱>`（約束名稱會在主鍵切換時改變）。

#### 3.1.1 【第十九輪定案，取代第十一輪的過渡期構想】`devices` 讀取正規化：`_row_to_device()` 永久雙 key，不收斂前端

**問題**：1.2 節決定 DB 欄位維持 `model`（不改資料庫），但 API／路由層對外一律使用 `device_model`（見 4.4 節路由設計）。若沒有一個集中的轉換點，`model` 這個名字會散落到 `app.py`、前端、`sentinel_pack` 驗證腳本等十幾個地方——一旦散布開，日後想統一命名就會變成到處要改的麻煩事，而不是改一行。

**【第十九輪實測與定案】** 第十一輪原本把「兩個 key 都給」寫成過渡期措施，暗示之後要收斂成只給 `device_model`。第十九輪實際掃描前端後推翻這個預設：`frontend/index.html` 40 處、`dashboard.html` 36 處讀取 `model`／`device_model` 相關字串，且 `.model`（機種物件的欄位）與 `.device_model`（警報物件的欄位）混雜出現，無法整批替換，必須逐處判斷物件類型。**改為永久雙 key 設計，不是過渡期，前端 76 處全部不動**：

```python
def _row_to_device(row: dict) -> dict:
    """devices 表的欄位叫 model，但 API 對外同時提供兩個 key。

    這不是過渡措施，是最終設計：
    - `model`        既有前端使用中（index.html 40 處、dashboard.html 36 處）
    - `device_model` 與 alarms 表的欄位名一致，新程式碼一律使用這個

    兩者永遠指向同一個值，在此處統一產生，不會有不同步的可能。
    """
    model = row["model"]
    return {
        "id":           row["id"],
        "model":        model,          # 既有前端讀這個
        "device_model": model,          # 新程式碼讀這個
        "category":     row["category"],
        "line":         row["line"],
        "department":   row["department"],
    }
```

**為什麼不收斂前端（三個理由，第十九輪定案依據）**：
1. **改動時機最不該加碼**——這 76 處若要改會落在階段 13（前端改動），該階段已經很滿（`api.js`、401 全域攔截、429 倒數、部門切換器、部門管理頁籤、`sw.js` 三件事）。在動安全邊界的同一個維護窗口裡再塞一個需要逐處分辨物件類型的改動，是不必要放大風險
2. **錯誤是沉默的**——`.model`／`.device_model` 混在同一批結果裡，判斷錯了的症狀是畫面顯示空白或 `undefined`，不會拋錯、不會被測試抓到、不會出現在 log 裡。76 次逐一判斷，錯一次就是一個難以發現的 bug
3. **收益是零**——這個改動不修 bug、不加功能、不改善安全性，純粹是讓兩個名字看起來一致。與先前「不改 `devices.model` 資料庫欄位名」（見 1.2 節）是同一條原則的延伸：不為了外觀一致，在安全邊界工程的同一個窗口裡引入零收益、有風險的改動

**三條配套規則（第十九輪新增，實作階段 5 必須遵守）**：
1. **轉換只能在 `_row_to_device()` 這一個函式發生**——`app.py`、路由層、前端、驗證腳本全部只看得到轉換後的結果，只有 `storage.py` 這一處知道實際欄位叫 `model`。若讓 `row["model"]` 直接漏到 `app.py`，命名不一致會擴散到十幾個地方，之後真的收不回來
2. **寫入路徑對稱處理**——`POST`/`PUT /api/devices` 的 body 兩個 key（`model`、`device_model`）都要接受，寫入時一律轉成 `model` 欄位名寫入 DB，避免「讀得到但寫不進去」這種難查的不對稱
3. **新程式碼一律用 `device_model`**——接下來要寫的部門切換器、部門管理頁籤、`frontend/js/api.js`，全部採用 `device_model`。既有 76 處維持不動，讓不一致的範圍隨新舊程式碼交替自然縮小，不必靠一次性大改

**未來若真的要收斂（非本次範圍，記錄供日後參考）**：需單獨排一次、不與任何其他改動綁在一起，時機是多部門完全穩定運作之後、且前端已抽出 `js/api.js`（屆時部分讀取可能已走統一介面，實際要改的處數會少於 76）。屆時收斂動作的後端部分就是把 `_row_to_device()` 裡 `"model": model` 那一行刪掉——這正是「轉換只能在一個函式發生」這條規則的價值所在。

#### 3.2 【第四輪審查修正】`JsonStore` 維持單租戶，開發環境另闢，不讓 JsonStore 承擔多部門角色

`load()`/`save()` 加 `department=None` 參數但忽略、行為不變，維持單租戶。它的唯一職責是服務 pytest 的 `ALARM_DATA_DIR` 隔離機制，**不承擔開發環境的角色**。

**問題**：曾考慮讓 `JsonStore` 也支援多部門過濾，方便本機開發時測試。但這會製造一條幾乎不會被執行到的第二實作路徑。日常開發用它、驗證用 Supabase，兩邊行為遲早分岔，而分岔方向多半是「本機測起來沒事、上線才出問題」——剛好是這整份規劃最想避免的失效模式。

更關鍵的是：這套隔離真正的風險點——PostgREST 的 `eq` 過濾語意、NULL 不匹配、`save()` 的整表刪除掃描、`on_conflict` 目標——**在 `JsonStore` 裡根本不存在**。在 `JsonStore` 上把多部門測通，對真正的風險沒有任何保證。

**修正**：需要在本機開發或除錯多部門功能時，**指向另一個 Supabase 專案**（免費層即可）：schema 與正式環境相同，資料可隨意破壞。理由：多部門隔離真正的風險點在 `JsonStore` 裡不存在，讓它支援多部門只會產生一條與正式環境行為不同、且幾乎不會被驗證的第二實作路徑。本機跑的應該就是正式那條 code path。開發用專案的連線資訊放 `.env.local`，並加入 `.gitignore`。

#### 3.3 新增 `DepartmentStore` 類別
```python
class DepartmentStore:
    def list(self, active_only: bool = False) -> list
        # 【第四輪審查修正】回傳 id/name/active/hidden/purgeable/created_at，絕不回傳密碼雜湊
    def get_by_id(self, dept_id) -> dict | None
        # 【第四輪審查修正】回傳需含 session_version、active（2.1 節的 assert_session_valid() 依賴這兩個欄位）
    def check_login(self, dept_id, password, admin=False) -> bool
    def create(self, id, name, pw_hash, admin_pw_hash) -> dict
    def update_name(self, id, name) -> None
    def update_password(self, id, pw_hash=None, admin_pw_hash=None) -> None
        # 內部同時執行 session_version += 1
    def set_active(self, id, active: bool) -> None
        # 軟停用/復原，取代硬刪除
    def purge(self, dept_id: str, confirm_id: str) -> dict
        # 硬刪除，見 3.4 的保護機制
```
本機/測試模式（`_use_supabase()` 為 False）下，登入 fallback 回現有 `.env` 明文比對，`session["department"]` 固定填 `"local"`。

#### 3.4 【審查補強】部門刪除路徑

一般部門不提供真正的 `DELETE`，只提供 `set_active(id, False)` 軟停用——硬刪除會留下大量孤兒資料（該部門的機種、警報、掃描歷史全部斷鏈），且工廠場景幾乎不會真的「刪除一個部門」，停用遠比刪除安全。

**例外：測試/哨兵部門的硬刪除。【審查修正：原保護條件會自我否定】** 原設計「拒絕對已登入過、或建立時間超過門檻的部門執行」——但測試部門在驗證過程中**必然**會被登入（否則驗證不了什麼），而清除時機通常在真實部門驗證通過之後，早已超過任何合理的時間門檻。用「時間」或「登入紀錄」當判斷依據，會導致真正要清的時候反而清不掉，條件自我矛盾。

**修正為確定性保護**：`departments.purgeable` 欄位在**建立當下就固定**，不由時間或登入紀錄推斷——只有明確建立測試/哨兵部門時才設 `purgeable=true`，正式部門一律 `purgeable=false` 且沒有任何後續操作能把它改成 `true`（`update` 系列方法不開放修改這個欄位）。`purge()` 拒絕任何 `purgeable=false` 的部門，並要求呼叫端在 request body 裡明確重打一次目標部門 id 當二次確認（`confirm_id` 參數需與 `dept_id` 完全相符，防止誤觸/複製貼上錯誤）：
```python
def purge(self, dept_id: str, confirm_id: str) -> dict:
    """硬刪除一個部門與其所有關聯資料。僅限 purgeable=true 的部門，正式部門一律用 set_active()。"""
    dept = self.get_by_id(dept_id)
    if dept is None or not dept["purgeable"]:
        raise PermissionError("此部門不可硬刪除")
    if confirm_id != dept_id:
        raise ValueError("二次確認的部門 id 不相符")
    # 依序刪除：alarms WHERE department=X → ai_scans/ai_corrections/ai_logs WHERE department=X
    # → feedback/alarm_views WHERE department=X → alarm_history WHERE department=X
    # → devices WHERE department=X → departments WHERE id=X
    # 回傳各表刪除筆數，供操作紀錄與人工核對
```
執行時機：見第 7 節「purge 時機的判斷」——不預設固定在某一步，依當下是否還需要拿這個部門做回歸驗證而定。清除前建議先手動確認一次 `GET /api/admin/departments` 列表與各表筆數，避免清錯對象。

#### 3.5 【審查補強】AI 記憶跨部門污染
`ai_memory.py` 的「拍照歷史分級保留」機制，讀取歷史紀錄/候選代碼清單時**必須限縮在呼叫者的部門範圍內**，不只是寫入時記錄部門而已——否則 A 部門的辨識歷史會混進 B 部門的候選建議清單，同時是正確性問題（誤判機率上升）和資料洩漏問題（B 部門操作員能從候選清單看到 A 部門的代碼）。

具體修改：`ai_memory.py` 所有查詢函式（讀取歷史記錄、產生候選建議）新增 `department` 參數並在查詢條件中帶入，不可省略；`ai_pipeline.py` 呼叫時必須把 `session["department"]` 一路往下傳，不能中途遺漏。

#### 3.6 【第四輪審查新增】所有部門相關的參數一律必填，不給預設值

**問題**：原本靠「code review 時 grep 一次確認每個呼叫點有沒有帶 `department`」。但這與 4.8 已用過的解法不一致——4.8 明確寫了「漏改的呼叫點應該直接 `TypeError`，而不是靜默略過過濾」。grep 是一次性的，且要求日後每個新增寫入點的人都知道有這條規矩，屬於容易隨時間流失的人為紀律，不是結構性保證。

**修正**：`department` 在下列所有方法上都是**沒有預設值的參數**：

| 類別 | 方法 |
|---|---|
| `AuditLogger` | `log()` 及所有內部寫入點 |
| `FeedbackStore` | `add()`、`stats()` |
| `ViewStore` | `record()`、`stats()`、`top()` |
| `AiScanStore` | `add()`、`recent()`、`ranking()`、`stats()` |
| `SupabaseStore` | `load()`、`save()`、`upsert_one()`、`delete_one()` |
| `ai_memory` | 所有查詢與寫入函式 |

讀取方法（`stats()`、`ranking()` 等）接受的是 `scope_department()` 回傳的 `(DeptScope, id)` 元組，而不是裸字串——這樣「總管不過濾」也是必須明確傳入的值，不存在「沒傳就不過濾」的隱性分支。

**過渡**：4.8 已定義的遷移期（第 7 節第 4 階段）暫時保留預設值 `None`。第 5 階段 `app.py` 上線後，**同一次 commit 內移除所有預設值**。這件事不能延後——只要預設值還在，就存在靜默略過過濾的可能。移除後，任何漏改的呼叫點會在 pytest 或啟動階段直接 `TypeError`。

#### 3.7 【第四輪審查補強】`DeptScope.ALL` 的實作約束，防止 NULL 語意被無聲收窄

**問題**：1.5 節定義的「`feedback`/`alarm_views` 舊資料回填不到的保留 NULL、僅總管可見」，這個約定目前只存在於文件裡。最可能的劣化路徑是日後有人在 query builder 加一個看起來很合理的「防呆過濾」（例如 `.not.is.null`），把孤兒資料從總管視野裡也一起藏掉——而且不會有任何測試失敗，因為表面行為看起來完全正常（只是「總管少看到幾筆奇怪的舊資料」，不會被當成 bug）。

**修正**：當 `scope_department()` 回傳 `DeptScope.ALL`（總管），storage 層**不得加上任何 `department` 相關的查詢條件**，包含看似無害的 `.not.is.null`。query builder 相關程式碼加註解說明此約束，並在哨兵資料中放入 `department IS NULL` 的孤兒列，讓這個行為變成可驗證的斷言，而不是只靠註解防守（見第 8 節哨兵資料驗證項目 T-14）。

**1.5 節補充**：遷移完成後，`feedback`/`alarm_views` 的 `department` **不加 `NOT NULL` 約束**（與 `devices`/`alarms` 不同），因為 NULL 在這兩張表是有意義的值：代表「無法歸屬的歷史資料」。

---

### ⬜ 4. `app.py` 改動

#### 4.1 【審查修正：原設計 fail-open】`scope_department()` 改為顯式型別

**問題**：原設計用 `None` 同時代表「總管，不過濾」和「異常/取不到部門」，這是危險的 fail-open：
- 遷移前就登入、session 裡沒有 `department` 的既有使用者（cookie 還有效）
- 生產環境 Supabase 環境變數漏設，`_use_supabase()` 回 `False` 悄悄降級成 JsonStore 單租戶
- 部門被停用或 session 版本不符後，`session["department"]` 仍帶著舊值

以上任何一種情況，若「取不到部門」也回傳 `None`，就會被誤判成「總管，看全部」，資料外洩。

**修正**：
```python
from enum import Enum

class DeptScope(Enum):
    ALL = "all"       # 僅總管
    DEPT = "dept"      # 一般/部門管理員，附帶 department id

def scope_department() -> tuple[DeptScope, Optional[str]]:
    if is_superadmin():
        return (DeptScope.ALL, None)
    dept = session.get("department")
    if not dept:
        abort(401, "登入狀態異常，請重新登入")  # 顯式失敗，不悄悄放行
    return (DeptScope.DEPT, dept)
```
所有呼叫端必須對 `scope_department()` 的回傳值做窮舉處理（`DeptScope.ALL` → 不加過濾條件；`DeptScope.DEPT` → 加 `department=eq.<id>`），不存在「都不是就當作看全部」的隱性分支。

**【審查修正】讀取過濾 vs 寫入目標部門是兩套語意，不可混用**：`scope_department()` 只回答「這次請求**可以看到**哪些部門的資料」，用於 `GET` 系列端點加過濾條件。但寫入端點（`POST`/`PUT`/`DELETE`）需要的是**明確的目標部門**（要寫進哪個部門），這不能從 `scope_department()` 推導。

**【第六輪審查修正：不接受第三個來源，改成寫入永遠只有一個來源】** 原設計讓 `resolve_target_department()` 的 `requested` 可以來自「路徑參數或 body」，但集合端點（例如 `POST /api/devices`）原本沒有路徑部門段，導致超管的目標部門「該從哪裡填」變成一個沒被定義清楚的銜接點，容易讓實作時兩邊各自猜一種做法。

**修正**：**所有寫入端點的路徑都帶部門段**，不再有「有些端點路徑帶部門、有些不帶、要 fallback 到 body」的分裂情況：
```
POST   /api/devices/<department>
GET/PUT/DELETE /api/devices/<department>/<device_model>

POST   /api/alarms/<department>
GET/PUT/DELETE /api/alarms/<department>/<device_model>/<code>
```
**【第十九輪補充】路由參數名 `<device_model>` 維持不變，即使 `devices.model` 才是 DB 實際欄位名**——這個參數名是 API 層的邏輯命名，本來就不要求跟 DB 欄位名一致（`alarms` 表本身的欄位就叫 `device_model`），與 3.1.1 節的 `_row_to_device()` 雙 key 決策是同一條原則：只在 `storage.py` 一處做轉換，路由/前端層維持既有命名不變。8.1 節的 `ROUTE_AUTH_REGISTRY` 白名單同樣不用因此調整（路由字串本身沒變）。

這樣 `resolve_target_department()` 的 `requested` 永遠來自路徑參數，沒有第二個來源，也就沒有「該從哪裡填」的問題。`?dept=` 從此純粹是讀取過濾的概念，跟寫入完全無關——三個函式的職責因此徹底分開：

| 函式 | 讀哪裡 | 用在哪 |
|---|---|---|
| `assert_session_valid()` | session | 每個請求的前置檢查 |
| `scope_department()` | session ＋（超管時）`?dept=` | `GET` 的過濾範圍 |
| `resolve_target_department()` | 只有 URL path | `POST`/`PUT`/`DELETE` 的寫入目標 |

```python
def scope_department() -> tuple[DeptScope, Optional[str]]:
    ...  # 讀取過濾用，如上（5.2.1 節，超管讀 ?dept=）

def resolve_target_department(path_department: str) -> str:
    """寫入端點用：決定這次寫入要落在哪個部門。path_department 只來自 URL path，
    不讀 request.args、不讀 body 的 department 欄位（見下方三條配套規則）。"""
    if is_superadmin():
        return path_department   # 超管：path 段就是明確指定的目標，直接採用
    dept = session.get("department")
    if not dept:
        abort(401, "登入狀態異常，請重新登入")
    if path_department != dept:
        abort(404)  # 不透露「這個部門存在但你無權寫入」，一律裝作路徑不存在
    return dept
```

**三條配套規則**：
- **(a) `resolve_target_department()` 禁止讀 `request.args`**——這是最容易在重構時被弄壞的地方，若有人為了「方便」讓它 fallback 到 `?dept=`，超管的讀寫來源就再度分岔。函式內寫死註解禁止，並在 `tests/test_route_auth_registry.py` 旁邊加一條原始碼檢查（`inspect.getsource` 斷言函式體不含 `request.args`），粗暴但有效
- **(b) body 若也帶了 `department` 且與 path 不符 → 400，不做優先序判斷**——「path 優先」或「body 優先」都會讓錯誤靜默通過；直接拒絕才會讓前端的 bug 當場現形
- **(c) 沒有路徑部門段的端點，一律只認 session，超管操作一律 400**——`POST /api/feedback`、`/api/view`、`/api/analyze`、`/api/confirm`、`/api/correct` 都是部門操作員的動作，超管本來就不該執行這些寫入。回 400「請以部門帳號操作」比讓它去猜目標部門乾淨，也順便讓 `confirmed_by`（見 5.2.2）不會出現 `None/...` 這種殘留可能

**前端契約**：超管在 `?dept=line21` 模式下操作時，前端組寫入 URL 一律把 `line21` 放進 path（`js/api.js` 裡統一處理，見 5.1）：
```js
// 讀取：dept 進 query
AlarmApi.get(`/api/alarms?${qs({dept: viewing})}`)

// 寫入：dept 進 path
AlarmApi.post(`/api/devices/${viewing}`, {device_model: 'PILM003'})
```
一般帳號的 `viewing` 就是自己的部門（從 `whoami` 拿），前端邏輯對兩種身分是同一份，不需要 if-else 分支。

**UI 連帶決定**：超管選了 `?dept=__all__`（見 5.2.1）時沒有目標部門，「新增機種／新增警報」按鈕必須停用並提示「請先選擇部門」——這不是額外限制，是把上面「總管寫入必須明確指定目標部門」這條規則在 UI 上顯性化，否則使用者要按下按鈕才拿到 400，體驗差且看起來像 bug。

#### 4.2 【審查補強】啟動時 fail fast
`create_app()` 啟動階段新增檢查：若環境變數標示這是生產環境（例如 `FLASK_ENV=production` 或偵測到 Render 的環境變數）但 `_use_supabase()` 回 `False`，直接拋例外中止啟動，不允許悄悄降級成 JsonStore 單租戶模式在生產環境跑。

#### 4.3 【審查補強】部署時清空既有 session
多部門改造上線當下，**更換 `FLASK_SECRET_KEY`**，讓所有舊 cookie 一次性失效，避免任何人帶著改造前簽發的 session 跨過這個邊界（此時 session 裡沒有 `department` 欄位，若沒清空、又搭配 4.1 的顯式失敗，行為上會是全部被踢出重新登入，這是預期且正確的）。

**【審查修正】`FLASK_SECRET_KEY` 部署後必須是固定的環境變數**，不可讓應用程式啟動時隨機產生——若隨機產生，每次 Render 重啟或擴容都會讓所有使用者被登出，這在正式環境是不能接受的干擾。多部門上線那一次性換新之後，往後就固定寫在 Render 的環境變數設定裡，不再變動（除非有明確理由要強制全體重新登入）。

#### 4.4 各端點改動摘要

**【第七輪審查修正：關鍵結構問題】權限層級是按 HTTP method 分的，不是按路徑分的——白名單與裝飾器設計都要跟著改**

**問題**：`GET/PUT/DELETE /api/alarms/<department>/<device_model>/<code>` 這條路徑上，`GET` 是前台查詳情（應為 `login` 層級），`PUT`/`DELETE` 是後台警報管理（應為 `admin` 層級）——三種方法權限不同，但如果用 `@app.route(..., methods=["GET","PUT","DELETE"])` 一個函式處理三種方法，Flask 的 `_auth_level` 標記掛在 view function 上，**物理上無法讓同一個函式對不同 method 回傳不同標記**。反過來若整條路徑掛 `admin_required`，前台就查不了警報詳情。這不是排版問題，是白名單資料結構在一開始就選錯了維度（路徑字串 vs 路徑+方法的組合）。`/api/devices/<department>/<device_model>` 是完全一樣的情況。

**修正**：**同一 rule 依 method 拆成多個 view function**（Flask 原生支援同一 rule 註冊多個 endpoint），各自掛對應的裝飾器；`ROUTE_AUTH_REGISTRY` 的 key 從單純的 rule 字串改成 `(rule, method)` 元組。這個決定必須在寫 `app.py` 之前定案——事後把合併的 view function 拆開，比一開始就拆分麻煩很多。

```python
@app.get("/api/alarms/<department>/<device_model>/<code>")
@login_required
def get_alarm(department, device_model, code): ...

@app.put("/api/alarms/<department>/<device_model>/<code>")
@admin_required
def update_alarm(department, device_model, code): ...

@app.delete("/api/alarms/<department>/<device_model>/<code>")
@admin_required
def delete_alarm(department, device_model, code): ...
```

**警報（`alarms`）**——按 method 拆分權限層級：
- `GET /api/alarms`（列表）：`login`，依 `scope_department()` 過濾（`alarms` 現在直接有 `department` 欄位，見 1.3，過濾邏輯簡化為直接 WHERE，不用再反查 `devices`）
- `POST /api/alarms/<department>`：`admin`，目標部門由 `resolve_target_department(department)` 從路徑解析，一般帳號帶錯部門 → 404，超管的 `<department>` 就是明確指定的目標
- `GET /api/alarms/<department>/<device_model>/<code>`：`login`（前台查詳情）；`department` 路徑參數走 `resolve_target_department()` 解析
- `PUT /api/alarms/<department>/<device_model>/<code>`：`admin`（後台編輯）
- `DELETE /api/alarms/<department>/<device_model>/<code>`：`admin`（後台刪除）

**機種（`devices`）**——同理拆分：
- `GET /api/devices`（列表）：`login`，依 `scope_department()` 過濾
- `POST /api/devices/<department>`：`admin`，目標部門由 `resolve_target_department(department)` 從路徑解析（不再從 body 的 `department` 欄位取，見 4.1 節第六輪修正）
- `GET /api/devices/<department>/<device_model>`：`login`
- `PUT /api/devices/<department>/<device_model>`：`admin`
- `DELETE /api/devices/<department>/<device_model>`：`admin`

連帶更新 8.1 節的 `ROUTE_AUTH_REGISTRY`（`(rule, method)` 為 key，完整範例見 8.1 節）。

**其他寫入端點——沒有部門操作意義的路徑，超管一律 400（見 4.1 節配套規則 c）**：
- `POST /api/feedback`、`POST /api/view`：**新增 `login_required`**（原本無驗證，必要的行為變更），寫入帶 `department`（從 `session["department"]` 取，超管呼叫直接 400「請以部門帳號操作」）
- `GET /api/feedback/stats`、`GET /api/view/stats`：加驗證＋部門過濾（依 `scope_department()`，超管可用 `?dept=` 檢視，這兩個是讀取端點不受配套規則 c 限制）
- `POST /api/analyze`：加驗證，部門傳入 `run_pipeline()` 新參數，寫入 `ai_scans.department`，且 AI 記憶查詢一併限縮（見 3.5）；超管呼叫直接 400
- `POST /api/confirm`、`POST /api/correct`：加驗證；超管呼叫直接 400；`confirmed_by` 伺服器端組成，**組法見 5.2.2 節（原 `f"{department}/..."` 在超管情境會產生 `"None/admin"`，已修正為用 `resolve_target_department()` 的目標部門＋含 `superadmin` 的三態角色——但依配套規則 c，超管本來就不會走到這個端點，此修正主要是文件正確性與未來若放寬限制時的保險）**，不再信任前端傳值
- `GET /api/audit`：依部門過濾
- `GET /api/admin/scan-stats`、`scan-recent`、`scan-ranking`：依部門過濾
- **`GET /api/admin/ai-logs`（若 Dashboard 的「AI 辨識失敗率」讀 `ai_logs` 表）**：【審查補強，原計畫漏列】確認這個讀取路徑也套用部門過濾，不只是 `ai_scans`/`ai_corrections`
- `POST /api/admin/cleanup-expired`：改為 `superadmin_required`，行為不變（全庫清理）

#### 4.5 新增部門管理端點（`superadmin_required`）
```
GET    /api/admin/departments                       列表（含 active/hidden/purgeable 狀態，絕不回傳密碼雜湊）
POST   /api/admin/departments                        新增（可指定 hidden/purgeable，一般部門一律留預設 false）
PUT    /api/admin/departments/<id>                    改名稱
PUT    /api/admin/departments/<id>/reset-password      重設密碼（連帶 session_version += 1）
PUT    /api/admin/departments/<id>/active              軟停用／復原
DELETE /api/admin/departments/<id>                     硬刪除，僅 purgeable=true 可執行，body 需帶 {"confirm_id": "<id>"} 二次確認
```

#### 4.6 新增 `GET /api/whoami`（掛 `@public_endpoint`，一律 200）
`{"auth", "admin", "superadmin", "department"}`，供前端判斷要不要顯示「部門管理」頁籤。**【審查修正】不是「不掛任何裝飾器」，而是明確掛上 `@public_endpoint`**（純標記，不做任何驗證動作）——讓「刻意公開」在程式碼裡是主動宣告，跟 8.1 節的路由權限測試對應。

#### 4.7 新增 `GET /api/departments/public`（掛 `@public_endpoint`）
只回傳 `active=true` **且 `hidden=false`** 的部門 `id`+`name`，給登入頁下拉選單用（見 1.1 節 `active`/`hidden` 分軸說明）。同 4.6，明確掛 `@public_endpoint` 標記。

#### 4.8 【審查補強】department 參數在遷移期與正式期的不同要求
第 7 節部署第 3 階段（`storage.py` 先上但 `app.py` 還沒改）時，`department` 參數必須有預設值 `None` 才能讓舊版 `app.py` 呼叫不出錯。但**第 4 階段 `app.py` 正式上線後，把這些預設值拿掉、改成必填參數**——這樣任何漏改的呼叫點會直接 `TypeError` 在測試/部署階段就爆出來，而不是靜默略過過濾、回傳全部資料。

---

### ⬜ 5. 前端改動

- `login.html`、`admin-login.html`：新增部門下拉選單（選項來自 `/api/departments/public`，含 `__super__` 系統管理員選項，見第 2 節登入分岔設計）
- `dashboard.html`：`mounted()` 呼叫 `/api/whoami`，依 `isSuperadmin` 條件顯示「部門管理」頁籤（列表含 active 狀態＋新增部門/重設密碼/停用對話框，沿用現有 dialog 模式）；超管的部門範圍改採「切換檢視部門」模式，見 5.2 節，**不在既有畫面逐一加部門欄位**
- `index.html`：移除四處 `confirmed_by: 'operator'` 字面值（後端會忽略/覆蓋，見 5.2.2 節 `confirmed_by` 組法修正），前端完全不需處理部門邏輯

#### 【第五輪審查：前端架構決策】採用共用 `js/api.js`＋超管切換檢視部門模式，取代原本逐頁面加部門欄位的構想

**背景**：Vue 3 CDN 沒有模組系統，但純 `<script src>` 一直可用，不需要 ESM、不需要打包、不需要改任何部署方式。原規劃若照「超管在每個列表/篩選器/新增對話框都多一個部門欄位」的方式做，會讓 `dashboard.html` 大幅膨脹——每個列表要加部門欄位、每個篩選器要加部門維度、每個統計要決定是否跨部門加總。改採以下兩個決策後，前端實際增量遠小於此。

**不做的事（刻意按住）**：不藉這次機會把前後台拆乾淨、上 build step、模組化。理由與「Render 部署要在多部門改造前先做完」同一條——不要同時改兩件大事。現在動的是安全邊界，若同時換前端架構，出問題時分不清是隔離邏輯寫錯還是打包搞壞的。抽 `js/api.js` 是重構的最小增量，不改變任何既有結構，隨時可以停在那裡；等多部門穩定跑一段時間，再回頭談要不要進一步拆。

#### 5.1 抽出 `frontend/js/api.js`：統一封裝，兩個 HTML 共用

新增 `frontend/js/api.js`（純 `<script src>`，掛 `window.AlarmApi = {...}`，不用 ESM/打包）：
- `apiFetch(url, options)`：統一封裝 `fetch`，內建：
  - **401 全域攔截**：任何回應是 401，直接導向登入頁，不用每個呼叫點各自寫 `if (r.status === 401) location.href = '/login'`
  - **429 讀 `Retry-After` 倒數**：讀取 `Retry-After` 標頭，回傳結構化的剩餘秒數給呼叫端顯示倒數，呼應 2.2 節「延遲交給客戶端處理」的設計
  - **`whoami` 結果快取**：`/api/whoami` 這種一個頁面生命週期內不太會變的資料，快取在記憶體裡，避免每個元件掛載都重打一次

`index.html`、`dashboard.html` 都改用 `<script src="/js/api.js"></script>`，原本散落幾十處各自寫 `.then().catch()` 的 fetch 呼叫，抽完之後每處只剩一行（例如 `await AlarmApi.get('/api/alarms')`）。**淨效果是兩個 HTML 反而變薄**，不是變厚。

**【重要】新增靜態檔案要同步更新 `sw.js` 的 `STATIC_SHELL` 快取清單**（見 5.4 節）——這是 5.4 改 SW 時順手要做的事，漏掉的話 `js/api.js` 不會被預先快取，離線時整個前端會直接壞掉（因為所有 fetch 呼叫都依賴這支檔案先載入成功）。

#### 5.2 【第五輪審查決策】超管改用「切換檢視部門」模式，不做跨部門混合列表

**問題**：若照原規劃「超管在每個列表/篩選器/新增對話框都多一個部門欄位」的方式做，會讓每個列表加部門欄位、每個篩選器加部門維度、每個統計決定是否跨部門加總——這才是真正會讓 `dashboard.html` 大幅膨脹的部分。

**決策**：超管登入後，後台頂端多一個部門切換器（「目前檢視：2.1線」），選了之後**所有既有畫面完全不變**，只是資料範圍變成那個部門。效果：
- 列表不用加部門欄位（一次只看一個部門）
- 篩選器不用加部門維度
- Dashboard 統計不用決定跨部門怎麼加總
- 寫入的目標部門自動有了——就是當前檢視的部門，`resolve_target_department()` 直接拿這個值，不用在每個新增/編輯對話框各加一個部門選擇欄位

`dashboard.html` 實際要新增的只有兩塊：頂端切換器，以及「部門管理」頁籤本身。其餘既有畫面一行都不用改。這比混合檢視更安全：超管永遠不會在一個混著兩個部門同名 `PILM003` 的列表裡點錯行去編輯。

**代價**：超管看不到全公司加總的統計。目前 14 台機種、一個部門，這個需求還不存在——**明確列為延後項目**：等真的有三四個部門、有人開口要，再做也不遲，那時候才知道實際想看什麼加總方式。

#### 5.2.1 檢視部門狀態存哪裡：URL query param 為唯一權威，不放 session

**問題**：若把「目前檢視部門」存進伺服器 session，看起來最省事（一次設定、所有端點自動生效、重整不掉），但會製造隱藏的全域可變狀態，有三個具體後果：
1. **分頁之間互相污染**——超管維護時常開多個分頁（一個看 2.1 線警報、一個查 3 線掃描紀錄），session 共用，其中一個分頁切換部門，另一個分頁無提示跟著變。允許 `device_model` 跨部門重複之後特別危險：兩個部門都有 `PILM003`，畫面長得一模一樣，容易在錯的分頁改錯資料
2. **同一個 URL 回傳不同資料**——壞掉瀏覽器上一頁、書籤、以及「把連結傳給同事看」這些正常行為
3. **違背整份規劃已建立的原則**——`DeptScope` 用顯式型別取代 `None`、`resolve_target_department()` 要求明確指定目標、各 Store 的 `department` 改必填參數，一整條線都在講「不要有隱性狀態」，存 session 就是開一個例外

**決策：URL query param 是唯一權威，`sessionStorage` 只是重載後的補位**：
```
/admin/dashboard?dept=line21        → 檢視 2.1線
/admin/dashboard?dept=__all__       → 跨部門全覽（明確選擇，不是預設）
/admin/dashboard                    → 讀 sessionStorage，沒有就導向 ?dept=__all__
```
API 呼叫時，超管的讀取一律帶上 `?dept=`：
```python
def scope_department() -> tuple[DeptScope, Optional[str]]:
    if is_superadmin():
        viewing = request.args.get("dept")
        if not viewing or viewing == "__all__":
            return (DeptScope.ALL, None)
        return (DeptScope.DEPT, viewing)
    # 一般帳號：完全不讀 request 參數，只認 session
    dept = session.get("department")
    if not dept:
        abort(401, "登入狀態異常，請重新登入")
    return (DeptScope.DEPT, dept)
```
寫入不受影響——4.4 節之後路徑本來就帶部門（`/api/alarms/<department>/...`），切換器只是提供 UI 要填進那個位置的值。讀寫兩邊的資訊來源因此一致，不會出現「看的是 A、寫進 B」。

用 `sessionStorage` 而不是 `localStorage` 有兩個好處：分頁獨立（每個分頁記住自己的選擇），且關掉分頁就消失（共用平板上，下一個超管登入不會沿用前一個人的選擇）。

**兩條不能破的規則**：
1. **`dept` 參數只在 `is_superadmin()` 為真時才讀**。上面的程式碼結構已保證這點，但這是最容易在重構時弄壞的地方——若有人把 `request.args.get("dept")` 提到分支外面，一般使用者就能靠 `?dept=別的部門` 越權。8.1/8.2 節的驗證要加一條：一般帳號帶 `?dept=zztest` 打 `/api/alarms`，回應必須仍然只有自己部門的資料
2. **`__all__` 必須是使用者的明確選擇，不是參數遺失的預設值**。下拉選單第一項就是「全部部門」，對應 `?dept=__all__`。這樣「跨部門混合清單」永遠是有人刻意選的結果，而不是前端某處漏帶參數造成的意外

#### 5.2.2 【連帶修正】`confirmed_by` 組法在超管情境下的缺口

**問題**：4.4 節原本寫的 `confirmed_by` 組法是 `f"{department}/{'admin' if is_admin() else 'user'}"`，但切換檢視部門模式做出來後，超管的 `session["department"]` 恆為 `None`——會組出 `"None/admin"` 這種無意義的稽核字串。

**修正**：改用**寫入的目標部門**（`resolve_target_department()` 的回傳值）加上真實角色：
```python
target = resolve_target_department(requested)
role = "superadmin" if is_superadmin() else ("admin" if is_admin() else "user")
confirmed_by = f"{target}/{role}"
```
GMP 角度這反而更好——稽核軌跡能區分「這筆是該部門管理員改的」還是「總管代為處理的」，這是兩件在稽核上意義不同的事。**此修正取代 4.4 節原本的 `confirmed_by` 組法描述。**

#### 5.3 前台顯示目前部門（共用平板防呆）

`index.html` 的 topbar 加上目前登入部門的名稱顯示（例如「2.1線 · 警報查找系統」）。工廠共用平板場景下，交班或換人操作時，這是唯一能讓操作者一眼確認「我現在是哪個部門的身份」的地方，避免誤在別的部門底下拍照分析/查詢。資料來源是 `/api/whoami` 的 `department` 欄位（見 4.6 節），透過 5.1 節的 `AlarmApi` 快取取得。

#### 5.4 【審查修正】Service Worker 跨帳號快取洩漏
現有 `sw.js` 對 `/api/*` 是 network-first + cache fallback——工廠共用平板場景下，若 A 部門登出、B 部門登入時網路不穩，`sw.js` 可能吐出 A 部門殘留在快取裡的 API 回應給 B 部門，造成資料洩漏。

修正：`logout`（`/logout`、`/admin/logout`）流程新增前端邏輯，登出時主動呼叫：
```js
if ('caches' in window) {
  caches.open('alarm-query-v10').then(cache => {
    // 清掉所有 /api/ 開頭的快取項目，只留靜態資源快取
  });
}
```
或更徹底：`sw.js` 的 fetch handler 直接把 `/api/*` 排除在 cache-fallback 之外（network 失敗就回錯誤，不回舊快取）——這是更簡單可靠的做法，工廠內網環境穩定性通常足夠，不需要為了離線容錯犧牲多租戶安全，**採用此方案**。

**【第四輪審查補強】既有快取必須主動清除，排除規則不會回溯**：上面的排除規則只約束**新版** SW 接管之後的請求。已裝在共用平板上的**舊版** `sw.js` 仍在運作，其快取裡已經存著改造前的 API 回應。新版 SW 要等下一次頁面生命週期才會 `activate`，在那之前舊快取照樣可能被吐給下一個登入的人。排除規則解決的是未來，既有快取是已經存在的資料，兩者要分開處理。

新版 `sw.js` 需要三件事同時做到：

```js
const CACHE = 'alarm-query-v11';   // 版本號必須遞增

self.addEventListener('install', (e) => {
  self.skipWaiting();              // 不等舊 SW 自然退場
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    // 1. 刪掉所有舊版 cache
    const names = await caches.keys();
    await Promise.all(names.filter(n => n !== CACHE).map(n => caches.delete(n)));

    // 2. 清掉當前 cache 裡殘留的 API 回應（防禦性）
    const cache = await caches.open(CACHE);
    const keys = await cache.keys();
    await Promise.all(
      keys.filter(req => new URL(req.url).pathname.startsWith('/api/'))
          .map(req => cache.delete(req))
    );

    await self.clients.claim();    // 3. 立即接管既有分頁
  })());
});
```

`skipWaiting()` + `clients.claim()` 是必要的：沒有它們，使用者要關掉所有分頁才會換到新版 SW，而工廠平板上的 PWA 經常整天不關。

**登出時同樣清一次**：即使 `/api/*` 已排除快取，登出流程仍應主動 `caches.delete(CACHE)`，作為縱深防禦。成本是下次載入重新抓靜態資源，內網環境可忽略。

**【與 5.1 節連動】`STATIC_SHELL` 快取清單需加入 `js/api.js`**：既然新增了共用的 `frontend/js/api.js`，`sw.js` 的靜態資源預先快取清單（`STATIC_SHELL`）要把這支檔案加進去，否則它不會被快取，離線或網路不穩時整個前端會直接壞掉——因為兩個 HTML 現在都依賴它先載入成功才能發任何 API 請求。

#### 5.5 【第五輪審查補強】SW 快取行為的手動驗證步驟（無法自動化，必須寫下來）

Service Worker 的快取生命週期（`install`/`activate`/`skipWaiting`/`clients.claim`）無法用一般的 pytest 或 curl 驗證，必須在真實瀏覽器手動走一遍，且容易漏測。上線前逐項確認：

1. 在共用平板（或模擬同等環境的裝置）上，用**改造前的舊版 PWA** 先登入 A 部門，瀏覽幾頁讓 API 回應被快取（若曾經歷過 A8 之前的版本）
2. 部署新版前端與 `sw.js`，**不要手動清瀏覽器快取**（刻意模擬真實使用者不會做這件事的情境）
3. 重新整理頁面，開瀏覽器開發者工具的 Application/Service Worker 面板，確認新版 SW 已 `activate` 且舊版已被替換（不是停在 `waiting` 狀態）
4. 檢查 Cache Storage，確認只剩新版本的 cache 名稱，舊版本（`alarm-query-v10` 等）已被清除
5. 登出 A 部門、登入 B 部門，確認看不到任何 A 部門殘留資料（尤其網路手動切成離線/弱網模擬，確認 `/api/*` 請求失敗時是回錯誤而不是回舊快取）
6. 確認 `js/api.js`、`manifest.webmanifest`、圖示等靜態資源在離線模式下仍可載入（驗證 `STATIC_SHELL` 清單有正確涵蓋）

這份清單本身列入第 7 節部署步驟 7（前端部署）的驗收項目，不是可選的附加測試。

---

### ⬜ 6. 批次匯入工具（新增，原計畫遺漏）

目標「新增部門幾乎零成本」的真正瓶頸在這裡——後台警報管理是單筆 CRUD，新部門上線動輒 1000+ 筆警報，不可能手動輸入。

新增 `backend/import_alarms.py`（CLI 工具，總管/開發者操作）：
- 輸入：CSV 或 Excel 檔（欄位對應現有 `alarms` 表結構：`code, device_model, severity, description, cause, solution, keywords, sol_steps`）
- 參數：`--department <id>` 必填，明確指定要匯入到哪個部門，避免匯錯

**【審查修正】`save()` 的「刪除不在新清單裡的舊資料」語意在批次匯入情境下是破壞性的**：`alarms_store.save(items, department=<id>)` 走的是 3.1 節已修正過、有部門過濾的刪除掃描邏輯（不會誤刪其他部門），但**同一部門內**，若匯入檔只是該部門的子集（例如只有 200 筆，但該部門實際有 1000 筆），`save()` 語意上會把「不在這 200 筆清單裡」的其餘 800 筆全部當作「已刪除」清掉。首次匯入（部門原本是空的）沒問題，但只要有人事後拿一個子集重跑，就會誤刪大量既有資料。

**修正**：新增 `--mode` 參數，預設值不是直接呼叫 `save()`：
- `--mode append`（預設）：只新增/更新檔案裡出現的代碼，不刪除任何既有資料（走 `upsert_one()` 逐筆處理，不呼叫 `save()`）
- `--mode upsert`：同 `append`，語意上更明確是「有則更新、無則新增」，效果相同，提供這個別名讓意圖更清楚
- `--mode replace`：才是原本 `save()` 的「整批取代」語意，**必須額外帶 `--yes-i-mean-replace` 旗標**才會執行，防止誤觸這個危險模式
- `--dry-run` 除了印出「將寫入 N 筆」，**在 `replace` 模式下必須額外印出「將刪除 N 筆」**，讓操作者在真正執行前看到破壞性的一面，不是只看到正面的寫入數字
- 機種（`devices`）批次建立比照辦理，或允許在同一支腳本裡先建機種再匯警報

#### 6.1 【第四輪審查修正】匯入前置驗證與交易語意

**問題**：原文預期「FK／邏輯錯誤會在匯入中途爆出」。但依 1.3，`alarms.department` 的外鍵指向 `departments(id)`，**`alarms` 與 `devices` 之間沒有任何外鍵**。匯入不存在的 `device_model` 不會報錯，會安靜寫入；而前台機種下拉選單來自 `devices`，這些警報永遠不會出現在任何畫面上。

這比中途爆掉更難處理：中途爆掉當下就知道；這個要等到某天有人問「為什麼查不到 XX 代碼」才會發現，而那時已經上線。

**前置驗證（在寫入任何一筆之前執行）**：
1. 取出 CSV 中所有相異的 `device_model`
2. 查詢該部門 `devices` 表中已存在的機種
3. 若有任何 `device_model` 不存在於該部門 → **整批中止**，列出缺少的機種清單與各自的警報筆數，不寫入任何一筆

這一步不能省略，也不能改成「邊匯邊檢查」——`alarms` 與 `devices` 之間沒有外鍵，寫入孤兒警報不會產生任何錯誤，只會安靜地產生查不到的資料。

`--create-missing-devices`：opt-in 旗標，允許自動建立缺少的機種。**預設關閉**，因為預設自動建立會讓 CSV 裡的錯字（`PILM003` 打成 `PLIM003`）直接變成一台新機種，而不是被擋下來。

**交易語意：全有全無。** 過程中任何一筆失敗，整批回滾。半批資料比完全沒匯更難收拾——你無法從結果反推哪些進去了。若透過 PostgREST 逐筆寫入無法保證原子性，則先寫入暫存表再一次 `INSERT ... SELECT`，或改用直連 Postgres 執行單一交易。

`--dry-run` 必須輸出：
- 將寫入 N 筆
- 缺少的機種清單（有的話直接標為 `ERROR`，即使 dry-run 也要明確顯示這批不會成功）
- `--mode replace` 時額外輸出「將刪除 N 筆」

**建議執行順序**：先用 `import_devices.py`（或同一支腳本的 `--devices-only`）建立機種，驗證無誤後再匯警報。機種數量少、人工核對成本低，而機種一旦正確，警報匯入的前置驗證就會直接通過。

---

### ⬜ 7. 部署順序（分階段，避免一次到位風險）

**【審查修正：單部門環境對隔離失效是盲點】** 原設計把「只有一個部門時上線過濾邏輯」當作安全順序——這個順序本身沒錯（能避免過濾邏輯誤傷自己），但有個沒講清楚的前提：**單部門環境下，「過濾條件寫錯/漏寫/根本沒生效」這類 bug 全部是隱形的**——`department=eq.<id>` 這個條件不管有沒有正確套用，反正資料庫裡就只有一個部門，查詢結果看起來都一樣「正常」，測試全綠。等到真正的第二部門進來、資料已經上線在用，才會第一次讓這類 bug 現形，而這正是最難處理、代價最高的時間點（已有真實使用者、真實資料）。

**修正**：框架階段就建立一個**內部測試部門**（假資料，跟正式資料完全脫鉤），提前到第 4 階段跟 `app.py` 一起上線，讓隔離驗證矩陣（第 8 節）在有真正兩份不同資料的情況下跑過，而不是等到推廣給第二個真實部門才第一次測試。

1. **加欄位＋索引**（全部 nullable）— 零行為變更，驗證現有 API 照常
2. **跑遷移腳本** — 執行 1.7 節的重複值預檢，通過後建立預設部門、回填機種與警報部門，驗證 `devices`/`alarms` 無 NULL 後加 `NOT NULL` 約束
3. **【審查修正：原本只在 1.2/1.3 節文字說明、沒有真的列進部署清單】主鍵/唯一約束切換** — 確認 `devices`、`alarms` 現行約束的實際型態（1.2 節「情況 A / 情況 B」），若需要 drop 舊約束、建立 `(department, device_model)`／`(department, device_model, code)` 複合唯一索引，在這一步獨立完成並驗證。若有其他表以外鍵指向這兩張表的舊主鍵，此時一併處理。**此步驟必須在下一步 `storage.py` 部署（含 `upsert_one()`）之前完成**——`upsert_one()` 的 `ON CONFLICT` 目標欄位取決於當時生效的約束，順序顛倒會讓 upsert 打到錯誤的約束
4. **部署新版 `storage.py`**（`app.py` 還沒改，`department` 參數有預設值 `None`，過濾邏輯視同不啟用）— 驗證行為與改動前一致
5. **部署新版 `app.py`**（真正套用過濾，`department` 參數拿掉預設值改必填；同時更換 `FLASK_SECRET_KEY` 清空既有 session）
6. **【正式採用 sentinel_pack】灌入哨兵測試資料並跑完整驗證**（見第 8 節）——這一步取代原本「自建內部測試部門」的簡化構想，改用 `/Applications/My Project/sentinel_pack/` 這套已經備妥的完整工具，確認隔離真的生效，而不只是「沒有明顯錯誤」——這是新增的關卡，通過才能往下走。**筆數與驗證項目數以 `sentinel_pack/README.md` 當時的實際內容為準**（本計畫第 8.2 節寫的是套件目前版本的內容摘要，套件本身若因應本輪審查而更新，以套件內容為準，不受這份計畫文件的版本落差影響）
7. **部署前端改動**（含 5.1 的 service worker 快取修正）— 純增量 UI，其他人畫面不受影響
8. **用批次匯入工具（第 6 節）建立第一個真實的第二部門的機種與警報**
9. **建立第二個真實部門的登入帳號** — 交給該部門使用者前，再跑一次 `verify_isolation.sh`（用真實部門取代哨兵部門的位置），確認結果與步驟 6 一致
10. **是否執行 `02_teardown_sentinel.sql` 清除哨兵部門，見下方「purge 時機的判斷」——不預設固定在這一步，依當下是否還需要回歸驗證而定**

#### 【判斷分歧點，已與使用者討論並修正】purge 時機的判斷

原規劃預設「步驟 9 驗證通過後就清除哨兵部門」，理由是不留假資料在正式庫、GMP 稽核乾淨。但這個預設忽略了一個後續成本：**驗證矩陣本身含破壞性操作**（8.2 節的 `PUT ... {"description":"tampered"}` 竄改測試、新增/刪除機種測試）。哨兵部門存在時，這些操作打在假資料上零風險；哨兵部門被清除之後，**下一次改完 `storage.py`/`app.py` 想重新跑回歸驗證，就沒有安全的靶子可用**——只能對著某個真實部門正在使用中的資料做竄改測試，這比留一個假部門的風險高得多。

**修正為不預設固定時機，改為兩個選項擇一，由當時判斷哪個更符合現況**：
- **選項 A（清除）**：確認往後不會再需要對隔離邏輯做重大改動（例如已穩定上線一段時間），或公司稽核規範明確要求正式庫不得留測試資料 → 執行 `02_teardown_sentinel.sql`。之後若真的需要回歸驗證，重新跑一次 `01_seed_sentinel.sql` 即可重建（腳本設計為可重複執行）
- **選項 B（保留但清空敏感內容）**：只清資料列（`alarms`/`ai_scans`/`ai_corrections`/`ai_logs`/`feedback`/`alarm_views`/`devices` 裡 `department` 對應的資料），保留 `departments` 表裡那筆 `hidden=true` 的空部門記錄本身——之後任何時候都能重新灌一批新的哨兵資料進這個既有部門當靶場，不需要重新走一次建部門/建密碼的流程

**選擇依據**：若預期近期還會頻繁修改隔離相關程式碼（例如剛上線、還在觀察穩定性），選 B；若已經穩定運作、下次改動預期是遙遠的未來，選 A（畢竟重新跑 `01_seed_sentinel.sql` 成本也不高）。這個決定留給實際到達這一步時，依當下狀況判斷，不在此預先鎖死。

---

### ⬜ 8. 驗證方式

#### 8.1 自動化測試【審查補強，取代單純手動 curl；並修正「只驗字典、不驗執行」的漏洞】
隔離是安全邊界，只靠一次性手動驗證不可靠——之後任何人新增一個 `/api/*` route 忘記加過濾就會破功，且不會有任何提示。

**【審查修正】原設計只驗證「路由有沒有登記在白名單字典裡」，驗不到「裝飾器有沒有真的掛上去」**——有人新增路由、記得把它加進 `ROUTE_AUTH_REGISTRY`、但忘了在 view function 上加對應的裝飾器，這支測試依然會全部通過，因為它只檢查字典裡有沒有這個 key，不檢查實際執行行為。

**修正**：在 `login_required`/`admin_required`/`superadmin_required` 三個裝飾器內部，各自替被包裝的函式設一個標記屬性（例如 `wrapper._auth_level = "login"` / `"admin"` / `"superadmin"`）。測試改成比對「字典宣告的層級」vs「view function 實際帶著的 `_auth_level` 屬性」，兩邊不一致（包含「字典說要 admin_required 但函式上根本沒有這個屬性」）就失敗。

**【審查修正】需要第四種層級 `public`，且必須是主動宣告，不能是「沒掛裝飾器」的預設狀態**：`GET /api/whoami`、`GET /api/departments/public` 兩個端點刻意不掛任何驗證裝飾器（見 4.6、4.7），但它們仍在 `/api/*` 底下，會被測試遍歷到。若只有 `login`/`admin`/`superadmin` 三種層級，這兩個端點在白名單裡無處安放，測試會直接失敗。但如果只是「白名單裡也允許沒有值＝視為公開」，就沒辦法區分「這個端點是刻意設計成公開」還是「開發者忘記加裝飾器」——兩者在測試結果上必須看起來不一樣，否則這個測試形同虛設。

新增一個 `@public_endpoint` 裝飾器（功能上什麼都不做，純粹是標記），一樣設 `wrapper._auth_level = "public"`——這樣「公開」就跟其他三種層級一樣，是明確寫在程式碼裡的宣告，而不是「沒有標記就當作公開」的隱性後路。

**【第七輪審查修正：關鍵結構問題】白名單的 key 必須是 `(rule, method)`，不能只用 rule 字串**：4.4 節已確定同一路徑上不同 HTTP method 權限層級不同（例如 `GET /api/alarms/<department>/<device_model>/<code>` 是 `login`，`PUT`/`DELETE` 同路徑是 `admin`）。若白名單只用 rule 字串當 key，物理上無法表達「同一路徑、不同方法、不同權限」，測試會在第一天就失敗或誤判：

```python
# tests/test_route_auth_registry.py
# 維護一份白名單，明確標註每個 (路徑, HTTP method) 組合屬於哪種權限層級
ROUTE_AUTH_REGISTRY = {
    ("/api/alarms", "GET"): "login",
    ("/api/alarms/<department>", "POST"): "admin",
    ("/api/alarms/<department>/<device_model>/<code>", "GET"):    "login",
    ("/api/alarms/<department>/<device_model>/<code>", "PUT"):    "admin",
    ("/api/alarms/<department>/<device_model>/<code>", "DELETE"): "admin",
    ("/api/devices", "GET"): "login",
    ("/api/devices/<department>", "POST"): "admin",
    ("/api/devices/<department>/<device_model>", "GET"):    "login",
    ("/api/devices/<department>/<device_model>", "PUT"):    "admin",
    ("/api/devices/<department>/<device_model>", "DELETE"): "admin",
    ("/api/admin/scan-stats", "GET"): "admin",
    ("/api/admin/departments", "GET"): "superadmin",
    ("/api/whoami", "GET"): "public",              # 刻意公開，見 4.6
    ("/api/departments/public", "GET"): "public",   # 刻意公開，見 4.7
    # ... 完整列出，每次新增端點都要在這裡登記
}

def test_all_api_routes_have_matching_auth_decorator(client_app):
    """遍歷 app.url_map，對每個 (路徑, method) 組合斷言：
    (1) 都在白名單裡登記了層級 (2) view function 上實際掛著對應的裝飾器標記，
    包含 'public' 這種刻意公開的層級也要有明確的 @public_endpoint 標記，
    不能靠「沒有標記」來代表公開——那樣會跟真正忘記加裝飾器的錯誤無法區分。
    只驗字典不驗執行的話，忘記掛裝飾器的路由測試照樣會綠——這裡兩者都要對上。"""
    for rule in client_app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            key = (rule.rule, method)
            assert key in ROUTE_AUTH_REGISTRY, f"{method} {rule.rule} 未登記權限層級"
            view_func = client_app.view_functions[rule.endpoint]
            declared_level = ROUTE_AUTH_REGISTRY[key]
            actual_level = getattr(view_func, "_auth_level", None)
            assert actual_level == declared_level, (
                f"{method} {rule.rule} 宣告層級為 {declared_level!r}，"
                f"但實際裝飾器標記為 {actual_level!r}（可能忘記掛裝飾器或掛錯層級，"
                f"若確實要公開必須明確加上 @public_endpoint）"
            )
```

`app.py` 對應要在 `GET /api/whoami`、`GET /api/departments/public` 上加 `@public_endpoint`（純標記，無驗證行為，見 4.6、4.7 節補充）；同一 rule 的不同 method 依 4.4 節拆成獨立 view function，各自掛對應裝飾器。

**【第七輪審查補強】`resolve_target_department()` 不讀 `request.args` 的原始碼檢查**：4.1 節配套規則 (a) 要求 `resolve_target_department()` 禁止讀 `request.args`，這條規則最容易在重構時被悄悄破壞。加一條原始碼層級的斷言：
```python
import inspect

def test_resolve_target_department_does_not_read_request_args():
    """確保 resolve_target_department() 的目標部門只來自 URL path，
    不會被重構時 fallback 到 request.args（那樣會讓超管的讀寫來源再度分岔）。"""
    source = inspect.getsource(resolve_target_department)
    assert "request.args" not in source, (
        "resolve_target_department() 不得讀取 request.args —— "
        "目標部門必須只來自 URL path 參數（見 4.1 節配套規則 a）"
    )
```

**【第七輪審查補強】一般帳號帶 `?dept=別的部門` 越權測試**：對應 5.2.1 節的「兩條不能破的規則」第一條，`scope_department()` 的 `?dept=` 只在 `is_superadmin()` 為真時才生效。這條要在整合測試（8.2 節，需要真實 Supabase）而非本節的純結構測試裡驗證：一般帳號登入後帶 `?dept=zztest` 打 `/api/alarms`，回應必須仍然只有自己部門的資料，不受 query string 影響。

#### 8.2 【正式採用】sentinel_pack — 哨兵部門隔離驗證

原本規劃裡簡化的「內部測試部門＋手動 curl 矩陣」，正式改用 `/Applications/My Project/sentinel_pack/` 這套已經備妥的完整工具，取代 8.2~8.4 舊版內容。這套工具比手動矩陣更嚴謹，理由：

**【文件一致性提醒，第七輪確認：套件仍是 T-01~T-10／50 筆版本，尚未跟上本計畫第四~六輪的審查內容】** 以下的 50 筆、T-01~T-10 編號，經第七輪確認仍是 `sentinel_pack/README.md` 的當前內容。但本計畫第四~六輪新增的登入分岔（第 2 節）、session 三態檢查（2.1）、`devices` 路徑改法（4.4）等設計，`sentinel_pack` 目前**還沒有對應的驗證項目**——例如沒有測「登入路徑分岔是否真的互不 fallthrough」、沒有測「一般帳號帶 `?dept=` 越權」、沒有 `department IS NULL` 孤兒列驗證 `DeptScope.ALL` 是否被無聲收窄（見 3.7）。**執行第 7 節步驟 6 之前，先確認 `sentinel_pack` 是否已經補上這些項目**（若你的專家／維護者正在同步更新，以屆時的實際版本為準；若尚未更新，這些新增的安全機制暫時只能靠 8.1 節的結構測試 + 手動 curl 驗證，不能只依賴 `verify_isolation.sh` 全過就認定完整）。本節內容僅供理解工具設計理念，不是精確的操作規格。

- **50 筆資料涵蓋每一張有 `department` 欄位的表**（含 `devices`/`alarms`/`ai_scans`/`ai_corrections`/`ai_logs`/`alarm_history`/`feedback`/`alarm_views`）——避免「某張表零筆資料，測試看起來全過，實際上根本沒測到」的假陽性
- **`ACM001` 撞名機種直接驗證第 1.3 節的關鍵設計決策**——INSERT 成功與否本身就是在回答「`alarms` 到底要不要加 `department` 欄位」，比純手動驗證更貼近真實風險場景
- **T-07「反向確認哨兵資料真的存在」是防呆機制**——如果哨兵資料沒載入成功，其他測試會全部誤判通過，這點原規劃沒有涵蓋
- **10 項驗證涵蓋原規劃的全部項目並擴充**：撞名反查（T-02）、雙向寫入隔離（T-03）、`save()` 誤刪防護（T-04）、六個統計/稽核端點過濾（T-05）、共用關鍵字跨部門搜尋隔離（T-06）、總管跨部門可見（T-08）、未登入攔截含原本無驗證的 `feedback`/`view`（T-09）、哨兵部門不出現在公開登入清單（T-10）

**使用流程**：
1. `pip install 'werkzeug>=3.0' && python gen_hashes.py` 產生密碼雜湊（密碼隨機產生，不寫死進檔案）
2. 替換 `01_seed_sentinel.sql` 中的三個佔位字串：`__PW_HASH__`/`__ADMIN_PW_HASH__`（上一步輸出）、`__HOME_DEPT__`（你的部門 id）、`__COLLIDE_CODE__`（`ACM001` 底下一個真實存在的警報代碼）
3. 對照 Supabase 實際 schema 檢查各段 INSERT 的假設欄位名稱是否吻合（README 已標註，尤其留意 `alarm_views` 是計數表還是事件表）
4. 載入前先記錄 Dashboard 基準值（今日掃描數、機種數、Top10）
5. `psql "$SUPABASE_URL" -f 01_seed_sentinel.sql`，確認自我檢查印出 `1/4/20/7/3/4/3/4/4`（合計 50）且 `null_dept_devices=0`
6. `export` 好各組密碼環境變數後執行 `./verify_isolation.sh`，全過 exit 0
7. **使用節奏**：每次改完 `storage.py`/`app.py` 就重跑一次；每新增一個 `/api/*` 端點就在 T-05 清單加一行；推廣給第一個真實部門前完整跑一次並留存輸出；正式稽核前才執行 `02_teardown_sentinel.sql`

**GMP 考量**：正式資料庫放測試資料，依規範可能需要記錄與說明。哨兵部門的存在、目的、資料範圍應寫進驗證文件當作測試紀錄的一部分（若公司規範不允許在正式庫放測試資料，退路是另開 Supabase project 專跑隔離驗證，但會犧牲「驗證的正是正式環境 `save()` 行為」這個核心價值，非優先選項）。

**【審查修正：舊版殘留，撞名測試的意義已經改變】** 原文寫「`ACM001` 那筆 INSERT 若因 `device_model` 全域唯一約束而失敗就跳過 T-02」，這是還沒決定 1.3 節設計時的說法。1.3、1.2 節現在已經明確決定「允許跨部門同名，`devices`/`alarms` 的唯一約束都改成含 `department` 的複合鍵」——所以 `ACM001` 這筆 INSERT **應該要成功**，如果它失敗了，代表 1.2/1.3 節的複合唯一約束沒有正確套用到資料庫（約束範圍還停留在只看 `device_model`），這本身就是一個需要回頭檢查的訊號，不是「預期內、跳過即可」的分支。

**T-02 現在真正在測的是什麼**：不再是「反查會不會串」（因為已經不用反查模型了，`alarms` 直接有 `department` 欄位），而是**過濾條件到底有沒有正確帶上 `department`，還是只比對了 `device_model`**。具體來說：`ACM001` 這台機種在正式部門和哨兵部門各存在一份、且各自底下都有警報代碼。若查詢邏輯某處漏寫了 `department=eq.<id>`、只用 `device_model=eq.ACM001` 去比對，就會把兩個部門的 `ACM001` 資料混在一起回傳——這才是 T-02 要抓的實際 bug，也是這一版計畫裡最容易在程式碼審查中被忽略的一種寫法錯誤（因為 `device_model` 過濾條件本身沒寫錯，只是少了另一個必要條件）。

---

### 關鍵檔案
- `backend/storage.py`（SupabaseStore 過濾＋修 save() 地雷、新增 DepartmentStore、alarms 表新增部門過濾邏輯）
- `backend/app.py`（登入/權限/新端點、`scope_department()`／`resolve_target_department()` 顯式型別、fail-fast 啟動檢查、同 rule 依 method 拆分 view function，見 4.4）
- `backend/ai/ai_memory.py`、`backend/ai/ai_pipeline.py`（department 參數往下傳，含查詢限縮，非只有寫入）
- `frontend/login.html`、`frontend/admin-login.html`（部門選單，含 `__super__` 選項）
- `frontend/dashboard.html`（超管部門切換器＋部門管理頁籤，含停用/重設密碼，見 5.2）
- `frontend/index.html`（移除 confirmed_by 字面值，加上目前部門顯示，見 5.3）
- `frontend/js/api.js`（**新檔**，統一封裝 `apiFetch`：401 攔截、429 倒數、`whoami` 快取，見 5.1；`STATIC_SHELL` 快取清單需要同步加入，見 5.4）
- `frontend/sw.js`（`/api/*` 排除快取＋`activate` 清除舊快取＋登出清快取，避免跨帳號洩漏，見 5.4）
- `backend/migrate_add_departments.py`（**新檔**，一次性遷移，含重複值預檢與備份提醒，見 1.7）
- `backend/import_alarms.py`（**新檔**，批次匯入工具，含前置機種驗證與 `--mode` 保護，見第 6 節）
- `backend/requirements.txt`（補 `werkzeug>=3.0` 依賴）
- `tests/test_route_auth_registry.py`（**新檔**，路由權限白名單自動測試，key 為 `(rule, method)` 元組，見 8.1；同檔或旁邊需含 `resolve_target_department()` 不讀 `request.args` 的 `inspect.getsource` 原始碼檢查）
- Supabase 新表 `login_attempts`（登入節流狀態儲存，見 2.2.1；`department` 對不到 `departments` 表是刻意設計，不可被清理腳本誤刪，見表定義註記）
- `/Applications/My Project/sentinel_pack/`（已備妥的哨兵驗證工具，第 7 節步驟 6 開始使用，見第 8.2 節）：`01_seed_sentinel.sql`、`02_teardown_sentinel.sql`、`gen_hashes.py`、`sentinel_data.json`、`verify_isolation.sh`，實際檔案清單與內容以套件當時版本為準（第七輪確認套件尚未涵蓋本計畫第四~六輪新增的驗證項目，見 8.2 節提醒）

---

## 三、待確認事項

- ~~你自己目前這個部門（ACM001、TFM001 等機種）要取什麼正式名稱？~~ **【第十四輪定案，已解決】** `id = "mf4d"`、`name = "製造四部包裝組"`，詳見附錄第十四輪審查

---

## 四、文件存放

本規劃文件已複製至專案根目錄（跟 `CLAUDE.md`/`README.md` 同層）：
`/Applications/My Project/testing/PLAN_department_isolation.md`

---

## 附錄：審查發現與修正對照

一輪安全審查針對原始規劃提出以下發現，本版已全部採納並整合進上述章節：

**🔴 會直接造成隔離失效（已修正）**
1. `device_model` 跨部門撞名 → `alarms` 表改加 `department` 欄位，複合主鍵含部門（見 1.3）
2. `scope_department()` 用 `None` 代表「不過濾」是 fail-open 設計 → 改用顯式 `DeptScope` 型別，取不到部門直接 401（見 4.1）
3. AI memory 跨部門汙染 → 讀取查詢明確限縮部門範圍，不只是寫入記錄（見 3.5）
4. **單部門環境是隔離驗證的盲點**——過濾邏輯寫錯/漏寫/沒生效，在只有一個部門時全部隱形，測試全綠，直到真實第二部門上線才第一次現形（此時代價最高）→ 框架階段就用哨兵資料驗證，跟 `app.py` 同步上線並跑完整驗證，不等推廣才第一次測試（見第 7 節）。**這一項後續由 `sentinel_pack` 正式落地實作，取代原本簡化的「自建測試部門」構想（見 8.2）。套件的資料筆數與驗證項目數量，以套件當時的實際內容為準**

**🟠 建議補強（已納入）**
- NULL department 語意未定義 → 明確規範遷移驗證與 NOT NULL 收緊時機（見 1.5）、加 index（見 1.4）
- Service Worker 快取跨帳號洩漏 → `/api/*` 排除快取（見 5.4）
- 測試策略太弱 → 新增路由權限白名單自動測試（見 8.1）、正式採用 `sentinel_pack` 完整驗證取代手動矩陣（見 8.2）
- 改密碼後既有 session 不失效 → 新增 `session_version` 機制（見 2.1）
- 登入頁部門下拉選單與「完全看不到」需求矛盾 → 需求文字修正為「登入後資料完全隔離」，明確記錄此權衡（見「需求確認」）
- 登入沒有防爆破 → 新增失敗次數節流（見 2.2）
- 沒有刪除／停用部門的路徑 → 正式部門改軟停用 `active` 旗標，不做硬刪除；測試部門例外提供 `purge()` 硬刪除，驗證完畢後執行（見 3.4）
- 新部門批次匯入沒規劃 → 新增 `import_alarms.py` 工具（見第 6 節）
- `ai_logs`/`ai_corrections` 讀取端點未列入過濾清單 → 明確補列（見 4.4）
- 遷移前備份與回滾方式缺失 → 新增備份提醒與回滾說明（見 1.6）
- Supabase key 與 RLS → 應用層過濾已足夠，RLS 作為縱深防禦，列為長期強化項目（非本次必要項）

**🟡 小項（已納入）**
- `department` 參數遷移期需預設值、正式上線後應拿掉改必填 → 見 4.8
- 超管密碼比對改用 `hmac.compare_digest` → 見第 2 節
- Render 部署後 cookie 需設 `Secure`/`HttpOnly`/`SameSite=Lax` → 併入 Render 部署工作項目，不重複列在此規劃（多部門規劃聚焦隔離邏輯本身）
- GMP 稽核只到部門層級是已知限制，非疏漏 → 已寫入「已知決定不做」章節，供稽核時佐證決策紀錄

---

第二輪審查（動工前必須解掉的邏輯漏洞）：

**🔴 會讓實作卡死或安全機制自我矛盾（已修正）**
1. **`/api/alarms/<device_model>/<code>` 路由歧義**——`device_model` 允許跨部門重複後，這條路徑對總管無法唯一定位資料 → 路由改為 `/api/alarms/<department>/<device_model>/<code>`，並明確拆開「讀取用 `scope_department()`」vs「寫入用 `resolve_target_department()`」兩套語意（見 1.3、4.1、4.4）
2. **主鍵切換在部署順序裡沒有落腳點，且缺重複值預檢**——`create unique index` 若庫裡已有重複 `(device_model, code)` 會直接失敗；且主鍵真正切換不是「零行為變更」，需要獨立部署階段，也要確認 `upsert_one()` 的上線順序在主鍵切換完成之後 → 遷移腳本前置預檢（見 1.7），部署順序新增獨立階段（見 1.3）
3. **`active` 與「不出現在登入頁」是兩個不同的軸**——單一 `active` 旗標無法同時滿足「哨兵部門能登入但不出現在下拉選單」 → 拆成獨立的 `hidden` 欄位（見 1.1）
4. **`purge()` 的保護條件會自我否定**——用「已登入過」或「建立時間超過門檻」當判斷依據，會導致測試部門在真正要清除時反而清不掉（因為驗證過程必然登入過、時間也早已超過門檻）→ 改用建立時就固定的 `purgeable` 欄位 + 呼叫端二次確認 dept id（見 3.4）

**🟠 建議調整（已納入）**
- `session_version` 每次請求查 DB 造成額外網路來回 → 加行程內快取，TTL 30~60 秒（見 2.1）
- 批次匯入工具直接呼叫 `save()` 是破壞性的（子集重跑會誤刪既有資料）→ 新增 `--mode append|upsert|replace`，預設 `append`，`replace` 需額外旗標確認，`--dry-run` 需印出「將刪除 N 筆」（見第 6 節）
- 工廠共用出口 IP，硬鎖節流會讓整部門被單一人誤觸鎖死、甚至被當阻斷手段 → 改漸進式延遲（`2^N` 秒，有上限），硬鎖只留給明顯自動化攻擊的極高頻樣態（見 2.2）
- 路由白名單測試只驗證「有沒有登記在字典」，驗不到「裝飾器有沒有真的掛上」→ 裝飾器內部設標記屬性，測試比對宣告層級 vs 實際裝飾器標記（見 8.1）
- `FLASK_SECRET_KEY` 需確認是固定環境變數而非啟動時隨機產生，否則每次重啟/擴容全體被登出 → 明確要求（見 4.3）
- 前端是否曾直連 Supabase（若有會讓應用層過濾完全繞過）→ 已用 `grep` 確認 `frontend/` 內完全無 Supabase URL/anon key/直連字樣，RLS 維持長期強化項目定位不變

**判斷分歧點（已與使用者討論並修正）**
- 「驗證完畢後立即清除哨兵部門」與「驗證矩陣含破壞性操作、清除後回歸驗證缺乏安全靶場」互相矛盾 → 不預設固定清除時機，改為「清除」或「保留部門但清空資料列供重複使用」兩個選項，依當下是否還需要頻繁回歸驗證而定（見第 7 節「purge 時機的判斷」）

---

第三輪審查（「還沒解掉的」——上一版指示寫了但沒有真的套用到對應章節）：

1. **`devices` 的唯一性約束沒跟著改，會讓 1.3 的前提在最前面就卡住**——`alarms` 改了複合鍵，但 `devices` 若現行約束就是單獨的 `device_model`，第二部門連建立同名機種都會被擋下，1.3 節「允許跨部門同名」的設計根本走不到 `alarms` 那一步就先失敗 → `devices` 唯一約束一併改為 `(department, device_model)`，並列出情況 A（代理鍵，只需加索引）／情況 B（`device_model` 本身是約束對象，需要連動切換）兩種可能，執行前先確認現況（見 1.2）
2. **主鍵切換「必須獨立列一個部署階段」的指示沒有真的出現在第 7 節編號清單裡**——1.3 節文字寫了要求，但清單本身沒有對應步驟，等於指示沒被套用 → 插入為新的第 3 步「主鍵/唯一約束切換」，明確排在 `storage.py` 部署（含 `upsert_one()`）之前，後續步驟編號全部順移（見第 7 節）
3. **`/api/whoami`、`/api/departments/public` 會讓 8.1 的路由白名單測試直接失敗**——兩者刻意不掛驗證裝飾器，但測試只認得 `login`/`admin`/`superadmin` 三種層級，白名單裡無處安放這兩個公開端點 → 新增第四種 `public` 層級，並用主動宣告的 `@public_endpoint` 標記裝飾器區分「刻意公開」與「忘記加裝飾器」，兩者不能共用「沒有標記」這個狀態（見 8.1、4.6、4.7）
4. **`2^N` 秒延遲若用 `sleep()` 實作，會佔用 Flask worker，反而製造比原本硬鎖更好用的阻斷手段**——Render worker 數量有限，攻擊者開幾十個連線觸發延遲就能把服務打滿 → 改為伺服器不等待、直接回 `429` + `Retry-After` 標頭，等待邏輯交給客戶端，伺服器請求處理保持即時返回、不佔用資源（見 2.2）
5. **8.2 節「前提確認」段落是舊版殘留**，內容還停留在「反查模型會不會串」的舊設計，但 1.3 節已經決定 `alarms` 直接加 `department` 欄位、不用反查 → 重寫為撞名測試現在真正驗證的目標：過濾條件是否確實帶上 `department`，而不只是比對 `device_model`；`ACM001` INSERT 現在預期應該成功，若失敗代表複合唯一約束沒有正確套用，需要回頭檢查（見 8.2）

---

第四輪審查（依🔴🟠🟡🔵分級，動工前必須解掉的邏輯漏洞與一致性問題）：

**🔴 權限提升與安全機制自我破功（已修正）**
1. **超管登入 fallthrough 造成權限提升風險**——原設計「先試超管密碼，不符再試部門密碼」，加上各部門密碼允許重複，只要任一部門管理員密碼剛好等於 `SUPERADMIN_PASSWORD`，該管理員登入即取得總管權限，且無跡可循 → 登入路徑在表單選擇的入口就分岔，`__super__` 與真實部門各走各的比對，互不回退；`create`/`reset-password` 額外拒絕與超管密碼相同的明文密碼（見第 2 節）
2. **session 檢查只涵蓋密碼重設，漏了停用與硬刪除**——`set_active(false)` 後舊 cookie 仍暢通，`purge()` 後 session 帶著不存在的部門 id 會撞外鍵變 500 而非乾淨的 401 → 改為 `assert_session_valid()` 一次檢查部門存在性＋`active`＋`session_version` 三件事的合取，共用同一份行程內快取，含 `None`（不存在）也要快取的細節（見 2.1）
3. **`2^N` 秒退避沒定義失敗次數存在哪裡，計畫無法直接動工** → 新增 `login_attempts` 表為唯一真實來源（不用行程內狀態，避免 worker 數量把防護強度稀釋），定義 N 為「最近成功登入之後、15 分鐘窗口內的連續失敗數」，清理併入既有 `cleanup-expired` 端點（見 2.2.1）

**🟠 一致性未套用到位（已修正）**
4. **`devices` 單筆路由沒跟著 `alarms` 一起改成三段式**——`device_model` 跨部門重複後，`DELETE /api/devices/PILM003` 對總管一樣無法唯一定位，4.4 節卻只在 `alarms` 改了、`devices` 仍寫籠統的 `*` → 改為 `/api/devices/<department>/<device_model>`，同步更新 `ROUTE_AUTH_REGISTRY`（見 4.4）
5. **批次匯入沒有做「機種是否存在」的前置驗證**——`alarms` 與 `devices` 之間沒有外鍵，匯入不存在的 `device_model` 不會報錯，只會安靜產生前台永遠查不到的孤兒警報，比匯入中途報錯更難察覺 → 新增前置驗證（整批比對機種存在性，缺任何一個就整批中止並列出清單）與全有全無交易語意，`--create-missing-devices` 預設關閉防止錯字建立新機種（見 6.1）
6. **各 Store 方法沒有明確要求 `department` 必填**——原本只靠 code review 時人工 grep 確認，與 4.8 節已經用過的「拿掉預設值讓漏改直接 TypeError」原則不一致 → 新增 3.6 節，列出所有需要必填 `department`/`(DeptScope, id)` 參數的方法清單，過渡期與正式期的轉換時機比照 4.8（見 3.6）
7. **`upsert_one()` 沒有指定 PostgREST 的 `on_conflict` 目標**——衝突目標預設取主鍵，在「unique index 已建、主鍵未切換」的中間狀態下會安靜打到舊約束 → 一律在 URL 明確帶 `on_conflict=department,device_model,code`（`alarms`）或 `on_conflict=department,device_model`（`devices`）（見 3.1）
8. **`sw.js` 的快取排除規則只約束新版接管後的請求，既有裝置上的舊快取不會自動消失** → `activate` 事件主動清除舊版 cache 與當前 cache 裡殘留的 API 回應，搭配 `skipWaiting()`/`clients.claim()` 立即接管，登出流程額外做一次 `caches.delete()` 當縱深防禦（見 5.4）

**🟡 實作可測性與環境一致性（已修正）**
9. **`DeptScope.ALL` 沒有明確約束「不得加任何 department 相關過濾」**——日後有人加一個看似合理的 `.not.is.null` 防呆過濾，會把 1.5 節定義的孤兒資料從總管視野也一併藏掉，且不會被任何測試發現 → 新增 3.7 節明確約束，哨兵資料裡放入 `department IS NULL` 的孤兒列讓這件事可被驗證斷言，而非只靠註解防守；`feedback`/`alarm_views` 明確不加 `NOT NULL`（見 3.7、1.5）
10. **本機開發若讓 `JsonStore` 支援多部門過濾，會製造一條不會被真正驗證的第二實作路徑**——多租戶真正的風險點（PostgREST `eq` 語意、NULL 不匹配、`save()` 整表刪除掃描、`on_conflict`）在 `JsonStore` 裡完全不存在，在其上測通不代表任何保證 → `JsonStore` 維持單租戶（僅服務 pytest），本機開發改連獨立的 Supabase 開發專案，schema 相同、資料可隨意破壞（見 3.2）

**⚪ 文件一致性（已修正）**
11. `sentinel_pack` 的資料筆數與驗證項目數量，本計畫僅摘要工具設計當下的版本內容，執行前一律以 `sentinel_pack/README.md` 當時版本為準，避免套件更新後與本文件的具體數字（50 筆、T-01~T-10）產生落差誤導（見第 7 節步驟 6、8.2、附錄第 4 項）
12. `DepartmentStore.list()`/`get_by_id()` 的回傳欄位註解補齊 `hidden`/`purgeable`/`session_version`/`active`，與第四輪新增的欄位、`assert_session_valid()` 的依賴對齊（見 3.3）

---

第五輪審查（前端架構決策，來自使用者與專家共同確認）：

1. **前端會因超管跨部門功能大幅膨脹**——若照「每個列表/篩選器/新增對話框都加部門欄位」的方式做 → 抽出 `frontend/js/api.js` 統一封裝 fetch（401 攔截、429 倒數、whoami 快取），兩個 HTML 反而變薄；超管改採「切換檢視部門」模式取代跨部門混合列表，既有畫面不用逐一加部門欄位（見 5.1、5.2）
2. **檢視部門狀態存哪裡**——存 session 會造成分頁互相污染、同一 URL 回傳不同資料、違背整份規劃已建立的「拒絕隱性狀態」原則 → 改用 URL query param（`?dept=`）為唯一權威，`sessionStorage` 僅作重載後補位；`__all__` 必須是明確選擇而非參數遺失的預設值（見 5.2.1）
3. **`confirmed_by` 組法在超管情境下會產生 `"None/admin"`** → 改用寫入目標部門＋含 `superadmin` 的三態角色組成（見 5.2.2）
4. **不順便改前端架構**——刻意不藉這次機會拆前後台、上 build step、模組化，理由與「Render 部署先做完」同一條：不要同時改兩件大事（見第 5 節開頭）

第六輪審查（`resolve_target_department()` 來源清理、登入節流的枚舉防護與繞過修正）：

1. **`resolve_target_department()` 曾規劃「路徑或 body」兩個來源，造成集合端點（如 `POST /api/devices`）銜接點空白**——超管的目標部門該從哪裡填沒有定義清楚，容易兩邊各自猜一種做法 → 改為**所有寫入端點路徑都帶部門段**，`requested` 永遠只來自 URL path，沒有第二個來源；`scope_department()`／`resolve_target_department()`／`assert_session_valid()` 三個函式各自唯一的資訊來源徹底分開（見 4.1、4.4）
2. **「部門不存在」與「密碼錯誤」若耗時不同，會被拿來枚舉部門 id**——節流本身防不了枚舉，兩者是不同的防線 → 部門不存在時用 dummy hash 消耗與真實比對相同的時間，回應與密碼錯誤完全一致（見 2.2.2）
3. **`login_attempts` 的節流查詢用 `department` 當過濾條件，攻擊者只要每次換一個假部門 id，`N` 永遠是 0，細網形同虛設** → 加一層只看 IP、不看部門的粗網，取兩者最大值；門檻設 20 次，並解釋「共用 NAT」在此設計下因為「最近成功登入即歸零」的定義，反而不會被誤傷（見 2.2.3）
4. **寫入 `login_attempts` 本身若無節制，會變成放大攻擊的目標** → 三段式判斷：格式不合法直接拒絕不寫入、格式合法但部門不存在照常記錄並計入粗網、已在節流窗口內的請求只回 429 不再寫入（見 2.2.4）
5. **`login_attempts.department` 對不到 `departments` 表是刻意設計，不是資料品質問題**——若未來清理腳本誤把這些列當髒資料刪除，會連帶清掉唯一能看出「有人在探測部門 id」的證據 → 明確在建表 SQL 旁加註記（見 2.2.1 表定義）

---

第七輪審查（結構性錯誤修正＋文件內部一致性總校對）：

**🔴 會讓 8.1 測試第一天就失敗（已修正）**
1. **`ROUTE_AUTH_REGISTRY` 用 rule 字串當 key，但權限是按 HTTP method 分的**——`GET/PUT/DELETE /api/alarms/<department>/<device_model>/<code>` 這條路徑上 `GET` 是 `login`、`PUT`/`DELETE` 是 `admin`，但 `_auth_level` 標記掛在 view function 上，一個函式處理三種 method 物理上無法回傳不同標記 → 同一 rule 依 method 拆成多個 view function（Flask 原生支援），白名單 key 改為 `(rule, method)` 元組；此決定必須在寫 `app.py` 之前定案，事後拆分更麻煩（見 4.4、8.1）

**🟠 文件內部一致性錯誤（已修正）**
2. **第 5 節編號錯亂**——缺 5.4、5.6 排在 5.5 前面，且兩處交叉引用（「見 5.3 節」）實際指向錯誤章節 → 統一改回 5.1→5.2→5.2.1→5.2.2→5.3→5.4→5.5 的正確順序，修正全文交叉引用
3. **關鍵檔案清單漏三項**——`frontend/js/api.js`（5.1 新檔，所有前端改動的基礎）、Supabase 新表 `login_attempts`（2.2.1，只出現在內文未列入清單）、`inspect.getsource` 原始碼檢查（4.1 規則 a 的配套測試）→ 全部補入關鍵檔案清單
4. **待確認事項裡的 `--department` 參數歸屬錯誤**——原文暗示是遷移腳本的參數，實際是 `import_alarms.py` 的必填參數，兩支工具都需要部門名稱但用途不同 → 修正歸屬說明
5. **`sentinel_pack` 的 8.2 節提醒需要更新**——確認套件目前仍是 T-01~T-10／50 筆版本，尚未涵蓋本計畫第四~六輪新增的驗證項目（登入分岔、`?dept=` 越權、`DeptScope.ALL` 孤兒列驗證等）→ 明確提醒執行前需確認套件版本是否已跟上，不能只靠 `verify_isolation.sh` 全過就認定新機制已被驗證

**待辦（非本輪修正範圍，留待 EXECUTION_CHECKLIST 重新生成時一併吸收）**
6. `EXECUTION_CHECKLIST.md` 落後本計畫約三輪，尤其階段 4（密碼與登入安全）與階段 11（前端）內容嚴重過時，其中「超管新增機種/警報時加部門選擇欄位」與 5.2 節的切換檢視部門模式直接矛盾——**該檔案已於本輪之後整份重新生成**，不再逐條修補
7. 路由權限測試（原階段 13）應排在部署 `app.py`（原階段 10）之前而非之後，測試的把關意義才有效——已在重新生成的執行清單中調整順序

---

第八輪審查（部署窗口的可用性風險、前置階段缺漏）：

**🔴 部署窗口期間系統對使用者不可用，原清單沒有明確標註（已修正）**
1. **舊版 `app.py` 部署（原階段 11）與前端部署（原階段 13/14）之間存在一段「登入頁還沒改、但後端路由已經變了」的空窗**——`/login` 現在需要 `department` 欄位，舊版 `login.html` 不會送 → 登入直接失敗；`/api/alarms/<device_model>/<code>` 舊路由已不存在（改三段式）→ 舊版前台警報詳情、後台編輯全部 404。若中途收工，唯一使用部門會在這段期間完全無法使用系統 → **採方案 B：階段 11~14（app.py 部署 → 哨兵驗證 → 前端部署 → SW 手動驗證）視為同一個維護窗口，一次做完再收工**，不拆分成「先讓登入能用」的中間狀態——半殘可用比乾脆離線更容易引發誤操作或困惑。EXECUTION_CHECKLIST 附註需明確寫下這條「開始前先確認當天有足夠時間，不要中途收工」

**🟠 清單缺兩個前置階段（已修正）**
2. **Render 部署完全沒有出現在 EXECUTION_CHECKLIST 裡**——PLAN 第 54 行明確要求「多部門改造前先完成」，但清單直接從階段 0 開始，只在階段 7 順帶提到環境變數 → 新增階段 -0.5「Render 正式部署」，含環境變數搬遷、cookie 屬性設定、**SW 在 HTTPS 下的實際行為確認**（本機 HTTP 環境下 Service Worker 從未真正驗證過運作情形，而 5.4 節把它當安全機制在處理，這件事的可信度建立在「它真的有在 HTTPS 下跑過」）、worker 數量確認（影響 2.2.3 節流設計的前提假設）
3. **專案已是 git repo（`main` 分支、有 `origin` 遠端），但有一大批先前多次對話累積、從未 commit 過的變更**——若不處理就直接動工，階段 10/11「刻意分兩步部署以便精確回滾」的設計精神會失效（無法區隔「舊工作的問題」vs「這次隔離改動的問題」，出問題也無法用 git 做二分法定位）→ 新增階段 -1「建立乾淨的版本控制基準點」，**這批舊變更如何處理（先 commit 當基準、或先確認內容）留給專家判斷**，本計畫僅記錄此發現與待決策狀態，不預設答案

---

第九輪審查（git 基準點定案：commit 前置檢查、`.gitignore`、commit/tag 紀律）：

**決策：階段 -1 由「待專家決策」轉為「已定案」，採選項甲**——先 commit 這批舊變更當基準點，再開始隔離工程。

**🔴 commit 前必須先做的兩件事（已修正）**
1. **commit 之前要先確認現況真的能跑，不能直接把未驗證過的累積變更當基準**——這批變更是多次對話交織產生的，若夾雜半完成的東西（mock 模式忘了關、debug print、改到一半的函式），commit 成基準線等於把問題烘進基準裡，之後多部門改造出錯時分不清是新改的還是本來就壞的 → commit 前花 20 分鐘實測：拍照分析、警報搜尋、後台四個分頁、清理過期資料逐一跑過；已知半完成的部分三選一（改完/還原/照樣 commit 但訊息寫清楚），不默默帶過（見階段 -1）
2. **`.gitignore` 必須先於 `git add` 寫好，順序不能顛倒**——祕密一旦進了 git 歷史就很難清乾淨，比「commit 怎麼切」重要得多。專案的 `.env` 含 Supabase key、Gemini API key、`LOGIN_PASSWORD`、`ADMIN_PASSWORD`（之後還會加 `SUPERADMIN_PASSWORD`），即使目前不打算推上 GitHub 也不該進版本庫——Render 部署很可能會接 GitHub 遠端，屆時才處理就晚了。`data/backup/` 內有原廠文件 PDF，一旦進了 git 歷史就永遠留在裡面，體積只會增不會減 → `.gitignore` 排除 `.env`/`.env.local`/`data/`/`data/backup/`/`__pycache__/`/`*.pyc`/`.DS_Store`/`venv/`，並建立 `.env.example`（只列 key 名稱無值）一併 commit，供換機器/交接時參考（見階段 -1）

**🟠 commit 切分與往後紀律（已修正）**
3. **是否拆成多個 commit**——路徑好拆就拆（`backend/ai/`、`frontend/dashboard.html`、RWD 相關各一批），但不超過 15 分鐘成本。這些變更本來就是交織開發出來的，中間 commit 從未被單獨驗證過能不能跑，硬拆出來的中間狀態很可能根本啟動不了，`git bisect`（拆分 commit 的主要價值）在這裡沒有意義——拆分的價值只剩「文件性質」，commit message 寫清楚就達成同樣效果，不必為此耗費太多手工（見階段 -1）
4. **【與階段 10/11 直接連動的關鍵前提】`storage.py` 的改動和 `app.py` 的改動必須落在不同 commit**——階段 10/11 刻意分兩步部署（先只上 `storage.py` 驗證行為不變，再上 `app.py`），這個設計的前提是能夠部署到「只有 `storage.py` 生效」的中間點；若兩者混在同一個 commit，就沒有這個中間點可以部署，整個分階段部署的安全設計形同虛設 → 明確要求兩者分開 commit（見階段 -1、階段 10）
5. **往後的 commit/tag 紀律**——每個階段結束 commit 一次（訊息帶階段編號，如 `stage 5: storage.py 部門過濾與 DepartmentStore`）；每個部署點打 tag（`deploy-stage10-storage`、`deploy-stage11-app`），部署時明確指定 tag、不用「當前 main」；**不開 branch**——單人操作、且階段中途要部署，開 branch 只會讓部署變成「還要先 merge」，反而增加出錯機會，直接在 main 上做、靠 tag 標記部署點即可。這樣階段 11 若出問題，回滾就是部署 `deploy-stage10-storage` tag，一行指令的事（見階段 -1、階段 10、階段 11）

---

第十輪審查（維護窗口內的小缺口：階段 14 的 B 帳號無處可登入）：

**🟡 階段 14 的跨帳號測試在哨兵部門身上做不了（已修正）**
1. **`sentinel_pack` 的哨兵部門 `hidden=true` 是刻意設計（見 1.1、8.2）**，目的是讓它能登入但不出現在正式登入頁下拉選單裡，避免正式使用者看到陌生部門名稱產生疑惑。但這與 5.5 節（4.7 節路由對應）的 5.5 節 SW 驗證步驟 5「登出 A、登入 B」直接衝突——`/api/departments/public` 只回傳 `hidden=false` 的部門，哨兵部門不會出現在選單裡，維護窗口這個時間點又還沒有真實的第二部門可用，B 帳號無處可登入 → **採方案一**：測試前執行 `UPDATE departments SET hidden=false WHERE id='<哨兵部門id>'` 暫時讓哨兵部門出現，測完立即改回 `hidden=true`。比起把跨帳號測試延到階段 16（真實第二部門上線後）才做，這個做法不用調整維護窗口的結束點，也不需要在登入頁加一個保留的手動輸入部門 id 入口（會增加一個平常用不到、但長期存在的攻擊面）

---

第十一輪審查（首次針對正式 Supabase schema 實測，`00_preflight_check.sql` 揭露的落差）：

**背景**：階段 -1（git 基準點）完成後，換上 `sentinel_pack` v3，執行其中的 `00_preflight_check.sql`（純唯讀，PLAN 1.7 節要求的遷移前預檢）——這是整份計畫從第一輪到第十輪以來，**第一次真正對正式 Supabase 資料庫查詢，而非憑推測撰寫 SQL**。結果揭露一個此前十輪審查都沒抓到的落差。

**🔴 `devices` 表實際欄位名與 PLAN 全文假設不符（已修正）**
1. **`devices` 表的型號欄位實際叫 `model`，不是 PLAN 全文統一使用的 `device_model`**（`alarms` 表則確實是 `device_model`，兩表命名本來就不同，非本次疏漏）。`\d devices` 顯示：`id text PRIMARY KEY`、`model text UNIQUE`、`category text`、`line text`。這代表 1.2 節先前所有 SQL 範例、3.1 節 `on_conflict` 參數、4.4 節路由設計裡凡是提到 `devices.device_model` 的地方，直接照抄執行都會因欄位不存在而報錯 → **決策：不改資料庫欄位名，PLAN 改用實際欄位名**——這是本次工程唯一不會碰觸正式表結構的選項，符合「只動安全邊界、不做順手清理」的既有原則（1.2 節新增完整說明）；新增 `_row_to_device()` 作為整個系統唯一知道 `model`/`device_model` 對應關係的轉換點（見 3.1.1 節），避免命名不一致擴散到十幾個呼叫點；順帶查明現有 `app.py` 的機種端點目前就是用 `model` 這個 key 對外回應，代表引入正規化會是一個**改變現有 API 回應格式的行為變更**，需在階段 5 動手前同步檢查前端

**🟢 附帶的好消息：`devices` 主鍵切換比原先假設輕（已修正）**
2. **原先 1.2/1.3 節的「情況 A / 情況 B」二選一假設，實際是介於兩者之間的混合型**——主鍵 `id` 是獨立代理鍵（如 `M-201`），與型號完全脫鉤；另外查明 `id` 不是型號本身，代表新機種只需給一個新 `id` 即可安全建立，不需要像原本擔心的那樣額外設計 id 產生規則 → 階段 3 的 `devices` 部分只需要 drop 舊 `UNIQUE(model)` 約束、建立 `(department, model)` 複合唯一約束兩行 SQL，主鍵完全不用動，比 PLAN 先前預期的工作量小（見 1.2 節）

**🟡 `devices.line` 欄位一度疑似隱藏的跨部門資料，已排除（已修正）**
3. **`00_preflight_check.sql` 順帶查出 `devices.line` 欄位分成 `2.1`／`2.2` 兩組各 7 筆**，一度懷疑現行系統其實已經混著兩個部門的資料在跑，而多部門隔離工程的必要性被低估了。**已與使用者確認：`line` 只是同一使用單位內部的產線分類標記，不是不同的登入/權限邊界**，不作為部門拆分依據 → 明確記錄 `department`（權限與隔離邊界）與 `line`（部門內產線分類）的關係，避免日後誤認兩者是同一件事（見 1.2 節）

**待辦（列入階段 12 執行前提醒，非本輪修正範圍）**
4. `sentinel_pack` v3 的 `01_seed_sentinel.sql` 目前假設 `devices` 表有 `device_model` 欄位，依本輪結論需要改用 `model`，且 `id` 欄位需明確給值（無預設值）——執行階段 12 前需要先核對 `01_seed_sentinel.sql` 是否已依這次查明的 schema 更新，若尚未更新，需要在灌入哨兵資料前手動修正這段 INSERT

---

第十二輪審查（部署平台決策反轉：Railway → Render，成本考量）：

**決策：部署平台由 Railway 改為 Render**——原因是 Railway 的免費額度已不足以支撐長期正式運作，需要付費方案，而 `git log` 早先就已顯示這個專案原本就是接 Render（`cron-job.org` 防止 Render 休眠的既有機制），改回 Render 是回到原本的臨時方案並轉正，不是全新嘗試一個平台。

**處理方式**：PLAN 與 EXECUTION_CHECKLIST 全文所有「Railway」字樣已統一置換為「Render」（階段 -0.5 標題、環境變數搬遷說明、worker 數量確認、`FLASK_SECRET_KEY` 固定值要求、cookie 設定前提、`.gitignore` 的 GitHub 遠端說明等）。這些引用全部是平台無關的通用部署概念（環境變數、cookie 屬性、worker pool 大小、固定 secret key），沒有 Railway 專屬語法混在文字說明裡，置換後內容邏輯不變、無需另外改寫論述。

**已捨棄的產物**：先前已建立的 `railway.toml`（Nixpackage build/start command、健康檢查路徑設定）已刪除，未進入 git 版本控制，不留殘跡。

**決策：沿用免費層 + `cron-job.org` 防休眠**，不升級付費層。理由：這是專案本來就在用的既有方案（`git log` 可見 `0ac91de 新增 /ping 端點供 cron-job.org 防止 Render 休眠`），多部門上線並不改變流量規模到需要付費層的程度，維持零額外成本；已知取捨是防休眠 cron 並非 100% 保證，偶爾仍可能遇到喚醒延遲，這點不視為需要立即解決的問題，先上線觀察，若後續使用者實際反應延遲造成困擾，再重新評估升級付費層（不在本次多部門隔離工程範圍內預先解決）。

**待辦（列入階段 -0.5 執行前提醒）**：
- 確認既有的 `/ping` 端點（`backend/app.py`，`0ac91de` commit 加入）在 Render 部署後仍正常運作，`cron-job.org` 的排程設定需要指向新的 Render 網域（若網域跟先前的臨時部署不同）
- Render 的部署設定檔（`render.yaml` 或直接在 Render Dashboard 設定 build/start command）尚未建立，需要在階段 -0.5 實際操作時建立，取代原本規劃但已刪除的 `railway.toml`
- `_lan_ip()`／`RENDER_EXTERNAL_URL` 相關的既有程式碼（`backend/app.py` 的 `/api/server-url` 端點）已經有 Render 環境變數的處理邏輯，這代表接回 Render 比接 Railway 更貼合現有程式碼、改動更少——這是決策反轉後意外發現的優點，值得記錄

---

第十三輪審查（Render 實際部署驗證中發現的既有 bug，非本次隔離工程引入）：

**背景**：階段 -0.5 建立 `render.yaml` 並實際部署到 Render 後，逐項驗證功能時發現兩個問題，過程本身印證了「不要同時改兩件大事」原則的價值——若沒有先在乾淨環境（Render）跑一次，這些既有問題會被本機混雜的開發環境長期掩蓋，直到多部門功能上線後才會第一次浮現，那時候更難定位是新改動還是舊 bug。

**🔴 環境變數貼值錯誤（已修正，操作失誤非程式問題）**
1. **`SUPABASE_KEY` 貼到 Render Value 欄位時，把 `SUPABASE_KEY=` 這個變數名稱前綴也一併貼了進去**，導致實際送給 Supabase 的認證字串變成 `SUPABASE_KEY=eyJhbGc...`，Supabase 端完全無法辨識這把 key，`/api/devices`、`/api/alarms` 等所有走 `SupabaseStore` 的端點回 500，`urllib.error.HTTPError: HTTP Error 401: Unauthorized`（Render Logs 可見完整 traceback）→ 使用者重新只複製 JWT 值本身（不含變數名稱前綴）貼回 Value 欄位後解決。這個錯誤模式值得記住：Render／多數 PaaS 的環境變數輸入是「Key 獨立欄位＋Value 獨立欄位」兩格分離設計，貼值時只該貼 Value 部分

**🔴 依賴套件與程式碼不匹配（已修正，既有 bug，Render 環境下首次曝光）**
2. **`requirements.txt` 寫的是舊版 SDK `google-generativeai`，但 `backend/ai/ai_analyzer.py` 程式碼實際用的是新版 SDK 語法 `from google import genai`（屬於 `google-genai` 這個不同的 PyPI 套件）**——兩者 import 路徑不相容，新版套件沒有提供 `google.genai` 這個命名空間路徑給舊版寫法用，反之亦然。

   **本機為何沒發現**：檢查本機 `.venv` 發現兩個套件都被裝著（`google-genai==1.47.0` 與 `google-generativeai==0.8.6` 並存，推測是先前手動額外安裝新版套件時沒有同步移除舊版、也沒有同步更新 `requirements.txt`），讓這個不匹配長期被本機環境掩蓋。加上「拍照分析」這條 code path 因為相機需要 HTTPS，一直是計畫裡明確標註延後到 Render 部署後才補測的項目（見階段 -1 實測記錄），所以這是這條 code path 自新版 SDK 改寫以來**第一次在乾淨環境下真正被執行到**。

   **如何發現**：不透過瀏覽器（無法操作相機拍照），改用 `curl`／Python `urllib` 直接對 `POST /api/analyze` 送出一張測試圖片的 base64 payload，繞開前端 UI 直接測後端邏輯，取得明確錯誤訊息 `AI 模組未安裝：cannot import name 'genai' from 'google' (unknown location)`（`app.py` 的 `except ImportError as e: abort(503, ...)` 分支）——這個明確的 503 錯誤幫助排除了「AI 辨識判斷邏輯有問題」的懷疑方向，直接定位到套件安裝層級。

   **修正**：`requirements.txt` 的 `google-generativeai>=0.7` 改為 `google-genai>=1.0`（版本號比照本機 `.venv` 裡已驗證能正常運作的 `1.47.0`），與程式碼實際使用的 import 路徑一致。

   **【已驗證，修正生效】** commit、push 後 Render 自動重新部署，直接 API 測試（空白測試圖）確認 503 消失、回 200 且 Gemini 真的被呼叫成功（回應含 `analyzer.model`、`scan_id` 等完整欄位，`ERR_MODEL_UNKNOWN` 是測試圖本身無內容可辨識的正常結果，不是套件問題）；使用者接著用實機拍攝真實警報畫面測試，AI 辨識正常運作，確認端到端修復完整生效

**🟡 管線設計本身的可觀察性缺口（記錄，非本輪修正範圍，留待後續評估）**
3. **`ai_pipeline.py` 的 `run_pipeline()` 刻意把 Analyzer 層的例外全部吞掉，統一降級回傳 `tier: "failure"` 且 `alarms: []`**（見 `ai_pipeline.py` 第 61-97 行的 try/except），設計初衷是「不要讓 AI 服務的暫時性問題導致整個請求 500」，這個取捨本身合理。但目前前端（`index.html`）若沒有特別檢查 `tier === "failure"` 並顯示對應的錯誤訊息，`pipeline_error` 這種**系統性故障**（例如這次的套件缺失、API key 失效、額度用盡）跟 `tier: "no_alarm"` 這種**正常結果**（畫面上真的沒有警報）在使用者眼中會長得一模一樣，都顯示「未偵測到警報」——這正是這次問題一開始難以判斷方向的原因。是否要在前端明確區分這兩種情況（例如 `failure` 顯示「AI 服務暫時無法使用，請稍後再試或聯繫管理員」），不在本次多部門隔離工程範圍內，但值得記錄下來供後續獨立評估，尤其正式推廣給第二部門後，這種「看起來像沒警報、其實是服務故障」的情況會更難被工廠現場人員自行判斷

**階段 -0.5 剩餘項目完成記錄**：
4. **Cookie 屬性設定**——`app.py` 的 `create_app()` 過去完全沒有明確設定 session cookie 屬性，全靠 Flask 預設值。新增三個設定：`SESSION_COOKIE_HTTPONLY=True`、`SESSION_COOKIE_SAMESITE="Lax"` 為固定值；`SESSION_COOKIE_SECURE` 則依 `RENDER_EXTERNAL_URL` 是否存在動態開關——這個環境變數只有部署在 Render 時才會被自動注入，本機 HTTP 開發環境維持 `False`（若無條件設為 `True`，本機開發環境的 cookie 會因為非 HTTPS 而傳不出去，直接讓登入失效）。57 個既有 pytest 測試全數通過，確認未破壞既有行為。
5. **Render worker 數量確認**——部署 log 明確顯示 `Setting WEB_CONCURRENCY=1 by default, based on available CPUs in the instance`，免費層預設就是單一 worker。這是 2.2.3 節粗細節流設計「共用 IP 不會被誤傷」推論的前提假設之一，本機測試環境同樣通常是單一 worker（開發伺服器預設），數量差異不大，暫不需要重新校準粗網門檻（20 次），但正式上線有多部門真實流量後應持續觀察。

---

第十四輪審查（部門正式名稱定案：id/name 分工避免格式驗證衝突）：

**背景**：階段 -0.5 完成後，動工階段 0 前的唯一阻塞項——確認部門正式名稱。使用者最初提出兩個選項，都會與既有設計衝突。

**🔴 差點踩到的坑：大寫或中文 id 會讓真實部門完全無法登入（已避免，非既有 bug）**
1. **使用者一開始想用大寫 `MF4D`，接著改問「如果用中文呢」**——但 2.2.4 節定義的 `DEPT_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")` 只允許小寫英數字與底線，是登入節流三段式判斷（見 2.2.4 節）的第一道格式檢查：格式不合法的部門 id 會被直接判定為「不查 DB、不寫入、直接視同密碼錯誤回應」。若真的把大寫或中文字串設成 `departments.id`，這個部門自己的每一次登入都會先被這道格式檢查擋下——等於**唯一真實部門從第一天起完全無法登入**，而且錯誤表現得跟「打錯密碼」一模一樣，難以第一時間判斷是系統設計問題還是使用者輸入問題。

   **解法**：重申並套用 PLAN 從第一輪就存在的既有設計原則——`departments.id` 是建立後不可改的技術用鍵（給 URL 路徑、DB 查詢、regex 驗證、log 記錄用，必須是安全的 ASCII slug），`departments.name` 才是給人看的顯示名稱（可改、可以是任何語言文字）。這個 id/name 分工不是這輪新發明的規則，是 1.1 節建表時就定義好的兩個獨立欄位；這次只是把它具體套用到使用者的真實部門上。

   **最終定案**：`id = "mf4d"`（小寫 ASCII，符合 `DEPT_ID_RE`，且是使用者原本要求的 `MF4D` 唯一合法轉寫方式）、`name = "製造四部包裝組"`（中文顯示名稱）。

**判斷分歧點（已與使用者討論，非新增審查發現）**：使用者一度詢問「這個問題有沒有風險性，要不要問專家」——判斷為不需要，因為最終方案是直接套用文件裡已經定案的既有規則（1.1 節的 id/name 分工、2.2.4 節的格式驗證），不是臨時發明的新設計決策，不需要額外一輪外部審查。留待階段 1~9 實作完成後再送一次完整審查會更有效率。

**影響範圍**：`migrate_add_departments.py`（階段 2）建立第一筆 `departments` 資料列時，`id` 欄位填 `mf4d`、`name` 欄位填「製造四部包裝組」；`import_alarms.py`（第 6 節）日後若要匯入其他部門，`--department <id>` 參數同樣必須遵守小寫 ASCII slug 規則，這點應寫進工具的說明文字或 `--help` 輸出，避免下一個部門上線時重蹈覆轍。

---

第十五輪審查（遷移前備份策略定案，取代原先的二選一構想）：

**背景**：階段 0 最後一項阻塞——遷移前如何備份現有正式資料（1759 筆警報、14 筆機種）。使用者對「Dashboard 快照 vs CSV 匯出」兩個選項都表示無法判斷，選擇「我問專家」，交由外部專家評估後給出完整方案，不是原本設想的二選一。

**🔴 原設計的疏漏（已修正）**
1. **1.6 節原版本只考慮 `alarms`/`devices` 兩張表的備份，且只給「快照或 CSV 擇一」的簡化選項**——沒有涵蓋 `feedback`/`ai_scans`/`ai_corrections`/`ai_logs`/`alarm_history`/`alarm_views` 等表，也沒有定義「如果這一步做錯了要怎麼退回」的具體機制 → 專家給出六步驟完整流程並定案採納，取代原本的簡化版本（見 1.6 節）：
   1. 查清 `alarms` 主鍵實際名稱（不能只憑本計畫假設）
   2. 先寫好 `rollback_stage3.sql`（動手前就要有，不是出事才寫）
   3. `pg_dump -Fc` 完整備份並驗證可還原（`pg_restore -l` 確認非空非損毀）
   4. Supabase Dashboard 內建備份確認，作為額外保險、不當主力
   5. 正向遷移腳本全部包在 `BEGIN`/`COMMIT` 交易內
   6. 執行後重跑 `00_preflight_check.sql` 驗收

**🟢 兩個容易被誤解的地方，專家後續追加澄清（已納入）**
2. **CSV 匯出（原選項 B）不需要再另外做**——`pg_dump -Fc` 完整涵蓋 CSV 匯出想達成的目的且做得更完整（涵蓋全部表、格式更完整），兩者不必重複執行，選項 B 正式併入選項 A（見 1.6 節「已完全取代」註記）
3. **兩層安全網有明確的優先順序，但不代表次要的那層可以省略**——`rollback_stage3.sql`＋交易包裹（步驟 2、5）是處理「這一步做錯了」的第一線，日常操作出錯走這裡；`pg_dump`＋Dashboard 備份（步驟 3、4）是「整個專案出事」時的最後保險，只有前者失效或問題超出遷移範圍才會用到。專家特別追加澄清：這個排序不是「備份可有可無」的意思，六個步驟仍要全部做完才能進入下一階段（見 1.6 節「兩層安全網的定位」）

**影響範圍**：階段 0 的執行清單需要把原本的「Dashboard 快照或 CSV 匯出」單一勾選項，展開成六個獨立步驟；階段 2（`migrate_add_departments.py`）與階段 3（主鍵/約束切換）都必須包在同一組 `BEGIN`/`COMMIT` 交易語意下執行（原本兩者是否需要交易包裹沒有明確規定，這輪定案為必須）；新增 `rollback_stage3.sql` 作為版本控制內的新檔案，需在階段 3 動手前就寫好並 commit。

---

第十六輪審查（Supabase 免費方案無內建備份，兩層安全網的第二層實際不存在）：

**背景**：完成 1.6 節六步驟中的步驟 1~3（查證約束名稱、`rollback_stage3.sql`、`pg_dump` 完整備份並驗證可還原）後，執行步驟 4（到 Dashboard 確認內建備份能力）。

**🔴 發現：免費方案完全沒有內建備份能力（已記錄，尚未決定是否需要再問一次專家）**
1. **Supabase Dashboard 的「Scheduled backups」頁面明確顯示**：`Free Plan does not include project backups. Upgrade to the Pro Plan for up to 7 days of scheduled backups.`——不只排程備份，`Point in Time Restore`、`Restore to new project` 這兩個功能同樣要付費方案才有。也就是說，這個專案目前的 Supabase 免費方案**沒有任何官方託管的還原路徑**。

   **這件事動搖了什麼**：第十五輪整合的專家方案，其「兩層安全網」設計是「`rollback_stage3.sql`＋交易包裹」處理日常出錯、「`pg_dump`＋Dashboard 備份」處理災難情境。第二層原本預期是兩個獨立、互補的保險（一個是我們自己做的邏輯備份，一個是 Supabase 官方的自動快照）。查證後發現第二層裡的「Dashboard 備份」這個子項**實際上不存在**，第二層目前只剩 `pg_dump` 單獨扛著。換句話說，整個遷移工程現在只有「`rollback_stage3.sql`＋交易包裹」與「`pg_dump`」兩道防線，而不是原本設想的三道（rollback、交易、Dashboard 快照）。

   **目前判斷**：不視為阻塞項——`pg_dump` 本身已驗證可完整還原全部 8 張目標表，加上 `rollback_stage3.sql`＋交易包裹是精準對應「這次遷移」的第一線防護，兩者對階段 1~3（加欄位、遷移腳本、約束切換）這種規模的操作應屬足夠。但這是原方案的一個前提假設落空，記錄下來，是否要讓最初給出六步驟方案的專家知道這個落差、請他確認「少了 Dashboard 備份這層是否仍可接受目前的兩道防線」，留給使用者決定。

**影響範圍**：1.6 節步驟 4 的敘述需要補充查證結果（免費方案無此功能），提醒往後任何人重讀這份計畫時不要誤以為 Dashboard 備份真的有在運作。若日後帳號升級到 Pro 方案，這一項的結論會需要重新確認。

---

第十七輪審查（專家校正「兩層」定義，並補一個具體行動點與三個操作提醒）：

**背景**：使用者將第十六輪的發現拿去問給出六步驟方案的專家，得到回覆：「夠，可以往下走」，但先指出第十六輪的解讀有誤，並補充具體建議。

**🟢 校正：第十六輪誤解了「兩層」的邊界（已修正）**
1. **第十六輪把「少了 Dashboard 備份」解讀成「兩層安全網少了一層」，這是誤解**——專家釐清實際分層並非「`rollback_stage3.sql` vs `pg_dump`」，也不是「`pg_dump` vs Dashboard 備份」三者並列，而是兩層：第一層是 `BEGIN`/`COMMIT` 交易包裹＋`rollback_stage3.sql`（防「遷移失敗要退回」）；第二層是 `pg_dump`，**Dashboard 備份只是第二層的第二份拷貝**，不是獨立的一層（防「整個專案損毀」）。免費方案沒有 Dashboard 備份，代表第二層少了一份備援副本，不是少了一整層防護 → 1.6 節步驟 4 的說明改寫，明確標註兩層的正確邊界（見 1.6 節「分層防護的正確理解」）

**🟢 為什麼這批改動足夠（專家補充論證，已納入）**
2. **階段 1~3 幾乎不涉及資料遺失風險**——加 nullable 欄位、切換唯一約束/主鍵都是純 DDL，不動任何既有資料列，出錯用 `rollback_stage3.sql` 兩秒鐘還原；唯一真正寫入資料的動作是階段 2 的回填（`UPDATE ... SET department = ...`），但寫的是剛加出來的空欄位，不會覆蓋任何既有內容。「資料遺失」這個情境在這批改動裡幾乎不存在，真正可能出錯的是 schema 進入非預期狀態，而那正是第一層在防的東西 → 這個論證解釋了為何不需要為了補 Dashboard 備份這個缺口而升級付費方案（見 1.6 節「分層防護的正確理解」）

**🟡 新增具體行動點（已納入）**
3. **階段 2 回填完成、驗證無 NULL 之後，應再補一次 `pg_dump`**——原本只在遷移前做一次，但如果部門對應在回填時填錯（例如未來多部門並存時 slug 弄混），`rollback_stage3.sql` 救不了這個情境，它只還原 schema、不還原被覆蓋的資料值。回填後立刻補一次 dump，就有「遷移前」「回填後」兩個乾淨檢查點，階段 3 出事時可精準退回較近的點，不用整個遷移重跑 → 新增為 1.6 節步驟 7（見 1.6 節）

**🟡 三個操作面提醒（已納入，非結構性變更）**
4. `pg_dump` 檔案應搬到 Supabase 帳號體系以外的地方存放（外接硬碟／雲端硬碟），放在專案資料夾內即使被 `.gitignore` 排除也是常見疏忽——本機硬碟故障會連同專案資料夾一併遺失
5. 免費層 Supabase 專案閒置約一週會被自動暫停，若 `cron-job.org` 只打 `/ping` 而該端點不觸及資料庫查詢，Supabase 端仍可能被判定閒置——記錄下來，若日後出現「Render 存活但查詢全失敗」的情況，第一步應是檢查 Supabase 專案是否被暫停，而非優先懷疑程式碼
6. 415 個 TOC 項目、8 張表資料/約束/索引齊全的驗證方式本身正確，多數人執行 `pg_dump` 後就不再做 `pg_restore -l` 查驗，這次的做法值得保持

**影響範圍**：1.6 節新增步驟 7（回填後二次備份），需在實際執行 `migrate_add_departments.py` 遷移腳本時排進流程；`.env.example`／README 或個人筆記層級（非本計畫範圍）應提醒把 `pg_dump` 檔案定期搬出本機/專案目錄。

---

第十八輪審查（階段 1/2/3 遷移腳本實作，`werkzeug` scrypt 環境限制）：

**背景**：備份策略定案後，開始撰寫階段 1（加欄位/建表）、階段 2（遷移腳本）、階段 3（約束切換）的實際 SQL，過程中發現一個環境限制與一個前端影響範圍比預期大的問題。

**🟡 環境限制：本機與 Render 皆為 Python 3.9，`hashlib` 無 `scrypt` 支援（已修正）**
1. **`werkzeug.security.generate_password_hash()` 預設演算法是 `scrypt`**，但呼叫時直接噴 `AttributeError: module 'hashlib' has no attribute 'scrypt'`——查證 `.venv/bin/python3 --version` 與系統 `python3 --version` 皆為 3.9.6，這個 Python 版本的 `hashlib` 缺少 OpenSSL 的 scrypt 編譯支援，不是套件版本問題。由於本機與 Render 部署用的是同一份 `requirements.txt`／同一套 Python 版本策略，這個限制會持續存在，不只是本機一次性問題 → 修正為所有 `generate_password_hash()` 呼叫**明確指定 `method="pbkdf2:sha256"`**，不依賴預設值。已在新增的 `backend/gen_department_hashes.py` 套用，日後 `DepartmentStore.create()`／`update_password()`（階段 5 待實作）與任何呼叫 `generate_password_hash`/`check_password_hash` 的地方都要遵守同一約定，否則雜湊格式不一致會導致部分部門密碼比對失敗

**🟢 階段 1/2/3 遷移腳本已撰寫完成（待使用者確認後執行）**
2. 新增 `backend/migrations/001_add_department_columns.sql`——建立 `departments`／`login_attempts` 表，`devices`/`alarms`/其餘 6 張表加 `department` 欄位＋索引，全部 nullable，包在 `BEGIN`/`COMMIT`
3. 新增 `backend/migrations/002_migrate_add_departments.sql`——建立第一筆部門（`id=mf4d`、`name=製造四部包裝組`，密碼雜湊沿用現有 `.env` 的 `LOGIN_PASSWORD`/`ADMIN_PASSWORD`），回填 `devices`/`alarms`/`feedback`/`alarm_views` 的 `department`，含驗收用的 NULL 計數查詢
4. 新增 `backend/migrations/003_switch_constraints.sql`——收緊 `devices`/`alarms` 的 `department` 為 `NOT NULL`，切換 `devices_model_key` → `devices_dept_model_key unique (department, model)`、`alarms_pkey` → `primary key (department, device_model, code)`，約束命名與 `rollback_stage3.sql` 保持一致（已交叉核對）
5. 新增 `backend/gen_department_hashes.py`——讀取現有 `.env` 密碼，用 `pbkdf2:sha256` 產生雜湊供貼入 002 腳本，僅印在終端機、不寫入任何檔案
6. `backend/requirements.txt` 補上 `werkzeug>=3.0`（PLAN 第 2 節原已規劃，此輪正式加入依賴清單）

**影響範圍**：三支遷移腳本尚未實際對正式庫執行，需使用者過目 SQL 內容後才動手；`gen_department_hashes.py` 產出的雜湊已手動貼入 002 腳本的插入語句，往後若密碼變更需重新產生並更新腳本（一次性遷移腳本，非長期運行程式碼）。

**【執行更新】001 已於使用者確認後執行完成**：`BEGIN`~`COMMIT` 全部指令成功，驗證 9 張表皆已取得 `department` 欄位、`departments`／`login_attempts` 兩張新表結構與外鍵（`alarms_department_fkey`、`devices_department_fkey` 指向 `departments(id)`）皆符合預期。002、003 尚未執行。

---

第十九輪審查（`_row_to_device()` 從「過渡期」改為「永久雙 key」定案，`devices.line` 排除邏輯確認）：

**背景**：使用者將第十八輪整理的兩個問題（`devices.line` 排除邏輯是否嚴謹、`_row_to_device()` 前端影響範圍的處理方式）連同實測數據（前端 76 處引用、`.model`/`.device_model` 混用）一併問給專家，得到完整定案回覆。

**🟢 問題一：`devices.line` 排除邏輯確認合理，補上正式定義（已納入）**
1. **7/7 對半分佈本身是中性證據，不能拿來判斷是否為部門邊界**——兩個部門各七台會呈現同樣的分佈，資料形狀推不出答案。真正的判準是三個業務問題：「是否同一群使用者」「是否互相查對方機種的警報」「未來是否會限制只能看其中一條線」，三者答案一致指向「不是部門邊界」，比任何資料推論可靠 → 1.2 節補上正式定義句：「`department` 是權限與資料隔離的邊界；`line`（2.1／2.2）是部門內部的產線分類，同一部門可有多條線。兩者是不同維度，`line` 不參與任何權限判斷」
2. **「問系統擁有者」被專家確認為正確做法**——這類問題最終是業務語意問題，資料本身推不出正確答案；若當時問不到人，退而求其次的間接佐證是查警報代碼是否跨 `line` 重疊（重疊代表同一套設備體系），但已有直接答案時間接證據不必要
3. **附帶收穫**：`line` 可作為階段 2 回填後的交叉驗證維度——`select department, line, count(*) from devices group by 1, 2 order by 1, 2` 預期每個 `department` 對應到 `2.1`／`2.2` 兩列、合計 14 筆，出現非預期組合即代表回填腳本有問題，比單純檢查 `department IS NULL` 多一個交叉維度、成本是零

**🔴 問題二：`_row_to_device()` 從「過渡期雙 key」改為「永久雙 key」，前端 76 處確定不動（已修正第十一輪的預設方向）**
4. **第十一輪的「過渡期兩個 key 都給」暗示之後要收斂成單一 key，第十九輪推翻這個預設**——原因：(a) 改動時機最不該加碼，76 處會落在已經很滿的階段 13（前端改動）維護窗口內；(b) 錯誤是沉默的，`.model`／`.device_model` 混用、判斷錯了只會顯示空白或 `undefined`，不拋錯不進 log；(c) 收益是零，純粹外觀一致不修 bug 不加功能，與「不改 `devices.model` 資料庫欄位名」是同一條原則的延伸 → 定案為永久雙 key 設計，`_row_to_device()` 同時回傳 `model` 與 `device_model` 兩個 key 指向同一個值（見 3.1.1 節）
5. **新增三條配套規則**：轉換只能在 `_row_to_device()` 一處發生（不得讓 `row["model"]` 漏到 `app.py`）；寫入路徑對稱處理（`POST`/`PUT` body 兩個 key 都接受，寫入時統一轉成 `model`）；新程式碼一律用 `device_model`，既有 76 處不動，讓不一致範圍隨時間自然縮小（見 3.1.1 節）
6. **路由參數名 `<device_model>` 確認維持不變**——這是 API 層邏輯命名，不要求與 DB 欄位名一致，8.1 節白名單不受影響（見 4.4 節新增註記）
7. **未來若真要收斂，需單獨排一次**——時機是多部門穩定運作後、且 `js/api.js` 已抽出，屆時後端收斂動作只是刪掉 `_row_to_device()` 裡的 `"model": model` 一行，這正是「轉換只能在一個函式發生」這條規則的價值（非本次範圍，記錄供日後參考）

**共同邏輯（專家原話，記錄供日後類似判斷參考）**：兩題本質是同一件事——要不要為了「外觀乾淨」去動已經在運作的東西。`line` 長得像部門不代表要改設計；`model`/`device_model` 名字不一致不代表要為了一致去動 76 處前端。兩題答案都是「不動」，理由相同：外觀問題與實質風險不對等，這與先前「PLAN 改用實際欄位名、不改資料庫」是同一條原則的延伸。

**影響範圍**：1.2 節新增 `department`/`line` 定義句；3.1.1 節整段改寫為永久雙 key 設計＋三條配套規則；4.4 節新增路由參數命名說明；階段 12（`sentinel_pack` seed 腳本）與階段 3（devices 唯一約束切換）的內容原本就已對齊 `department, model` 複合鍵，此輪未發現需要額外修改之處，僅 3.1.1 節整段更新。

---

第二十輪審查（`storage.py` 完整改寫並用真實 Supabase 連線逐項實測，發現一個實作細節缺口）：

**背景**：階段 5 依 PLAN 第 3 節完整改寫 `backend/storage.py`（`department` 參數化、`_row_to_device()`／`_device_payload_to_row()` 雙 key 轉換、`upsert_one()`/`delete_one()`、`DepartmentStore`、`save()` 部門過濾），一次寫完整份檔案。由於這些改動大量涉及 `SupabaseStore`（走 PostgREST），pytest 只覆蓋 `JsonStore` 路徑，因此額外用 `.venv/bin/python3` 直接對真實 Supabase 連線逐項手動驗證，過程中發現一個實作疏漏。

**🟡 `upsert_one()` 首次實作遺漏 `id` 欄位傳遞（已修正，非設計缺陷）**
1. **`_device_payload_to_row()` 原本是為 `POST /api/devices` 的 body 轉換設計的**（body 不含 `id`，`id` 由伺服器端生成），但 `upsert_one()` 直接複用這個函式時，沒有把呼叫端傳入的 `id` 值保留下來，導致第一次測試 `devices_store.upsert_one()` 時撞上 `devices.id` 的 `NOT NULL` 約束（`23502` 錯誤）。修正為 `upsert_one()` 內部轉換後，若原始 `item` 含 `id`，明確保留該值，不依賴 `_device_payload_to_row()` 的通用轉換覆蓋掉呼叫端指定的主鍵 → 修正後用臨時測試資料重新驗證，`upsert_one()`／`delete_one()` 對 `devices`／`alarms` 皆正常運作，無殘留資料

**🟢 完整改寫並實測通過的項目**
2. `_row_to_device()` 永久雙 key：14 筆真實 `devices` 資料驗證 `model`/`device_model` 值一致
3. `upsert_one()`/`delete_one()`：`devices`（`on_conflict=department,model`）與 `alarms`（`on_conflict=department,device_model,code`）皆用真實寫入/刪除測試，資料無殘留
4. `save()` 部門過濾修正：建立臨時測試部門 `zztest`（`purgeable=true`），驗證 `save([], department='zztest')` 只刪除 `zztest` 部門資料，`mf4d` 14 筆完全不受影響——直接驗證了 PLAN 3.1 節要點的「刪除掃描地雷」修正確實生效
5. `DepartmentStore`：`list()` 確認不洩漏 `pw_hash`/`admin_pw_hash`；`get_by_id()` 含 `session_version`/`active`；`list_public()` 正確回傳 `id`/`name`；對不存在部門回傳 `None`；`purge()` 的二次確認與 `purgeable` 保護機制皆用臨時部門完整走過一次（建立→寫入關聯資料→purge→確認清除），過程中意外驗證了 `devices.department` 外鍵約束確實會擋下指向不存在部門的寫入（用不存在的假部門 `__zztest__save__` 測試 `save()` 時，直接被 FK 擋下 409，改用真實建立的臨時部門才測通）

**🟢 過渡期設計確認符合 PLAN 4.8 節**
6. 所有新加的 `department` 參數（`load()`/`save()`/`append()`/`log()` 等）皆保留 `None` 預設值，`AuditLogger`/`FeedbackStore`/`ViewStore`/`AiScanStore` 目前呼叫端（舊版 `app.py`）不帶 `department` 也能正常運作——57 個既有 pytest 全數通過，確認過渡期行為與階段 5 部署要求（先驗證行為不變）一致

**影響範圍**：`backend/storage.py` 完整改寫；驗證方式記錄在案（Supabase 真實連線手動測試，非 pytest），供日後类似「pytest 覆蓋不到 SupabaseStore 路徑」的情境參考；`DepartmentStore.check_login()` 尚未實作（原 PLAN 3.3 節列出但實際登入比對邏輯屬於階段 7 `app.py` 的職責，改為 `app.py` 直接呼叫 `get_by_id()` 取雜湊後用 `werkzeug.security.check_password_hash()` 比對，不在 `storage.py` 內建密碼比對方法）。

---

第二十一輪審查（`app.py` 完整改寫並用真實 Supabase 連線逐項驗證最高風險項目，發現一個測試陷阱）：

**背景**：依 PLAN 第 2、4 節完整改寫 `backend/app.py`——登入分岔（`__super__` vs 部門）、`assert_session_valid()` 三態校驗、`scope_department()`/`resolve_target_department()`、四種權限裝飾器（`login_required`/`admin_required`/`superadmin_required`/`public_endpoint`，含 `_auth_level` 標記）、全部路由依 method 拆分並改帶部門路徑段、部門管理端點、`/api/whoami`/`/api/departments/public`。同步更新 `tests/test_api.py` 反映新路由結構（`/api/alarms/<department>/...` 等）。

**🟡 `JsonStore` 缺少 `upsert_one()`/`delete_one()` 導致 pytest 全面失敗（已修正）**
1. **`app.py` 的單筆 CRUD 端點（`create_alarm`/`update_alarm`/`create_device` 等）統一呼叫 `upsert_one()`/`delete_one()`，但這兩個方法原本只在 `SupabaseStore` 實作**，`JsonStore`（pytest 使用）完全沒有對應介面，導致 6 個測試因 `AttributeError` 失敗 → 為 `JsonStore` 補上 `upsert_one()`/`delete_one()`（內部走 load-modify-save，不模擬 PostgREST 的 `on_conflict` 語意，僅求呼叫端介面一致）；同時 `JsonStore.load()` 補上 `_row_to_device()` 正規化（`devices.json` 讀出來原本沒有雙 key），修正後 `test_devices` 的 `device_model` 斷言才能通過（見 `backend/storage.py`）

**🟢 完整改寫並用真實 Supabase 連線逐項驗證的核心安全機制**
2. **登入分岔（PLAN 第 2 節最高風險項）**：實測「超管密碼登入部門帳號」與「部門密碼登入 `__super__`」皆被正確拒絕，兩條路徑完全不 fallthrough
3. **越權防護（PLAN 4.1 節）**：一般帳號存取其他部門的路徑段回 404（不透露部門存在）；一般帳號帶 `?dept=其他部門` 完全不影響查詢範圍（`scope_department()` 只在超管時讀 `?dept=`）
4. **`assert_session_valid()` 三態校驗（PLAN 2.1 節）**：透過真實 API（`PUT .../active`、`PUT .../reset-password`、`DELETE .../<dept_id>`）觸發部門停用、密碼重設、部門被 purge 三種情況，確認同一 process 內原本已登入的 session 立即失效（停用/重設回 401；purge 後回乾淨的 401，不是撞外鍵 500，驗證了 PLAN 2.1 節特別強調的第三種情況）
5. **超管密碼保護（PLAN 第 2 節第二道防線）**：`POST /api/admin/departments`、`PUT .../reset-password` 皆正確拒絕與 `SUPERADMIN_PASSWORD` 相同的明文密碼
6. **`_auth_level` 標記完整性**：30 個 `/api/*` 路由逐一檢查，全部帶有 `_auth_level`（`login`/`admin`/`superadmin`/`public`），且層級與 PLAN 4.4/4.5/4.6/4.7 節規格完全一致——這組結果直接對應階段 9 要寫的 `test_route_auth_registry.py`，先用手動腳本驗證邏輯正確，降低正式寫測試時踩坑的機率

**🟡 發現一個測試陷阱：`inspect.getsource` 字串比對法對 docstring 誤判（記錄供階段 9 參考）**
7. **PLAN 8.1 節要求「`resolve_target_department()` 不得讀取 `request.args`」的原始碼檢查，若直接用 `"request.args" not in source` 這種簡單字串比對，會被函式自己的 docstring 誤判**——`resolve_target_department()` 的說明文字寫著「不讀 `request.args`」，這行註解本身就含有 `request.args` 字串，導致簡單比對會誤報「含有 request.args」而失敗，即使函式本體完全沒有真正存取它。用 `ast` 解析、排除 docstring 後只檢查函式本體，才能得到正確結果 → 記錄此陷阱，階段 9 撰寫 `tests/test_route_auth_registry.py` 內的 `test_resolve_target_department_does_not_read_request_args` 時，要用 AST 解析排除 docstring，不能用naive 字串比對

**影響範圍**：`backend/app.py` 完整改寫（登入、權限框架、全部端點）；`tests/test_api.py` 同步更新路由路徑反映新結構，57 個測試全數通過；`backend/storage.py` 補上 `JsonStore.upsert_one()`/`delete_one()`；本輪所有驗證測試建立的臨時部門（`zztest2`~`zztest6`）皆已清除，正式資料（`mf4d`：`alarms` 1759、`devices` 14）全程未受影響；`tests/test_route_auth_registry.py`（階段 9）與登入節流機制（`login_attempts`，階段 4）仍未實作，為下一步工作。

---

第二十二輪審查（登入節流機制實作，發現並修正一個會導致永久鎖死的邏輯缺陷）：

**背景**：依 PLAN 2.2~2.2.4 節實作登入節流機制——`storage.py` 新增 `LoginAttemptStore`（`record()`/`count_fine()`/`count_coarse()`/`cleanup_expired()`），`app.py` 新增 `_check_login_throttle()`／`_remaining_delay()`，串進 `login_submit()`/`admin_login_submit()`，並在 `_do_login()` 內按 2.2.4 節三段式判斷寫入記錄。

**🔴 實作中發現並當場修正的邏輯缺陷：delay 未定義「相對哪個時間點倒數」，導致永久鎖死（已修正，非 PLAN 文件疏漏，是實作階段的自我糾正）**
1. **第一版實作把「`delay = min(2**N, 60)`」直接理解成「只要 N 達到門檻，這次請求就回 429」，沒有考慮 delay 是一個會隨時間流逝而過期的倒數視窗**。用真實 Supabase 連線測試時發現：第一次失敗後，任何後續嘗試（包含用正確密碼）永遠回 429——因為 `N`（連續失敗數）只有「成功登入」才會歸零，但要成功登入又得先通過節流檢查，形成無法打破的死鎖，等於把使用者永久鎖在門外，比原本要避免的「硬鎖」問題更嚴重。

   **判斷是否需要問專家**：這不是需要外部經驗判斷的設計取捨（不像「兩種做法都合理」的情境），而是實作邏輯本身的正確性問題——「節流視窗會過期」是 2.2/2.2.1 節「延遲窗口」「還在窗口內」這些用詞已經隱含的意思，只有一種修法是對的，不構成需要問專家的分歧點。

   **修正**：`LoginAttemptStore.count_fine()`/`count_coarse()` 改為同時回傳 `(N, 最後一次失敗的時間戳)`；新增 `_remaining_delay(n, last_failure_at, n_threshold, n_offset)`，用「目前時間 - 最後失敗時間」算出剩餘等待秒數，delay 歸零後即可再次嘗試（不論成功失敗），失敗會產生新的 `N` 與新的倒數視窗。修正後用真實連線驗證：立即重試被 429 擋下 → 等待 delay 秒後可重試 → 成功登入後計數確實歸零、下次錯誤不會立即被節流。

**🟢 三段式判斷、清理機制皆驗證通過**
2. **格式不合法**（如 `INVALID-DEPT!`）：確認完全不寫入 `login_attempts`，不查 DB
3. **格式合法但部門不存在**：確認正確寫入 `success=false` 記錄（稽核訊號保留）
4. **已在節流窗口內的請求**：確認不重複寫入新記錄（避免表被拿來當放大攻擊目標）
5. **細網/粗網取最大值**：粗網門檻 20 次、`n_offset=19` 的位移邏輯正確接上 `_remaining_delay()`
6. `cleanup_expired(days=90)` 驗證不會誤刪 90 天內的記錄；`POST /api/admin/cleanup-expired` 端點回應新增 `login_attempts_removed` 欄位

**影響範圍**：`backend/storage.py` 新增 `LoginAttemptStore` 類別與 `login_attempt_store` singleton；`backend/app.py` 新增 `_client_ip()`/`_check_login_throttle()`/`_remaining_delay()`，`_do_login()` 內建三段式記錄邏輯，`login_submit()`/`admin_login_submit()` 加上節流檢查，`cleanup_expired()` 端點回應格式擴充；驗證過程建立的臨時部門 `zztest7` 與測試期間產生的 `login_attempts` 記錄皆已清除，正式資料未受影響；57 個既有 pytest 全數通過（本機/測試模式因 `_use_supabase()` 為 False，節流邏輯完全不啟用，符合單租戶測試環境的定位）。

---

第二十三輪審查（階段 6：`ai_memory.py`/`ai_pipeline.py` 部門隔離實作與驗證）：

**背景**：依 PLAN 3.5 節，將 `department` 參數往下傳進 AI 記憶層（`ai_memory.py`）與管線層（`ai_pipeline.py`），確保拍照歷史/候選建議查詢限縮在呼叫者的部門範圍內，避免 A 部門的辨識歷史混進 B 部門候選建議清單（同時是正確性問題與資料洩漏問題）。

**🟢 完整改動並用真實 Supabase 連線驗證通過**
1. `ai_memory.py`：`record_scan()`/`record_confirmation()`/`record_correction()` 新增 `department` 參數（過渡期預設 `None`，符合 PLAN 4.8 節）並寫入 `ai_scans.department`/`ai_corrections.department`；`load_corrections()`/`load_confirmed_history()`/`_load_records()` 新增 `department` 參數，套用查詢過濾
2. `_sb_query()` 的 filters 改為自動跳過值為 `None` 的欄位（`{k: v for ... if v is not None}` 邏輯），這個小改動同時滿足了 PLAN 3.7 節「`department=None`（`DeptScope.ALL`）時不得加任何 `department` 相關過濾條件」的約束——不需要另外寫 if/else 分支，`None` 天然不會產生 `department=eq.` 這段查詢字串
3. `ai_pipeline.py`：`run_pipeline()`/`run_confirmation()`/`run_correction()` 新增 `department` 參數並一路傳給 `ai_memory` 的對應函式；`app.py` 的 `/api/analyze`、`/api/confirm`、`/api/correct` 三個端點改為傳入 `session.get("department")`
4. **實測驗證**：用真實 Supabase 連線寫入一筆帶 `department='mf4d'` 的 scan 記錄，確認用 `department='mf4d'` 查詢能找到、用其他部門查詢查不到、用 `department=None` 查詢（模擬總管視角）能看到全部——三種情境皆符合 PLAN 3.5/3.7 節的設計要求

**影響範圍**：`backend/ai/ai_memory.py`、`backend/ai/ai_pipeline.py`、`backend/app.py`（三個 AI 相關端點）；驗證過程建立的測試記錄（`ZZTEST-MODEL` scan）已清除；57 個既有 pytest 全數通過；`ai_logger.py`（LOG 層）目前未加 `department` 欄位——`ai_logs` 表雖已在階段 1 加上 `department` 欄位並有 `AiScanStore.load_logs()` 支援過濾，但 `log_scan()`/`log_confirmation()`/`log_correction()` 尚未實際寫入這個值，留待需要時再補（不影響本輪 MEM 層的核心隔離目標，LOG 層主要用途是除錯追蹤而非跨部門資料洩漏的主要風險點）。

---

第二十四輪審查（階段 9：`tests/test_route_auth_registry.py` 撰寫完成）：

**背景**：依 PLAN 8.1 節撰寫路由權限白名單自動測試，把第二十一輪手動驗證過的「30 個 `/api/*` 路由皆帶有正確 `_auth_level`」這個結果，轉成可長期執行、之後任何人新增路由忘記加裝飾器都能立即抓到的自動化測試。

**🟢 兩項測試皆撰寫完成並通過**
1. `test_all_api_routes_registered_and_decorated`：`ROUTE_AUTH_REGISTRY` 以 `(rule, method)` 為 key（非單純 rule 字串，對應第七輪審查修正），遍歷 `app.url_map` 逐一比對「白名單宣告層級」vs「view function 實際 `_auth_level` 屬性」，兩者不一致即失敗；額外加了反向檢查（白名單裡不能有 app.py 已不存在的路由，避免白名單本身跟著腐化不被發現）
2. `test_resolve_target_department_does_not_read_request_args`：用 `ast` 解析 `create_app()` 原始碼、找出巢狀定義的 `resolve_target_department` 函式節點、明確跳過開頭的 docstring `Expr` 節點後才檢查函式本體是否含 `request.args` 字串——避開了第二十一輪審查記錄的陷阱（docstring 本身就寫著「不讀 request.args」這句說明文字，naive 字串比對會被這行誤判為違規）
3. 兩項測試皆一次寫對、一次通過，沒有額外除錯——這是第二十一輪先用手動腳本驗證過 `_auth_level` 標記完整性、且提前記錄了 docstring 陷阱的直接效益，正式寫測試時沒有再踩到同樣的坑

**影響範圍**：新增 `tests/test_route_auth_registry.py`；全專案 pytest 從 57 個增加為 59 個，全數通過；PLAN 3.6/4.8 節「`department` 參數改必填」仍照計畫保留到 `app.py` 部署階段（階段 11）與 `storage.py`/`ai_memory.py` 的預設值在同一次 commit 一起移除，不在本輪處理。

---

第二十五輪審查（第 6/6.1 節：批次匯入工具 `import_alarms.py` 撰寫完成）：

**背景**：前端改動需等使用者完成 design 草稿才能開始，趁這段空檔完成與前端／部署都無關、可獨立驗證的第 6 節批次匯入工具。

**🟢 完整實作並用 JsonStore 本機驗證通過（PLAN 6/6.1 節全部要求皆已落地）**
1. `--department` 必填且驗證符合 `^[a-z0-9_]{1,32}$`（與 `app.py` 的 `DEPT_ID_RE` 定義一致；此工具刻意不 import `app.py`，避免觸發不必要的 `create_app()` Flask 初始化，改為在檔案內重複定義同一正則）
2. `--mode append|upsert|replace`，預設 `append`（逐筆 `upsert_one()`，不刪除既有資料）；`replace`（整批 `save()`，含刪除掃描）需額外 `--yes-i-mean-replace` 旗標，未帶旗標直接拒絕執行——已測試驗證擋下
3. **前置機種驗證（PLAN 6.1 節核心風控）**：`_validate_devices_exist()` 整批比對 CSV 內所有相異 `device_model` 是否已存在於該部門 `devices` 表，缺任何一個就整批中止、列出缺少機種清單與各自受影響筆數，不寫入任何一筆——已測試驗證：帶入不存在機種 `PLIM003` 的 CSV，正確擋下並回報「1 筆警報受影響」
4. `--create-missing-devices`：opt-in，預設關閉，帶上才會自動建立缺少的機種（用 `M-<model>` 命名慣例，比照 `app.py` 既有的 `create_device` 端點）
5. **CSV 內部重複值防護（實作時新增，PLAN 未明文要求但邏輯必要）**：`_dedupe_check()` 檢查 CSV 內是否有重複的 `(device_model, code)` 組合——若不擋，`upsert_one()` 逐筆處理時後者會安靜覆蓋前者且不報錯，這與 6.1 節「孤兒警報安靜產生、難以察覺」是同一類風險，故一併納入前置驗證，已測試驗證正確擋下
6. `--dry-run`：印出將寫入 N 筆、缺少機種清單（若有）、`replace` 模式額外印出將刪除 N 筆——皆已測試驗證輸出正確
7. **交易語意**：`append`/`upsert` 模式逐筆呼叫 `upsert_one()`（PostgREST 單筆請求已具備原子性）；`replace` 模式呼叫既有的 `save()`（PLAN 3.1 節已修正的部門過濾版本），沿用其既有的「先 upsert 全部、再刪除掃描」語意，未額外包一層應用層交易——這與 `storage.py` 現有其他呼叫端（如 `create_app()` 的機種管理）的一致性做法相同，不是這次新增的風險

**影響範圍**：新增 `backend/import_alarms.py`（CLI 工具，`argparse` 介面，不依賴 Flask app context）；59 個既有 pytest 全數通過（此工具未被 pytest 覆蓋，因為它是獨立 CLI 而非 `app.py` 路由，改用手動建立臨時 `ALARM_DATA_DIR` 資料夾＋CSV 直接執行驗證，涵蓋：正常匯入、機種缺失擋下、CSV 內部重複值擋下、`replace` 模式安全閥），驗證用臨時檔案已清除；尚未對真實 Supabase 連線測試（因為目前沒有第二個真實部門可匯入，且 `devices_store`/`alarms_store` 走 `SupabaseStore` 的路徑與 `JsonStore` 共用同一組公開方法簽名，已在階段 5/6 分別驗證過，這裡不必重複驗證底層 store 行為，只需驗證這支工具本身的參數解析與流程控制邏輯）。實際使用時機：PLAN 第 7 節部署順序步驟 8（建立第一個真實第二部門的機種與警報），屆時才會第一次對正式 Supabase 執行。

---

第二十六輪審查（整理程式碼交專家審查前的自我覆核，發現並修正一個 `--create-missing-devices` 失效的 bug）：

**背景**：使用者提出要把這批已完成的核心後端程式碼（`storage.py`/`app.py`/`ai_memory.py`/`ai_pipeline.py`/`import_alarms.py`/`test_route_auth_registry.py`）交給外部專家做一次完整審查——這是第一次針對「實際寫出來的程式碼」而非設計文件本身的審查。整理審查包的過程中重新逐檔通讀，在 `import_alarms.py` 發現一個先前手動測試沒有覆蓋到的邏輯錯誤。

**🔴 `--create-missing-devices` 實際上完全不會建立機種（已修正，實作 bug 非設計缺陷）**
1. **`_validate_devices_exist(rows, department, create_missing)` 原本的邏輯是：算出 `missing` 之後，若 `missing` 非空且 `create_missing=True`，直接 `return {}, csv_models`**——把 `missing` 清空後才回傳給呼叫端。但呼叫端 `main()` 判斷「要不要自動建立機種」的依據正是這個回傳值是否非空（`to_create = sorted(missing.keys()) if (missing and args.create_missing_devices) else []`）。兩段邏輯疊加的結果是：只要 `create_missing=True` 且真的有缺少的機種，函式回傳的 `missing` 必為空字典，導致呼叫端算出的 `to_create` 永遠是空列表——**`--create-missing-devices` 這個旗標形同虛設，帶了也不會建立任何機種**，然後程式會直接往下拿著不存在的機種寫入警報，變成 PLAN 6.1 節本來要防的「安靜產生孤兒警報」，且是這支工具自己的 opt-in 功能造成的。

   **為何先前的手動驗證沒抓到**：階段 15 完成時測試過三種情境（正常匯入、機種缺失被擋下、CSV 重複值被擋下），但沒有實際帶 `--create-missing-devices` 跑過一次「機種缺失且允許自動建立」的路徑——只驗證了「不給旗標時會被擋下」，沒有驗證「給了旗標後真的會建立」，這是測試覆蓋的缺口，不是邏輯本身難以發現。

   **修正**：`_validate_devices_exist()` 拿掉 `create_missing` 參數，單純回報缺什麼，不在函式內部因為旗標值改變回傳語意；是否自動建立完全交給 `main()` 依旗標判斷。修正後重新測試「機種缺失＋`--create-missing-devices`」情境：機種正確建立（`devices.json` 新增一筆）、警報正確寫入，不再產生孤兒資料。

**影響範圍**：`backend/import_alarms.py` 的 `_validate_devices_exist()` 簽名與內部邏輯；59 個既有 pytest 不受影響；此修正在把程式碼交給專家審查之前完成，屬於一次額外的自我覆核收穫，說明「回頭準備審查材料」這個動作本身也有除錯價值——重新以「別人要看」的心態通讀程式碼，跟寫完當下的心態不同，容易抓到當時漏掉的路徑組合。
