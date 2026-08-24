# 登入身分與流程對照表

本文件是**目前程式碼實際行為**的權威記錄，逐條對應 `backend/app.py` 的路由。若跟這份文件不符，以程式碼為準，並回頭更新這份文件——不要憑印象或猜測。

## 一、密碼欄位

每個部門在 Supabase `departments` 表有**兩個獨立密碼欄位**，彼此互不影響：

| 欄位 | 用途 | 誰用來登入 |
|---|---|---|
| `pw_hash` | 一般密碼 | `/login` 表單 |
| `admin_pw_hash` | 管理員密碼 | `/admin/login` 表單 |

兩者是完全獨立設定的密碼，**不是同一組密碼的兩種呈現**。已用程式查證（2026-08-18）：`mf4d`、`mf4d_2`、`zztest` 三個部門的 `pw_hash` 與 `admin_pw_hash` 雜湊值皆不同。

超管另有 `SUPERADMIN_PASSWORD`（環境變數，非部門欄位），部門欄位固定填 `__super__`（`SUPER_DEPT_SENTINEL`）才會走這條分支，見 `_do_login()` 第 338-345 行。

## 二、四個登入/存取入口

| 入口 | HTTP 方法 | 比對欄位 | 成功後 session | 頁面本身要不要先登入才看得到 |
|---|---|---|---|---|
| `/login` | GET（顯示表單）/ POST（送出） | `pw_hash` | `admin=False` | 不用，`@public_endpoint` |
| `/admin/login` | GET / POST | `admin_pw_hash` | `admin=True` | 不用，`@public_endpoint`（但已登入管理員會被導去 `/admin`） |
| `/app`（前台頁面） | GET | — | 不檢查，純顯示頁面 | **不用，`@public_endpoint`** |
| `/admin`（後台頁面） | GET | — | 讀取既有 session | **要，`@admin_required`，非管理員直接 302 到 `/admin/login`** |

**最容易誤解的一點**：`/app`（前台）本身頁面誰都能打開，不代表誰都能查到資料——資料是靠前端呼叫 `/api/*` 時，後端逐一檢查 session 決定要不要給。`/admin`（後台）則是頁面本身就被攔住，非管理員連 HTML 都拿不到。

## 三、身分只有兩個維度：`auth` 與 `admin`

`GET /api/whoami` 回傳：

```json
{
  "auth": true/false,      // 有沒有登入（任何一種身分）
  "admin": true/false,     // 是不是管理員（含超管）
  "superadmin": true/false // 是不是超管
}
```

前台 `frontend/index.html` 只看 `admin` 這一個布林值決定顯示什麼按鈕：

| `whoami.admin` | `whoami.auth` | 前台看到的按鈕 | 送出後的行為 |
|---|---|---|---|
| `true` | `true` | ✏️ 編輯 | `PUT /api/alarms/.../local`，直接寫入 `alarms.local_solution`，立即生效 |
| `false` | `true` | 💡 提出建議 | `POST /api/alarms/.../suggestions`，寫入 `alarm_suggestions` 待審表，需管理員在 `/admin` 的「待審建議」分頁按「接受」才會生效 |
| `false` | `false` | 兩個按鈕都不顯示（未登入看不到任何編輯入口） | — |

這個判斷式在 `frontend/index.html`（約第 1072-1073 行）：

```html
<button v-if="whoami.admin" ...>✏️ 編輯</button>
<button v-else-if="whoami.auth" ...>💡 提出建議</button>
```

**`whoami.admin` 的值完全由「你用哪組密碼、走哪個入口登入」決定，不是「你現在人在前台還是後台」決定。** 一旦登入拿到 `admin=True` 的 session，這個 session 對前台、後台都生效——去前台一樣是管理員視角，不需要特地跑去 `/admin` 才算數。

## 四、常見誤判與排除方式

### 誤判 1：「前台沒有分身份」
**排除方式**：直接看第三節的判斷式在程式碼裡確實存在（`v-if="whoami.admin"` / `v-else-if="whoami.auth"`），是兩個不同分支。若實測時只看到過「編輯」從沒看過「提出建議」，代表你用的帳號目前每次登入都被判定為 `admin=true`，不代表程式沒有另一半邏輯。

### 誤判 2：「我從 /login 登入卻看到編輯按鈕，代表兩個入口密碼混在一起」
**先排除 session 殘留再下結論**：`/login` 的 GET 頁面（第 233-238 行）有 `if is_logged_in(): return redirect("/")`——如果瀏覽器裡已經有一個**未登出**的登入 session（不論之前是從 `/login` 還是 `/admin/login` 登入的），再打開 `/login` 頁面會被直接導去 `/`，**表單根本不會顯示、也不會重新送出**，你看到的是延續舊 session 的畫面，不是這次操作的結果。

**正確測試方式**：先確認 `/logout`（清空 session）或用全新的無痕視窗，再登入，才能拿到乾淨的結果。

### 誤判 3：「這組密碼登進 /login 又登進 /admin/login，兩邊都成功，代表密碼相同」
**不成立**：兩個入口各自比對不同欄位（`pw_hash` vs `admin_pw_hash`），**除非這兩個欄位真的被設成同一組明文**，否則用同一組密碼字串不可能兩邊都登入成功。若真的兩邊都能登入，代表部門建立/重設密碼時，`pw_hash` 和 `admin_pw_hash` 被設成了同一組明文——這是資料設定的結果，不是程式邏輯把兩者混用。

## 五、要驗證「一般使用者建議 → 待審核」流程，具體要做什麼

⚠️ **本節描述的流程目前已停用**（commit `ee85162`，見 `PLAN_local_solution.md` 「審核路徑停用（決策記錄）」）。`alarm_suggestions` 表與三支端點保留、已對正式環境端到端驗證過，待個人帳號功能完成後重新評估啟用。**目前照本節操作會走到一條沒有前端入口的死路徑**——`dashboard.html` 的待審清單 UI 已移除，管理員登入後看不到任何待審項目。現行做法是所有登入者可直接編輯 `local_solution`，見 `PLAN_local_solution.md` 第七節階段 5。

1. 確認要測試的部門，其 `pw_hash` 對應的明文密碼（不是 `admin_pw_hash`）——如果不知道，需要用 `backend/gen_department_hashes.py` 或直接對 Supabase 執行 UPDATE 重設 `pw_hash`，並记下你剛设的新明文
2. 全新無痕視窗，開 `https://alarm-system-1.onrender.com/login`（不要碰 `/admin/login`）
3. 用該部門 ID + 第 1 步的一般密碼登入
4. 開發者工具 Console 執行 `fetch('/api/whoami').then(r=>r.json()).then(console.log)`，**確認 `admin: false`、`auth: true`** 再繼續，這是防呆步驟，避免又卡在身份判斷錯誤上
5. 確認後，點開一筆警報詳情，應該看到「💡 提出建議」或「💡 補充現場方案」，送出
6. 另開一個視窗（或同一個瀏覽器登出後改用管理員密碼登入 `/admin/login`），進 `/admin` 左側「待審建議」分頁，應該看到剛才那筆
