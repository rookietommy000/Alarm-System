# 階段 3-4 審查包：前台詳情卡片顯示兩層 ＋ 管理員編輯入口

生成時間：2026-08-15
對應 PLAN 章節：`PLAN_local_solution.md` 第七節「執行順序」表格階段 3、階段 4；對應第五節 5.1（詳情卡片顯示兩層）、5.2（編輯入口，僅 isAdmin 路徑）、5.3（對話框預填情境）

## 本階段完成項目

### 階段 3：前台詳情卡片顯示兩層（對應 5.1 節）— ✅ 完成

- 有 `local_solution` → 顯示「現場方案」，原廠 `solution` 收合在下方（預設收合，可展開）
- 只有 `solution`（無現場方案）→ 原樣顯示，不特別標示（避免既有畫面大幅變動，符合 5.1 節原始要求）
- 兩者皆無 → 顯示「這筆還沒有處置方式」提示
- 既有的 `sol_steps`（結構化處理步驟）功能優先權不變，這是既有功能，不在本次設計範圍內，維持原樣

### 階段 4：前台管理員編輯入口（對應 5.2、5.3 節）— 🟡 僅完成 isAdmin 路徑

- ✅ 5.2 節「入口要同時涵蓋兩種詳情卡片觸發路徑」：已確認搜尋結果與 AI 拍照辨識結果共用同一個 `openDetail`/`selected` Vue 元件，只改一處即涵蓋兩條路徑（未另外新增元件或重複邏輯）
- ✅ 5.2 節「兩個按鈕開同一個對話框」：目前只實作了其中一個按鈕（管理員「✏️ 編輯」），另一個按鈕（一般使用者「💡 補充現場方案」，走 `POST .../suggestions` 待審流程）**尚未實作**，留給階段 5
- ✅ 5.3 節「對話框要預填情境」：機種、代碼、描述、原廠建議、現有現場方案（若有）、現有理由（若有）全部預填；同機種其他警報的 `local_solution` 參考清單也已實作（最多列 5 筆）
- ⬜ 5.4 節「後台待審清單」：完全未動工，屬於階段 5 範圍

**已知缺口（如實記錄，非遺漏）**：`whoami.admin` 判斷只控制按鈕顯示，是 UI 層級的顯示邏輯，不是安全邊界——真正的授權仍在後端 `@admin_required` 裝飾器（見下方端點程式碼），這點已在 commit 前的 security-diff-review 中確認並記錄。

## 變更檔案清單

| 檔案 | 說明 |
|---|---|
| `frontend/index.html` | 詳情卡片新增四分支顯示邏輯；新增現場方案編輯對話框（markup + CSS + Vue data/methods） |
| `PLAN_local_solution.md` | 狀態區塊、第五節、第七節同步標記完成度；全文用字統一為「現場方案」 |

對應 commit（依序）：
- `1d6df6f` — 階段 3：四分支顯示邏輯
- `e6d84ec` — 階段 4：編輯入口（僅 isAdmin 路徑）
- `970146f` — 用字統一（「本廠做法」/「現場做法」→「現場方案」），涵蓋階段 3、4 產出的所有文字

## 完整程式碼變更

### `frontend/index.html` — 詳情卡片四分支顯示邏輯（階段 3）

```html
<template v-if="hasSolSteps(selected)">
  <dt>處理步驟</dt>
  <dd>
    <ul class="sol-steps-list">
      <li v-if="selected.sol_steps.check"><strong>① 檢查：</strong>{{ selected.sol_steps.check }}</li>
      <li v-if="selected.sol_steps.parts"><strong>② 零件：</strong>{{ selected.sol_steps.parts }}</li>
      <li v-if="selected.sol_steps.reset"><strong>③ 復歸：</strong>{{ selected.sol_steps.reset }}</li>
      <li v-if="selected.sol_steps.safety"><strong>④ 安全：</strong>{{ selected.sol_steps.safety }}</li>
    </ul>
  </dd>
  <template v-if="selected.solution">
    <dt></dt>
    <dd>
      <button class="old-sol-toggle" @click="showOldSolution = !showOldSolution">
        {{ showOldSolution ? '▲ 收起解決辦法' : '▼ 查看解決辦法' }}
      </button>
      <div v-if="showOldSolution" class="old-sol-body">{{ selected.solution }}</div>
    </dd>
  </template>
</template>
<template v-else-if="selected.local_solution">
  <dt>現場方案</dt>
  <dd>
    <div class="local-sol-block">
      <div class="local-sol-body">{{ selected.local_solution }}</div>
      <div v-if="selected.local_reason" class="local-sol-reason">為什麼不同：{{ selected.local_reason }}</div>
    </div>
    <button v-if="selected.solution" class="old-sol-toggle" @click="showOldSolution = !showOldSolution">
      {{ showOldSolution ? '▲ 收起原廠建議做法' : '▼ 原廠建議做法' }}
    </button>
    <div v-if="showOldSolution && selected.solution" class="old-sol-body">{{ selected.solution }}</div>
    <button v-if="whoami.admin" class="local-sol-edit-btn" @click="openLocalSolutionEditor">✏️ 編輯</button>
  </dd>
</template>
<template v-else-if="selected.solution">
  <dt>解決方案</dt>
  <dd>
    {{ selected.solution }}
    <button v-if="whoami.admin" class="local-sol-edit-btn" @click="openLocalSolutionEditor">💡 補充現場方案</button>
  </dd>
</template>
<template v-else>
  <dt>解決方案</dt>
  <dd>
    <span class="no-solution-hint">這筆還沒有處置方式</span>
    <button v-if="whoami.admin" class="local-sol-edit-btn" @click="openLocalSolutionEditor">💡 補充現場方案</button>
  </dd>
</template>
```

### `frontend/index.html` — 編輯對話框 markup（階段 4）

```html
<div v-if="localEdit.show" class="model-picker-backdrop" @click.self="closeLocalSolutionEditor">
  <div class="model-picker local-sol-editor">
    <div class="model-picker-title">補充現場方案</div>
    <div class="model-picker-sub">{{ localEdit.device_model }} · {{ localEdit.code }} · {{ localEdit.description }}</div>
    <div v-if="localEdit.vendorSolution" class="local-sol-editor-vendor">
      <div class="local-sol-editor-label">原廠建議</div>
      <div class="local-sol-editor-vendor-body">{{ localEdit.vendorSolution }}</div>
    </div>
    <label class="local-sol-editor-label" for="local-sol-input">現場方案</label>
    <textarea id="local-sol-input" class="local-sol-editor-input" rows="4" v-model="localEdit.local_solution" placeholder="實際在現場怎麼處理這個警報"></textarea>
    <label class="local-sol-editor-label" for="local-reason-input">為什麼不同（選填）</label>
    <textarea id="local-reason-input" class="local-sol-editor-input" rows="3" v-model="localEdit.local_reason" placeholder="與原廠建議不同的原因"></textarea>
    <div v-if="localEdit.references.length" class="local-sol-editor-refs">
      <div class="local-sol-editor-label">同機種其他警報的現場方案（參考）</div>
      <div class="local-sol-editor-ref-item" v-for="r in localEdit.references" :key="r.code">
        <strong>{{ r.code }}</strong>：{{ r.local_solution }}
      </div>
    </div>
    <div v-if="localEdit.error" class="local-sol-editor-error">{{ localEdit.error }}</div>
    <div class="local-sol-editor-actions">
      <button class="model-picker-cancel" @click="closeLocalSolutionEditor" :disabled="localEdit.saving">取消</button>
      <button class="local-sol-editor-save" @click="saveLocalSolution" :disabled="localEdit.saving">{{ localEdit.saving ? '儲存中…' : '儲存' }}</button>
    </div>
  </div>
</div>
```

### `frontend/index.html` — CSS（階段 3、4）

```css
/* 階段 3：現場方案顯示區塊 */
.local-sol-block { margin: 0 0 10px; padding: 10px 12px; background: var(--surface-hover); border-left: 3px solid var(--primary); border-radius: var(--radius-sm); }
.local-sol-label { font-size: 12px; font-weight: 700; color: var(--primary); margin-bottom: 4px; }
.local-sol-body { font-size: 14px; line-height: 1.6; white-space: pre-wrap; color: var(--text); }
.local-sol-reason { margin-top: 6px; font-size: 12px; color: var(--muted); white-space: pre-wrap; line-height: 1.5; }
.no-solution-hint { font-size: 13px; color: var(--muted); }

/* 階段 4：現場方案編輯 dialog（沿用既有 model-picker 的 backdrop/bottom-sheet 殼） */
.local-sol-editor { max-height: 88vh; overflow-y: auto; }
.local-sol-editor-label { font-size: 12px; font-weight: 700; color: var(--muted); }
.local-sol-editor-vendor { padding: 10px 12px; background: var(--surface-hover); border-radius: var(--radius-sm); display: flex; flex-direction: column; gap: 4px; }
.local-sol-editor-vendor-body { font-size: 13px; line-height: 1.6; white-space: pre-wrap; color: var(--muted); }
.local-sol-editor-input {
  width: 100%; box-sizing: border-box; font-family: inherit; font-size: 14px; line-height: 1.6;
  padding: 10px 12px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  background: var(--bg); color: var(--text); resize: vertical;
}
.local-sol-editor-input:focus { outline: 2px solid var(--primary); }
.local-sol-editor-refs { display: flex; flex-direction: column; gap: 6px; max-height: 20vh; overflow-y: auto; padding: 10px 12px; background: var(--surface-hover); border-radius: var(--radius-sm); }
.local-sol-editor-ref-item { font-size: 13px; line-height: 1.6; color: var(--muted); }
.local-sol-editor-error { padding: 10px 14px; background: #fef2f2; border: 1px solid #fca5a5; border-radius: 8px; color: #b91c1c; font-size: 14px; }
.local-sol-editor-actions { display: flex; gap: 8px; }
.local-sol-editor-actions .model-picker-cancel { flex: 1; }
.local-sol-editor-save {
  flex: 1; height: 48px; border: none; border-radius: var(--radius-sm, 0);
  background: var(--primary); color: var(--primary-ink); cursor: pointer;
  font-size: 15px; font-family: inherit; font-weight: 700;
}
.local-sol-editor-save:hover { filter: brightness(1.08); }
.local-sol-editor-save:disabled, .local-sol-editor-actions button:disabled { opacity: 0.6; cursor: not-allowed; }
.local-sol-edit-btn {
  display: block; margin-top: 8px; background: none; border: none; color: var(--primary);
  font-size: 12px; font-weight: 700; cursor: pointer; padding: 4px 0; font-family: inherit;
}
.local-sol-edit-btn:hover { color: var(--primary-hover); }
```

### `frontend/index.html` — Vue `data()` 新增欄位（階段 4）

```js
localEdit: {
  show: false, saving: false, error: '',
  department: '', device_model: '', code: '', description: '',
  vendorSolution: '', local_solution: '', local_reason: '',
  references: [],
},
```

`whoami` 預設值同步補上 `admin` 欄位（原本只有 `department`/`department_name`，後端 `/api/whoami` 其實一直有回傳 `admin`，只是前台未取用）：

```js
whoami: { department: null, department_name: null, admin: false },
```

### `frontend/index.html` — Vue methods（階段 4）

```js
async openLocalSolutionEditor() {
  const a = this.selected;
  if (!a) return;
  this.localEdit.show = true;
  this.localEdit.saving = false;
  this.localEdit.error = '';
  this.localEdit.department = this.whoami.department || '';
  this.localEdit.device_model = a.device_model;
  this.localEdit.code = a.code;
  this.localEdit.description = a.description || '';
  this.localEdit.vendorSolution = a.solution || '';
  this.localEdit.local_solution = a.local_solution || '';
  this.localEdit.local_reason = a.local_reason || '';
  this.localEdit.references = [];
  try {
    const res = await AlarmApi.get(`/api/alarms?device=${encodeURIComponent(a.device_model)}`);
    if (res.ok) {
      const all = await res.json();
      this.localEdit.references = all
        .filter(x => x.code !== a.code && x.local_solution)
        .slice(0, 5)
        .map(x => ({ code: x.code, local_solution: x.local_solution }));
    }
  } catch {}
},
closeLocalSolutionEditor() {
  if (this.localEdit.saving) return;
  this.localEdit.show = false;
},
async saveLocalSolution() {
  const e = this.localEdit;
  if (!e.local_solution || !e.local_solution.trim()) {
    e.error = '請填寫現場方案';
    return;
  }
  e.saving = true;
  e.error = '';
  try {
    const url = `/api/alarms/${encodeURIComponent(e.department)}/${encodeURIComponent(e.device_model)}/${encodeURIComponent(e.code)}/local`;
    const res = await AlarmApi.put(url, {
      local_solution: e.local_solution.trim(),
      local_reason: e.local_reason ? e.local_reason.trim() : '',
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      e.error = body.error || '儲存失敗，請稍後再試';
      e.saving = false;
      return;
    }
    const updated = await res.json();
    this.selected = updated;
    e.show = false;
    e.saving = false;
  } catch {
    e.error = '網路錯誤，請稍後再試';
    e.saving = false;
  }
},
```

### 對照：後端端點（未修改，階段 2 已完成，附上供對照前後端行為一致性）

`backend/app.py`：

```python
LOCAL_EDITABLE = {"local_solution", "local_reason"}

@app.put("/api/alarms/<department>/<device_model>/<code>/local")
@admin_required
def update_local_solution(department: str, device_model: str, code: str):
    """管理員直接編輯現場做法。只接受 local_solution/local_reason，
    其餘欄位一律忽略——這是防止原廠欄位（solution）被覆寫的最後一道
    （PLAN_local_solution.md 4.3 節），不能省。department 必須用
    resolve_target_department() 的目標部門，不能沿用 _confirmed_by()
    （那個服務的是無路徑部門段的端點，語意不同，見 4.4 節）。"""
    target = resolve_target_department(department)
    body = request.get_json(silent=True) or {}
    patch = {k: v for k, v in body.items() if k in LOCAL_EDITABLE}
    if not patch:
        abort(400, "沒有可更新的欄位")
    role = "superadmin" if is_superadmin() else "admin"
    patch["local_updated_by"] = f"{target}/{role}"
    patch["local_updated_at"] = datetime.now(timezone.utc).isoformat()
    old_row = alarms_store.get_one(department=target, match={"device_model": device_model, "code": code})
    if old_row is None:
        abort(404, "找不到此警報代碼")
    row = alarms_store.patch_one(department=target,
                                 match={"device_model": device_model, "code": code}, patch=patch)
    if row is None:
        abort(404, "找不到此警報代碼")
    audit_logger.log("local_update", department=target, new_data=row, old_data=old_row)
    return jsonify(row)
```

`GET /api/whoami`：

```python
@app.get("/api/whoami")
@public_endpoint
def whoami():
    dept_id = session.get("department")
    dept_name = None
    if dept_id and _use_supabase():
        dept = _dept_cached(dept_id)
        dept_name = dept.get("name") if dept else None
    return jsonify({
        "auth": is_logged_in(),
        "admin": is_admin(),
        "superadmin": is_superadmin(),
        "department": dept_id,
        "department_name": dept_name,
    })
```

## 驗證結果

### 自動化測試

```
$ pytest tests/ -q
......................................................................   [100%]
70 passed in 7.41s
```

（backend 完全未修改，這次是既有測試套件的回歸驗證，非新增測試——階段 3、4 是純前端改動，沒有對應的 pytest。）

### HTML template 結構完整性檢查

```
$ node -e "... 統計 <template v-...> 與 </template> 數量 ..."
template open: 12 close: 12
```

```
$ node -e "... 統計 <div 與 </div> 數量 ..."
div open: 133 close: 133
```

### 邏輯分支模擬驗證（Node.js 獨立腳本重現 v-if/v-else-if 判斷邏輯）

```js
// 五種情境 × 四分支選擇邏輯
sol_steps present -> sol_steps
local_solution + solution, no sol_steps -> local_solution
only solution -> vendor_solution
neither -> no_solution
local_solution only, no solution -> local_solution

// whoami.admin 按鈕顯示邏輯
admin user -> true
regular user -> false
unset (default) -> false

// saveLocalSolution 表單驗證邏輯
empty -> false
whitespace -> false
valid -> true
```

全部符合預期分支。

### 正式環境驗證

- 兩次 push（`1d6df6f`、`e6d84ec`、`970146f`）皆觸發 Render 自動部署
- 用 `curl` 直接抓取正式環境 `index.html` 靜態內容，確認新 CSS class（`local-sol-block`、`local-sol-edit-btn`、`local-sol-editor`）與最終用字「現場方案」都已出現在線上版本，非本機獨有

### security-diff-review（本專案內建 skill）結果

兩次改動都各自跑過一次，結論皆為 **SAFE TO COMMIT**。重點結論：
- `whoami.admin` 只控制按鈕顯示，是 UI 裝飾，真正授權邊界仍在後端 `@admin_required`
- `department` 值來自 session-derived 的 `whoami.department`，非使用者可自由輸入，無法被操弄成跨部門寫入
- 所有插值皆用 Vue `{{ }}`（預設 escape），沒有使用 `v-html`，無 XSS 注入面
- `saveLocalSolution` 送出的欄位對齊後端 `LOCAL_EDITABLE` 白名單，只送 `local_solution`/`local_reason`
- 錯誤訊息顯示直接透傳後端 `abort()` 的中文 description，未洩漏路徑/SQL/stack

### 未執行的驗證

- **未做實際瀏覽器互動測試**（例如用 Playwright/Selenium 實際點擊按鈕、送出表單、確認儲存後畫面更新）。這次驗證停留在「靜態結構正確」＋「邏輯分支模擬正確」＋「正式環境檔案確實更新」三層，沒有做端到端的使用者操作驗證。若專家認為這個功能的風險等級需要端到端驗證，需要額外安排。

## 已知未解決 / 待專家確認的問題

1. **階段 4 範圍縮減，只做了管理員路徑**：PLAN 5.2 節原始設計是「管理員編輯」與「一般使用者建議」共用同一個對話框、依角色走不同端點。這次只實作了管理員路徑（`whoami.admin` 才顯示按鈕、直接打 `PUT .../local`）。一般使用者的「補充現場方案」入口（應該打 `POST .../suggestions`，寫入待審表而非直接改資料）完全沒做，等階段 5 才會補上。**這代表目前一般使用者（非管理員）在前台完全看不到任何編輯/建議入口**——不是 bug，是刻意分階段的結果，但如實記錄以防被誤認為完整功能。

2. **`whoami.admin` 的信任層級**：`openLocalSolutionEditor`/`saveLocalSolution` 這兩個方法本身沒有再次檢查 `whoami.admin`（只有渲染按鈕時檢查）。理論上，一般使用者若懂得從瀏覽器 console 直接呼叫 `this.saveLocalSolution()`，前端不會攔，但後端 `@admin_required` 會擋（回 403）。這是「前端不做安全判斷，只做體驗判斷」的設計，符合 Vue SPA 的常規做法，但想請專家確認這個分工在稽核／GMP 情境下是否需要更明確的文件記錄（例如寫進 PLAN 第九節「已知限制」）。

3. **`references` 參考清單目前固定抓 5 筆、不分頁、不排序**：`openLocalSolutionEditor` 用 `GET /api/alarms?device=...` 抓同機種全部警報後在前端 `.filter().slice(0, 5)`。若某機種警報數量很大（目前資料規模下沒有這個問題，但未來不確定），這裡是全量抓回來才在前端過濾，沒有做後端分頁或限制筆數的查詢參數。目前判斷資料量可接受，先不處理，但列出來讓專家知道這個實作方式的成本假設。

4. **`local_solution` 編輯後沒有即時通知同部門其他人**：PLAN 2.2 節提到「一個人改、全部門立刻看到」，這句話目前的實作只保證「下次任何人重新查詢/整理該筆資料時會看到最新值」（因為儲存後 `this.selected = updated` 立即更新當下開著的詳情卡片），但如果同部門有其他人「當下」正開著同一筆警報的詳情卡片，不會自動推播更新給對方——除非那個人重新點開。這是靜態頁面重新整理才會拿到新值的正常行為，沒有做 WebSocket/輪詢同步，判斷這個落差可接受，但列出來確認。
