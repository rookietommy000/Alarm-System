"""批次 alarm 查詢：mock HTTP，不連線至 Supabase。"""
from unittest.mock import Mock, call

import pytest


@pytest.fixture
def supabase_store(monkeypatch):
    from storage import SupabaseStore

    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    store = SupabaseStore("alarms")
    monkeypatch.setattr(store, "_req", Mock(return_value=[]))
    return store


def test_groups_codes_with_one_request(supabase_store):
    rows = [
        {"code": "E002", "variant": "a"},
        {"code": "E001", "variant": ""},
        {"code": "E002", "variant": "b"},
    ]
    supabase_store._req.return_value = rows
    result = supabase_store.find_by_codes("dept /", "model&", ["E002", "E001", "missing", "E002"])
    assert result == {"E002": [rows[0], rows[2]], "E001": [rows[1]], "missing": []}
    assert list(result) == ["E002", "E001", "missing"]
    supabase_store._req.assert_called_once_with(
        "GET", "alarms?select=*&department=eq.dept%20%2F&device_model=eq.model%26&code=in.(E002,E001,missing)"
    )


def test_empty_codes_skip_request(supabase_store):
    assert supabase_store.find_by_codes("dept", "model", []) == {}
    supabase_store._req.assert_not_called()


def test_json_batch_delegates_once_per_unique_code(monkeypatch):
    from storage import JsonStore

    store = JsonStore("alarms.json")
    rows = [{"code": "E001", "variant": "a"}]
    lookup = Mock(side_effect=lambda dept, model, code: rows if code == "E001" else [])
    monkeypatch.setattr(store, "find_by_code", lookup)
    assert store.find_by_codes("dept", "model", []) == {}
    lookup.assert_not_called()
    assert store.find_by_codes("dept", "model", ["E001", "missing", "E001"]) == {
        "E001": rows, "missing": [],
    }
    assert lookup.call_count == 2
    assert lookup.call_args_list == [call("dept", "model", "E001"), call("dept", "model", "missing")]


def test_resolve_mixed_and_duplicate_codes_use_one_request(supabase_store, monkeypatch):
    import storage
    import ai.ai_pipeline as pipeline

    single = {"code": "one", "variant": "", "description": "DB description"}
    multiple = [{"code": "many", "variant": "a"}, {"code": "many", "variant": "b"}]
    supabase_store._req.return_value = [single, *multiple]
    batch = Mock(wraps=supabase_store.find_by_codes)
    monkeypatch.setattr(supabase_store, "find_by_codes", batch)
    monkeypatch.setattr(storage, "alarms_store", supabase_store)
    monkeypatch.setattr(pipeline, "_load_variant_translations", Mock(return_value={}))
    alarms = [{"code": code, "conf": conf} for code, conf in [
        ("missing", 70), ("one", 80), ("many", 90), ("many", 95),
    ]]
    result = pipeline._resolve_alarm_codes(alarms, "dept", "model")
    candidates = [{**row, "variant_zh": None, "translation_status": None} for row in multiple]
    assert result == [
        {**alarms[0], "db_matched": False, "variant": None, "candidates": None},
        {**alarms[1], **single, "db_matched": True, "candidates": None},
        {**alarms[2], "db_matched": True, "variant": None, "candidates": candidates},
        {**alarms[3], "db_matched": True, "variant": None, "candidates": candidates},
    ]
    batch.assert_called_once_with("dept", "model", ["missing", "one", "many", "many"])
    supabase_store._req.assert_called_once_with(
        "GET", "alarms?select=*&department=eq.dept&device_model=eq.model&code=in.(missing,one,many)"
    )
