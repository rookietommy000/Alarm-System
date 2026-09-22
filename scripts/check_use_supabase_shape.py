"""掃描：全專案所有 `_use_supabase()` 定義，是否都具備測試隔離豁免分支。

背景（外部審查/醫生查證，2026-09-22）：`_use_supabase()` 這個「該不該打
正式 Supabase」的判斷邏輯，在專案裡已經被獨立重寫至少三次——`storage.py`
原版、`ai_memory.py`、`ai_logger.py`。每次都各自寫一份、彼此不共用程式碼，
這正是 AI 記憶污染事件（`docs/verification/ai_memory_pollution_cleanup_
2026-09-22.md`）存在超過一個月才被發現的根本原因：不是「忘記加判斷」，
是「同一個安全概念散落成多份互不同步的實作」，其中一份（`ai_memory.py`/
`ai_logger.py` 最初的版本）漏掉了隔離豁免分支，且沒有任何機制能自動抓到
「這份定義跟別份定義的安全形狀不一致」。

**這支腳本刻意不禁止重複定義本身**——`storage.py`/`ai_memory.py`/
`ai_logger.py` 三份定義判斷不同的隔離環境變數（`ALARM_DATA_DIR`/
`AI_MEM_DIR`/`AI_LOG_DIR`），這是既有的合理模組化設計（alarms 業務資料
跟 AI 記憶/日誌是不同性質的本機資料，各自命名，見 `test_ai_pipeline.py`
`TestUseSupabaseFlagRespectsLocalDirEnvVar` docstring 的既有結論），
統一成同一個變數名稱才是架構缺陷，不是現狀。**真正要防的是「新的一份
定義忘記加隔離判斷分支」**，這才是實際發生過的失敗模式。

驗證方式：對每一份 `def _use_supabase(...)` 用 AST 靜態檢查函式體的固定
形狀——

1. 函式體第一句必須是 `if <某個 os.environ.get(...) 呼叫>: return False`
   這個形狀（`if` 的 test 是對 `os.environ.get()` 的呼叫，body 只有一句
   `return False`）——這就是「隔離豁免分支」，具體判斷哪個環境變數名稱
   不限制（那是各模組的自由，見上面說明），但「有沒有這一步」是可以
   機械驗證的。
2. 函式體最後一句必須是 `return bool(os.environ.get("SUPABASE_URL") and
   os.environ.get("SUPABASE_KEY"))` 這個形狀——判斷正式環境連線資訊是否
   齊全。

不符合這個形狀的 `_use_supabase()` 定義（例如漏掉第 1 步、或第 1 步的
`if` 條件不是查環境變數、或用了完全不同的判斷方式）會被回報成不一致，
需要人工複查是否是新的合理變體、還是重蹈覆轍的疏漏。

執行方式：`python scripts/check_use_supabase_shape.py`（獨立腳本，也被
`tests/test_use_supabase_consistency.py` import 使用）。
"""
import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"


def _is_env_get_call(node: ast.AST) -> bool:
    """node 是否為 `os.environ.get(...)` 這個形狀的呼叫（不限制傳入
    哪個環境變數名稱——那是各模組的自由，見檔案開頭說明）。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "environ"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "os"
    )


def _skip_docstring(func_body: list) -> list:
    """略過函式體開頭的 docstring（若有），回傳剩下的敘述句清單。"""
    if func_body and isinstance(func_body[0], ast.Expr) and isinstance(func_body[0].value, ast.Constant) and isinstance(func_body[0].value.value, str):
        return func_body[1:]
    return func_body


def _has_isolation_guard(func_body: list) -> bool:
    """函式體第一句（略過 docstring 後）是否為
    `if <os.environ.get(...)>: return False`。"""
    func_body = _skip_docstring(func_body)
    if not func_body:
        return False
    first = func_body[0]
    if not isinstance(first, ast.If):
        return False
    if not _is_env_get_call(first.test):
        return False
    if len(first.body) != 1 or not isinstance(first.body[0], ast.Return):
        return False
    ret_value = first.body[0].value
    return isinstance(ret_value, ast.Constant) and ret_value.value is False


def _has_supabase_env_check(func_body: list) -> bool:
    """函式體最後一句是否為 `return bool(os.environ.get("SUPABASE_URL")
    and os.environ.get("SUPABASE_KEY"))`（不限制順序，只要求兩個環境
    變數都是透過 os.environ.get() 讀取、且用 and 連接、包在 bool() 裡）。"""
    if not func_body:
        return False
    last = func_body[-1]
    if not isinstance(last, ast.Return):
        return False
    call = last.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "bool"):
        return False
    if len(call.args) != 1 or not isinstance(call.args[0], ast.BoolOp) or not isinstance(call.args[0].op, ast.And):
        return False
    values = call.args[0].values
    if len(values) != 2:
        return False
    env_var_names = set()
    for v in values:
        if not _is_env_get_call(v):
            return False
        if v.args and isinstance(v.args[0], ast.Constant):
            env_var_names.add(v.args[0].value)
    return env_var_names == {"SUPABASE_URL", "SUPABASE_KEY"}


def find_use_supabase_definitions() -> list:
    """回傳 [(檔案路徑, 函式節點), ...]，掃描 backend/ 底下所有
    `def _use_supabase(...)` 定義（含巢狀在 class 內的情況，雖然目前
    專案裡都是模組層級函式）。"""
    results = []
    for py_file in BACKEND.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_use_supabase":
                results.append((py_file, node))
    return results


def find_shape_violations() -> list:
    """回傳不符合固定形狀的 [(檔案路徑, 函式節點, 問題描述), ...]。"""
    violations = []
    for path, func in find_use_supabase_definitions():
        rel = path.relative_to(BACKEND.parent)
        if not _has_isolation_guard(func.body):
            violations.append((rel, func, "缺少測試隔離豁免分支（函式體第一句必須是 `if os.environ.get(...): return False`）"))
        elif not _has_supabase_env_check(func.body):
            violations.append((rel, func, "缺少或形式不符的正式環境連線檢查（函式體最後一句必須是 `return bool(os.environ.get(\"SUPABASE_URL\") and os.environ.get(\"SUPABASE_KEY\"))`）"))
    return violations


if __name__ == "__main__":
    defs = find_use_supabase_definitions()
    print(f"找到 {len(defs)} 處 _use_supabase() 定義：")
    for path, func in defs:
        print(f"  {path.relative_to(BACKEND.parent)}:{func.lineno}")

    violations = find_shape_violations()
    if violations:
        print(f"\n發現 {len(violations)} 處形狀不符：")
        for rel, func, reason in violations:
            print(f"  {rel}:{func.lineno} — {reason}")
        sys.exit(1)
    else:
        print("\n全部定義形狀一致，均具備測試隔離豁免分支。")
        sys.exit(0)
