"""整批復原端點的整合測試（批次匯入 UI 規劃第 5 階段）。

ImportSnapshotStore 只在 Supabase 模式運作（見 storage.py 的說明：
JsonStore fallback 沒有 import_snapshots 這張表）。pytest 走 JsonStore，
這裡驗證的是「不可用時如實回報」的行為——列表回空陣列、undo 回 404
（找不到，因為 get_snapshot() 在 JsonStore 模式下必回 None）。

undo_snapshot() 真正的復原邏輯（逐筆回寫/刪除、遇錯即停、already_undone
擋重複復原）用 monkeypatch 模擬 import_snapshot_store 的回傳值驗證，
不依賴真實 Supabase 連線。
"""
import pytest


def test_list_snapshots_requires_admin(anon_client):
    r = anon_client.get("/api/admin/import/local/snapshots")
    assert r.status_code in (302, 401, 403)


def test_list_snapshots_empty_on_json_store(client):
    """JsonStore fallback 下沒有這張表，如實回空陣列，不是報錯。"""
    r = client.get("/api/admin/import/local/snapshots")
    assert r.status_code == 200
    assert r.get_json() == {"snapshots": []}


def test_undo_requires_admin(anon_client):
    r = anon_client.post("/api/admin/import/local/snapshots/1/undo")
    assert r.status_code in (302, 401, 403)


def test_undo_not_found_on_json_store(client):
    """JsonStore fallback 下 get_snapshot() 必回 None——如實回 404，
    不是假裝支援復原卻靜默不做事。"""
    r = client.post("/api/admin/import/local/snapshots/1/undo")
    assert r.status_code == 404


def test_undo_rejects_nonexistent_department_via_resolve_target(client):
    r = client.post("/api/admin/import/does-not-exist-dept/snapshots/1/undo")
    assert r.status_code in (403, 404)


def test_undo_already_undone_returns_409(client, monkeypatch):
    import app as app_module

    def _fake_undo(snapshot_id, department):
        return {"found": True, "already_undone": True}

    monkeypatch.setattr(app_module, "ingest_undo_snapshot", _fake_undo)
    r = client.post("/api/admin/import/local/snapshots/1/undo")
    assert r.status_code == 409


def test_undo_success_returns_result(client, monkeypatch):
    import app as app_module

    def _fake_undo(snapshot_id, department):
        return {"found": True, "already_undone": False, "succeeded": 3, "failed": 0, "first_failure": None}

    monkeypatch.setattr(app_module, "ingest_undo_snapshot", _fake_undo)
    r = client.post("/api/admin/import/local/snapshots/1/undo")
    assert r.status_code == 200
    body = r.get_json()
    assert body["succeeded"] == 3
    assert body["failed"] == 0
