#!/usr/bin/env python3
"""PostToolUse hook for Edit/Write/MultiEdit — scan the modified file for
common code-level security issues. Exit 2 + stderr surfaces findings to Claude.

Add `# nosec` (Python) or `// security-check:ignore` (JS/TS/HTML) on the
offending line to suppress an individual false positive.
"""
import json
import re
import sys
from pathlib import Path

SCAN_EXT = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".vue", ".html", ".htm", ".sh", ".bash", ".zsh",
    ".yml", ".yaml", ".toml", ".cfg", ".ini", ".env",
    ".json",
}

RULES = [
    # ——— Secrets ———
    ("SECRET", re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
     "私鑰材料 (PRIVATE KEY)"),
    ("SECRET", re.compile(r"AKIA[0-9A-Z]{16}"),
     "AWS Access Key ID"),
    ("SECRET", re.compile(r"aws_secret_access_key\s*=\s*['\"][A-Za-z0-9/+=]{30,}['\"]", re.I),
     "AWS Secret Access Key"),
    ("SECRET", re.compile(r"ghp_[A-Za-z0-9]{30,}"),
     "GitHub personal access token"),
    ("SECRET", re.compile(r"gh[oprsu]_[A-Za-z0-9]{30,}"),
     "GitHub OAuth/refresh/server token"),
    ("SECRET", re.compile(r"xox[abpsr]-[A-Za-z0-9-]{10,}"),
     "Slack token"),
    ("SECRET", re.compile(r"sk-[A-Za-z0-9]{20,}"),
     "OpenAI / Anthropic-style API key (sk-...)"),
    ("SECRET", re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|passwd|password|client[_-]?secret)"
        r"\s*[:=]\s*['\"][A-Za-z0-9_\-./+=]{12,}['\"]"),
     "可能的硬編碼憑證"),

    # ——— Dangerous Python ———
    ("PY", re.compile(r"\beval\s*\("),             "eval() — 任意程式碼執行"),
    ("PY", re.compile(r"\bexec\s*\("),             "exec() — 任意程式碼執行"),
    ("PY", re.compile(r"subprocess\.[A-Za-z_]+\([^)\n]*shell\s*=\s*True"),
                                                   "subprocess 使用 shell=True"),
    ("PY", re.compile(r"\bos\.system\s*\("),       "os.system — 殼層注入風險"),
    ("PY", re.compile(r"\bos\.popen\s*\("),        "os.popen — 殼層注入風險"),
    ("PY", re.compile(r"\byaml\.load\s*\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)"),
                                                   "yaml.load 未指定 SafeLoader"),
    ("PY", re.compile(r"\bpickle\.load[s]?\s*\("), "pickle 反序列化不可信資料"),
    ("PY", re.compile(r"\bmarshal\.load[s]?\s*\("), "marshal 反序列化不可信資料"),
    ("PY", re.compile(r"app\.run\s*\([^)]*debug\s*=\s*True"),
                                                   "Flask debug=True — 生產環境禁用"),
    ("PY", re.compile(r"(?i)\.execute\s*\(\s*f['\"]"),
                                                   "SQL 使用 f-string 拼接 (注入風險)"),
    ("PY", re.compile(r"(?i)\.execute\s*\(\s*['\"][^'\"]*%[sd]"),
                                                   "SQL 使用 % 格式化 (注入風險)"),
    ("PY", re.compile(r"(?i)\.execute\s*\(\s*['\"][^'\"]*\"\s*\+\s*"),
                                                   "SQL 字串相加拼接 (注入風險)"),
    ("PY", re.compile(r"\brequest\.get_data\s*\([^)]*\)\s*\.decode\(\s*\)\s*\+\s*['\"]"),
                                                   "直接拼接 request 資料"),
    ("PY", re.compile(r"hashlib\.(md5|sha1)\s*\("),
                                                   "使用弱 hash (MD5/SHA1) — 僅限非安全用途"),
    ("PY", re.compile(r"random\.(random|randint|choice)\s*\("),
                                                   "random 模組非密碼學安全 — token/密碼請用 secrets"),

    # ——— Frontend XSS ———
    ("XSS", re.compile(r"\bv-html\s*="),           "Vue v-html 繞過跳脫"),
    ("XSS", re.compile(r"\bdangerouslySetInnerHTML\b"),
                                                   "React dangerouslySetInnerHTML"),
    ("XSS", re.compile(r"\.innerHTML\s*="),        "innerHTML 賦值"),
    ("XSS", re.compile(r"\.outerHTML\s*="),        "outerHTML 賦值"),
    ("XSS", re.compile(r"\bdocument\.write\s*\("), "document.write"),
    ("XSS", re.compile(r"\beval\s*\(", re.I),      "JS eval()"),
    ("XSS", re.compile(r"new\s+Function\s*\("),    "new Function() 動態編譯"),
    ("XSS", re.compile(r"setTimeout\s*\(\s*['\"]"), "setTimeout 傳字串 (= eval)"),
    ("XSS", re.compile(r"setInterval\s*\(\s*['\"]"), "setInterval 傳字串 (= eval)"),

    # ——— Path / file ———
    ("PATH", re.compile(r"open\s*\(\s*request\.", re.I),
                                                   "open() 直接使用 request 輸入"),
    ("PATH", re.compile(r"send_file\s*\(\s*request\.", re.I),
                                                   "send_file 直接使用 request 輸入"),
    ("PATH", re.compile(r"send_from_directory\s*\([^)]*request\.", re.I),
                                                   "send_from_directory 使用 request 輸入"),
    ("PATH", re.compile(r"os\.path\.join\s*\([^)\n]*request\.", re.I),
                                                   "os.path.join 使用 request 輸入 (traversal)"),
    ("PATH", re.compile(r"Path\s*\([^)\n]*request\.", re.I),
                                                   "Path() 使用 request 輸入"),

    # ——— Network / misc ———
    ("NET", re.compile(r"verify\s*=\s*False"),     "TLS 驗證停用 (verify=False)"),
    ("NET", re.compile(r"CURLOPT_SSL_VERIFYPEER\s*,\s*(0|false)"),
                                                   "cURL 停用 SSL 驗證"),
    ("NET", re.compile(r"rejectUnauthorized\s*:\s*false"),
                                                   "Node TLS 停用憑證驗證"),

    # ——— Deserialization / templating ———
    ("TPL", re.compile(r"render_template_string\s*\([^)\n]*request\.", re.I),
                                                   "Jinja render_template_string 吃 request 輸入 (SSTI)"),
    ("TPL", re.compile(r"Template\s*\(\s*request\.", re.I),
                                                   "Template() 吃 request 輸入 (SSTI)"),
]

IGNORE_LINE_PATTERNS = [
    re.compile(r"#\s*(nosec|security-check:ignore)\b"),
    re.compile(r"//\s*security-check:ignore\b"),
    re.compile(r"<!--\s*security-check:ignore\s*-->"),
]

# Path substrings to skip entirely
SKIP_PATH_PARTS = (
    "/.venv/", "/node_modules/", "/__pycache__/", "/.pytest_cache/",
    "/dist/", "/build/", "/.git/", "/.claude/hooks/",
)


def is_ignored(line: str) -> bool:
    return any(p.search(line) for p in IGNORE_LINE_PATTERNS)


def scan(file_path: Path) -> list[tuple[str, int, str, str]]:
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    hits: list[tuple[str, int, str, str]] = []
    for lineno, line in enumerate(content.splitlines(), 1):
        if is_ignored(line):
            continue
        for cat, pat, desc in RULES:
            if pat.search(line):
                hits.append((cat, lineno, desc, line.strip()[:140]))
                break  # one hit per line is enough
    return hits


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name not in {"Edit", "Write", "MultiEdit"}:
        return 0

    tool_input = data.get("tool_input") or {}
    file_path_str = tool_input.get("file_path") or ""
    if not file_path_str:
        return 0

    fp = Path(file_path_str)
    if any(part in str(fp) for part in SKIP_PATH_PARTS):
        return 0
    if fp.suffix.lower() not in SCAN_EXT and not fp.name.startswith(".env"):
        return 0
    if not fp.exists():
        return 0

    hits = scan(fp)
    if not hits:
        return 0

    print(f"🚫 security-check-code 在 {fp} 發現 {len(hits)} 個可疑點：", file=sys.stderr)
    by_cat: dict[str, list] = {}
    for cat, ln, desc, snippet in hits:
        by_cat.setdefault(cat, []).append((ln, desc, snippet))
    order = ["SECRET", "PY", "XSS", "PATH", "TPL", "NET"]
    label = {
        "SECRET": "敏感資料",
        "PY": "危險 Python 寫法",
        "XSS": "前端 XSS",
        "PATH": "路徑/檔案風險",
        "TPL": "模板注入",
        "NET": "網路/TLS",
    }
    for cat in order:
        if cat not in by_cat:
            continue
        print(f"\n[{label[cat]}]", file=sys.stderr)
        for ln, desc, snippet in by_cat[cat]:
            print(f"  L{ln}: {desc}", file=sys.stderr)
            print(f"         → {snippet}", file=sys.stderr)

    print(
        "\n請修正上述問題；若屬誤判，請於該行加上 '# nosec' (Python) 或 "
        "'// security-check:ignore' (JS/TS/HTML) 後重試。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
