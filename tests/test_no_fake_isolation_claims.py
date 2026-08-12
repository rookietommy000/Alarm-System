"""
守住「pytest 裡不該存在的斷言範圍」，不是驗證某個機制對不對。

背景（第三十五輪，外部審查）：JsonStore 刻意維持單租戶（PLAN 3.2 節），
_use_supabase()=False 時多項安全機制會提早 return（例如 assert_session_valid()、
_load_records() 的部門過濾）。這代表 pytest 環境裡：

  - 跨部門查詢隔離
  - session 三態檢查（部門存在性／active／session_version）

這兩件事在物理上不存在，任何在這裡宣稱驗證它們的測試都必然假通過——
不是「寫得不夠嚴謹」，是被測的機制在這個環境根本沒被載入。第一版
test_ai_pipeline.py 曾經差點加入這樣一條「假通過」的測試（見 PLAN 第
三十五輪記錄），當時靠人工重新檢查 _load_records() 原始碼才發現。

這個測試把那次的人工發現變成自動化防線：任何新測試函式名稱若宣稱驗證
隔離／跨部門／session 有效性，直接讓這裡失敗，逼寫測試的人去想清楚
「這個環境測得出這件事嗎」，而不是被測試名稱和綠燈騙過去。

真正的隔離驗證只有一個地方做得到：sentinel_pack/verify_isolation.sh
對真實 Supabase 執行。
"""

import pathlib
import re

FORBIDDEN = re.compile(
    r"def test_.*(isolation|cross_department|no_leak|session_valid|purge|停用|隔離)",
    re.I,
)

TESTS_DIR = pathlib.Path(__file__).resolve().parent


def test_no_test_function_claims_isolation_or_session_validity():
    """JsonStore fallback 下這兩類機制物理上不存在，任何宣稱驗證它們的
    測試函式名稱都必然假通過，直接擋在命名層級，不留給人工审查判斷。"""
    offenders = []
    for f in TESTS_DIR.glob("test_*.py"):
        if f.name == pathlib.Path(__file__).name:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{f.name}:{i}  {line.strip()}")
    assert not offenders, (
        "以下測試函式名稱宣稱驗證部門隔離／session 有效性，但 pytest 走的是 "
        "JsonStore fallback，_use_supabase()=False 時這些機制提早 return，"
        "測不到任何東西：\n  "
        + "\n  ".join(offenders)
        + "\n\n真正的隔離驗證請寫進 sentinel_pack/verify_isolation.sh"
          "（對真實 Supabase 執行），或把測試名稱改成誠實反映它實際驗證範圍"
          "的敘述（例如「department 值有沒有正確傳遞」而非「隔離是否生效」）。"
    )
