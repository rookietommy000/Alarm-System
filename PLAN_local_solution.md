# PLAN — 現場處置做法（local_solution）

## 狀態

⬜ **規劃階段，尚未動工**。本文件記錄第一輪可行性檢查的結論與待確認事項，實作時間未定。

與 `PLAN_department_isolation.md`（多部門隔離工程）的關係：**依賴**該工程已完成的部分——`alarms` 表的 `department` 欄位、複合主鍵 `(department, device_model, code)`、`scope_department()`/`resolve_target_department()`/三段式路由、`ROUTE_AUTH_REGISTRY`、`sentinel_pack` 驗證機制——全部直接沿用，不重新設計。本文件只記錄這個功能本身新增的部分。

---

## 一、需求背景

使用者回饋兩件事，實際上是同一個問題的兩面：

1. 很多警報沒有處置程序，一筆一筆進後台補很麻煩
2. **有些原廠提供的處置方式不符合現在的生產做法**，需要記錄本廠自己的做法

第 2 點決定了整個設計方向：需要的不是「把空欄位填滿」，是**讓兩種知識並存**。

| | 原廠處置（`solution`） | 現場做法（`local_solution`） |
|---|---|---|
| 來源 | 原廠文件 | 累積的經驗 |
| 權威性 | 設備保固／責任歸屬的依據 | 實際有效的做法 |
| 變動性 | 不變（除非原廠改版） | 隨製程、人員、設備狀況調整 |
| 稽核意義 | 可追溯到原始文件 | 需要有人負責 |

**原廠欄位永遠不被覆寫。** 失去原廠版本會在兩種情況出問題：設備保固爭議、以及 GMP 稽核要求追溯原始依據時。

---

## 二、核心設計決策

### 2.1 並存，不覆蓋

`solution` 維持唯讀，新增 `local_solution` 並存。前台顯示時**現場做法優先、原廠可展開**：

```
【本廠做法】                    ← 預設展開
降低進給速率至 80% 後手動復歸，不需停機。

▸ 原廠建議做法                  ← 預設收合
```

順序理由：現場人員要的是「現在該怎麼做」；原廠版本備查，但必須看得到——有時候現場做法不適用（設備狀況不同），那時需要退回原廠依據。

### 2.2 權限界線

| 角色 | 能做什麼 |
|---|---|
| 一般使用者 | **提交建議**（寫進待審表，不直接改資料） |
| 部門管理員 | **直接編輯** `local_solution` / `local_reason` |
| 所有人 | 都不能改 `solution`（原廠欄位） |

一般使用者只能建議，不是不信任，而是**一個人改、全部門立刻看到**——工廠環境下一筆錯誤的處置指示可能導致誤操作。

體驗上差別很小：兩者都是填一個框然後送出，按鈕文字都寫「補充本廠做法」，不強調審核流程。管理員多數時候就在現場，那條路徑本來就直接生效。

**【第一輪可行性檢查確認】審核者角色**：階段 5 的待審清單審核權限，第一版沿用既有的 `admin`（部門管理員）角色，不新增「資深人員」這類細分角色。之後若真的需要更細的審核權限分層，與「個人帳號層級稽核」（PLAN_department_isolation.md 已知限制章節）放在同一類待辦，不在本次範圍內處理。

### 2.3 `local_reason` 是必要的，不是裝飾

偏離原廠建議在受規範環境通常需要有依據。`local_reason` 記錄「為什麼採用不同做法」：

> 原廠建議停機更換濾網，本廠製程不允許中途停機，改為降速運轉至批次結束後處理。

平常沒人看，稽核時很有用。而且它會逼填寫的人想清楚——**寫不出理由，可能這個「現場做法」本身需要再檢視**。

### 2.4 部門獨立是天然的

`alarms` 已經帶 `department`，所以同一台機種在不同部門可以有不同的現場做法，不需要額外設計。這是當初「同一張表加 department 欄位」的附帶好處。

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

全部 nullable 且**不加 NOT NULL**——多數警報不會有現場做法，NULL 在這裡是有意義的值（代表「尚未建立」）。

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

不是「所有空白的都列出來」，而是**依實際造成的困擾排序**：

```sql
select a.device_model, a.code, a.description,
       count(s.id) as scan_count,
       (a.solution is null or a.solution = '') as no_vendor
from alarms a
left join ai_scans s
  on s.department = a.department
 and s.device_model = a.device_model
 and s.detected_code = a.code
where a.department = :dept
  and (a.local_solution is null or a.local_solution = '')
group by a.device_model, a.code, a.description, a.solution
order by no_vendor desc, scan_count desc
limit 50;
```

排序原則：**無原廠也無現場**的排最前面（真的沒有任何指引），其次依**掃描次數**（常發生的優先補，掃描 0 次的可能根本不會發生）。

---

## 五、⬜ 前端改動

### 5.1 詳情卡片顯示兩層

- 有 `local_solution` → 顯示「本廠做法」，原廠收合在下方
- 只有 `solution` → 直接顯示原廠內容，不特別標示（避免既有畫面大幅變動）
- 兩者皆無 → 顯示「這筆還沒有處置方式」＋ 補充入口

### 5.2 編輯入口：把時機移到知識產生的當下

現有流程麻煩的根本原因是**時機錯開**：知識產生在「剛處理完那個警報」的當下，但填寫要求「某天有空 → 打開後台 → 找到那筆 → 回想」。

**【第一輪可行性檢查確認】入口要同時涵蓋兩種詳情卡片觸發路徑**：`index.html` 的警報詳情卡片有兩個入口——搜尋結果點開、以及 AI 拍照辨識結果點開，兩者都要有「編輯」/「補充本廠做法」按鈕，不能只做其中一個。實作時要確認兩條路徑最終渲染的是不是同一個 Vue 元件（若是，只需改一處；若各自獨立渲染，需要兩處都加）。

入口放在**前台警報詳情卡片**，不是後台列表：

```
【本廠做法】
（尚未建立）
                          ✏️ 編輯          ← isAdmin
                          💡 補充本廠做法   ← 一般使用者
```

兩個按鈕開**同一個對話框**，差別只在送出後走哪支端點。

### 5.3 對話框要預填情境

不要給空白框：

```
機種：PILM003    代碼：E107
描述：主軸溫度異常

原廠建議：檢查冷卻水流量與熱交換器

本廠做法：[                              ]
為什麼不同：[                            ]

── 同機種其他警報的本廠做法（參考）──
E105：檢查冷卻水流量與熱交換器後降速運轉
E108：確認風扇運轉並清潔濾網
```

參考範例會大幅提高填寫品質與用語一致性，也降低「不知道要寫多細」的猶豫。**這比 AI 審核更早介入，效果也更直接。**

### 5.4 後台待審清單

管理員後台新增「待處理建議」提示（含未處理筆數），列表顯示：提交者、時間、警報、建議內容、目前的本廠做法（若有）。操作：接受（寫入 `local_solution`）／退回（填 `review_note`）。

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

除了格式檢查，讓 AI 摘要**現場做法與原廠做法的差異點**：

> 原廠要求停機後處理，本廠做法為不停機調整參數。

審核者看到摘要，能立刻判斷差異是否合理、有無安全疑慮。這是 AI 的強項——它做不了技術判斷，但很擅長指出兩段文字的差異，而那正是審核者最需要先看到的資訊。

### 6.4 一致性檢查

送審時**同時提供同機種的其他 `local_solution`** 給 AI 參考，讓它能發現「這台機器其他 20 筆都提到要先斷電，只有這筆沒提」。這是人工審核最容易漏的。

**【第一輪可行性檢查確認】審核用 AI 呼叫是獨立的新呼叫路徑，不沿用 `ai_pipeline.py` 現有的分析管線架構**——`ai_pipeline.py`（`run_pipeline()`/`run_confirmation()`/`run_correction()`）處理的是「拍照辨識警報畫面」這個任務，審核 `local_solution` 文字品質是完全不同的任務（不同 prompt、不同輸入輸出格式、不同觸發時機）。兩者只共用同一把 `GEMINI_API_KEY` 這一個底層資源，「增量成本很小」指的是不需要重新申請 API 額度或建立新的分析架構，但程式碼層級應該是新的、獨立的呼叫路徑，不要試圖把審核邏輯塞進 `ai_pipeline.py` 既有的 `run_pipeline()` 系列函式裡——那樣會讓一個函式承擔兩種不同語意的任務，違反這個專案一路堅持的單一職責原則（呼應 `_row_to_device()`「轉換只能在一個函式發生」同一種思維）。

---

## 七、⬜ 執行順序

每個階段都能獨立部署並單獨驗證，**不需要維護窗口**。

| 階段 | 內容 | 產生的價值 |
|---|---|---|
| 1 | 加四個欄位 ＋ 建 `alarm_suggestions` 表 | 零行為變更 |
| 2 | `PUT .../local` 端點 ＋ 白名單 ＋ 稽核 | 後端就緒 |
| 3 | 前台詳情卡片顯示兩層 | **既有知識可見** |
| 4 | 前台管理員編輯入口 ＋ 預填情境 | **知識開始累積** |
| 5 | 一般使用者建議 ＋ 待審表 ＋ 後台審核 | 擴大來源 |
| 6 | 缺處置清單排序 | 補資料有優先序 |
| 7 | AI 第一關分級 ＋ 差異摘要 | 降低審核負擔 |

**階段 3–4 是價值最高的一段**，做完就能讓管理員在現場順手記錄。

⚠️ **不要先做 AI 審核**——會變成「有很好的審核機制但沒東西可審」。

**【第一輪可行性檢查建議】階段 4 動工前，先花時間查清楚第九節那個「缺處置警報中有多少其實是原廠文件漏匯入」的問題**——使用者本人估計「一半一半」，比例不低。若成立，代表階段 6 的「缺處置清單」裡有相當比例應該用批次補匯入解決，而不是導向階段 4 的人工填寫流程。這個清查本身成本低（對照現有 CSV/原廠文件 vs `alarms.solution` 是否為空），建議排在階段 4 正式開工前、階段 1-2（資料庫與後端就緒）之後進行，避免管理員花時間手寫其實原廠早就有的內容。

---

## 八、⬜ 驗證方式

### 8.1 自動化

**路由白名單**：五個新端點加進 `ROUTE_AUTH_REGISTRY`。既有的裝飾器標記比對測試會自動守著。

**哨兵資料擴充**：撞名機種 `ACM001` 加一筆帶標記的現場做法：

```sql
update alarms
set local_solution   = '[SENTINEL] 本廠做法：此內容若出現在正式部門畫面，代表 local_solution 未依部門過濾',
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

---

## 十、刻意不做的事

- **不讓一般使用者直接編輯** — 一個人改、全部門立刻看到，工廠環境風險過高
- **不覆蓋 `solution`** — 保固爭議與稽核追溯都需要原廠版本
- **不做 AI 自動通過** — GMP 要求人工確認
- **不做版本歷史比對介面** — `alarm_history` 已有軌跡，介面等有需求再說
- **不在這次處理個人帳號** — 獨立的專案級改動
- **不把審核 AI 呼叫塞進 `ai_pipeline.py` 既有函式** — 見 6.4 節，任務語意不同，應為獨立呼叫路徑

---

## 十一、第一輪可行性檢查小結

**整體判斷：設計方向正確，可行。** 「原廠 vs 現場做法並存、不覆蓋」的核心決策，與 `PLAN_department_isolation.md` 一路貫徹的原則一致（保留可追溯性、明確白名單優於信任前端、單一職責的轉換點）。

本輪檢查確認/修正的五點，已整合進對應章節（標註「【第一輪可行性檢查...】」）：
1. `normalize()` 不動、新欄位走獨立白名單（4.1、4.3 節）
2. `local_updated_by` 用 `resolve_target_department()` 而非 `_confirmed_by()`（4.4 節）
3. 編輯入口要同時涵蓋搜尋結果與 AI 辨識結果兩種詳情卡片路徑（5.2 節）
4. AI 審核是獨立呼叫路徑，不沿用 `ai_pipeline.py` 既有函式（6.4 節）
5. 審核者角色第一版用 admin，不新增角色（2.2、9 節）

**下一步**：查清楚第九節「原廠文件缺口比例」，之後再排實際動工時間。動工時建議延續 `PLAN_department_isolation.md` 的節奏——分階段、每階段可獨立驗證、重要決策前先問過一輪外部意見。
