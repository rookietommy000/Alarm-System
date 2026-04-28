import os
import socket
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session, url_for
from flask_cors import CORS

from storage import alarms_store, audit_logger, devices_store, feedback_store, view_store

BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / "frontend"

ALARM_FIELDS = [
    "code", "device_model", "severity",
    "description", "cause", "solution", "keywords",
    "sol_steps",
]
SEVERITIES = {"嚴重", "警告", "資訊"}


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="")
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
    CORS(app)

    # ── Auth helpers ────────────────────────────────────────────────

    def is_logged_in() -> bool:
        return session.get("auth") is True or session.get("admin") is True

    def is_admin() -> bool:
        return session.get("admin") is True

    def login_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not is_logged_in():
                if request.path.startswith("/api/"):
                    return jsonify({"error": "未授權"}), 401
                return redirect(url_for("login_page", next=request.path))
            return f(*args, **kwargs)
        return wrapper

    def admin_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not is_admin():
                if request.path.startswith("/api/"):
                    return jsonify({"error": "需要管理員權限"}), 403
                return redirect(url_for("admin_login_page"))
            return f(*args, **kwargs)
        return wrapper

    # ── General login / logout ──────────────────────────────────────

    @app.get("/login")
    def login_page():
        if is_logged_in():
            return redirect("/")
        return send_from_directory(FRONTEND, "login.html")

    @app.post("/login")
    def login_submit():
        pw = (request.form.get("password") or "").strip()
        if pw == os.environ.get("LOGIN_PASSWORD", ""):
            session["auth"] = True
            next_url = request.form.get("next") or request.args.get("next", "/app")
            return redirect(next_url if next_url.startswith("/") else "/app")
        return redirect(url_for("login_page", error=1))

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect("/")

    # ── Admin login / logout ────────────────────────────────────────

    @app.get("/admin/login")
    def admin_login_page():
        if is_admin():
            return redirect("/admin")
        return send_from_directory(FRONTEND, "admin-login.html")

    @app.post("/admin/login")
    def admin_login_submit():
        pw = (request.form.get("password") or "").strip()
        if pw == os.environ.get("ADMIN_PASSWORD", ""):
            session["admin"] = True
            session["auth"] = True
            return redirect("/admin")
        return redirect(url_for("admin_login_page", error=1))

    @app.get("/admin/logout")
    def admin_logout():
        session.pop("admin", None)
        return redirect("/app")

    # ── Data normalize ──────────────────────────────────────────────

    def normalize(payload: dict, require_code: bool = True) -> dict:
        if require_code and not payload.get("code"):
            abort(400, "code 為必填")
        if payload.get("severity") and payload["severity"] not in SEVERITIES:
            abort(400, f"severity 必須為 {sorted(SEVERITIES)} 之一")
        result = {}
        for k in ALARM_FIELDS:
            default = [] if k == "keywords" else ({} if k == "sol_steps" else "")
            v = payload.get(k, default)
            if k == "keywords" and isinstance(v, str):
                v = [s.strip() for s in v.split(",") if s.strip()]
            if k == "sol_steps" and not isinstance(v, dict):
                v = {}
            result[k] = v
        return result

    # ── Read API (一般登入即可) ──────────────────────────────────────

    @app.get("/api/alarms")
    @login_required
    def list_alarms():
        q = request.args.get("q", "").strip().lower()
        device = request.args.get("device", "").strip()
        severity = request.args.get("severity", "").strip()
        items = alarms_store.load()

        def match(a: dict) -> bool:
            if device and a.get("device_model") != device:
                return False
            if severity and a.get("severity") != severity:
                return False
            if q:
                hay = " ".join([
                    a.get("code", ""), a.get("description", ""),
                    a.get("cause", ""), a.get("solution", ""),
                    " ".join(a.get("keywords", [])),
                ]).lower()
                if q not in hay:
                    return False
            return True

        return jsonify([a for a in items if match(a)])

    @app.get("/api/alarms/<device_model>/<code>")
    @login_required
    def get_alarm(device_model: str, code: str):
        for a in alarms_store.load():
            if a["code"] == code and a.get("device_model") == device_model:
                return jsonify(a)
        abort(404, "找不到此警報代碼")

    @app.get("/api/devices")
    @login_required
    def list_devices():
        return jsonify(devices_store.load())

    @app.get("/api/server-url")
    @login_required
    def server_url():
        public = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_URL")
        if public:
            return jsonify({"url": public.rstrip("/") + "/"})
        host = request.host or ""
        port = host.split(":", 1)[1] if ":" in host else "5001"
        return jsonify({"url": f"http://{_lan_ip()}:{port}/"})

    # ── Write API (需要管理員) ───────────────────────────────────────

    @app.post("/api/alarms")
    @admin_required
    def create_alarm():
        body = normalize(request.get_json(silent=True) or {})
        items = alarms_store.load()
        if any(a["code"] == body["code"] and a.get("device_model") == body.get("device_model") for a in items):
            abort(409, "代碼已存在")
        items.append(body)
        alarms_store.save(items)
        audit_logger.log("CREATE", new_data=body)
        return jsonify(body), 201

    @app.put("/api/alarms/<device_model>/<code>")
    @admin_required
    def update_alarm(device_model: str, code: str):
        body = normalize(request.get_json(silent=True) or {}, require_code=False)
        body["code"] = code
        body["device_model"] = device_model
        items = alarms_store.load()
        for i, a in enumerate(items):
            if a["code"] == code and a.get("device_model") == device_model:
                old = a
                items[i] = body
                alarms_store.save(items)
                audit_logger.log("UPDATE", new_data=body, old_data=old)
                return jsonify(body)
        abort(404, "找不到此警報代碼")

    @app.delete("/api/alarms/<device_model>/<code>")
    @admin_required
    def delete_alarm(device_model: str, code: str):
        items = alarms_store.load()
        old = next((a for a in items if a["code"] == code and a.get("device_model") == device_model), None)
        new = [a for a in items if not (a["code"] == code and a.get("device_model") == device_model)]
        if len(new) == len(items):
            abort(404, "找不到此警報代碼")
        alarms_store.save(new)
        audit_logger.log("DELETE", old_data=old)
        return "", 204

    @app.post("/api/feedback")
    @login_required
    def submit_feedback():
        body = request.get_json(silent=True) or {}
        code = body.get("code", "").strip()
        device_model = body.get("device_model", "").strip()
        result = body.get("result", "").strip()
        if not code or result not in ("effective", "ineffective"):
            abort(400, "code 與 result（effective/ineffective）為必填")
        entry = {
            "code": code,
            "device_model": device_model,
            "result": result,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        feedback_store.append(entry)
        return jsonify({"ok": True}), 201

    @app.get("/api/feedback/stats")
    @login_required
    def feedback_stats():
        records = feedback_store.load()
        stats: dict[tuple, dict] = {}
        for r in records:
            key = (r.get("code", ""), r.get("device_model", ""))
            if key not in stats:
                stats[key] = {"code": key[0], "device_model": key[1], "effective": 0, "total": 0}
            stats[key]["total"] += 1
            if r.get("result") == "effective":
                stats[key]["effective"] += 1
        return jsonify(list(stats.values()))

    @app.post("/api/view")
    @login_required
    def record_view():
        body = request.get_json(silent=True) or {}
        code = body.get("code", "").strip()
        device_model = body.get("device_model", "").strip()
        if not code:
            abort(400, "code 為必填")
        view_store.append({
            "code": code,
            "device_model": device_model,
            "viewed_at": datetime.now(timezone.utc).isoformat(),
        })
        return jsonify({"ok": True}), 201

    @app.get("/api/view/stats")
    @login_required
    def view_stats():
        records = view_store.load()
        counts: dict[tuple, int] = {}
        for r in records:
            key = (r.get("code", ""), r.get("device_model", ""))
            counts[key] = counts.get(key, 0) + 1
        result = [{"code": k[0], "device_model": k[1], "count": v}
                  for k, v in sorted(counts.items(), key=lambda x: -x[1])]
        return jsonify(result)

    @app.get("/api/audit")
    @admin_required
    def list_audit():
        limit = min(int(request.args.get("limit", 100)), 500)
        return jsonify(audit_logger.load(limit))

    # ── Pages ───────────────────────────────────────────────────────

    def _no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/")
    def portal():
        return _no_cache(send_from_directory(FRONTEND, "portal.html"))

    @app.get("/app")
    @login_required
    def index():
        return _no_cache(send_from_directory(FRONTEND, "index.html"))

    @app.get("/admin")
    @admin_required
    def admin():
        return _no_cache(send_from_directory(FRONTEND, "admin.html"))

    @app.get("/admin/dashboard")
    @admin_required
    def admin_dashboard():
        return _no_cache(send_from_directory(FRONTEND, "dashboard.html"))

    # ── Error handlers ──────────────────────────────────────────────

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def handle_error(e):
        return jsonify({"error": e.description}), e.code

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
