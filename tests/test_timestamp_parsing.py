"""
PostgREST 時間戳解析（第四輪外部審查發現的既有 bug：_remaining_delay()
用 datetime.fromisoformat() 直接解析登入節流的 last_failure_at，遇到
PostgREST 常見的非標準微秒/時區格式會拋例外，except 分支原本回傳
「完整延遲」而非「剩餘延遲」，導致使用者永遠卡在同一個節流秒數。

_parse_pg_timestamp() 是純函式，這裡直接測得到——跟 PLAN_department_
isolation.md 反覆強調的「跨部門隔離只有 sentinel_pack 對真實 Supabase
才測得到」是不同類問題，這個沒有那種環境限制。

import 刻意延遲到各測試函式內部、不放在模組頂層：test_api.py 的
client fixture 靠設定 ALARM_DATA_DIR 後 sys.modules.pop("app") 強制
重新 import 來做環境隔離（見 CLAUDE.md）；若這裡在模組載入當下就
`from app import ...`，會搶在任何 fixture 執行之前把「連正式 Supabase」
的 app/storage 塞進 sys.modules 快取，污染同一次 pytest 執行裡其他
測試檔案的隔離環境（曾實際發生：test_ai_pipeline.py 因此讀到真實
Supabase 的 14 筆歷史資料而斷言失敗，不是隔離機制本身壞了，是這個
檔案的 import 順序把它先污染了）。
"""
from datetime import timezone

import pytest


def _get_parse_fn():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from app import _parse_pg_timestamp
    return _parse_pg_timestamp


@pytest.mark.parametrize("ts", [
    "2026-08-15T03:20:11.123456+00:00",
    "2026-08-15T03:20:11.12+00:00",      # 微秒被裁切（原始 bug 的觸發條件）
    "2026-08-15T03:20:11.1+00:00",
    "2026-08-15T03:20:11+00:00",         # 完全沒有微秒
    "2026-08-15T03:20:11.123456+00",     # 時區只有兩位，缺冒號分鐘
    "2026-08-15T03:20:11.123456Z",       # Z 結尾
])
def test_parse_pg_timestamp_accepts_postgrest_variants(ts):
    dt = _get_parse_fn()(ts)
    assert dt.tzinfo is not None


def test_parse_pg_timestamp_preserves_actual_instant():
    """補齊微秒不能改變原本代表的時間點——.12 補成 .120000，不是 .120001
    這種算錯的值，否則節流的剩餘秒數會算出錯誤結果。"""
    parse = _get_parse_fn()
    a = parse("2026-08-15T03:20:11.12+00:00")
    b = parse("2026-08-15T03:20:11.120000+00:00")
    assert a == b


def test_parse_pg_timestamp_naive_input_gets_utc():
    """萬一輸入完全沒有時區資訊，補上 UTC 而不是留 naive datetime——
    naive 跟 datetime.now(timezone.utc) 相減會直接 TypeError，這是
    _remaining_delay() 呼叫端會踩到的隱藏地雷。"""
    dt = _get_parse_fn()("2026-08-15T03:20:11.123456")
    assert dt.tzinfo == timezone.utc


def test_parse_pg_timestamp_raises_on_garbage():
    """解析不出來要往外拋，不能吞掉——這是本輪修正的核心：fail-closed
    而非靜默回傳看似合理的預設值。"""
    with pytest.raises(ValueError):
        _get_parse_fn()("not-a-timestamp")
