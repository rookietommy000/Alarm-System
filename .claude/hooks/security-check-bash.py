#!/usr/bin/env python3
"""PreToolUse Bash hook — block dangerous shell commands.

Reads tool input from stdin (JSON). Exit 2 + stderr to block with a reason
Claude can read and act on.
"""
import json
import re
import sys

DANGEROUS = [
    # ——— Remote code execution ———
    (r"curl\s+[^|&;]*\|\s*(sh|bash|zsh|ksh|fish)\b",       "curl 管線直接執行遠端腳本 (curl | sh)"),
    (r"wget\s+[^|&;]*\|\s*(sh|bash|zsh|ksh|fish)\b",       "wget 管線直接執行遠端腳本 (wget | sh)"),
    (r"(curl|wget)\s+[^\s]*\s+-O\s+[^\s]+\s*(&&|;)\s*(sh|bash|\./)", "下載後立即執行腳本"),

    # ——— Install from arbitrary URL / git ———
    (r"\bpip\s+install\s+[^#\n]*?(git\+|https?://)",       "pip install 外部 URL 或 git repo"),
    (r"\bnpm\s+(install|i|add)\s+[^#\n]*?(git\+|https?://)", "npm install 外部 URL 或 git repo"),
    (r"\byarn\s+add\s+[^#\n]*?(git\+|https?://)",          "yarn add 外部 URL 或 git repo"),
    (r"\bgem\s+install\s+[^#\n]*?https?://",               "gem install 外部 URL"),
    (r"\bcargo\s+install\s+--git\s+",                      "cargo install --git 外部 repo"),
    (r"\bgo\s+install\s+[^\s]+@",                          "go install 任意 module"),

    # ——— Destructive ———
    (r"\brm\s+-[rRfF]+\s*/(?:\s|$)",                       "rm -rf / — 摧毀根目錄"),
    (r"\brm\s+-[rRfF]+\s+~(\s|$|/\s|/$)",                  "rm -rf ~ — 摧毀家目錄"),
    (r"\brm\s+-[rRfF]+\s+\$HOME",                          "rm -rf $HOME"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}",                  "Fork bomb"),
    (r"\bdd\s+[^#\n]*\bof=/dev/(sd|nvme|disk|rdisk)",      "dd 寫到實體磁碟"),
    (r"\bmkfs\.",                                          "格式化檔案系統 (mkfs)"),
    (r"\bshred\s+",                                        "shred — 不可逆抹除"),

    # ——— Permission weakening ———
    (r"\bchmod\s+(-R\s+)?[0-7]*777",                       "chmod 777 — 任意使用者可讀寫執行"),
    (r"\bsudo\s+chown\s+-R\s+\S+\s+/",                     "遞迴改 / 擁有者"),

    # ——— Writing to sensitive paths ———
    (r"(>|>>|tee\s+)\s*/etc/(?!hosts\.tmp)",               "寫入 /etc/"),
    (r"(>|>>|tee\s+)\s*~/\.ssh/",                          "寫入 ~/.ssh/"),
    (r"(>|>>|tee\s+)\s*~/\.aws/",                          "寫入 ~/.aws/"),
    (r"(>|>>|tee\s+)\s*~/\.gnupg/",                        "寫入 ~/.gnupg/"),

    # ——— Exfiltration ———
    (r"\benv\s*\|\s*(curl|wget|nc|netcat)\b",              "把環境變數送到網路"),
    (r"(cat|tail)\s+~/\.(ssh|aws|gnupg)[^\s]*\s*\|\s*(curl|wget|nc)", "把金鑰檔送到網路"),
    (r"\bcurl\s+[^|&;]*\s+--data[- ]?(binary|raw)?\s+[\"']?@~/\.(ssh|aws|gnupg)", "上傳金鑰檔"),

    # ——— Bypass security ———
    (r"\bssh\s+[^\n]*-o\s+StrictHostKeyChecking=no",       "SSH 停用 host key 驗證"),
    (r"\bgit\s+[^\n]*-c\s+http\.sslVerify=false",          "git 停用 SSL 驗證"),
    (r"\bcurl\s+[^\n]*(-k|--insecure)\b",                  "curl --insecure 略過 TLS 驗證"),
    (r"\bnpm\s+[^\n]*--ignore-scripts=false.*--unsafe-perm", "npm 關閉 script 沙箱"),

    # ——— Credential / history tampering ———
    (r">\s*~/\.bash_history",                              "竄改 bash history"),
    (r"\bhistory\s+-c\b",                                  "清除 shell history"),
    (r"\bunset\s+HIST",                                    "停用 history 紀錄"),
]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        return 0

    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if not cmd:
        return 0

    hits = []
    for pat, desc in DANGEROUS:
        if re.search(pat, cmd, re.IGNORECASE):
            hits.append(desc)

    if not hits:
        return 0

    print("🚫 security-check-bash 擋下以下風險：", file=sys.stderr)
    for d in hits:
        print(f"  • {d}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Command:", file=sys.stderr)
    print(f"  {cmd}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "若此命令為刻意執行，請先向使用者解釋風險並取得明確同意後，再以不觸發規則的方式改寫。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
