"""AiScanStore.usage_stats() 的聚合邏輯測試（commit 8616e4c 補測）。

背景：這支方法的聚合排除邏輯（只計入有 total_token_count 的記錄，
timeout/http_error/None usage 都要被排除在月計數與 token 加總之外）
原本只在 QA 驗收 8616e4c 時用臨時注入腳本手動驗證過，沒有留下正式
的回歸測試——test_ai_usage_summary.py 只測到 JsonStore fallback 分支
（_use_supabase() 為 False，直接回全 0），聚合迴圈本身從未被自動化
測試真正執行到。這裡直接 monkeypatch urllib.request.urlopen 模擬
Supabase 回傳的 ai_logs rows，餵入 ok/timeout/http_error/None 四種
usage 形狀，驗證聚合邏輯本身。
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import storage


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _row(department, usage):
    return {"department": department, "data": json.dumps({"usage": usage})}


def _patch_urlopen(monkeypatch, rows):
    payload = json.dumps(rows).encode()
    monkeypatch.setattr(storage.urllib.request, "urlopen", lambda req: _FakeResponse(payload))


def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")


def test_excludes_timeout_and_http_error_from_count_and_tokens(monkeypatch):
    """timeout/http_error 的 usage 字典只有 outcome、沒有 total_token_count，
    不該被誤算進月計數或 token 加總（8616e4c 修正的核心邏輯）。"""
    _env(monkeypatch)
    rows = [
        _row("mf4d", {"outcome": "ok", "total_token_count": 100,
                       "prompt_token_count": 60, "candidates_token_count": 40}),
        _row("mf4d", {"outcome": "timeout"}),
        _row("mf4d", {"outcome": "http_error"}),
    ]
    _patch_urlopen(monkeypatch, rows)

    store = storage.AiScanStore()
    result = store.usage_stats(department="mf4d")

    assert result["month_count"] == 1
    assert result["month_total_tokens"] == 100
    assert result["month_prompt_tokens"] == 60
    assert result["month_candidates_tokens"] == 40


def test_excludes_none_usage_record(monkeypatch):
    """usage 整個是 None（例如 LocalAnalyzer 或 analyzer 失敗降級時）
    也要被排除，不能讓 `not usage` 這段判斷漏掉。"""
    _env(monkeypatch)
    rows = [
        _row("mf4d", {"outcome": "ok", "total_token_count": 50,
                       "prompt_token_count": 30, "candidates_token_count": 20}),
        _row("mf4d", None),
    ]
    _patch_urlopen(monkeypatch, rows)

    store = storage.AiScanStore()
    result = store.usage_stats(department="mf4d")

    assert result["month_count"] == 1
    assert result["month_total_tokens"] == 50


def test_by_department_only_counts_included_records(monkeypatch):
    """by_department 分列只包含真正被計入（有 token 數字）的記錄，
    department=None（總管視角）才會回傳非空 by_department。"""
    _env(monkeypatch)
    rows = [
        _row("mf4d", {"outcome": "ok", "total_token_count": 100,
                       "prompt_token_count": 60, "candidates_token_count": 40}),
        _row("mf4d", {"outcome": "timeout"}),
        _row("zztest", {"outcome": "ok", "total_token_count": 30,
                         "prompt_token_count": 10, "candidates_token_count": 20}),
    ]
    _patch_urlopen(monkeypatch, rows)

    store = storage.AiScanStore()
    result = store.usage_stats(department=None)

    assert result["month_count"] == 2
    assert result["month_total_tokens"] == 130
    by_dept = {d["department"]: d for d in result["by_department"]}
    assert by_dept["mf4d"]["count"] == 1
    assert by_dept["mf4d"]["total_tokens"] == 100
    assert by_dept["zztest"]["count"] == 1
    assert by_dept["zztest"]["total_tokens"] == 30


def test_department_scoped_call_returns_empty_by_department(monkeypatch):
    """帶 department 時 by_department 固定為空，跟既有 scan_stats() 的
    scope 慣例一致——分列清單只在跨部門（department=None）視角才有意義。"""
    _env(monkeypatch)
    rows = [_row("mf4d", {"outcome": "ok", "total_token_count": 100,
                           "prompt_token_count": 60, "candidates_token_count": 40})]
    _patch_urlopen(monkeypatch, rows)

    store = storage.AiScanStore()
    result = store.usage_stats(department="mf4d")

    assert result["by_department"] == []


def test_supabase_query_failure_falls_back_to_zero(monkeypatch):
    """查詢本身失敗（連線錯誤等）要 fail-open 回全 0，不能讓整個
    /api/admin/ai-usage-stats 端點掛掉（唯讀統計，非安全邊界）。"""
    _env(monkeypatch)

    def _raise(req):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(storage.urllib.request, "urlopen", _raise)

    store = storage.AiScanStore()
    result = store.usage_stats(department="mf4d")

    assert result == {
        "month_count": 0, "month_total_tokens": 0,
        "month_prompt_tokens": 0, "month_candidates_tokens": 0,
        "by_department": [],
    }
