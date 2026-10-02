"""Password hash parameters and compatibility; Store writes are mocked."""
import sys
from unittest.mock import Mock

import pytest
from werkzeug.security import check_password_hash, generate_password_hash


@pytest.fixture
def super_client(anon_client, monkeypatch):
    import app

    monkeypatch.setenv("SUPERADMIN_PASSWORD", "test-super-password")
    monkeypatch.setattr(app.department_audit_log_store, "log", Mock())
    with anon_client.session_transaction() as session:
        session.update(auth=True, admin=True, superadmin=True, department=None)
    return anon_client


def _assert_new_hash(value, password):
    assert value.split("$", 1)[0] == "pbkdf2:sha256:100000"
    assert check_password_hash(value, password)
    assert not check_password_hash(value, "wrong-password")


def test_create_department_hash_iterations(super_client, monkeypatch):
    create = Mock(return_value={"id": "test", "name": "Test"})
    monkeypatch.setattr(sys.modules["app"].department_store, "create", create)
    response = super_client.post("/api/admin/departments", json={
        "id": "test", "name": "Test", "password": "user-password",
        "admin_password": "admin-password",
    })
    assert response.status_code == 201
    create.assert_called_once()
    _, _, pw_hash, admin_pw_hash = create.call_args.args
    _assert_new_hash(pw_hash, "user-password")
    _assert_new_hash(admin_pw_hash, "admin-password")


@pytest.mark.parametrize("fields", [
    {"password": "user-password"},
    {"admin_password": "admin-password"},
    {"password": "user-password", "admin_password": "admin-password"},
])
def test_reset_department_hash_iterations(super_client, monkeypatch, fields):
    update = Mock()
    monkeypatch.setattr(sys.modules["app"].department_store, "update_password", update)
    response = super_client.put("/api/admin/department-actions/test", json={
        "action": "reset_password", **fields,
    })
    assert response.status_code == 200
    update.assert_called_once()
    assert update.call_args.args == ("test",)
    for field, hash_field in (("password", "pw_hash"), ("admin_password", "admin_pw_hash")):
        value = update.call_args.kwargs[hash_field]
        if field in fields:
            _assert_new_hash(value, fields[field])
        else:
            assert value is None


def test_legacy_hash_remains_verifiable():
    legacy = generate_password_hash("legacy-password", method="pbkdf2:sha256:1000000")
    assert legacy.startswith("pbkdf2:sha256:1000000$")
    assert check_password_hash(legacy, "legacy-password")
    assert not check_password_hash(legacy, "wrong-password")


def test_dummy_hash_iterations(anon_client):
    _assert_new_hash(sys.modules["app"]._DUMMY_HASH, "__never_matches__")
