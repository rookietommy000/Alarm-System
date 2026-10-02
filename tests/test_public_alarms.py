"""公開原廠查詢契約；部門查詢使用替身，不驗證真實租戶隔離。"""
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def public_client(anon_client, monkeypatch):
    import app

    monkeypatch.setattr(app.department_store, "get_by_id", lambda dept: {
        "local": {"id": "local", "active": True},
        "disabled": {"id": "disabled", "active": False},
    }.get(dept))
    return anon_client


def seed(rows):
    (Path(os.environ["ALARM_DATA_DIR"]) / "alarms.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8",
    )


@pytest.mark.parametrize("suffix", ["", "/CNC-A100/E001"])
def test_public_projection_and_unchanged_authenticated_response(public_client, suffix):
    public = {
        "department": "local", "device_model": "CNC-A100", "code": "E001",
        "variant": "", "severity": "嚴重", "description": "主軸過載",
        "cause": "負荷過大", "solution": "降低進給", "keywords": ["主軸"],
        "sol_steps": {"zh": ["停止"]},
    }
    private = dict.fromkeys([
        "local_solution", "local_reason", "local_updated_by", "local_updated_at",
        "local_future_field", "internal_future_field",
    ], "private")
    row = {**public, **private}
    seed([row])
    url = "/api/public/alarms/local" + suffix
    response = public_client.get(url)
    assert response.status_code == 200
    assert response.get_json() == (public if suffix else [public])

    old_url = "/api/alarms/local" + suffix if suffix else "/api/alarms"
    assert public_client.get(old_url).status_code == 401
    public_client.post("/admin/login", data={"password": "test-admin-pw"})
    response = public_client.get(old_url)
    assert response.status_code == 200
    assert response.get_json() == (row if suffix else [row])
    assert public_client.get(url).get_json() == (public if suffix else [public])


@pytest.mark.parametrize("department", ["missing", "disabled"])
@pytest.mark.parametrize("suffix", ["", "/CNC-A100/E001"])
def test_unavailable_department(public_client, department, suffix):
    assert public_client.get(f"/api/public/alarms/{department}{suffix}").status_code == 404


def test_filters_and_no_local_search(public_client):
    seed([{"code": "E001", "device_model": "CNC-A100", "severity": "嚴重",
           "local_solution": "secret"}])
    url = "/api/public/alarms/local"
    for params, count in [({"device": "CNC-A100", "severity": "嚴重"}, 1),
                          ({"device": "OTHER"}, 0), ({"severity": "警告"}, 0),
                          ({"q": "secret"}, 0), ({"missing_local": "true"}, 1)]:
        response = public_client.get(url, query_string=params)
        assert response.status_code == 200
        assert len(response.get_json()) == count


def test_exact_variant(public_client):
    seed([{"code": "E001", "device_model": "CNC-A100", "variant": v,
           "description": v} for v in ["", "A", "B"]])
    url = "/api/public/alarms/local/CNC-A100/E001"
    for variant in ["", "A", "B"]:
        response = public_client.get(url, query_string={"variant": variant})
        assert response.status_code == 200
        assert response.get_json()["description"] == variant
    assert public_client.get(url, query_string={"variant": "missing"}).status_code == 404
    assert public_client.get("/api/public/alarms/local/CNC-A100/NOPE").status_code == 404


@pytest.mark.parametrize("field", ["code", "description", "cause", "solution", "keywords"])
@pytest.mark.parametrize("query,text,expected", [
    ("arm", "Prealarm", False),
    ("arm", "Alarm", False),
    ("arm", "armature", False),
    ("arm", "robotic arm failure", True),
    (" ARM ", "robotic Arm failure", True),
    ("0095", "00950", False),
    ("0095", "0095", True),
    ("e-514", "error e-514 detected", True),
    ("e.514", "error ex514 detected", False),
    ("e.514", "error e.514 detected", True),
    ("預警", "端部成型材料預警", True),
    ("arm預警", "prearm預警通知", True),
    ("arm預警", "arm故障預警", False),
])
def test_public_search_query_boundaries(public_client, field, query, text, expected):
    # 使用 fixture 的本機 JSON 資料，逐一確認所有搜尋欄位套用相同規則。
    row = {"code": "E002", "device_model": "CNC-A100"}
    row[field] = [text] if field == "keywords" else text
    data_path = Path(os.environ["ALARM_DATA_DIR"]) / "alarms.json"
    data_path.write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")

    response = public_client.get("/api/public/alarms/local", query_string={"q": query})

    assert response.status_code == 200
    assert response.get_json() == ([row] if expected else [])


    public_client.post("/admin/login", data={"password": "test-admin-pw"})
    original = public_client.get("/api/alarms", query_string={"q": query})
    assert original.status_code == 200
    assert original.get_json() == response.get_json()


@pytest.mark.parametrize("single", [False, True])
def test_store_receives_path_department(public_client, monkeypatch, single):
    """驗證呼叫參數來自 path；不是資料庫隔離測試。"""
    import app

    calls = []
    if single:
        def get_one(*, department, match):
            calls.append((department, match))
            return {"code": "E001"}
        monkeypatch.setattr(app.alarms_store, "get_one", get_one)
    else:
        def load(*, department):
            calls.append(department)
            return []
        monkeypatch.setattr(app.alarms_store, "load", load)
    with public_client.session_transaction() as sess:
        sess["department"] = "other-session-department"
    suffix = "/CNC-A100/E001" if single else ""
    response = public_client.get(
        "/api/public/alarms/local" + suffix,
        query_string={"department": "other-query-department", "variant": "A"},
    )
    assert response.status_code == 200
    assert calls == ([("local", {"device_model": "CNC-A100", "code": "E001", "variant": "A"})]
                     if single else ["local"])


@pytest.mark.parametrize("path", [
    "/api/public/alarms", "/api/public/alarms/",
    "/api/public/alarms/CNC-A100/E001",
    "/api/public/alarms/local/E001", "/api/public/alarms/local/CNC-A100",
    "/api/public/alarms//CNC-A100/E001",
    "/api/public/alarms/local//E001", "/api/public/alarms/local/CNC-A100/",
])
def test_incomplete_public_paths_return_404(public_client, path):
    response = public_client.get(path)
    assert response.status_code == 404
    assert "Location" not in response.headers
