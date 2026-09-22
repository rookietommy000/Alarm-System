---
name: security-diff-review
description: 針對本輪工作中「剛改動的檔案」做聚焦安全 review（非全專案）。適用於完成一段編輯後、commit 前快速驗證不要把新漏洞帶進去。範圍較小、深度較深，並結合本專案架構脈絡判斷。
---

# security-diff-review — 近期改動安全 review

## 與其他 review 的差別

| 工具 | 範圍 | 深度 | 用途 |
|------|------|------|------|
| `security-check-code` hook | 單檔，regex | 淺 | 每次 Edit 自動擋 |
| `security-review`（內建） | git 分支 pending changes | 中 | 泛用 |
| **`security-audit`**（本專案） | 全專案 | 深 | 上線前 / 定期 |
| **`security-diff-review`**（本專案，此 skill） | 近期改動 | 深 | commit 前最後一道 |

## 決定「改動範圍」的順序

此專案目前**不是 git repo**（`Is a git repository: false`），因此：

1. **如果使用者在 args 指定了檔案/目錄** → 只審該範圍
2. **如果專案已 `git init`** → 審 `git diff HEAD` + `git status` 的 untracked files
3. **否則 fallback**：用 `find . -type f -newer <最久修改的 dotfile> -not -path '*/.venv/*' -not -path '*/.pytest_cache/*' -not -path '*/.git/*'` 抓出最近 6 小時內被修改過的原始碼檔
4. **仍為空** → 明確告知使用者，詢問要 review 的範圍

## Review 步驟

對選定的每個檔案：

1. **`Read` 完整內容**（不要只看片段 — 關聯函式可能在前後）
2. **判斷改動意圖**：這檔案是新增端點？改 storage？改前端？不同意圖會觸發不同的檢查清單
3. **套用專案上下文**：
   - 這是 Flask API + JsonStore，**任何接到 `request.` 的路徑、檔名、key 都要特別留意**
   - 前端是 Vue 3 CDN，Vue template 預設會 escape，但 `v-html` 不會
   - `normalize()` 在 [backend/app.py:27](backend/app.py#L27) 是集中驗證點 — 新增欄位要走這裡
   - `severity` 是白名單 `{"嚴重","警告","資訊"}`，繞過的修改要警示
4. **用 `security-check-code` hook 邏輯當基線**，再手動覆核以下它**無法偵測**的問題：
   - 授權繞過（新端點是否該有 auth？目前全無 auth — 加了更敏感的端點要標示）
   - 資料驗證缺漏（型別、長度、字元白名單）
   - 非預期的副作用（例如讀 `data/*.json` 外的檔案）
   - 錯誤訊息 leak（`abort(400, "...")` 的訊息是否洩漏內部路徑、SQL、stack）
   - CORS / CSRF 影響（加 POST 端點等於擴大攻擊面）
   - 檔案上傳（本專案目前沒有，若新增 → 必須檢查 MIME、大小、路徑）

## 輸出格式

**簡短、聚焦、可行動**。不做全專案報告。

```
## Diff Review — <N> files

### ✅ <filename>
- 無發現，或：低風險提醒（一兩行）

### ⚠️ <filename>
**L<line>** — <問題>
  Evidence: `...`
  Fix: <具體建議（一行）>

---
Verdict: **SAFE TO COMMIT** / **FIX BEFORE COMMIT** / **DISCUSS**
```

## 規則

- **只 review 改動範圍** — 看到無關的老問題請留給 `security-audit`，不要發散
- **每個問題都要 actionable**（可以直接複製修法去貼）
- **不要重複 hook 已經擋下的東西** — 那些已被擋就不會進來；你要找的是 hook 抓不到的
- **給明確結論**（SAFE / FIX / DISCUSS），不要模稜兩可
- 若改動只是 typo/註解/重新命名無邏輯變化 → 1 行結案：`No logic change — nothing to review.`
