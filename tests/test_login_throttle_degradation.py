"""LoginAttemptStore 查詢失敗時的降級節流測試（回歸測試）。

背景：Supabase 查詢失敗原本會被 _count_since_last_success() 靜默
偽裝成 (0, None)，等於「沒有任何失敗記錄」，讓節流在資料庫不穩時
直接失效且毫無警示。改為 fail-open 降級：捕捉例外、記錄、退回行程
內計數（5 分鐘窗口、同一 IP 超過 20 次才擋），不是完全不節流、也
不是 fail-closed 擋下所有正常使用者（見 storage.py LoginAttemptStore
class docstring 的完整取捨說明）。

【PLAN 效能優化第 3 項後更新】count_fine/count_coarse 現在回傳
(n, last_failure_at, degraded) 三元組，degraded 旗標不再由這兩個
方法自己寫回 self.degraded——併行執行時各自直接寫共享屬性會互相
覆蓋，改由呼叫端（app.py _fetch_login_precheck()）OR 合併後才寫一次。
同理，_fallback_count() 預設查詢失敗時只「讀」目前次數
（不自動記錄），避免一次登入嘗試觸發 count_fine+count_coarse 兩支
查詢都降級時被計成 2 次命中——記錄的時機交給呼叫端在兩支都跑完後
呼叫一次 note_fallback_hit()。這裡的測試因此直接呼叫
note_fallback_hit() 模擬「呼叫端已判斷本次登入嘗試降級」這件事，
不再單靠重複呼叫 count_fine() 來累積次數。

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


def test_query_failure_reports_degraded_in_return_value(broken_store):
    """查詢失敗時不該回傳跟「真的零筆」一樣的 (0, None)——回傳的第三個
    值（degraded）要能讓呼叫端感知到，即使這個方法本身不再直接寫
    self.degraded（那件事交給呼叫端 OR 合併後統一寫，見本檔案開頭
    說明與 storage.py 的取捨）。"""
    n, last_failure_at, degraded = broken_store.count_fine("1.2.3.4", "line21")
    assert degraded is True


def test_fallback_allows_requests_under_limit(broken_store):
    """降級門檻內（<= 20 次/5分鐘）應該持續放行，不是查詢一失敗
    就立刻節流——fail-open 的核心精神。每次呼叫 count_fine() 後都
    模擬呼叫端記一筆（note_fallback_hit()），對齊實際呼叫模式
    （一次登入嘗試最多記一次）。"""
    for _ in range(broken_store._FALLBACK_LIMIT):
        n, last_failure_at, degraded = broken_store.count_fine("1.2.3.4", "line21")
        assert n == 0
        assert last_failure_at is None
        broken_store.note_fallback_hit("1.2.3.4")


def test_fallback_count_returns_nonzero_after_exceeding_limit(broken_store):
    """純邏輯：超過降級門檻後，count_fine() 應該回傳非零 N 與
    時間戳（不是驗證真正的節流延遲計算或生效與否，那需要
    _remaining_delay() 搭配真實 Supabase 環境，見本檔案開頭說明）。"""
    for _ in range(broken_store._FALLBACK_LIMIT + 1):
        broken_store.note_fallback_hit("1.2.3.4")
    n, last_failure_at, degraded = broken_store.count_fine("1.2.3.4", "line21")
    assert n > 0
    assert last_failure_at is not None


def test_one_login_attempt_only_counts_once_even_if_both_queries_degrade(broken_store):
    """【修正既有計數語意】一次登入嘗試會同時觸發 count_fine 與
    count_coarse 兩支查詢，若兩支都降級卻各自記一筆，等於一次登入
    嘗試被計成 2 次降級命中，把 _FALLBACK_LIMIT=20 實質砍半。驗證
    呼叫端只呼叫一次 note_fallback_hit() 時，計數確實只增加 1——
    這是「呼叫端只記一次」這個約定本身的正確性，不是重新測
    ThreadPoolExecutor 併行時序。"""
    ip = "9.9.9.9"
    broken_store.count_fine(ip, "line21")
    broken_store.count_coarse(ip)
    n_before = broken_store._fallback_count(ip, record=False)
    assert n_before == 0  # 兩支查詢單純讀取都不該留下記錄

    broken_store.note_fallback_hit(ip)
    n_after = broken_store._fallback_count(ip, record=False)
    assert n_after == 1  # 呼叫端統一記一次，只增加 1 而非 2


def test_fallback_is_per_ip(broken_store):
    """降級計數以 IP 為 key，不同 IP 互不影響——一個 IP 觸發節流
    不該連帶擋住其他正常使用者。"""
    for _ in range(broken_store._FALLBACK_LIMIT + 1):
        broken_store.note_fallback_hit("1.2.3.4")
    n, _, _ = broken_store.count_fine("5.6.7.8", "line21")
    assert n == 0


def test_successful_query_reports_not_degraded(monkeypatch):
    """查詢恢復正常後，回傳的 degraded 值要能反映最新狀態，不能永久
    卡在降級狀態（self.degraded 的復原由呼叫端每次 OR 合併後重寫，
    這裡驗證的是回傳值本身，因為併行化後這個方法不再自己寫共享屬性）。"""
    monkeypatch.delenv("ALARM_DATA_DIR", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")
    store = storage.LoginAttemptStore()
    monkeypatch.setattr(store, "_req", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("fail")))
    _, _, degraded = store.count_fine("1.2.3.4", "line21")
    assert degraded is True

    monkeypatch.setattr(store, "_req", lambda *a, **k: [])
    _, _, degraded = store.count_fine("1.2.3.4", "line21")
    assert degraded is False
