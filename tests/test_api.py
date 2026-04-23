import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "devices.json").write_text(
        json.dumps([{"id": "M-1", "model": "CNC-A100", "category": "車床"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "alarms.json").write_text(
        json.dumps(
            [
                {
                    "code": "E001",
                    "device_model": "CNC-A100",
                    "severity": "嚴重",
                    "description": "主軸過載",
                    "cause": "負荷過大",
                    "solution": "降低進給",
                    "keywords": ["主軸"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ALARM_DATA_DIR", str(data_dir))

    for mod in ("app", "storage"):
        sys.modules.pop(mod, None)
    from app import create_app

    monkeypatch.setenv("LOGIN_PASSWORD", "test-pw")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    # Establish admin session (covers both general + admin access)
    client.post("/admin/login", data={"password": "test-admin-pw"})
    return client


def test_list_alarms(client):
    r = client.get("/api/alarms")
    assert r.status_code == 200
    assert len(r.get_json()) == 1


def test_search_by_keyword(client):
    assert len(client.get("/api/alarms?q=主軸").get_json()) == 1
    assert len(client.get("/api/alarms?q=不存在").get_json()) == 0


def test_filter_by_device_and_severity(client):
    assert len(client.get("/api/alarms?device=CNC-A100").get_json()) == 1
    assert len(client.get("/api/alarms?device=OTHER").get_json()) == 0
    assert len(client.get("/api/alarms?severity=嚴重").get_json()) == 1
    assert len(client.get("/api/alarms?severity=警告").get_json()) == 0


def test_get_single_alarm(client):
    r = client.get("/api/alarms/E001")
    assert r.status_code == 200
    assert r.get_json()["description"] == "主軸過載"
    assert client.get("/api/alarms/NOPE").status_code == 404


def test_create_alarm(client):
    payload = {
        "code": "E999",
        "device_model": "CNC-A100",
        "severity": "警告",
        "description": "測試",
        "cause": "",
        "solution": "",
        "keywords": ["test"],
    }
    r = client.post("/api/alarms", json=payload)
    assert r.status_code == 201
    assert len(client.get("/api/alarms").get_json()) == 2


def test_create_duplicate_rejected(client):
    payload = {"code": "E001", "description": "dup"}
    assert client.post("/api/alarms", json=payload).status_code == 409


def test_create_missing_code_rejected(client):
    assert client.post("/api/alarms", json={"description": "x"}).status_code == 400


def test_create_invalid_severity_rejected(client):
    assert client.post("/api/alarms", json={"code": "X1", "severity": "致命"}).status_code == 400


def test_update_alarm(client):
    r = client.put("/api/alarms/E001", json={"description": "更新後"})
    assert r.status_code == 200
    assert client.get("/api/alarms/E001").get_json()["description"] == "更新後"


def test_update_missing(client):
    assert client.put("/api/alarms/NOPE", json={"description": "x"}).status_code == 404


def test_delete_alarm(client):
    assert client.delete("/api/alarms/E001").status_code == 204
    assert client.get("/api/alarms/E001").status_code == 404


def test_devices(client):
    r = client.get("/api/devices")
    assert r.status_code == 200
    assert r.get_json()[0]["model"] == "CNC-A100"


def test_keywords_string_normalized(client):
    r = client.post("/api/alarms", json={"code": "K1", "keywords": "a, b ,c"})
    assert r.status_code == 201
    assert r.get_json()["keywords"] == ["a", "b", "c"]
