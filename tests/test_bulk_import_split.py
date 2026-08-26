"""split 端點的整合測試（批次匯入 UI 規劃第 6 階段）。

只驗證端點層的組裝與 HTTP 行為（權限、輸入驗證、分批上限、回應格式）
——split_texts() 本身的切分邏輯、verify_no_generation() 的護欄已在
test_alarm_ingest.py 涵蓋。這裡一律 monkeypatch 掉 split_texts()，不
真的呼叫 Gemini API。
"""
import pytest


def _fake_split_texts(texts):
    return [
        {"cause": None, "solution": t, "confident": False, "downgraded": True}
        for t in texts
    ]


@pytest.fixture(autouse=True)
def _stub_split(anon_client, monkeypatch):
    """依賴 anon_client 確保 app 模組已重新 import 完成才 patch——
    這個 fixture 跟 anon_client 一樣沒有宣告依賴順序時，pytest 不保證
    先後，若先 patch 舊模組、anon_client 才重新 import，patch 會套用
    在被丟棄的模組實例上，實際呼叫仍會打真正的 Gemini API。"""
    import app as app_module
    monkeypatch.setattr(app_module, "ingest_split_texts", _fake_split_texts)


def test_split_requires_admin(anon_client):
    r = anon_client.post("/api/admin/import/local/split", json={"texts": ["a"]})
    assert r.status_code in (302, 401, 403)


def test_split_missing_texts_rejected(client):
    r = client.post("/api/admin/import/local/split", json={})
    assert r.status_code == 400


def test_split_empty_array_rejected(client):
    r = client.post("/api/admin/import/local/split", json={"texts": []})
    assert r.status_code == 400


def test_split_non_string_element_rejected(client):
    r = client.post("/api/admin/import/local/split", json={"texts": ["ok", 123]})
    assert r.status_code == 400


def test_split_over_batch_limit_rejected(client):
    import app as app_module
    texts = ["文字"] * (app_module.INGEST_SPLIT_MAX_BATCH + 1)
    r = client.post("/api/admin/import/local/split", json={"texts": texts})
    assert r.status_code == 400


def test_split_returns_results_aligned_with_input(client):
    texts = ["原因A，處置B", "只有處置"]
    r = client.post("/api/admin/import/local/split", json={"texts": texts})
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["results"]) == 2
    for item in body["results"]:
        assert set(item.keys()) == {"cause", "solution", "confident", "downgraded"}


def test_split_upstream_failure_returns_502(client, monkeypatch):
    import app as app_module

    def _boom(texts):
        raise RuntimeError("API 額度用盡")

    monkeypatch.setattr(app_module, "ingest_split_texts", _boom)
    r = client.post("/api/admin/import/local/split", json={"texts": ["文字"]})
    assert r.status_code == 502


def test_split_rejects_nonexistent_department_via_resolve_target(client):
    """同 inspect 端點的驗證範圍：resolve_target_department() 純函式行為，
    不是跨部門隔離本身（見 test_bulk_import_inspect.py 同名測試的說明）。"""
    r = client.post("/api/admin/import/does-not-exist-dept/split", json={"texts": ["a"]})
    assert r.status_code in (403, 404)
