"""機種更新回歸測試；Supabase 部分只模擬 HTTP，不連線資料庫。"""
import sys
from urllib.parse import parse_qs

import pytest
from flask import abort


@pytest.fixture(params=["json", "supabase"])
def device_client(request, client, monkeypatch):
    if request.param == "supabase":
        storage = sys.modules["storage"]
        monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
        monkeypatch.setenv("SUPABASE_KEY", "test-only")
        store = storage.SupabaseStore(
            "devices", pk="id", pk_fields=["department", "model"], is_devices=True,
        )
        row = {"id": "M-1", "department": "local", "model": "CNC-A100",
               "category": "車床", "line": ""}

        def fake_req(method, path, body=None, extra_headers=None):
            table, _, query = path.partition("?")
            assert table == "devices"
            params = parse_qs(query)
            if method == "POST":
                # 新 unique key 走 INSERT，舊 id 仍佔用：重現原本的 409。
                if body[0]["model"] != row["model"] and body[0]["id"] == row["id"]:
                    abort(409, "duplicate key value violates devices_pkey")
                row.update(body[0])
                return [dict(row)]
            assert method in {"GET", "PATCH"}
            matches = all(
                row.get(key) == values[0][3:]
                for key, values in params.items() if values[0].startswith("eq.")
            )
            if not matches:
                return []
            if method == "PATCH":
                assert params == {"department": ["eq.local"], "id": ["eq.M-1"],
                                  "model": ["eq.CNC-A100"]}
                assert "id" not in body
                row.update(body)
            return [dict(row)]

        monkeypatch.setattr(store, "_req", fake_req)
        monkeypatch.setattr(sys.modules["app"], "devices_store", store)
    return client


@pytest.mark.parametrize("field", ["model", "device_model"])
def test_rename_device_preserves_id(device_client, field):
    response = device_client.put("/api/devices/local/CNC-A100", json={field: " CNC-B200 "})
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["id"] == "M-1"
    assert updated["model"] == updated["device_model"] == "CNC-B200"
    assert updated["category"] == "車床"
    fetched = device_client.get("/api/devices/local/CNC-B200")
    assert fetched.status_code == 200
    assert fetched.get_json()["id"] == "M-1"
    assert fetched.get_json()["model"] == "CNC-B200"
    assert device_client.get("/api/devices/local/CNC-A100").status_code == 404
    assert len(device_client.get("/api/devices").get_json()) == 1


@pytest.mark.parametrize("patch", [{"category": " 銑床 "}, {"line": " LINE3 "},
                                   {"category": " 銑床 ", "line": " LINE3 "}])
def test_update_device_metadata(device_client, patch):
    response = device_client.put("/api/devices/local/CNC-A100", json=patch)
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["id"] == "M-1"
    assert updated["model"] == updated["device_model"] == "CNC-A100"
    assert updated["category"] == patch.get("category", "車床").strip()
    assert updated["line"] == patch.get("line", "").strip()
    fetched = device_client.get("/api/devices/local/CNC-A100").get_json()
    assert fetched["category"] == updated["category"]
    assert fetched["line"] == updated["line"]
