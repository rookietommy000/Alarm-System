"""POST /api/analyze 節流邏輯測試（commit 7202bdb/8e9f066）。

不觸發真實 Gemini API——直接 monkeypatch AiScanStore.count_recent()
的回傳值，驗證節流判斷本身（未達上限放行、達上限回 429）與真正呼叫
Gemini 是兩件事，不需要真實流量就能測到這段邏輯。count_recent() 本身
對 Supabase 的 PostgREST 查詢不在此測試範圍內（JsonStore 環境下
_use_supabase() 為 False，本來就 fail-open 回 0，見 storage.py 的
既有取捨說明）。
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YA"
    "AAAASUVORK5CYII="
)


def test_under_limit_does_not_block(client, monkeypatch):
    import ai
    import storage
    monkeypatch.setattr(storage.ai_scan_store, "count_recent", lambda department, since_minutes: 29)
    # 這裡只驗證「未達上限時節流不擋」，不驗證辨識結果本身——mock 掉
    # run_pipeline 避免測試意外觸發真實 Gemini API 呼叫（花錢）。
    monkeypatch.setattr(ai, "run_pipeline", lambda *a, **kw: {"scan_id": "test", "model": None})

    resp = client.post("/api/analyze", json={"image": TINY_PNG_B64})
    assert resp.status_code != 429


def test_at_limit_blocks_with_429(client, monkeypatch):
    import storage
    monkeypatch.setattr(storage.ai_scan_store, "count_recent", lambda department, since_minutes: 30)

    resp = client.post("/api/analyze", json={"image": TINY_PNG_B64})
    assert resp.status_code == 429
    body = resp.get_json()
    assert "30" in body["error"]


def test_over_limit_blocks_with_429(client, monkeypatch):
    import storage
    monkeypatch.setattr(storage.ai_scan_store, "count_recent", lambda department, since_minutes: 999)

    resp = client.post("/api/analyze", json={"image": TINY_PNG_B64})
    assert resp.status_code == 429


def test_429_response_returns_before_run_pipeline_call(client, monkeypatch):
    """驗證的是呼叫順序（純邏輯，不涉及 Supabase 節流機制本身是否生效）：
    429 分支必須在呼叫 run_pipeline()（觸發真實 Gemini API）之前 return，
    否則就算節流數字判斷本身沒錯，也已經先花錢打過 API 才擋，達不到
    防呆效果。"""
    import ai

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("節流已達上限時不該呼叫 run_pipeline()（會觸發真實 Gemini API 呼叫）")

    monkeypatch.setattr(ai, "run_pipeline", _fail_if_called)

    import storage
    monkeypatch.setattr(storage.ai_scan_store, "count_recent", lambda department, since_minutes: 30)

    resp = client.post("/api/analyze", json={"image": TINY_PNG_B64})
    assert resp.status_code == 429
