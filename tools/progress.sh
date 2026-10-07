#!/bin/sh
# Usage: PROGRESS_BY=老師 PROGRESS_TITLE='任務名稱' tools/progress.sh T-012 待辦 [ref] [note]
# PROGRESS_TITLE is required for 待辦; optional (empty by default) otherwise.
# ref contains only a job id, commit hash, or brief path, never full diffs/code.
# Keep note short. Requires Python 3 and its standard library (macOS/Linux).
set -eu

if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
    printf '%s\n' 'Usage: PROGRESS_BY=<role> PROGRESS_TITLE=<title> tools/progress.sh <task> <stage> [ref] [note]' >&2
    exit 1
fi
if [ -z "${PROGRESS_BY-}" ]; then
    printf '%s\n' 'progress.sh: 必須明確設定 PROGRESS_BY。' >&2
    exit 1
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
events="$repo_root/session_status/events.jsonl"

# Open append-only, creating an empty file if needed. Python holds the lock
# across reading the latest state AND appending so concurrent callers cannot
# validate against the same stale state. Invalid input never appends a record.
exec 9>>"$events"
exec python3 - "$events" "$@" <<'PY'
import datetime
import fcntl
import json
import os
import re
import sys


def main():
    path, task, stage, *optional = sys.argv[1:]
    ref, note = (optional + ["", ""])[:2]
    by = os.environ["PROGRESS_BY"]
    title = os.environ.get("PROGRESS_TITLE", "")
    stages = {"待辦", "委派中", "測試中", "QA中", "已commit", "測試失敗", "待你決定"}
    if not re.fullmatch(r"T-[0-9]+", task):
        raise ValueError("task 必須為 T- 加數字，例如 T-012")
    if stage not in stages:
        raise ValueError(f"不合法的階段：{stage}")
    if by not in {"使用者", "老師", "員工", "QA", "hook"}:
        raise ValueError(f"不合法的 PROGRESS_BY：{by}")
    if stage == "待辦" and not title.strip():
        raise ValueError("待辦 必須設定非空的 PROGRESS_TITLE 任務名稱")

    fcntl.flock(9, fcntl.LOCK_EX)
    previous = None
    with open(path, encoding="utf-8") as history:
        for number, line in enumerate(history, 1):
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"events.jsonl 第 {number} 行不是完整 JSON") from exc
            if (not line.endswith("\n") or not isinstance(event, dict)
                    or not isinstance(event.get("task"), str)
                    or not isinstance(event.get("stage"), str)
                    or event["stage"] not in stages):
                raise ValueError(f"events.jsonl 第 {number} 行紀錄不完整或階段不合法")
            if event["task"] == task:
                previous = event["stage"]

    if previous is None and stage != "待辦":
        raise ValueError("新任務只能從 待辦 開始")
    # The brief's explicit validation rules take precedence over its general
    # stage descriptions: no full sequence enforcement, but failure can ONLY
    # return to 待辦/委派中 (even 待你決定 is blocked here).
    if previous == "測試失敗" and stage not in {"待辦", "委派中"}:
        raise ValueError(f"測試失敗 只能轉回 待辦 或 委派中，不可轉為 {stage}")

    event = {
        "t": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": task, "title": title, "stage": stage, "by": by,
        "ref": ref, "note": note,
    }
    payload = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    # Descriptor 9 was opened by shell >>, never by truncating/replacing history.
    with os.fdopen(9, "ab", closefd=False) as output:
        output.write(payload)
    # Flush before unlocking; close the inherited descriptor before spawning.
    fcntl.flock(9, fcntl.LOCK_UN)
    os.close(9)
    os.path.isfile(generator := os.path.join(os.path.dirname(os.path.dirname(path)), "tools", "generate_progress.sh")) and os.spawnl(os.P_WAIT, "/bin/sh", "sh", generator)


try:
    main()
except (OSError, ValueError) as exc:
    print(f"progress.sh: {exc}", file=sys.stderr)
    sys.exit(1)
PY
