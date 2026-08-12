"""
守住「pytest 裡不該存在的斷言範圍」，不是驗證某個機制對不對。

背景（第三十五輪，外部審查）：JsonStore 刻意維持單租戶（PLAN 3.2 節），
_use_supabase()=False 時多項安全機制會提早 return 或完全不執行。第一版
test_ai_pipeline.py 曾經差點加入一條「假通過」的跨部門隔離測試（見 PLAN
第三十五輪記錄），當時靠人工重新檢查 _load_records() 原始碼才發現。這個
測試把那次的人工發現變成自動化防線。

第三十七輪外部審查完整列出下列機制在 pytest 環境裡的實際狀態，這份清單
是本測試 FORBIDDEN 關鍵字的權威依據，也是給人看的（regex 只是自動化的
第一道）：

  機制                                  | pytest 環境          | 是否可測
  --------------------------------------|----------------------|----------
  跨部門查詢過濾（_load_records 等）      | 不存在                | 否
  assert_session_valid() 三態檢查        | 提早 return           | 否
  _check_login_throttle()               | 完全不執行             | 否
  登入分岔（__super__ / fallthrough）    | 走 .env 明文比對舊路徑  | 否
  DepartmentStore 全部方法               | 不存在                | 否
  _paginated_get() 分頁邏輯              | JsonStore 不分頁       | 否
  _count() / GET .../impact             | 不存在                | 否
  save() 刪除掃描 / on_conflict          | 不存在                | 否
  scope_department()                    | 純邏輯                | 是（可測）
  resolve_target_department()           | 純邏輯                | 是（可測）
  DepartmentStore.purge() 的純邏輯部分    | 視實作而定              | 視情況

「是否可測」欄位不是絕對的——scope_department()/resolve_target_department()
是純函式，pytest 測得到而且應該測；但同樣叫 purge 的東西，如果測的是
「purgeable=false 時拒絕」這種純邏輯判斷（不經過 Supabase），也是可以測的。
這正是下面 regex 用 purge 當關鍵字會有誤判風險的原因：它會擋到合法測試。
FORBIDDEN 的目的是擋意外（沒想清楚環境限制就寫了假測試），不是要求每個
含這些字的測試都不能存在——真的要寫這類測試，把名稱改得更精確反映它的
實際範圍（例如 test_purge_rejects_when_not_purgeable，而非
test_purge_isolation_works），繞過 regex 是預期路徑，不是漏洞。

這道防線擋得住無意間寫出假測試，擋不住存心規避（改個名字就繞過去）。
它的價值是讓「忘記想清楚環境限制」變成當場失敗，不是形式驗證的保證。

真正的隔離／節流／session 驗證只有一個地方做得到：
sentinel_pack/verify_isolation.sh 對真實 Supabase 執行。
"""

import pathlib
import re

FORBIDDEN = re.compile(
    r"(def test_|class Test)\w*"
    r"(isolation|cross_department|no_leak|session_valid|"
    r"throttle|rate_limit|fallthrough|purge|停用|隔離)",
    re.I,
)

TESTS_DIR = pathlib.Path(__file__).resolve().parent


def test_no_test_name_claims_supabase_only_mechanisms():
    """JsonStore fallback 下這些機制物理上不存在或走完全不同的路徑，任何
    宣稱驗證它們的測試函式/類別名稱都必然假通過，直接擋在命名層級，不留給
    人工审查判斷。用 rglob 而非 glob，涵蓋未來若拆出 tests/unit、
    tests/integration 等子目錄的情況；同時掃函式名與類別名，避免用
    class TestXxxIsolation 包一組方法就繞過純函式名稱的檢查。"""
    offenders = []
    for f in TESTS_DIR.rglob("test_*.py"):
        if f.resolve() == pathlib.Path(__file__).resolve():
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{f.relative_to(TESTS_DIR)}:{i}  {line.strip()}")
    assert not offenders, (
        "以下測試名稱宣稱驗證只在真實 Supabase 環境才存在的機制（跨部門隔離／"
        "session 有效性／登入節流／登入分岔／purge），但 pytest 走的是 "
        "JsonStore fallback，測不到這些東西：\n  "
        + "\n  ".join(offenders)
        + "\n\n真正的驗證請寫進 sentinel_pack/verify_isolation.sh"
          "（對真實 Supabase 執行）。若這條測試驗證的其實是純邏輯（不經過"
          "Supabase，例如 scope_department()/resolve_target_department()"
          "這類純函式，或 purge 前置條件判斷這類不碰資料庫的邏輯），把名稱"
          "改成明確反映實際驗證範圍的敘述即可繞過本檢查——這是預期路徑，"
          "不是要繞過的漏洞（例如「department 值有沒有正確傳遞」而非"
          "「隔離是否生效」）。"
    )
