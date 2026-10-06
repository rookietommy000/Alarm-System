# Git 進度事件 hooks

新 clone 不會自動安裝 hook。於 repo 執行一次：

```sh
sh tools/git-hooks/install.sh
```

需要 Git、POSIX sh，以及 `tools/progress.sh` 所需的 Python 3。
安裝器在 Git hooks 目錄建立轉接腳本，呼叫本目錄的可執行腳本；
不改 Git 設定、不覆寫既有 hook（包含失效 symlink）。遇到既有
post-commit/pre-push 或 core.hooksPath 時停止，請先由使用者確認處理方式。
重跑安裝也會保留原檔並停止。

任務須先由呼叫者以 PROGRESS_BY、PROGRESS_TITLE 建立「待辦」。
hook 不建立任務、不重設狀態；T-A 拒絕寫入時只印一行提示，Git 照常繼續。
commit 訊息第一個 `[T-數字]` 標籤決定任務，無標籤時靜默跳過。
post-commit 寫入 `stage=已commit`、`by=hook`、`ref=完整 hash`、`note=""`。

## Push 選擇：A（pre-push）

涵蓋終端機、Codex、Claude Code 等透過一般 Git hook 執行的 push，
不依賴 Claude Code session 設定。依 stdin 的實際 ref 更新選取 push 範圍
最新 commit；不使用當前 HEAD，也不回溯尋找較舊的帶標籤 commit。
多 ref 時按候選 commit 的 committer timestamp 選最新一筆，同時間以 hash 排序。
刪除 ref、非 commit tag、空範圍不寫入；新 ref 或本機缺遠端舊物件時，
以本機已知的該 remote refs 排除歷史，不自動 fetch，範圍精度受本機資訊限制。

經確認的事件格式：`stage=已commit`、`by=hook`、`ref=完整 hash`，
`note="push 嘗試（pre-push，尚未確認成功）"`。
這不是 push 成功證明：遠端拒絕、網路失敗或 dry-run 都可能已留下事件。
不做事後修正。`--no-verify`、覆寫 hooks 設定等繞過 Git hook 的操作不會記錄。

## 驗收

```sh
.venv/bin/python -m pytest tools/git-hooks/test_hooks.py -q
.venv/bin/python -m pytest tests/ -q
```

hook 測試在暫存 repo／本機 bare remote 執行真實 commit、push 和 clone，
不修改工作專案的 commit 或真實 events.jsonl。
