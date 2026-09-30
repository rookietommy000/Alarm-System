"""登入 JSON / redirect 回應契約與 session 回歸測試。

以模擬 Store 資料執行既有登入、密碼比對與節流計算，不連線 Supabase。
session 失效案例明確清除部門快取；不宣稱驗證正式環境的即時失效或隔離。
"""
import socket
import sys
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def login_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ALARM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SUPABASE_KEY", "fake")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake")

    def deny_network(*args, **kwargs):
        raise AssertionError("Network forbidden in login response tests")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    # 與共用 fixture 一致：重載模組以切換至本次測試的資料目錄。
    for module in ("app", "storage"):
        sys.modules.pop(module, None)
    import app

    state = {
        "dept": {
            "id": "test",
            "active": True,
            "pw_hash": generate_password_hash("correct", method="pbkdf2:sha256"),
            "session_version": 7,
        },
        "fine": (0, None, False),
        "coarse": (0, None, False),
        "records": [],
    }
    monkeypatch.setattr(app.department_store, "get_by_id", lambda *args: state["dept"])
    monkeypatch.setattr(app.login_attempt_store, "count_fine", lambda *args: state["fine"])
    monkeypatch.setattr(app.login_attempt_store, "count_coarse", lambda *args: state["coarse"])
    monkeypatch.setattr(app.login_attempt_store, "record", lambda *args: state["records"].append(args))
    application = app.create_app()
    application.config.update(TESTING=True, SECRET_KEY="test")
    monkeypatch.setattr(app, "_use_supabase", lambda: True)
    return application, state


@pytest.mark.parametrize("ajax", [False, True])
@pytest.mark.parametrize("case", ["empty", "fine", "coarse", "wrong", "missing", "success"])
def test_login_response_formats(login_setup, ajax, case):
    application, state = login_setup
    client = application.test_client()
    data = {"department": "test", "password": "correct", "next": "/app?x=1"}
    if case == "empty":
        data["department"] = ""
    if case in ("fine", "coarse"):
        state[case] = (
            3 if case == "fine" else 22,
            datetime.now(timezone.utc).isoformat(),
            False,
        )
    if case == "wrong":
        data["password"] = "wrong"
    if case == "missing":
        state["dept"] = None

    response = client.post(
        "/login", data=data,
        headers={"X-Requested-With": "XMLHttpRequest"} if ajax else {},
    )
    expected_status = 200 if case == "success" else 429 if case in ("fine", "coarse") else 401
    assert response.status_code == (expected_status if ajax else 302)
    if ajax:
        assert "Location" not in response.headers
        if case == "success":
            assert response.json == {"ok": True, "next": "/app?x=1"}
        elif case in ("fine", "coarse"):
            assert response.json["ok"] is False
            assert 0 < response.json["throttled"] <= 8
        else:
            assert response.json == {
                "ok": False,
                "error": "密碼錯誤，請確認選擇的部門與密碼是否正確",
            }
    elif case in ("fine", "coarse"):
        assert response.location.startswith("/login?throttled=")
    else:
        assert response.location == ("/app?x=1" if case == "success" else "/login?error=1")

    if case == "success":
        with client.session_transaction() as session:
            assert dict(session) == {
                "auth": True, "admin": False, "superadmin": False,
                "department": "test", "dept_session_version": 7, "_permanent": True,
            }
        assert "Expires=" in response.headers["Set-Cookie"]
    if case in ("empty", "fine", "coarse"):
        assert not state["records"]


@pytest.mark.parametrize("change", ["reset", "disabled"])
def test_mocked_session_revocation(login_setup, change):
    application, state = login_setup
    client = application.test_client()
    response = client.post(
        "/login", data={"department": "test", "password": "correct"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 200
    assert client.get("/app").status_code == 200
    if change == "reset":
        state["dept"]["session_version"] += 1
    else:
        state["dept"]["active"] = False
    application.config["_DEPT_CACHE"].clear()
    assert client.get("/api/devices").status_code == 401
