#!/bin/sh
# Read-only diagnostic; no files, hooks, scheduling, or repairs.
# Latest/stuck logic's single source of truth: generate_progress.sh lines 32-36,
# 55 and 59 (6 executable lines). Copied with approval because T-C is a heredoc,
# not an importable module. Changes to that logic MUST be synchronized here.
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 - "$repo_root" <<'PY'
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import sys


def plain(value):
    return str(value).replace("\r", " ").replace("\n", " ").replace("`", "&#96;")


def report(root):
    now = dt.datetime.now().astimezone()
    events = root / "session_status/events.jsonl"
    latest = {}
    inconsistent = []
    # Fail explicitly outside Git rather than misreporting all refs as missing.
    if subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError("腳本所在根目錄不是 Git repository")
    if events.exists():
        for number, line in enumerate(events.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            event = json.loads(line)
            timestamp = dt.datetime.fromisoformat(event["t"].replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError(f"第 {number} 行時間缺少時區")
            task = event["task"]
            key = (timestamp, number)
            if task not in latest or key > latest[task][0]:
                latest[task] = (key, event)
            # Check EVERY committed record, not only each task's latest state.
            if event["stage"] == "已commit":
                ref = event.get("ref", "")
                prefix = f"- {plain(task)}：events.jsonl記錄已commit於{plain(event['t'])}，但"
                if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", ref):
                    inconsistent.append(prefix + f"commit hash `{plain(ref)}` 為空或格式不合法")
                elif subprocess.run(
                    ["git", "-C", str(root), "cat-file", "-e", ref + "^{commit}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ).returncode:
                    inconsistent.append(prefix + f"commit hash `{ref}` 在git歷史中找不到")
    stuck = []
    for task in sorted(latest):
        (timestamp, _), event = latest[task]
        age = now - timestamp
        if event["stage"] != "已commit" and age > dt.timedelta(hours=24):
            minutes = int(age.total_seconds() // 60)
            time_text = timestamp.astimezone().isoformat(sep=" ", timespec="minutes")
            stuck.append(f"- {plain(task)}：目前狀態{plain(event['stage'])}，最後更新於{time_text}"
                         f"（距今{minutes // 1440} 天 {minutes % 1440 // 60} 小時 {minutes % 60} 分鐘）")
    count = len(inconsistent) + len(stuck)
    lines = [f"# 狀態檢查日報 — {now.isoformat(timespec='seconds')}", "",
             "## 紀錄與git不一致", ""] + (inconsistent or ["無"])
    lines += ["", "## 卡住的任務（超過24小時無新紀錄，非終態）", ""] + (stuck or ["無"])
    lines += ["", "## 總結", "", f"- 檢查的任務總數：{len(latest)}",
              f"- 發現異常：{count} 項" + ("（無異常）" if count == 0 else "")]
    print("\n".join(lines))


try:
    report(Path(sys.argv[1]))
except (OSError, ValueError, KeyError, TypeError) as exc:
    print(f"status_check.sh: 檢查失敗：{exc}", file=sys.stderr)
    sys.exit(1)
PY
