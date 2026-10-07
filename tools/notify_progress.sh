#!/bin/sh
# Usage: notify_progress.sh <task> <stage> <title>
if [ "$#" -ne 3 ]; then
    printf '%s\n' 'Usage: notify_progress.sh <task> <stage> <title>' >&2
    exit 1
fi
case "$2" in
    已commit) heading='✅ 已完成' ;;
    待你決定) heading='⏸ 待你決定' ;;
    *) exit 0 ;;
esac

# Pass user text as argv, never interpolate it into AppleScript source.
exec osascript - "$heading" "$1 $3" <<'APPLESCRIPT'
on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
end run
APPLESCRIPT
