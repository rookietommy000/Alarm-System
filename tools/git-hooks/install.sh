#!/bin/sh
# Install forwarding scripts, never replace existing hooks (including symlinks).
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$repo_root"
if git config --get core.hooksPath >/dev/null 2>&1; then
    printf '%s\n' '偵測到 core.hooksPath，未安裝；請確認既有 hook 管理方式。' >&2
    exit 1
fi
[ -x tools/progress.sh ] || { printf '%s\n' 'tools/progress.sh 不存在或不可執行。' >&2; exit 1; }
hooks_dir=$(git rev-parse --git-path hooks)
for hook in post-commit pre-push; do
    if [ -e "$hooks_dir/$hook" ] || [ -L "$hooks_dir/$hook" ]; then
        printf '偵測到既有 hook：%s；沒有覆寫，請確認處理方式。\n' "$hooks_dir/$hook" >&2
        exit 1
    fi
done
mkdir -p "$hooks_dir"
for hook in post-commit pre-push; do
    # noclobber also protects against another installer creating a hook
    # after the initial check. The installed files contain no absolute paths.
    (
        set -C
        cat > "$hooks_dir/$hook" <<EOF
#!/bin/sh
root=\$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
if [ -x "\$root/tools/git-hooks/$hook" ]; then
    if ! "\$root/tools/git-hooks/$hook" "\$@"; then
        printf '%s\n' 'progress: hook 記錄未完成，不影響 Git 操作。' >&2
    fi
fi
exit 0
EOF
    )
    chmod +x "$hooks_dir/$hook"
done
printf '%s\n' '已安裝 post-commit / pre-push 進度 hooks。'
