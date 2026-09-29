"""SemanticReviewStore 的本機（JsonStore）路徑 + Supabase 路徑測試。

Supabase 路徑用 monkeypatch 假造 urllib.request.urlopen，不依賴真實
Supabase 連線（同 CLAUDE.md「測試的能力邊界」：pytest 只測到「程式碼
邏輯對假造回應的處理是否正確」，不是「對真實 Supabase 是否真的生效」，
兩者是不同等級的結論）。
"""
import pytest
import io
import json
import sys
import urllib.error
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import storage as storage_mod


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


SAMPLE_FINDING = {
    "department": "local",
    "code": "0003", "device_model": "CNC-A100",
    "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
    "issue": "units 被誤譯為單位", "confidence": "high",
    "suggested_zh": "單元開啟/檢測停用",
    "suggested_description": "OPEN UNITS/CHECK DISABLE 單元開啟/檢測停用",
}


def test_load_all_missing_file_returns_empty_list(monkeypatch, tmp_path):
    monkeypatch.setenv("ALARM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: False)
    store = storage_mod.SemanticReviewStore()
    assert store.load_all(department="local") == []


def test_load_all_reads_local_json_with_default_pending_status(monkeypatch, tmp_path):
    monkeypatch.setenv("ALARM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: False)
    path = tmp_path / "semantic_scan_fixes.json"
    path.write_text(json.dumps({"findings": [dict(SAMPLE_FINDING)]}, ensure_ascii=False), encoding="utf-8")
    store = storage_mod.SemanticReviewStore()
    result = store.load_all(department="local")
    assert len(result) == 1
    assert result[0]["status"] == "pending"
    assert result[0]["code"] == "0003"


def test_save_all_then_load_all_round_trips_local_json(monkeypatch, tmp_path):
    monkeypatch.setenv("ALARM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: False)
    store = storage_mod.SemanticReviewStore()
    finding = {**SAMPLE_FINDING, "status": "accepted", "final_zh": "審核者改的文字"}
    store.save_all([finding], department="local")
    result = store.load_all(department="local")
    assert result == [finding]


def test_supabase_load_all_maps_review_status_to_status_and_orders_by_created_at(monkeypatch):
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    rows = [
        {**SAMPLE_FINDING, "review_status": "pending", "final_zh": None, "snapshot_id": None},
    ]
    captured_url = {}

    def fake_urlopen(req, *a, **kw):
        captured_url["url"] = req.full_url
        return FakeResponse(json.dumps(rows).encode())

    monkeypatch.setattr(storage_mod.urllib.request, "urlopen", fake_urlopen)
    store = storage_mod.SemanticReviewStore()
    result = store.load_all(department="local")

    assert "department=eq.local" in captured_url["url"]
    assert "order=created_at.asc" in captured_url["url"]
    assert result == [{
        "department": "local", "device_model": "CNC-A100", "code": "0003",
        "description": SAMPLE_FINDING["description"], "issue": SAMPLE_FINDING["issue"],
        "confidence": "high", "suggested_zh": "單元開啟/檢測停用",
        "suggested_description": SAMPLE_FINDING["suggested_description"],
        "status": "pending",
    }]
    # final_zh/snapshot_id 為 None 時不該出現在回傳的 finding 裡——跟
    # 既有 JSON 格式一致，未處理過的項目本來就沒有這兩個欄位。
    assert "final_zh" not in result[0]
    assert "snapshot_id" not in result[0]


def test_supabase_load_all_includes_final_zh_and_snapshot_id_when_present(monkeypatch):
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    rows = [{**SAMPLE_FINDING, "review_status": "accepted", "final_zh": "審核者改的文字", "snapshot_id": 42}]
    monkeypatch.setattr(storage_mod.urllib.request, "urlopen",
                         lambda req, *a, **kw: FakeResponse(json.dumps(rows).encode()))
    store = storage_mod.SemanticReviewStore()
    result = store.load_all(department="local")

    assert result[0]["status"] == "accepted"
    assert result[0]["final_zh"] == "審核者改的文字"
    assert result[0]["snapshot_id"] == 42


def test_supabase_load_all_query_failure_propagates(monkeypatch):
    """查詢失敗沒有備援，不能偽裝成沒有資料。"""
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    def fake_urlopen(req, *a, **kw):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(storage_mod.urllib.request, "urlopen", fake_urlopen)
    store = storage_mod.SemanticReviewStore()
    with pytest.raises(urllib.error.HTTPError):
        store.load_all(department="local")


def test_supabase_save_all_posts_with_on_conflict_device_model_code(monkeypatch):
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    captured = {}

    def fake_urlopen(req, *a, **kw):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["body"] = json.loads(req.data.decode())
        return FakeResponse(b"")

    monkeypatch.setattr(storage_mod.urllib.request, "urlopen", fake_urlopen)
    store = storage_mod.SemanticReviewStore()
    finding = {**SAMPLE_FINDING, "status": "rejected"}
    store.save_all([finding], department="local")

    assert "on_conflict=department,device_model,code" in captured["url"]
    assert captured["body"][0]["department"] == "local"
    assert "merge-duplicates" in captured["headers"]["prefer"]
    assert captured["body"][0]["review_status"] == "rejected"
    assert captured["body"][0]["device_model"] == "CNC-A100"


def test_supabase_save_all_write_failure_propagates_not_silent():
    """跟 variant_translations/save_snapshot 那類「失敗不影響主流程」
    的加值功能不同：accept/reject 這筆狀態如果沒真的存進去，使用者
    會以為操作成功但下次載入時狀態消失，比讓呼叫端知道寫入失敗更
    誤導人，這裡刻意不 catch，交給呼叫端（Flask 端點）決定怎麼處理。"""
    import pytest as _pytest

    def fake_urlopen(req, *a, **kw):
        raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {}, None)

    import unittest.mock
    with unittest.mock.patch.object(storage_mod, "_use_supabase", lambda: True), \
         unittest.mock.patch.dict("os.environ", {"SUPABASE_URL": "https://example.invalid", "SUPABASE_KEY": "test-key"}), \
         unittest.mock.patch.object(storage_mod.urllib.request, "urlopen", fake_urlopen):
        store = storage_mod.SemanticReviewStore()
        with _pytest.raises(urllib.error.HTTPError):
            store.save_all([SAMPLE_FINDING], department="local")


def test_json_department_filter_and_upsert_preserve_other_rows(monkeypatch, tmp_path):
    """只驗證此 Store 的 JSON 過濾/upsert，不代表真實 Supabase 隔離。"""
    monkeypatch.setenv("ALARM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: False)
    legacy = {k: v for k, v in SAMPLE_FINDING.items() if k != "department"}
    other = {**SAMPLE_FINDING, "department": "mf4d", "status": "accepted"}
    path = tmp_path / "semantic_scan_fixes.json"
    path.write_text(json.dumps({"findings": [legacy, other]}))
    store = storage_mod.SemanticReviewStore()
    assert store.load_all(department="local") == []
    store.save_all([legacy], department="local")
    assert store.load_all(department="mf4d") == [other]
    assert store.load_all(department="local") == [{**legacy, "department": "local", "status": "pending"}]
    assert len(json.loads(path.read_text())["findings"]) == 3
    store.save_all([{**legacy, "status": "rejected"}], department="local")
    assert store.load_all(department="local")[0]["status"] == "rejected"
    assert store.load_all(department="mf4d") == [other]


def test_supabase_department_query_is_url_encoded(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    department = "部門/&department=neq.mf4d"
    def fake_urlopen(req):
        query = parse_qs(urlsplit(req.full_url).query)
        assert query["department"] == ["eq." + department]
        return FakeResponse(b"[]")
    monkeypatch.setattr(storage_mod, "_urlopen", fake_urlopen)
    assert storage_mod.SemanticReviewStore().load_all(department=department) == []


@pytest.mark.parametrize("department", [None, ""])
def test_department_cannot_be_empty(department):
    store = storage_mod.SemanticReviewStore()
    with pytest.raises(ValueError):
        store.load_all(department=department)
    with pytest.raises(ValueError):
        store.save_all([], department=department)


def test_department_argument_is_required():
    store = storage_mod.SemanticReviewStore()
    with pytest.raises(TypeError):
        store.load_all()
    with pytest.raises(TypeError):
        store.save_all([])
