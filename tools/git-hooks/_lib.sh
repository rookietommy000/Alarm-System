#!/bin/sh
# Shared parser: first exact [T-digits] tag anywhere in the commit message.
progress_task() {
    LC_ALL=C grep -oE '\[T-[0-9]+\]' | head -n 1 | tr -d '[]'
}

progress_record() {
    progress_hash=$1
    progress_note=$2
    progress_context=$3
    progress_message=$(git log -1 --format=%B "$progress_hash" 2>/dev/null) || return 0
    progress_id=$(printf '%s\n' "$progress_message" | progress_task)
    [ -n "$progress_id" ] || return 0
    if ! PROGRESS_BY=hook "$progress_root/tools/progress.sh" \
        "$progress_id" 已commit "$progress_hash" "$progress_note" >/dev/null 2>&1; then
        printf 'progress: %s；%s 進度未記錄，不影響 Git 操作。\n' "$progress_context" "$progress_id" >&2
    fi
    return 0
}
