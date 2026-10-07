"""Static guards must match normalized file paths, including case variants."""
from pathlib import Path

import pytest


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SENTINEL = b"ADMIN_JS_STATIC_GUARD_SENTINEL"


@pytest.fixture
def admin_js_file():
    directory = FRONTEND / "admin-js"
    directory.mkdir()
    path = directory / "x.js"
    try:
        path.write_bytes(SENTINEL)
        yield
    finally:
        path.unlink(missing_ok=True)
        directory.rmdir()


@pytest.mark.parametrize("path", [
    "/admin-js/x.js", "/admin-js/nope.js", "/admin-js",
    "/js/../admin-js/x.js", "/x/../admin-js/x.js", "/./admin-js/x.js",
    "/js/%2e%2e/admin-js/x.js", "/%2e/admin-js/x.js", "/js//../admin-js/x.js",
    "/Admin-js/x.js", "/ADMIN-JS/x.js",
    "/admin-j%C5%BF/x.js",
    "/dashboard.html", "/dashboard.html/", "/dashboard.html/.",
    "/dashboard.html/x/..", "/dashboard.html/%2e", "/login.html/.",
    "/DASHBOARD.HTML", "/js/../dashboard.html",
])
def test_normalized_static_paths_are_blocked(anon_client, admin_js_file, path):
    response = anon_client.get(path)
    assert response.status_code == 404
    assert SENTINEL not in response.data
    assert (FRONTEND / "dashboard.html").read_bytes() not in response.data
    assert (FRONTEND / "login.html").read_bytes() not in response.data


@pytest.mark.parametrize("path", ["/js/api.js", "/app"])
def test_public_paths_remain_accessible(anon_client, path):
    assert anon_client.get(path).status_code == 200
