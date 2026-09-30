"""以 mock Store 驗證登入 session 與 cookie，不連線 Supabase。"""
import sys
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from unittest.mock import Mock

import pytest
from werkzeug.security import generate_password_hash


@pytest.mark.parametrize("previous_permanent", [False, True])
@pytest.mark.parametrize(
    "endpoint, department, admin, superadmin, permanent",
    [
        ("/login", "testdept", False, False, True),
        ("/admin/login", "testdept", True, False, False),
        ("/admin/login", "__super__", True, True, False),
    ],
)
def test_login_session_lifetime(
    anon_client, monkeypatch, previous_permanent,
    endpoint, department, admin, superadmin, permanent,
):
    app_module = sys.modules["app"]
    password_hash = generate_password_hash("test-password", method="pbkdf2:sha256")
    monkeypatch.setattr(app_module, "_use_supabase", lambda: True)
    monkeypatch.setattr(app_module, "department_store", Mock(get_by_id=Mock(return_value={
        "id": "testdept", "active": True, "session_version": 1,
        "pw_hash": password_hash, "admin_pw_hash": password_hash,
    })))
    monkeypatch.setattr(app_module, "login_attempt_store", Mock(
        count_fine=Mock(return_value=(0, None, False)),
        count_coarse=Mock(return_value=(0, None, False)),
    ))
    monkeypatch.setenv("SUPERADMIN_PASSWORD", "test-password")
    with anon_client.session_transaction() as sess:
        sess.permanent = previous_permanent

    before = datetime.now(timezone.utc)
    response = anon_client.post(endpoint, data={
        "department": department, "password": "test-password",
    })
    after = datetime.now(timezone.utc)

    assert response.status_code == 302
    assert response.headers["Location"] == ("/admin" if admin else "/app")
    assert anon_client.application.permanent_session_lifetime == timedelta(days=30)
    with anon_client.session_transaction() as sess:
        assert sess["auth"] is True
        assert sess["admin"] is admin
        assert sess["superadmin"] is superadmin
        assert sess.permanent is permanent
        if not permanent:
            assert "_permanent" not in sess

    cookie = SimpleCookie()
    cookie.load(response.headers["Set-Cookie"])
    expires = cookie[anon_client.application.config["SESSION_COOKIE_NAME"]]["expires"]
    if permanent:
        from email.utils import parsedate_to_datetime

        expiry = parsedate_to_datetime(expires)
        assert before.replace(microsecond=0) + timedelta(days=30) <= expiry
        assert expiry <= after + timedelta(days=30)
    else:
        assert expires == ""
