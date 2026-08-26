"""LoginAttemptStore 查詢失敗時的降級節流測試（回歸測試）。

背景：Supabase 查詢失敗原本會被 _count_since_last_success() 靜默
偽裝成 (0, None)，等於「沒有任何失敗記錄」，讓節流在資料庫不穩時
直接失效且毫無警示。改為 fail-open 降級：捕捉例外、記錄、退回行程
內計數（5 分鐘窗口、同一 IP 超過 20 次才擋），不是完全不節流、也
不是 fail-closed 擋下所有正常使用者（見 storage.py LoginAttemptStore
class docstring 的完整取捨說明）。

純本地邏輯測試，不依賴真實 Supabase——直接 monkeypatch _req() 模擬
查詢失敗，驗證降級計數（_fallback_count()）本身的行為。這裡驗證的
是「查詢失敗時退回行程內計數」這條純邏輯路徑本身對不對，不是驗證
「登入節流」這個機制在正常路徑下是否真的擋住了暴力嘗試——那需要
真實 Supabase 環境，見 sentinel_pack/verify_isolation.sh
（test_no_fake_isolation_claims.py 守住這個邊界，見該檔案說明）。
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import storage


@pytest.fixture
def broken_store(monkeypatch):
    """一個 SUPABASE_URL/KEY 有設定（讓 _use_supabase() 為 True）、
    但底層 _req() 一律拋例外的 LoginAttemptStore，模擬查詢失敗情境。

    顯式清掉 ALARM_DATA_DIR：_use_supabase() 只要偵測到這個環境變數
    就強制回 False（conftest.py 的測試隔離機制設的），若跟其他測試
    在同一 pytest session 裡跑、殘留下來，會讓這裡的 SUPABASE_URL/KEY
    設定失效、測試整組跳過真正要測的降級路徑。"""
    monkeypatch.delenv("ALARM_DATA_DIR", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")
    store = storage.LoginAttemptStore()

    def _always_fails(*args, **kwargs):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(store, "_req", _always_fails)
    return store


def test_query_failure_does_not_silently_report_zero(broken_store):
    """查詢失敗時不該回傳跟「真的零筆」一樣的 (0, None)——這裡驗證
    degraded 旗標會被設起來，讓呼叫端（/ping、後台橫幅）能感知到。"""
    broken_store.count_fine("1.2.3.4", "line21")
    assert broken_store.degraded is True


def test_fallback_allows_requests_under_limit(broken_store):
    """降級門檻內（<= 20 次/5分鐘）應該持續放行，不是查詢一失敗
    就立刻節流——fail-open 的核心精神。"""
    for _ in range(broken_store._FALLBACK_LIMIT):
        n, last_failure_at = broken_store.count_fine("1.2.3.4", "line21")
        assert n == 0
        assert last_failure_at is None


def test_fallback_count_returns_nonzero_after_exceeding_limit(broken_store):
    """純邏輯：超過降級門檻後，_fallback_count() 應該回傳非零 N 與
    時間戳（不是驗證真正的節流延遲計算或生效與否，那需要
    _remaining_delay() 搭配真實 Supabase 環境，見本檔案開頭說明）。"""
    for _ in range(broken_store._FALLBACK_LIMIT):
        broken_store.count_fine("1.2.3.4", "line21")
    n, last_failure_at = broken_store.count_fine("1.2.3.4", "line21")
    assert n > 0
    assert last_failure_at is not None


def test_fallback_is_per_ip(broken_store):
    """降級計數以 IP 為 key，不同 IP 互不影響——一個 IP 觸發節流
    不該連帶擋住其他正常使用者。"""
    for _ in range(broken_store._FALLBACK_LIMIT + 1):
        broken_store.count_fine("1.2.3.4", "line21")
    n, _ = broken_store.count_fine("5.6.7.8", "line21")
    assert n == 0


def test_successful_query_clears_degraded_flag(monkeypatch):
    """查詢恢復正常後，degraded 旗標要能復原，不能永久卡在降級狀態。"""
    monkeypatch.delenv("ALARM_DATA_DIR", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")
    store = storage.LoginAttemptStore()
    monkeypatch.setattr(store, "_req", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("fail")))
    store.count_fine("1.2.3.4", "line21")
    assert store.degraded is True

    monkeypatch.setattr(store, "_req", lambda *a, **k: [])
    store.count_fine("1.2.3.4", "line21")
    assert store.degraded is False
