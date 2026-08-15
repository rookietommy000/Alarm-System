# PLAN — 現場處置做法（local_solution）

## 狀態

🟡 **實作中——階段 1-4 已完成，階段 5 起尚未開始。**

**下一個 session 接手時，從這裡開始：** 第七節「執行順序」表格的階段 5（一般使用者建議 ＋ 待審表 ＋ 後台審核）。階段 1-2 的所有程式碼已 commit 並 push 到 `main`，`verify_isolation.sh` 對正式 Render 環境跑出 **48 通過、0 失敗**（含新增的 T-15~T-17），70 個 pytest 全數通過。詳細過程見第十二節「第四輪外部審查與階段 1-2 收尾」。

**階段 3（前台詳情卡片顯示兩層）已完成**：`frontend/index.html` 的詳情卡片改為四分支（`sol_steps` 結構化步驟優先 → 有 `local_solution` 顯示現場方案＋原廠收合 → 只有 `solution` 原樣顯示 → 兩者皆無顯示「這筆還沒有處置方式」），純文字插值無 `v-html`，已過安全 review，已 commit（`1d6df6f`）並 push。

**階段 4（前台管理員編輯入口）已完成，但只做了 `isAdmin` 這條路徑**：詳情卡片新增「✏️ 編輯」/「💡 補充現場方案」入口與同一個編輯對話框（預填機種/代碼/描述/原廠建議，帶同機種其他警報的 `local_solution` 參考清單，見 5.3 節），送出打既有的 `PUT /api/alarms/<department>/<device_model>/<code>/local` 端點。一般使用者的建議入口（走 `POST .../suggestions` 待審流程）**沒有做**，留給階段 5。純前端改動，已過安全 review（SAFE TO COMMIT），尚未 commit。

與 `PLAN_department_isolation.md`（多部門隔離工程）的關係：**依賴**該工程已完成的部分——`alarms` 表的 `department` 欄位、複合主鍵 `(department, device_model, code)`、`scope_department()`/`resolve_target_department()`/三段式路由、`ROUTE_AUTH_REGISTRY`、`sentinel_pack` 驗證機制——全部直接沿用，不重新設計。本文件只記錄這個功能本身新增的部分。

---

## 一、需求背景

使用者回饋兩件事，實際上是同一個問題的兩面：

1. 很多警報沒有處置程序，一筆一筆進後台補很麻煩
2. **有些原廠提供的處置方式不符合現在的生產做法**，需要記錄現場自己的方案

第 2 點決定了整個設計方向：需要的不是「把空欄位填滿」，是**讓兩種知識並存**。

| | 原廠處置（`solution`） | 現場方案（`local_solution`） |
|---|---|---|
| 來源 | 原廠文件 | 累積的經驗 |
| 權威性 | 設備保固／責任歸屬的依據 | 實際有效的做法 |
| 變動性 | 不變（除非原廠改版） | 隨製程、人員、設備狀況調整 |
| 稽核意義 | 可追溯到原始文件 | 需要有人負責 |

**原廠欄位永遠不被覆寫。** 失去原廠版本會在兩種情況出問題：設備保固爭議、以及 GMP 稽核要求追溯原始依據時。

---

## 二、核心設計決策

### 2.1 並存，不覆蓋

`solution` 維持唯讀，新增 `local_solution` 並存。前台顯示時**現場方案優先、原廠可展開**：

```
【現場方案】                    ← 預設展開
降低進給速率至 80% 後手動復歸，不需停機。

▸ 原廠建議做法                  ← 預設收合
```

順序理由：現場人員要的是「現在該怎麼做」；原廠版本備查，但必須看得到——有時候現場方案不適用（設備狀況不同），那時需要退回原廠依據。

### 2.2 權限界線

| 角色 | 能做什麼 |
|---|---|
| 一般使用者 | **提交建議**（寫進待審表，不直接改資料） |
| 部門管理員 | **直接編輯** `local_solution` / `local_reason` |
| 所有人 | 都不能改 `solution`（原廠欄位） |

一般使用者只能建議，不是不信任，而是**一個人改、全部門立刻看到**——工廠環境下一筆錯誤的處置指示可能導致誤操作。

體驗上差別很小：兩者都是填一個框然後送出，按鈕文字都寫「補充現場方案」，不強調審核流程。管理員多數時候就在現場，那條路徑本來就直接生效。

**【第一輪可行性檢查確認】審核者角色**：階段 5 的待審清單審核權限，第一版沿用既有的 `admin`（部門管理員）角色，不新增「資深人員」這類細分角色。之後若真的需要更細的審核權限分層，與「個人帳號層級稽核」（PLAN_department_isolation.md 已知限制章節）放在同一類待辦，不在本次範圍內處理。

### 2.3 `local_reason` 是必要的，不是裝飾

偏離原廠建議在受規範環境通常需要有依據。`local_reason` 記錄「為什麼採用不同做法」：

> 原廠建議停機更換濾網，現場製程不允許中途停機，改為降速運轉至批次結束後處理。

平常沒人看，稽核時很有用。而且它會逼填寫的人想清楚——**寫不出理由，可能這個「現場方案」本身需要再檢視**。

### 2.4 部門獨立是天然的

`alarms` 已經帶 `department`，所以同一台機種在不同部門可以有不同的現場方案，不需要額外設計。這是當初「同一張表加 department 欄位」的附帶好處。

### 2.5 這次不需要維護窗口

所有改動都是**增量**：加 nullable 欄位、加新表、加新端點、加新 UI。既有行為完全不變，每個階段都能獨立部署並單獨驗證。跟多部門改造不同，這次沒有「新舊版本接不上」的問題。

---

## 三、⬜ 資料庫改動

### 3.1 `alarms` 加四個欄位

```sql
begin;
alter table alarms add column local_solution   text;
alter table alarms add column local_reason     text;
alter table alarms add column local_updated_by text;         -- department/role
alter table alarms add column local_updated_at timestamptz;
commit;
```

全部 nullable 且**不加 NOT NULL**——多數警報不會有現場方案，NULL 在這裡是有意義的值（代表「尚未建立」）。

**【第一輪可行性檢查提醒】執行前先用 `\d alarms` 或 `information_schema` 核對實際欄位型別**，不要憑本文件假設直接下 SQL——比照 `PLAN_department_isolation.md` 第十一輪「不要憑推測撰寫 SQL，第一次真正查詢正式庫時發現欄位名稱與假設不符」的教訓。這批新欄位不影響既有欄位，風險本身很低，但核對成本也很低，沒有理由跳過。

### 3.2 新表 `alarm_suggestions`

```sql
create table alarm_suggestions (
  id            bigserial primary key,
  department    text not null references departments(id),
  device_model  text not null,
  code          text not null,
  suggestion    text not null,
  reason        text,
  submitted_by  text not null,                    -- 伺服器端組成 department/role
  submitted_at  timestamptz not null default now(),
  status        text not null default 'pending',  -- pending / accepted / rejected
  reviewed_by   text,
  reviewed_at   timestamptz,
  review_note   text,
  ai_grade      text,                             -- green / yellow / red（階段 7 才填）
  ai_notes      text,
  foreign key (department, device_model, code)
    references alarms (department, device_model, code) on delete cascade
);

create index idx_suggestions_pending
  on alarm_suggestions (department, status, submitted_at desc);
```

**複合外鍵指向 `alarms`**：防止建議指向不存在的警報。`on delete cascade` 是刻意的——警報都刪了，針對它的建議沒有保留意義。

---

## 四、⬜ 後端改動

### 4.1 讀取路徑

`GET /api/alarms*` 已經是 `select=*`，四個新欄位**自動包含在回應裡，不需要改程式碼**。

**【第一輪可行性檢查確認】已查證 `backend/app.py` 現況**：寫入路徑的 `normalize()` 函式（`app.py` 第 387 行）用 `ALARM_FIELDS` 白名單（`code`/`device_model`/`severity`/`description`/`cause`/`solution`/`keywords`/`sol_steps`）處理 `create_alarm`/`update_alarm`，**這個函式維持不動**——它是原廠欄位既有的寫入路徑，`local_solution`/`local_reason` 不透過它寫入，而是走 4.3 節獨立的 `PUT .../local` 端點與獨立白名單。讀取路徑（`GET /api/alarms`）沒有欄位白名單，是 Supabase `select=*` 回應直接透傳，4.1 節「不需要改程式碼」的判斷成立。

### 4.2 新增端點

| 端點 | 權限 | 說明 |
|---|---|---|
| `PUT /api/alarms/<department>/<device_model>/<code>/local` | `admin` | 管理員直接編輯 |
| `POST /api/alarms/<department>/<device_model>/<code>/suggestions` | `login` | 一般使用者提交建議 |
| `GET /api/admin/suggestions` | `admin` | 待審清單，依 `scope_department()` 過濾 |
| `PUT /api/admin/suggestions/<id>` | `admin` | 接受／退回 |
| `GET /api/admin/alarms/missing` | `admin` | 缺處置清單 |

全部要同步加進 `ROUTE_AUTH_REGISTRY`，鍵為 `(rule, method)`。`/local` 與 `/suggestions` 是同一個 rule 前綴下的不同路徑段（不是同路徑不同 method），不會撞上 PLAN_department_isolation.md 4.4 節「同一 rule 依 method 拆分 view function」那條規則要處理的情境，可以各自獨立宣告。

### 4.3 欄位白名單（最重要的一條）

`PUT .../local` **只接受 `local_solution` 和 `local_reason`，其餘一律忽略**（不是報錯，是直接不理會）：

```python
LOCAL_EDITABLE = {"local_solution", "local_reason"}

body = request.get_json(silent=True) or {}
patch = {k: v for k, v in body.items() if k in LOCAL_EDITABLE}
if not patch:
    abort(400, "沒有可更新的欄位")
```

這樣就算前端哪天送多了東西，`solution`、`code`、`device_model`、`department` 都動不了。這是防止原廠欄位被覆寫的最後一道，不能省。

### 4.4 稽核軌跡

寫入時同步記 `alarm_history`：

- **`changed_by` 由伺服器端組成 `f"{department}/{role}"`，不信任前端傳值**
- **【第一輪可行性檢查修正】`department` 這個值必須來自 `resolve_target_department()` 的回傳值（PUT 端點路徑段解析出的目標部門），不能直接沿用 `app.py` 現有的 `_confirmed_by()` helper（第 633 行）的寫法**——`_confirmed_by()` 是用 `session.get("department")`，服務的是 `/api/confirm`/`/api/correct` 這類沒有路徑部門段的端點（超管本來就不會走到那些端點，見 PLAN_department_isolation.md 4.1 節配套規則 c）。但 `PUT .../local` 是三段式路由、有路徑部門段，語意上要用 `resolve_target_department()` 的目標部門，不能複製貼上 `_confirmed_by()` 的實作，兩者依賴的資訊來源不同
- `action` 用 `local_update`，跟一般 `update` 區分
- `local_updated_by` / `local_updated_at` 同步寫入 `alarms`

### 4.5 缺處置清單的排序邏輯

**【第三輪外部審查：發現原始設計依賴的資料結構不存在，最終定案為第一版不做排序】**

原始構想是「無原廠也無現場」優先、其次依「掃描次數」排序。第二輪審查先指出排序 SQL 是原生跨表 `JOIN`，PostgREST 跑不起來，建議改用 view。但用真實 Supabase 連線查證 `ai_scans` 實際 schema 後，發現問題比 SQL 語法更根本：

```json
// ai_scans 一筆記錄的實際結構
{
  "scan_id": "...",
  "model": "TFM002",
  "alarms": "[{\"code\":\"0003\",\"conf\":98},{\"code\":\"1985\",\"conf\":98}, ...]",  // JSON 陣列字串，非單一欄位
  "tier": "success_high",
  "department": null
}
```

`ai_scans` 根本沒有 `detected_code` 這種單一代碼欄位——一次掃描的 `alarms` 是 **AI 回傳的候選清單**（可能同時包含好幾個候選代碼、各帶信心分數），不是「一次掃描對應一個實際發生的代碼」。這解釋了為什麼現有的 `GET /api/admin/scan-ranking`（`app.py` 771 行）從一開始就只按 `model`（機種）分組，從未按單一代碼統計過——不是疏漏，是這個資料結構在語意上本來就只適合按機種分組。

**更關鍵的是：就算解決了 JSON 陣列展開的技術問題，這個指標測的方向也是錯的。** 若把 `alarms` 陣列展開計數，一次掃描會讓陣列裡的每個候選代碼各 +1——辨識越不準、候選越多的情況，貢獻的計數反而越多。得到的排序會是「AI 多常猜到這個代碼」，不是「這個警報多常真的發生」，兩者方向相反。

**曾考慮的替代方案：改用 `alarm_views`**（使用者確認並點開查看警報詳情，語意正確、天然按代碼記錄、已在支撐 Dashboard Top10，且用真實連線確認是事件表非計數表）：

```sql
-- 曾考慮但最終未採用的版本
create view alarms_missing_local as
select a.department, a.device_model, a.code, a.description,
       (a.solution is null or a.solution = '') as no_vendor,
       coalesce(v.view_count, 0) as view_count
from alarms a
left join (
  select department, device_model, code, count(*) as view_count
  from alarm_views
  group by department, device_model, code
) v
  on v.department = a.department
 and v.device_model = a.device_model
 and v.code       = a.code
where a.local_solution is null or a.local_solution = '';
```

這個版本技術上可行，但發現兩個限制：(1) `department` 有 NULL 的歷史孤兒列（1.5 節刻意保留），`eq` 匹配不到 NULL，改造前累積的瀏覽記錄完全不計入，初期數字會很小；(2) 建這個 view 仍然是為了一個「錦上添花」的排序維度，付出的複雜度（額外的表、額外的部署步驟、RLS 開啟時要記得處理）與帶來的價值不成比例。

**最終決定：第一版完全不做排序，也不建任何 view。** `no_vendor`（有沒有原廠處置）已經是最重要的判準，而且是 `alarms` 表上的純欄位判斷——不需要 join、不需要跨表查詢、不需要 view，直接在既有的 `GET /api/alarms` 加一個 `?missing_local=true` 篩選參數（`local_solution is null or local_solution = ''`）就完成。`alarm_views` 排序這個維度留到日後真的有需要、且累積了足夠改造後的資料時再單獨評估加回來——view 隨時可以事後補，不影響現在的實作。

**連帶影響**：階段 1 的資料庫改動回到最單純的「加四個欄位 + 建 `alarm_suggestions` 表」，不需要建 view；4.2 節的 `GET /api/admin/alarms/missing` 端點改為 `GET /api/alarms?missing_local=true`（沿用既有讀取端點加篩選參數，不算新端點，`ROUTE_AUTH_REGISTRY` 不需要為此新增項目，只是既有路由的行為擴充）。

---

## 五、🟡 前端改動

### 5.1 ✅ 詳情卡片顯示兩層

- 有 `local_solution` → 顯示「現場方案」，原廠收合在下方
- 只有 `solution` → 直接顯示原廠內容，不特別標示（避免既有畫面大幅變動）
- 兩者皆無 → 顯示「這筆還沒有處置方式」＋ 補充入口

### 5.2 ✅（僅管理員路徑）編輯入口：把時機移到知識產生的當下

現有流程麻煩的根本原因是**時機錯開**：知識產生在「剛處理完那個警報」的當下，但填寫要求「某天有空 → 打開後台 → 找到那筆 → 回想」。

**【第一輪可行性檢查確認】入口要同時涵蓋兩種詳情卡片觸發路徑**：`index.html` 的警報詳情卡片有兩個入口——搜尋結果點開、以及 AI 拍照辨識結果點開，兩者都要有「編輯」/「補充現場方案」按鈕，不能只做其中一個。實作時要確認兩條路徑最終渲染的是不是同一個 Vue 元件（若是，只需改一處；若各自獨立渲染，需要兩處都加）。

入口放在**前台警報詳情卡片**，不是後台列表：

```
【現場方案】
（尚未建立）
                          ✏️ 編輯          ← isAdmin
                          💡 補充現場方案   ← 一般使用者
```

兩個按鈕開**同一個對話框**，差別只在送出後走哪支端點。

**實作備註（這次動工的範圍）**：目前只做了 `isAdmin` 這條路徑（按鈕文字「✏️ 編輯」，打 `PUT .../local` 直接寫入），一般使用者的「💡 補充現場方案」按鈕與 `POST .../suggestions` 待審流程留給階段 5（見 5.4 節、第七節）。詳情卡片與 AI 拍照辨識結果共用同一個 `openDetail`/`selected` 元件（已於階段 3 確認），只改了一處，兩條觸發路徑都已涵蓋。

### 5.3 ✅ 對話框要預填情境

不要給空白框：

```
機種：PILM003    代碼：E107
描述：主軸溫度異常

原廠建議：檢查冷卻水流量與熱交換器

現場方案：[                              ]
為什麼不同：[                            ]

── 同機種其他警報的現場方案（參考）──
E105：檢查冷卻水流量與熱交換器後降速運轉
E108：確認風扇運轉並清潔濾網
```

參考範例會大幅提高填寫品質與用語一致性，也降低「不知道要寫多細」的猶豫。**這比 AI 審核更早介入，效果也更直接。**

### 5.4 後台待審清單

管理員後台新增「待處理建議」提示（含未處理筆數），列表顯示：提交者、時間、警報、建議內容、目前的現場方案（若有）。操作：接受（寫入 `local_solution`）／退回（填 `review_note`）。

---

## 六、⬜ AI 第一關審核（階段 7）

### 6.1 定位必須寫清楚

> **AI 把關「寫得完不完整」，不把關「對不對」。**

**AI 能審的**——形式與一致性，它做得比人快也比人穩：

- 有沒有具體動作，還是只寫「檢查一下」
- 與同機種其他警報的用語一致性
- 明顯矛盾（描述說溫度過高，處置寫「加熱」）
- 安全要素缺漏（斷電、上鎖、防護裝備）
- 錯字、語句不通、中英義混雜

**AI 不能審的**——技術正確性。它不知道你們的設備，「檢查冷卻水流量」和「檢查潤滑油位」對它來說一樣合理。

### 6.2 輸出分級，不輸出判定

不要讓 AI 給「通過／不通過」，那會讓人把它當判斷者：

| 標記 | 意義 | 人的動作 |
|---|---|---|
| 🟢 格式完整 | 有具體動作、用語一致、無矛盾 | 快速確認 |
| 🟡 建議補充 | 缺安全提醒、動作不夠具體 | 看 AI 的具體建議 |
| 🔴 需要注意 | 與描述矛盾、內容空泛、疑似複製錯 | 仔細看 |

**每一筆都要人按下確認，不得有「🟢 自動通過」。**

理由是 GMP：處置指示未經人工確認就上線，稽核時交代不過去。而且 AI 的錯誤特性是**它不會空著，它會編一個看起來合理的**——空白很好發現，一句通順但錯誤的處置沒人會懷疑。

**【第一輪可行性檢查記錄，與這個系統既有教訓呼應】**「AI 不會空著會編」這個判斷，跟 `PLAN_department_isolation.md` 第二十九輪外部審查記錄的 `_count()` 解析失敗回 0 那個問題是同一類模式——「拿不到正確答案時回傳一個看起來正常的值，比明確報錯更危險」。人工確認機制在這裡扮演的角色，等同於那次修正把 `return 0` 改成 `raise RuntimeError` 的精神：不讓看似合理的結果免於檢驗。

### 6.3 AI 真正能加值的地方：差異摘要

除了格式檢查，讓 AI 摘要**現場方案與原廠做法的差異點**：

> 原廠要求停機後處理，現場方案為不停機調整參數。

審核者看到摘要，能立刻判斷差異是否合理、有無安全疑慮。這是 AI 的強項——它做不了技術判斷，但很擅長指出兩段文字的差異，而那正是審核者最需要先看到的資訊。

### 6.4 一致性檢查

送審時**同時提供同機種的其他 `local_solution`** 給 AI 參考，讓它能發現「這台機器其他 20 筆都提到要先斷電，只有這筆沒提」。這是人工審核最容易漏的。

**【第一輪可行性檢查確認】審核用 AI 呼叫是獨立的新呼叫路徑，不沿用 `ai_pipeline.py` 現有的分析管線架構**——`ai_pipeline.py`（`run_pipeline()`/`run_confirmation()`/`run_correction()`）處理的是「拍照辨識警報畫面」這個任務，審核 `local_solution` 文字品質是完全不同的任務（不同 prompt、不同輸入輸出格式、不同觸發時機）。兩者只共用同一把 `GEMINI_API_KEY` 這一個底層資源，「增量成本很小」指的是不需要重新申請 API 額度或建立新的分析架構，但程式碼層級應該是新的、獨立的呼叫路徑，不要試圖把審核邏輯塞進 `ai_pipeline.py` 既有的 `run_pipeline()` 系列函式裡——那樣會讓一個函式承擔兩種不同語意的任務，違反這個專案一路堅持的單一職責原則（呼應 `_row_to_device()`「轉換只能在一個函式發生」同一種思維）。

---

## 七、⬜ 執行順序

每個階段都能獨立部署並單獨驗證，**不需要維護窗口**。

| 階段 | 內容 | 產生的價值 |
|---|---|---|
| 1 | 加四個欄位 ＋ 建 `alarm_suggestions` 表（第三輪審查後：不建 view，見 4.5 節） | 零行為變更 |
| 2 | `PUT .../local` 端點 ＋ 白名單 ＋ 稽核 | 後端就緒 |
| 3 | ✅ 前台詳情卡片顯示兩層 | **既有知識可見** |
| 4 | ✅ 前台管理員編輯入口 ＋ 預填情境 | **知識開始累積** |
| 5 | 一般使用者建議 ＋ 待審表 ＋ 後台審核 | 擴大來源 |
| 6 | 缺處置清單（`GET /api/alarms?missing_local=true` 篩選參數，不做排序，見 4.5 節） | 補資料有優先序 |
| 7 | AI 第一關分級 ＋ 差異摘要 | 降低審核負擔 |

**🟡【第二輪外部審查提醒】哨兵資料同步要併進 seed 檔本身，不能單獨手動執行**：8.1 節的 `[SENTINEL]` `UPDATE` 語句必須寫進 `sentinel_pack/01_seed_sentinel.sql`（而非只在 Dashboard 手動跑一次）。`sentinel_pack` 的設計原則是「可重複執行、隨時能重建」（`PLAN_department_isolation.md` 第七節 purge 時機的判斷邏輯依賴這一點）——若 `local_solution` 只在外面手動下一次 SQL，之後任何人重跑 seed 腳本，T-15～T-17 會安靜失效（哨兵資料被清空重建，`[SENTINEL]` 標記的 `local_solution` 卻沒有一併重建），而且不會報錯，只是這三項測試從此測不到東西——這正是 `PLAN_department_isolation.md` 第三十六輪那個「假通過測試」問題的同一種模式：測試綠燈，但已經沒有在驗證原本要驗證的機制。

**階段 3–4 是價值最高的一段**，做完就能讓管理員在現場順手記錄。

⚠️ **不要先做 AI 審核**——會變成「有很好的審核機制但沒東西可審」。

**【第一輪可行性檢查建議】階段 4 動工前，先花時間查清楚第九節那個「缺處置警報中有多少其實是原廠文件漏匯入」的問題**——使用者本人估計「一半一半」，比例不低。若成立，代表階段 6 的「缺處置清單」裡有相當比例應該用批次補匯入解決，而不是導向階段 4 的人工填寫流程。這個清查本身成本低（對照現有 CSV/原廠文件 vs `alarms.solution` 是否為空），建議排在階段 4 正式開工前、階段 1-2（資料庫與後端就緒）之後進行，避免管理員花時間手寫其實原廠早就有的內容。

---

## 八、⬜ 驗證方式

### 8.1 自動化

**路由白名單**：五個新端點加進 `ROUTE_AUTH_REGISTRY`。既有的裝飾器標記比對測試會自動守著。

**哨兵資料擴充**：撞名機種 `ACM001` 加一筆帶標記的現場方案：

```sql
update alarms
set local_solution   = '[SENTINEL] 現場方案：此內容若出現在正式部門畫面，代表 local_solution 未依部門過濾',
    local_reason     = '[SENTINEL] 隔離驗證用',
    local_updated_by = 'zztest/admin',
    local_updated_at = now()
where department = 'zztest' and device_model = 'ACM001' and code = 'ZZC001';
```

**`verify_isolation.sh` 新增三項**：

| 編號 | 測什麼 | 預期 |
|---|---|---|
| T-15 | `mf4d` 管理員 `PUT /api/alarms/zztest/ACM001/ZZC001/local` | 404 |
| T-16 | `mf4d` 查詢 ACM001，回應含 `local_solution` 但無 `[SENTINEL]` | 乾淨 |
| T-17 | 一般使用者 `PUT .../local` | 403 |

### 8.2 手動

- 管理員編輯後，`alarms.solution`（原廠）**未被改動**——這是整個設計的核心保證，值得單獨確認一次
- `alarm_history` 有 `local_update` 記錄，`changed_by` 是伺服器端組的值，且部門段來自 `resolve_target_department()`（見 4.4 節修正）
- 送出只含 `solution` 欄位的請求 → 該欄位不受影響（白名單生效）

---

## 九、⬜ 已知限制與待確認

**稽核只到部門層級。** 部門共用密碼，所以 `local_updated_by` 只能記到 `mf4d/admin`，追不到人。而 `local_solution` 是實質的製程決定（偏離原廠建議），稽核時可能被問「是誰決定的」。

這不是這次要解決的問題——個人帳號本來就在後續待辦裡。但**要明確記錄成已知限制**，稽核時才是「已知並有計畫」而非「沒想到」。

**審核者角色暫用 admin**（見 2.2 節第一輪可行性檢查確認），之後若需要更細的角色分層（例如「資深人員」跟「一般管理員」區分），與個人帳號層級稽核放在同一個未來待辦，不在本次範圍。

**待確認**：

- ⬜ **缺處置的警報中，有多少是「原廠文件其實有、但當初沒匯進來」？**（使用者估計約一半一半，比例不低）若確認比例高，補匯入的效率會比人工填寫高一個量級，應排在階段 4 前面（見第七節建議）
- 差異較大的（AI 標 🔴）是否需要更資深的人確認？（與審核者角色待辦同一類，暫不處理）

**🟡【第二輪外部審查提醒】若原廠文件缺口比例確認偏高，會連帶牽動一件目前只是佔位、沒有實質內容的待辦**：`PLAN_department_isolation.md` 目前有一節提到「PDF／Word → 標準 CSV 的抽取流程」，但只佔了位置、沒有實際規劃。若查清楚後決定走批次補匯入這條路，那個抽取流程要先補上實質內容（怎麼從原廠文件擷取結構化資料），否則「查完比例、決定要補匯入」和「真的能動手補匯入」之間還有一段沒規劃的距離。這件事排在完成第九節那項待確認之後，不是本輪範圍，但值得先記下來，避免查完比例後才發現卡在這裡。

---

## 十、刻意不做的事

- **不讓一般使用者直接編輯** — 一個人改、全部門立刻看到，工廠環境風險過高
- **不覆蓋 `solution`** — 保固爭議與稽核追溯都需要原廠版本
- **不做 AI 自動通過** — GMP 要求人工確認
- **不做版本歷史比對介面** — `alarm_history` 已有軌跡，介面等有需求再說
- **不在這次處理個人帳號** — 獨立的專案級改動
- **不把審核 AI 呼叫塞進 `ai_pipeline.py` 既有函式** — 見 6.4 節，任務語意不同，應為獨立呼叫路徑

---

## 十一、可行性檢查小結（兩輪）

**整體判斷：設計方向正確，可行。** 「原廠 vs 現場方案並存、不覆蓋」的核心決策，與 `PLAN_department_isolation.md` 一路貫徹的原則一致（保留可追溯性、明確白名單優於信任前端、單一職責的轉換點）。

**第一輪檢查**確認/修正五點，已整合進對應章節（標註「【第一輪可行性檢查...】」）：
1. `normalize()` 不動、新欄位走獨立白名單（4.1、4.3 節）
2. `local_updated_by` 用 `resolve_target_department()` 而非 `_confirmed_by()`（4.4 節）
3. 編輯入口要同時涵蓋搜尋結果與 AI 辨識結果兩種詳情卡片路徑（5.2 節）
4. AI 審核是獨立呼叫路徑，不沿用 `ai_pipeline.py` 既有函式（6.4 節）
5. 審核者角色第一版用 admin，不新增角色（2.2、9 節）

**第二輪外部審查**發現 4.5 節排序 SQL 是原生跨表 JOIN、PostgREST 跑不起來，建議改建 view；同時指出哨兵資料同步、辨識修正比例等提醒（已部分被第三輪取代，見下）：
3. 🟡 哨兵資料的 `[SENTINEL]` local_solution 必須寫進 `01_seed_sentinel.sql` 本身，不能只手動下一次 SQL，否則之後重跑 seed 會讓 T-15~T-17 安靜失效（七節，此點仍成立）
4. 提醒：若原廠文件缺口比例確認偏高，會牽動目前只是佔位的「PDF/Word→CSV 抽取流程」待辦，需要先補實質內容才能真的動手補匯入（九節，此點仍成立）

**第三輪外部審查發現比 SQL 語法更根本的問題，4.5 節整段改寫**：用真實 Supabase 連線查證後，`ai_scans.alarms` 是 JSON 陣列（AI 一次掃描的候選代碼清單，非單一代碼欄位），`detected_code` 這個欄位根本不存在。就算解決 JSON 展開的技術問題，這個指標測的也是「AI 多常猜到這個代碼」而非「這個警報多常發生」，方向是錯的。曾考慮改用語意正確的 `alarm_views`，但該表有 `department IS NULL` 的孤兒列問題（改造前資料不計入）且複雜度與價值不成比例。**最終決定：第一版完全不做排序，也不建任何 view**——`no_vendor` 判斷直接用 `GET /api/alarms?missing_local=true` 篩選參數完成，階段 1 回到最單純的「加四個欄位＋建 `alarm_suggestions` 表」（4.5、七節）。

**下一步**：查清楚第九節「原廠文件缺口比例」，之後再排實際動工時間。動工時建議延續 `PLAN_department_isolation.md` 的節奏——分階段、每階段可獨立驗證、重要決策前先問過一輪外部意見。這次三輪審查也印證了同一個模式：**憑合理推測寫的 SQL，一旦用真實資料查證，經常會發現資料結構跟假設不符**——跟 `PLAN_department_isolation.md` 第十一輪「devices 表欄位名稱與全文假設不符」是同一類教訓。

---

## 十二、第四輪外部審查與階段 1-2 收尾（實際動工開始）

**背景**：使用者決定不再等第九節的原廠文件缺口比例查清楚，直接開始動工。依第七節執行順序，完成階段 1（資料庫）與階段 2（後端端點），過程中經歷四輪外部審查，並意外牽出兩個既有系統的結構性問題（登入節流的時間戳解析 bug、哨兵密碼與 seed 腳本的耦合問題）。

### 12.1 階段 1：資料庫改動（已對正式 Supabase 執行）

`backend/migrations/004_add_local_solution.sql`：`alarms` 加四個 nullable 欄位（`local_solution`/`local_reason`/`local_updated_by`/`local_updated_at`），新建 `alarm_suggestions` 表（複合外鍵指向 `alarms` 的 `(department, device_model, code)`，`on delete cascade`）。真實連線驗證：四個新欄位皆為 `null`、`alarm_suggestions` 空表、`mf4d` 部門警報數仍 1759 筆，既有資料未受影響。

`backend/migrations/005_add_pending_suggestion_constraint.sql`（第四輪審查後補）：`alarm_suggestions` 加部分唯一索引 `uniq_pending_suggestion (department, device_model, code) WHERE status='pending'`，防止同一筆警報被重複提交建議——只約束 `pending` 狀態，已審核的歷史記錄不受影響。

### 12.2 階段 2：後端端點（已對正式 Supabase 端到端驗證）

**`storage.py` 新增**：
- `SupabaseStore.patch_one()`/`JsonStore.patch_one()`：單筆部分更新，只改呼叫端明確給的欄位，不用整筆覆蓋的 `upsert_one()`
- `SupabaseStore.get_one()`/`JsonStore.get_one()`（第四輪審查後補）：單筆精確查詢，取代原本為了取一列而 `load()` 整個部門（`mf4d` 1759 筆，兩次分頁 HTTP 往返）的效能問題
- `AlarmSuggestionStore`（`create`/`list_pending`/`get_by_id`/`review`/`has_pending`）：只服務 Supabase，比照 `DepartmentStore` 既有模式。`has_pending()`（第四輪審查後補）用 `Range: 0-0` + `Prefer: count=exact` 只確認存在性，取代原本為了檢查一筆而撈全部門待審清單的問題

**`app.py` 新增四個端點**：
- `PUT /api/alarms/<department>/<device_model>/<code>/local`（`admin`）：白名單 `LOCAL_EDITABLE = {"local_solution", "local_reason"}`，其餘欄位一律忽略——防止原廠 `solution` 被覆寫的最後一道防線
- `POST /api/alarms/<department>/<device_model>/<code>/suggestions`（`login`）：一般使用者提交建議
- `GET /api/admin/suggestions`（`admin`）：待審清單，依 `scope_department()` 過濾
- `PUT /api/admin/suggestions/<id>`（`admin`）：接受寫入 `local_solution`／退回只改狀態

`GET /api/alarms` 加 `missing_local` 篩選參數（沿用既有讀取端點，非新路由）。稽核軌跡 `local_updated_by` 用 `resolve_target_department()` 的目標部門組成，不信任前端傳值，不沿用 `_confirmed_by()`（後者服務的是無路徑部門段的端點，語意不同）。四個新端點與 `missing_local` 篩選皆已加進 `ROUTE_AUTH_REGISTRY`。

### 12.3 第四輪外部審查：四個真實 bug（自查清單抓出，非審查者直接指出）

專家因程式碼未附上無法直接審查，改給一份自查清單，逐項核對後確認四個問題屬實並修正：

1. **`patch_one()` 回 `None` 時沒有防護**——`PATCH` 打空不報錯，兩處呼叫端原本直接把 `None` 餵給 `audit_logger.log()` 和 `jsonify()`，前端會收到 `null` 卻以為成功。修正：`patch_one()` 回 `None` 時明確 404
2. **`POST .../suggestions` 沒有防重複提交**——已用資料庫部分唯一索引（12.1 節）+ 應用層 `has_pending()` 檢查雙重防護
3. **`PUT /api/admin/suggestions/<id>` 沒有防重複審核**——已 `accepted`/`rejected` 的建議可以再審一次、重複寫入 `local_solution`。修正：檢查 `row["status"]`，非 `pending` 直接 409
4. **`reviewer` 組成邏輯依 `scope` 在 `row["department"]` 和 `dept` 之間切換**——外部審查指出這段不必要地繞。簡化為永遠用 `row["department"]`（建議所屬部門，這次寫入實際影響的對象，不是審核者當下的檢視範圍）

### 12.4 意外發現並修復：登入節流既有 bug（與本次功能無關）

實作過程中用真實密碼端到端測試時，一般使用者登入卡在 `throttled=4` 超過 4 分鐘不下降。追查發現 `_remaining_delay()` 用 `datetime.fromisoformat()` 解析 `last_failure_at`，PostgREST 回傳的微秒尾端為 0 時會被裁切成非 3/6 位（例如 `.78161` 而非 `.781610`），Python 3.9 解析失敗，`except` 分支回傳「完整延遲」而非根據實際經過時間算出的「剩餘延遲」——不管過了多久都卡在同一秒數。

外部審查介入後修法加固：
- 除微秒位數外，一併處理時區標記變體（`+00:00`/`+00`/`Z`）
- `except` 分支改為 **fail-closed**（往外拋），不再靜默回傳「完整延遲」這種看似合理的預設值——節流是安全機制，解析失敗該被立刻發現
- 補上 naive datetime 的 `tzinfo` 防護（跟 `utcnow()` 相減會 `TypeError`）
- `_parse_pg_timestamp()` 從 `create_app()` 內部閉包搬到模組層級，讓它能被 pytest 直接測到（純函式邏輯關在閉包裡是這次才發現測不到的結構問題）；新增 `tests/test_timestamp_parsing.py` 九個測試案例

**過程中的教訓**：新測試檔案第一版在模組頂層 `from app import ...`，搶在 `test_api.py` 的 `client` fixture 設定 `ALARM_DATA_DIR` 之前執行，污染 `sys.modules` 快取，導致 `test_ai_pipeline.py` 讀到真實 Supabase 的 14 筆歷史資料而斷言失敗（不是隔離機制壞了，是測試檔案自己的 import 時機沒有遵守既有規則）。修正為延遲 import（各測試函式內部才 import）。

### 12.5 意外發現並修復：哨兵密碼與 seed 腳本的結構性耦合問題

補上 T-15~T-17（`local_solution` 隔離驗證）需要哨兵資料的 `local_solution` 也標記 `[SENTINEL]`，因此重跑了 `01_seed_sentinel.sql`。重跑後 `verify_isolation.sh` 出現 T-00（哨兵資料存在性）與 T-03c（反向刪除應 404 卻回 403）兩項失敗。

**根因**：`01_seed_sentinel.sql` 開頭的 `DELETE FROM departments WHERE id='zztest'` + `INSERT` 會把密碼**重置回 SQL 檔案裡寫死的雜湊**——但那組雜湊是第三十二輪替換完成後就沒再變過的固定值，跟第三十三輪（密碼遺失、重新產生）之後存進 `sentinel_pack/.env.sentinel` 的密碼已經不是同一組。任何人重跑 seed 都會靜默讓 `.env.sentinel` 裡的密碼失效，且失效表現是「`verify_isolation.sh` 一片紅字」——看起來像隔離邏輯壞了，實際上只是登入不了（T-03c 的 403 也是連鎖反應：`admin_required` 對「沒登入」與「登入了但非管理員」都回 403，`zz_admin` 登入失敗導致 cookie 是空的，打過去自然是 403，不透露任何額外資訊）。

**修正（結構性，非一次性補丁）**：
1. **`departments` 那筆從 seed 拆出來**，新增 `sentinel_pack/00b_create_sentinel_dept.sql`——只負責建立/更新哨兵部門帳號，`01_seed_sentinel.sql` 之後不再碰 `departments`，可以安全地隨時重跑而不動密碼
2. **新增 `sentinel_pack/reset_sentinel_password.py`，取代並刪除舊的 `gen_hashes.py`**——舊工具的輸出格式「請把這兩個值貼進 SQL」本身就是誘因（曾經真的有人把雜湊直接寫回範本檔案，導致範本被污染、保護機制隨之失效）。新腳本把「填值」動作從人的手上拿掉：範本永遠保持佔位符狀態，替換只發生在記憶體裡，產物寫到 `/tmp`（不落在專案目錄），同時原子性地更新 `.env.sentinel`（含產生時間戳、當時 commit hash、一句提醒「T-00 失敗先查密碼同步而非隔離邏輯」的警語）
3. **`00b_create_sentinel_dept.sql` 內建佔位符检查**（`do $$ ... position(...) ... raise exception`），防止有人跳過腳本直接貼未替換的範本——這層防護買到的不是攔截（那條路徑本來就會因格式錯誤失敗），是把一個指向錯誤方向的失敗訊息（登入失敗、隔離看起來壞了）換成明確的「佔位符未替換」
4. **`00b` 的部門建立邏輯從 `DELETE + INSERT` 改為 `INSERT ... ON CONFLICT (id) DO UPDATE`**——第一次嘗試執行時撞上外鍵約束（`devices`/`alarms` 等子表仍有記錄指向 `zztest`，`DELETE FROM departments` 直接觸發 `23503 foreign key violation`）。這支腳本的目的單純是「設定/重設密碼」，用 `UPDATE` 精準改密碼欄位不需要整個部門砍掉重建

**這次教訓與既有原則的呼應**：跟 `PLAN_department_isolation.md` 反覆出現的模式一致——「需要人記得做對的事」的流程，遲早會有人做錯（`department` 參數必填化、路由白名單比對實際裝飾器標記、這次的 seed 腳本範本保護），解法都是「把『記得』換成『做不到』」，只是這次出現在維運腳本而非應用程式碼。

### 12.6 最終驗證結果

`verify_isolation.sh` 對正式 Render 環境：**48 通過，0 失敗**（T-13 依設計預設跳過）。新增的 T-15~T-17（`local_solution` 隔離）全數通過；密碼同步問題修正後 T-00/T-03c 恢復正常。70 個 pytest 全數通過（61 既有 + 9 個新增的時間戳解析測試）。

**Commit 記錄**（依序）：
- `99c84d0` — feat: 階段 1-2 資料庫改動與後端端點
- `510a7fd` — fix: 登入節流時間戳解析 bug（第一版修法）
- `95724e2` — fix: 第四輪審查四個真實 bug + 節流修法加固 + 測試污染修正
- `3ffbf03` — perf: `get_one()`/`has_pending()` 效能優化 + 部分唯一索引

`sentinel_pack/` 不受 git 版本控制（獨立於 `testing/` repo 之外），以下檔案異動未進版本庫但已在本機/正式環境生效：
- 新增 `00b_create_sentinel_dept.sql`、`reset_sentinel_password.py`
- 刪除 `gen_hashes.py`
- 修改 `01_seed_sentinel.sql`（移除 `departments` 段落、新增 `local_solution` 哨兵標記 UPDATE、自我檢查區塊補查詢）
- 修改 `verify_isolation.sh`（新增 T-15~T-17）
- `.env.sentinel` 已更新為最新一組密碼（腳本自動維護，含時間戳與警語）

### 12.7 下一步（明確的接手點）

**階段 3**（第七節）：前台詳情卡片顯示兩層——有 `local_solution` 時顯示「現場方案」並將原廠 `solution` 收合在下方；只有 `solution` 時維持現狀不特別標示；兩者皆無時顯示補充入口。純前端改動，`index.html`，不需要碰後端。

**階段 4**：前台管理員編輯入口（搜尋結果與 AI 辨識結果兩種詳情卡片路徑都要有，見 5.2 節第一輪審查確認）+ 對話框預填同機種其他警報的 `local_solution` 當參考範例（5.3 節）。

**階段 5 起**：一般使用者建議入口、後台待審清單 UI、缺處置清單、AI 審核——見第七節表格，尚未開始。

**仍未查清楚的事**：第九節「原廠文件缺口比例」——使用者決定先動工，這件事還沒查，但不阻塞階段 3-4（純前端顯示/編輯，不涉及批次匯入判斷）。真正需要這個答案的是階段 6（缺處置清單怎麼排優先序）之前。
