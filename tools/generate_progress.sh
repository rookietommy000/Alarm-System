#!/bin/sh
# Regenerate PROGRESS.md from events; Python standard library only.
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 - "$repo_root" <<'PY'
import datetime as dt
import html
import json
import os
from pathlib import Path
import sys
import tempfile


def cell(value):
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\r", " ").replace("\n", "<br>")


def generate(root):
    now = dt.datetime.now().astimezone()
    events = root / "session_status/events.jsonl"
    latest = {}
    titles = {}
    if events.exists():
        for number, line in enumerate(events.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            event = json.loads(line)
            timestamp = dt.datetime.fromisoformat(event["t"].replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError(f"第 {number} 行時間缺少時區")
            task = event["task"]
            # Timestamp first; later line breaks exact timestamp ties.
            key = (timestamp, number)
            if task not in latest or key > latest[task][0]:
                latest[task] = (key, event)
            # Later events may omit title: retain newest nonempty title.
            if event.get("title") and (task not in titles or key > titles[task][0]):
                titles[task] = (key, event["title"])
    total = len(latest)
    done = sum(event["stage"] == "已commit" for _, event in latest.values())
    percent = done * 100 / total if total else 0
    filled = done * 10 // total if total else 0
    lines = ["# 專案進度", "", f"最後更新：{now.isoformat(timespec='seconds')}", "",
             "## 總覽", "", f"進度條：{'▓' * filled}{'░' * (10 - filled)} {done}/{total} ({percent:.0f}%)",
             "", "## 任務清單", "", "| 任務 | 標題 | 狀態 | 最後更新 | 參照 |",
             "|---|---|---|---|---|"]
    waiting, stuck = [], []
    for task in sorted(latest):
        (timestamp, _), event = latest[task]
        time_text = timestamp.astimezone().isoformat(sep=" ", timespec="minutes")
        title = titles.get(task, (None, ""))[1]
        lines.append("| " + " | ".join(cell(v) for v in
                     (task, title, event["stage"], time_text, event.get("ref", ""))) + " |")
        age = now - timestamp
        if event["stage"] == "待你決定":
            minutes = max(0, int(age.total_seconds() // 60))
            waiting.append(f"- {cell(task)}：{cell(event.get('note') or '未提供補充說明')}（{minutes // 1440} 天 {minutes % 1440 // 60} 小時 {minutes % 60} 分鐘）")
        if event["stage"] != "已commit" and age > dt.timedelta(hours=24):
            stuck.append(f"- {cell(task)}：{cell(event['stage'])}，最後更新於 {time_text}")
    lines += ["", "## ⏸ 待你決定", ""] + (waiting or ["無"])
    lines += ["", "## ⚠ 卡住的任務（超過24小時沒有新紀錄）", ""] + (stuck or ["無"])
    # Replace atomically so a Markdown preview never sees half a report.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                         prefix=".progress-", delete=False) as output:
            temporary = output.name
            output.write("\n".join(lines) + "\n")
        os.replace(temporary, root / "PROGRESS.md")
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


try:
    generate(Path(sys.argv[1]))
except (OSError, ValueError, KeyError, TypeError) as exc:
    print(f"generate_progress.sh: 報表未更新：{exc}", file=sys.stderr)
    sys.exit(1)
PY
