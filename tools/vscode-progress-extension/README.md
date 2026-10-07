# Workspace Progress（T-D）

唯讀 VS Code 狀態列：監看 workspace 根目錄的 `session_status/events.jsonl`，
點擊開啟 `PROGRESS.md`。不產生事件、不改報表、不發送通知、不做排序 UI。
多根 workspace 使用第一個根目錄；請將目標 repo 排在第一個，或單獨開啟。

## 獨立安裝與編譯

需要 Node.js/npm 與 VS Code 1.85 以上。所有 npm 操作都在此子目錄執行：

```sh
cd tools/vscode-progress-extension
npm install --workspaces=false
npm run compile
```

依賴與 lockfile 留在本目錄，`node_modules/` 與 `out/` 由本目錄的
`.gitignore` 排除，不使用主 repo 的套件設定。沒有執行期 npm 依賴。
狀態列透過 `createStatusBarItem` 建立；manifest 註冊啟動事件與開報表命令，
VS Code 並沒有 `contributes.statusBar` 這個靜態貢獻點。

## 由驗收者自行打包／安裝

本次交付不執行打包、不安裝到使用者 VS Code、不發布 Marketplace。
需要驗收 UI 時，在本目錄自行執行以下命令（npx 可能下載 CLI）：

```sh
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository --skip-license
```

VS Code 命令面板選「Extensions: Install from VSIX…」，選產生的 `.vsix`，
依提示重新載入。`internal-tools` 是內部 manifest 識別，不需要註冊發布者帳號。
開啟 repo 根目錄，而不是本擴充套件子目錄。卸載可在 Extensions 面板操作。

## 統計與錯誤處理

- 同 task 按含時區時間戳取最新事件；時間相同取後一行，保留微秒順序。
- 分子只算最新狀態為「已commit」的任務；分母是任務總數，沒有新增「已取消」。
- 方塊數採 `floor(已commit * 10 / 任務總數)`，與 T-C 實作一致。
  T-D brief 的四捨五入敘述與 T-C 不同；依本次要求以 T-C 一致性為準。
- 測試失敗與待你決定為零時省略；最新 hash 從全部已commit事件按時間取最新，
  並非僅從各 task 最新狀態選取。無 commit 時顯示「最新 —」。
- tooltip 的卡住數僅計算超過 24 小時且不是已commit的任務。
  以本次檔案更新時計算；沒有背景計時或 polling，靜置跨過 24 小時不會自行刷新。
- 監看 events 檔及 session_status 目錄的建立、修改、刪除；使用 FileSystemWatcher。
- 缺事件檔／空檔顯示「進度：尚無紀錄」，缺 session_status 顯示「進度：未偵測到專案」。
- 壞 JSON 或無效欄位只跳過該行，在 Output → Workspace Progress 留 warning；不彈窗。
- 點擊時報表不存在／不可讀，顯示短暫狀態列提示並寫 output，不跳通知。

## 手動驗收（六項）

建議在暫存 repo 副本操作，下列破壞格式的測試不要用正式 append-only 紀錄。
本專案依要求不提供自動化測試；編譯通過不代表已完成 VS Code UI 驗收。

1. **安裝隔離與編譯**：記錄主 repo 的 package.json/package-lock.json 狀態，
   在上述子目錄跑 `npm install --workspaces=false` 與 `npm run compile`。
   確認 exit code 0，產生本目錄 node_modules、package-lock.json、out/extension.js；
   主 repo 套件檔與 node_modules 不應有新增安裝內容。
2. **打包、安裝、首次顯示**：依前節由驗收者打包安裝，開啟測試 repo。
   建立 session_status/events.jsonl，放入下列四行，再重新開啟 workspace。
   預期 `▓▓░░░░░░░░ 1/4 · 🔴1 · ⏸1 · 最新 abcdef1`。
   tooltip 不應將舊的 T-1 已commit計為卡住；其餘舊任務會計入。
3. **即時更新與排序**：將 T-2 新增為較新時間的已commit（保留七個欄位，ref 換 hash），
   儲存後不重啟便應顯示 2/4，🔴 消失；再追加較舊的 T-2 測試失敗事件，
   統計不可退回。將 T-3 更新為 QA中，⏸ 應消失。
   可另用同一毫秒不同微秒、不同時區但相同瞬間檢查時間順序與同時間後行優先。
4. **點擊報表**：在測試根目錄準備 PROGRESS.md，點狀態列應開啟該檔。
   暫時改名後再次點擊，應只有短暫狀態列提示與 output 訊息，不 crash、不彈窗。
5. **缺檔與非 repo**：改名 events.jsonl，應即時顯示尚無紀錄；恢復後數字回來。
   空檔同樣顯示尚無紀錄。再改名 session_status 或開啟不含此目錄的資料夾，
   應顯示未偵測到專案；重新建立目錄與事件檔後應恢復，不需重啟。
6. **壞行**：在正常事件之間加入一行 `not-json`，儲存後其他事件仍正確統計；
   Output → Workspace Progress 應顯示行號 warning，沒有通知彈窗。
   也試全部壞行、無時區時間、缺欄位，應跳過並顯示尚無紀錄，不 crash。

測試用 fixture（僅寫入測試副本）：

```jsonl
{"t":"2026-01-01T00:00:00+08:00","task":"T-1","title":"完成","stage":"已commit","by":"hook","ref":"abcdef1234567890","note":""}
{"t":"2026-01-01T00:00:00+08:00","task":"T-2","title":"失敗","stage":"測試失敗","by":"員工","ref":"","note":""}
{"t":"2026-01-01T00:00:00+08:00","task":"T-3","title":"決策","stage":"待你決定","by":"老師","ref":"","note":"請確認"}
{"t":"2026-01-01T00:00:00+08:00","task":"T-4","title":"QA","stage":"QA中","by":"QA","ref":"","note":""}
```
