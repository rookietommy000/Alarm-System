"""全庫語意品質審核端點的整合測試（規劃第 1c 項）。

清單檔案（data/semantic_scan_fixes.json）是離線工具的產物，不進版控。
測試用 monkeypatch 直接控制 _load_semantic_review()/_save_semantic_review()
背後的檔案路徑（走 conftest 的 ALARM_DATA_DIR 隔離），不依賴真實跑過
scan_semantic_quality.py。
"""
import json

import pytest


def _write_review_file(tmp_path_dir, findings):
    import os
    data_dir = os.environ["ALARM_DATA_DIR"]
    path = __import__("pathlib").Path(data_dir) / "semantic_scan_fixes.json"
    path.write_text(json.dumps({"findings": findings}, ensure_ascii=False), encoding="utf-8")


SAMPLE_FINDING = {
    "code": "0003", "device_model": "CNC-A100",
    "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
    "issue": "units 被誤譯為單位", "confidence": "high",
    "suggested_zh": "單元開啟/檢測停用",
    "suggested_description": "OPEN UNITS/CHECK DISABLE 單元開啟/檢測停用",
}


def test_list_requires_admin(anon_client):
    r = anon_client.get("/api/admin/semantic-review/local")
    assert r.status_code in (302, 401, 403)


def test_list_empty_when_no_file(client):
    """還沒跑過掃描工具時如實回空清單，不是報錯。"""
    r = client.get("/api/admin/semantic-review/local")
    assert r.status_code == 200
    assert r.get_json() == {"findings": []}


def test_list_returns_findings_with_default_pending_status(client, tmp_path):
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.get("/api/admin/semantic-review/local")
    body = r.get_json()
    assert len(body["findings"]) == 1
    assert body["findings"][0]["status"] == "pending"


def test_update_requires_admin(anon_client):
    r = anon_client.put("/api/admin/semantic-review/local/0", json={"action": "reject"})
    assert r.status_code in (302, 401, 403)


def test_update_index_out_of_range_404(client, tmp_path):
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.put("/api/admin/semantic-review/local/5", json={"action": "reject"})
    assert r.status_code == 404


def test_update_invalid_action_400(client, tmp_path):
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.put("/api/admin/semantic-review/local/0", json={"action": "delete"})
    assert r.status_code == 400


def test_reject_marks_status_without_writing_alarm(client, tmp_path):
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.put("/api/admin/semantic-review/local/0", json={"action": "reject"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "rejected"

    listed = client.get("/api/alarms").get_json()
    assert not any(a["code"] == "0003" and a.get("device_model") == "CNC-A100" for a in listed)


def test_accept_without_existing_alarm_404(client, tmp_path):
    """審核清單裡的 device_model/code 若資料庫已經找不到（可能已被刪除），
    採用時要如實回報，不能安靜略過或寫出一筆孤兒資料。"""
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
    assert r.status_code == 404


def test_accept_writes_new_description_and_marks_accepted(client, tmp_path):
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "0003", "device_model": "CNC-A100", "variant": "", "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
         "severity": "", "cause": "", "solution": "", "keywords": []},
        department="local",
    )
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])

    r = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "accepted"
    assert body["final_zh"] == "單元開啟/檢測停用"

    written = alarms_store.get_one(department="local", match={"device_model": "CNC-A100", "code": "0003", "variant": ""})
    assert written["description"] == "OPEN UNITS/CHECK DISABLE 單元開啟/檢測停用"


def test_accept_with_custom_final_zh_overrides_suggestion(client, tmp_path):
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "0003", "device_model": "CNC-A100", "variant": "", "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
         "severity": "", "cause": "", "solution": "", "keywords": []},
        department="local",
    )
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])

    r = client.put("/api/admin/semantic-review/local/0", json={"action": "accept", "final_zh": "審核者自己改的文字"})
    assert r.status_code == 200
    written = alarms_store.get_one(department="local", match={"device_model": "CNC-A100", "code": "0003", "variant": ""})
    assert written["description"] == "OPEN UNITS/CHECK DISABLE 審核者自己改的文字"


def test_double_processing_returns_409(client, tmp_path):
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "0003", "device_model": "CNC-A100", "variant": "", "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
         "severity": "", "cause": "", "solution": "", "keywords": []},
        department="local",
    )
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r1 = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
    assert r1.status_code == 200
    r2 = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
    assert r2.status_code == 409


def test_accept_rejects_nonexistent_department_via_resolve_target(client):
    r = client.put("/api/admin/semantic-review/does-not-exist-dept/0", json={"action": "reject"})
    assert r.status_code in (403, 404)


class TestAcceptRequiresSnapshotAvailability:
    """303 筆語意修正暫緩套用的前提之一（migration 007 執行完成）原本
    只是業務共識，沒有程式碼層面的阻擋——save_snapshot() 是 fail-open
    設計，import_snapshots 表不存在時靜默回傳 None，accept 分支原本
    不檢查這個 None 就繼續寫入 alarms 正式表。這裡驗證新增的防呆：
    只在 Supabase 模式生效（JsonStore 本來就不支援復原，不該被這層
    防呆誤擋，見上面 12 個既有測試維持不變）。"""

    def test_jsonstore_mode_not_blocked_by_snapshot_availability(self, client, tmp_path):
        """JsonStore（本機/測試）模式下，即使 is_available() 會回 False
        （本來就沒有這張表），accept 也不該被擋——這是既有且有意的行為，
        不是這次防呆要處理的對象。"""
        from storage import alarms_store, import_snapshot_store
        assert import_snapshot_store.is_available() is False  # 前提：確實是 JsonStore 模式
        alarms_store.upsert_one(
            {"code": "0003", "device_model": "CNC-A100", "variant": "", "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
             "severity": "", "cause": "", "solution": "", "keywords": []},
            department="local",
        )
        _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
        r = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
        assert r.status_code == 200

    def test_supabase_mode_blocks_accept_when_snapshot_table_missing(self, client, tmp_path, monkeypatch):
        """Supabase 模式下 is_available() 回 False（表不存在／migration
        007 未執行）時，accept 要被擋在 409，不能靜默繼續寫入正式表。

        _use_supabase() 同時也是 assert_session_valid() 拿來判斷要不要
        走部門有效性查詢的依據（本機/測試模式的 admin session 用
        department="local"，department_store 裡沒有真的這筆資料，
        硬切 Supabase 模式會讓部門查詢回 None 而被誤判成登入失效變
        401）——這裡額外 mock department_store.get_by_id() 讓它對
        "local" 回傳一筆 active 部門，只隔離測試防呆邏輯本身，不連帶
        測到跟這個防呆無關的 session 有效性驗證行為。"""
        from storage import alarms_store, import_snapshot_store, department_store
        alarms_store.upsert_one(
            {"code": "0003", "device_model": "CNC-A100", "variant": "", "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
             "severity": "", "cause": "", "solution": "", "keywords": []},
            department="local",
        )
        _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
        monkeypatch.setattr("app._use_supabase", lambda: True)
        monkeypatch.setattr(department_store, "get_by_id", lambda dept_id: {"id": "local", "active": True})
        monkeypatch.setattr(import_snapshot_store, "is_available", lambda: False)

        r = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
        assert r.status_code == 409

        # 被擋下時不該真的寫入正式表，也不該把 pending 標記成已處理——
        # 使用者之後解除前提後應該還能重新嘗試這一筆。
        written = alarms_store.get_one(department="local", match={"device_model": "CNC-A100", "code": "0003", "variant": ""})
        assert written["description"] == "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用"
        listed = client.get("/api/admin/semantic-review/local").get_json()
        assert listed["findings"][0]["status"] == "pending"

    def test_supabase_mode_allows_accept_when_snapshot_table_available(self, client, tmp_path, monkeypatch):
        """migration 007 執行完成、is_available() 回 True 時，accept
        照常運作，不該被這層防呆誤擋。"""
        from storage import alarms_store, import_snapshot_store, department_store
        alarms_store.upsert_one(
            {"code": "0003", "device_model": "CNC-A100", "variant": "", "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
             "severity": "", "cause": "", "solution": "", "keywords": []},
            department="local",
        )
        _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
        monkeypatch.setattr("app._use_supabase", lambda: True)
        monkeypatch.setattr(department_store, "get_by_id", lambda dept_id: {"id": "local", "active": True})
        monkeypatch.setattr(import_snapshot_store, "is_available", lambda: True)
        # save_snapshot() 本身走真實 HTTP 請求，測試環境沒有真的
        # Supabase 連線可打，這裡只驗證防呆本身不會誤擋，snapshot 寫入
        # 失敗與否是 save_snapshot() 既有的 fail-open 責任範圍，不是
        # 這個防呆要驗證的行為。
        monkeypatch.setattr(import_snapshot_store, "save_snapshot", lambda **kw: 999)

        r = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
        assert r.status_code == 200


class TestImportSnapshotStoreIsAvailable:
    """is_available() 本身的行為，不透過 Flask 端點。"""

    def test_jsonstore_mode_returns_false(self):
        from storage import import_snapshot_store
        assert import_snapshot_store.is_available() is False

    def test_supabase_mode_table_missing_returns_false(self, monkeypatch):
        import storage as storage_mod
        import urllib.error
        monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
        monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
        monkeypatch.setenv("SUPABASE_KEY", "test-key")

        def fake_urlopen(req, *a, **kw):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        monkeypatch.setattr(storage_mod.urllib.request, "urlopen", fake_urlopen)
        store = storage_mod.ImportSnapshotStore()
        assert store.is_available() is False

    def test_supabase_mode_table_exists_returns_true(self, monkeypatch):
        import io
        import storage as storage_mod
        monkeypatch.setattr(storage_mod, "_use_supabase", lambda: True)
        monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
        monkeypatch.setenv("SUPABASE_KEY", "test-key")

        class FakeResponse(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(storage_mod.urllib.request, "urlopen", lambda req, *a, **kw: FakeResponse(b"[]"))
        store = storage_mod.ImportSnapshotStore()
        assert store.is_available() is True
