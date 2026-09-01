"""_sb_query() 的排序保證（拍照辨識速度優化前置修正）。

背景：ai_alert.py 的 _check_consecutive_low_conf() 用 scan_history[-limit:]
取「最新 limit 筆」判斷連續低信心，隱含假設清單順序等於時間順序（最後
幾筆＝最新幾筆）。但 _sb_query() 原本呼叫 PostgREST 完全沒有帶 order
參數——PostgREST 沒有明確 order 時不保證回傳順序（同 storage.py
_paginated_get() 踩過的教訓），ALERT_LOW_CONF（HIGH 等級、會 block=True
擋住流程的安全機制）可能因為順序不保證而誤判或漏判。這裡直接
monkeypatch urllib.request.urlopen 驗證查詢 URL 確實帶了明確的
order=created_at.asc，不靠猜測 PostgREST 的隱含行為。
"""
import json
import sys
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from ai import ai_memory


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")


def test_history_query_requests_ascending_created_at_order(monkeypatch):
    """_load_records("history", ...) 底層打的 URL 必須明確帶
    order=created_at.asc，不能靠 PostgREST 預設行為（不保證順序）。"""
    _env(monkeypatch)
    captured = {}

    def _fake_urlopen(req):
        captured["url"] = req.full_url
        return _FakeResponse(json.dumps([]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    ai_memory._sb_query("ai_scans", {"model": "PILM004", "department": "mf4d"})

    assert "order=created_at.asc" in captured["url"], (
        f"查詢缺少明確排序，_check_consecutive_low_conf() 的 [-limit:] "
        f"取值會失去時間序保證：{captured['url']}"
    )


def test_corrections_query_also_requests_ascending_order(monkeypatch):
    """_sb_query() 是 history/corrections 共用的 helper，排序保證要
    對兩張表都生效，不能只修 history 那條呼叫路徑。"""
    _env(monkeypatch)
    captured = {}

    def _fake_urlopen(req):
        captured["url"] = req.full_url
        return _FakeResponse(json.dumps([]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    ai_memory._sb_query("ai_corrections", {"corrected_model": "PILM004", "department": "mf4d"})

    assert "order=created_at.asc" in captured["url"]


def test_returned_order_lets_alert_take_last_n_as_most_recent(monkeypatch):
    """端對端驗證排序方向：升冪排序下，回傳清單最後 N 筆確實對應
    check_alerts() 的 _check_consecutive_low_conf() 想要的「最新 N 筆」
    語意，不是巧合過關——用明確標了新舊順序的假資料驗證。"""
    _env(monkeypatch)
    rows = [
        {"tier": "success", "scanned_at": "oldest"},
        {"tier": "low_confidence", "scanned_at": "middle"},
        {"tier": "low_confidence", "scanned_at": "newest"},
    ]

    def _fake_urlopen(req):
        return _FakeResponse(json.dumps(rows).encode())

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    result = ai_memory._sb_query("ai_scans", {"model": "PILM004", "department": None})

    # _sb_query 本身不重排，只負責把 PostgREST 已排序好的結果原樣回傳；
    # 這裡假設 PostgREST 確實依 order=created_at.asc 回傳（見上面兩條
    # 測試已驗證請求端有帶這個參數），驗證呼叫端拿到的仍是這個順序。
    assert result[-1]["scanned_at"] == "newest"
    assert result[0]["scanned_at"] == "oldest"
