import hmac
import json
import os
import re
import socket
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session, url_for
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

from storage import (
    ai_scan_store, alarm_suggestion_store, alarms_store, audit_logger, department_store,
    devices_store, feedback_store, login_attempt_store, view_store, _use_supabase,
)

BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / "frontend"

ALARM_FIELDS = [
    "code", "device_model", "severity",
    "description", "cause", "solution", "keywords",
    "sol_steps",
]
SEVERITIES = {"嚴重", "警告", "資訊"}

SUPER_DEPT_SENTINEL = "__super__"
DEPT_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
DEPT_NAME_MAX_LEN = 64


def _validate_dept_name(name: str) -> None:
    """部門名稱是顯示用自由文字，不用白名單（會誤傷合法字元），只擋
    長度與控制字元；真正的 XSS 防線在前端一律 textContent 輸出。"""
    if len(name) > DEPT_NAME_MAX_LEN:
        abort(400, f"name 長度不可超過 {DEPT_NAME_MAX_LEN} 字元")
    if any(ord(c) < 0x20 for c in name):
        abort(400, "name 不可包含控制字元")

# 跨部門存取／部門不存在／無權存取，三種情況一律用這同一句話，刻意不區分
# （不透露「部門存在但你無權存取」與「部門根本不存在」的差異）。跟 Werkzeug
# 路由層 404 的預設英文訊息不同，讓使用者截圖裡的錯誤訊息能區分「URL 結構
# 沒對上任何路由規則」還是「部門解析失敗」——這不是放寬安全設計，訊息內容
# 在四處都完全相同，只是把原本由 Werkzeug 決定的措辭換成我們自己控制的字串。
NOT_FOUND_MSG = "找不到指定資源"
_DUMMY_HASH = generate_password_hash("__never_matches__", method="pbkdf2:sha256")

_TS_FRAC_RE = re.compile(r"(\.\d+)")
_TS_SHORT_TZ_RE = re.compile(r"[+-]\d{2}$")


def _parse_pg_timestamp(ts: str) -> datetime:
    """PostgREST 回傳的 timestamptz 格式不完全固定：微秒尾端為 0 時會被
    裁切成非 3/6 位（例如 .78161 而非 .781610），時區標記可能是
    +00:00／+00／Z。Python 的 fromisoformat() 對這些變體要求嚴格，
    任一種都會直接拋 ValueError。這裡統一正規化後再解析；解析不出來
    就讓例外往外拋，不在這裡吞掉（見 _remaining_delay() 的 fail-closed
    說明）。獨立成模組層級函式（不是 create_app() 內的閉包）是刻意的：
    純函式邏輯關在閉包裡 pytest 測不到，這是一個第四輪外部審查發現的
    既有 bug 才被抓出來——之後任何同類的時間戳/格式解析邏輯都該直接
    寫成模組層級函式，不要圖方便塞進 create_app()。"""
    s = ts.strip().replace("Z", "+00:00")
    m = _TS_FRAC_RE.search(s)
    if m:
        frac = m.group(1)[1:].ljust(6, "0")[:6]
        s = s[:m.start()] + "." + frac + s[m.end():]
    if _TS_SHORT_TZ_RE.search(s):
        s += ":00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class DeptScope(Enum):
    ALL = "all"    # 僅總管
    DEPT = "dept"  # 一般/部門管理員，附帶 department id


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="")

    _DEV_SECRET_KEY = "dev-secret-change-me"
    _is_production = bool(os.environ.get("RENDER_EXTERNAL_URL")) or os.environ.get("FLASK_ENV") == "production"
    _secret_key = os.environ.get("FLASK_SECRET_KEY", "")
    if _is_production and (not _secret_key or _secret_key == _DEV_SECRET_KEY):
        raise RuntimeError(
            "生產環境偵測到但 FLASK_SECRET_KEY 未設定或仍為預設值，"
            "拒絕以可預測的金鑰啟動——session 可被偽造，包含 superadmin"
        )
    app.secret_key = _secret_key or _DEV_SECRET_KEY
    # Secure 只在正式環境（Render 提供 HTTPS）開啟，本機 HTTP 開發環境開啟會讓 cookie 傳不出去
    app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RENDER_EXTERNAL_URL"))
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    CORS(app)

    # ── 靜態路徑保護 ────────────────────────────────────────────────
    # static_folder=FRONTEND, static_url_path="" 讓 frontend/ 下所有檔案
    # 掛在根路徑，Flask 的靜態伺服器完全繞過 @app.route 的裝飾器（含
    # @admin_required/@login_required）。實測 /dashboard.html 等 HTML
    # 未登入即可直接讀取。verify_isolation.sh 測不到這件事——它比對的是
    # API 回應裡有無哨兵標記，HTML 模板本身不含任何資料，永遠不會命中，
    # 是工具能力邊界問題，不是部門隔離失效。
    #
    # 目前風險等級低（純 Vue 模板、無內嵌資料、真正的資料都要走
    # /api/*，那裡裝飾器仍然生效），但這個架構讓「檔案放進 frontend/
    # 就會被公開」變成一條沒有記錄在任何地方的規則——本專案已經在這個
    # 目錄留下過兩個孤兒 HTML（admin.html、portal.html）。
    #
    # 不改 static_url_path 本身：那會牽動所有靜態資源的 URL、sw.js 的
    # STATIC_SHELL 清單、manifest.webmanifest 的 icons 路徑，且讓平板上
    # 既有的 Service Worker 快取全部失效，風險遠高於這裡要解決的問題。
    # 改為只擋 .html 直接存取，零遷移成本。
    @app.before_request
    def _block_direct_html_access():
        if request.path.endswith(".html"):
            abort(404)  # 404 而非 403，不透露檔案是否存在

    # ── 4.2 節：啟動時 fail fast ────────────────────────────────────
    is_production = _is_production
    if is_production and not _use_supabase():
        raise RuntimeError(
            "生產環境偵測到但 SUPABASE_URL/SUPABASE_KEY 未設定，"
            "拒絕悄悄降級成 JsonStore 單租戶模式啟動"
        )

    # ── 部門快取（PLAN 2.1 節）────────────────────────────────────────

    _DEPT_CACHE: dict = {}
    _DEPT_CACHE_TTL = 60  # 秒

    def _dept_cached(dept_id: str) -> Optional[dict]:
        """回傳部門資料列；None 代表部門不存在（含已被 purge）。"""
        now = time.monotonic()
        hit = _DEPT_CACHE.get(dept_id)
        if hit is not None and now - hit[1] < _DEPT_CACHE_TTL:
            return hit[0]
        row = department_store.get_by_id(dept_id)
        _DEPT_CACHE[dept_id] = (row, now)  # None 也要快取，避免被不存在的 id 打穿
        return row

    def _invalidate_dept_cache(dept_id: str) -> None:
        _DEPT_CACHE.pop(dept_id, None)

    # ── Auth helpers ────────────────────────────────────────────────

    def is_logged_in() -> bool:
        return session.get("auth") is True or session.get("admin") is True

    def is_admin() -> bool:
        return session.get("admin") is True

    def is_superadmin() -> bool:
        return session.get("superadmin") is True

    def assert_session_valid() -> None:
        """三件事的合取：部門仍存在（未被 purge）、active=true（未被停用）、
        session_version 與資料庫一致（密碼未被重設）。任一不成立即視同未登入。
        本機/測試模式（非 Supabase）不做此檢查，走 .env 明文比對的舊 fallback。"""
        if not _use_supabase():
            return
        if is_superadmin():
            return  # 超管不綁部門
        dept_id = session.get("department")
        if not dept_id:
            abort(401, "登入狀態異常，請重新登入")  # 改造前簽發的舊 session
        dept = _dept_cached(dept_id)
        if dept is None:
            abort(401, "登入狀態異常，請重新登入")  # 部門已被 purge
        if not dept.get("active"):
            abort(401, "登入狀態異常，請重新登入")  # 部門已被停用
        if session.get("dept_session_version") != dept.get("session_version"):
            abort(401, "登入狀態異常，請重新登入")  # 密碼已被重設

    def login_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not is_logged_in():
                if request.path.startswith("/api/"):
                    return jsonify({"error": "未授權"}), 401
                return redirect(url_for("login_page", next=request.path))
            assert_session_valid()
            return f(*args, **kwargs)
        wrapper._auth_level = "login"
        return wrapper

    def admin_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not is_admin():
                if request.path.startswith("/api/"):
                    return jsonify({"error": "需要管理員權限"}), 403
                return redirect(url_for("admin_login_page"))
            assert_session_valid()
            return f(*args, **kwargs)
        wrapper._auth_level = "admin"
        return wrapper

    def superadmin_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not is_superadmin():
                if request.path.startswith("/api/"):
                    return jsonify({"error": "需要總管理員權限"}), 403
                return redirect(url_for("admin_login_page"))
            assert_session_valid()
            return f(*args, **kwargs)
        wrapper._auth_level = "superadmin"
        return wrapper

    def public_endpoint(f):
        """純標記，不做任何驗證動作——讓「刻意公開」是主動宣告（PLAN 4.6/8.1 節）。"""
        @wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        wrapper._auth_level = "public"
        return wrapper

    # ── 4.1 節：scope_department() / resolve_target_department() ────

    def scope_department() -> tuple:
        """讀取過濾用：回答「這次請求可以看到哪些部門的資料」。"""
        if is_superadmin():
            viewing = request.args.get("dept")
            if not viewing or viewing == "__all__":
                return (DeptScope.ALL, None)
            # 超管的 ?dept= 打錯字時，過去會安靜回空清單（外部審查發現）。
            # 加一次存在性驗證，不存在就 404，跟一般帳號越權時的行為一致。
            if _use_supabase() and _dept_cached(viewing) is None:
                abort(404, NOT_FOUND_MSG)
            return (DeptScope.DEPT, viewing)
        dept = session.get("department")
        if not dept:
            abort(401, "登入狀態異常，請重新登入")
        return (DeptScope.DEPT, dept)

    def resolve_target_department(path_department: str) -> str:
        """寫入端點用：決定這次寫入要落在哪個部門。path_department 只來自 URL path，
        不讀 request.args、不讀 body 的 department 欄位（配套規則 a）。"""
        if is_superadmin():
            # 超管路徑打錯字時，過去會在有 FK 的表變 500、在無 FK 的表
            # （ai_scans/feedback）安靜寫入孤兒列（外部審查發現）。加一次
            # 存在性驗證，不存在就 404。
            if _use_supabase() and _dept_cached(path_department) is None:
                abort(404, NOT_FOUND_MSG)
            return path_department  # 超管：path 段就是明確指定的目標，直接採用
        dept = session.get("department")
        if not dept:
            abort(401, "登入狀態異常，請重新登入")
        if path_department != dept:
            abort(404, NOT_FOUND_MSG)  # 不透露「這個部門存在但你無權寫入」，一律裝作路徑不存在
        return dept

    def _check_body_department_conflict(body: dict, path_department: str) -> None:
        """配套規則 (b)：body 若也帶了 department 且與 path 不符 → 400。"""
        body_dept = body.get("department")
        if body_dept is not None and body_dept != path_department:
            abort(400, "department 與路徑不符")

    # ── General login / logout ──────────────────────────────────────

    @app.get("/login")
    @public_endpoint
    def login_page():
        if is_logged_in():
            return redirect("/")
        return send_from_directory(FRONTEND, "login.html")

    @app.post("/login")
    @public_endpoint
    def login_submit():
        pw = (request.form.get("password") or "").strip()
        form_department = (request.form.get("department") or "").strip()

        # 守衛條件只看環境，不看表單內容——若含 form_department 判斷，正式環境下
        # 只要不送 department 欄位就會掉進下方的 .env 明文比對 fallback，繞過節流
        # 與雜湊比對，變成一個無節流、無稽核紀錄的密碼預言機（外部審查發現）。
        if _use_supabase():
            if not form_department:
                return redirect(url_for("login_page", error=1))
            throttled = _check_login_throttle(form_department, login_page_endpoint="login_page")
            if throttled is not None:
                return throttled
            role = _do_login(form_department, pw, admin=False)
            if role is None:
                return redirect(url_for("login_page", error=1))
            next_url = request.form.get("next") or request.args.get("next", "/app")
            return redirect(next_url if next_url.startswith("/") else "/app")

        # 本機/測試模式 fallback：.env 明文比對（只有 _use_supabase()=False 才會到這裡）
        if pw == os.environ.get("LOGIN_PASSWORD", ""):
            session.clear()
            session["auth"] = True
            session["department"] = "local"
            next_url = request.form.get("next") or request.args.get("next", "/app")
            return redirect(next_url if next_url.startswith("/") else "/app")
        return redirect(url_for("login_page", error=1))

    @app.get("/logout")
    @public_endpoint
    def logout():
        session.clear()
        return redirect("/")

    # ── Admin login / logout ────────────────────────────────────────

    def _client_ip() -> str:
        return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()

    def _remaining_delay(n: int, last_failure_at: Optional[str], n_threshold: int, n_offset: int) -> int:
        """delay 是相對「最後一次失敗時間」的倒數計時，不是「N>=門檻就永久節流」——
        過了 2**effective_n 秒窗口，同一個 N 不再節流，直到下一次失敗才會觸發下一輪
        （且下一輪的 N 會遞增，delay 也隨之變長）。

        【第四輪外部審查修正】第一版遇到時間戳解析失敗時 except 分支回傳
        「完整延遲」，這是靜默降級——不管實際過了多久，永遠回報同一個固定
        秒數，使用者會被永久卡住且沒有任何錯誤訊息可循，跟這個系統反覆
        踩過的「例外分支回傳看似合理的預設值」是同一個模式（/ping 漏傳
        department、_count() 解析失敗回 0）。改為 fail-closed：解析失敗
        直接往外拋，讓呼叫端（登入端點）變成明確的 500，而不是把節流機制
        悄悄弄壞——節流是安全機制，資料格式跟預期不符時應該被立刻發現，
        不該被吞掉。"""
        if n < n_threshold or last_failure_at is None:
            return 0
        effective_n = n if n_offset == 0 else (n - n_offset)
        full_delay = min(2 ** effective_n, 60)
        last_dt = _parse_pg_timestamp(last_failure_at)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        remaining = full_delay - elapsed
        return max(0, int(remaining) + (1 if remaining % 1 else 0))

    def _check_login_throttle(form_department: str, *, login_page_endpoint: str):
        """PLAN 2.2/2.2.3 節：伺服器不等待，還在延遲窗口內直接回應。細網
        （ip, department）與粗網（ip）取延遲最大值。回傳 None 代表不節流，
        可以繼續走 _do_login()；否則回傳要直接 return 的 Flask response。

        表單提交（/login、/admin/login）不是 /api/*，若這裡回 JSON+429，
        使用者送出表單後會直接看到一個裸的 JSON 頁面、沒有路可以回去，
        且 Retry-After 在 header 裡使用者根本看不到剩餘秒數（外部審查
        發現）。改為重導回登入頁並把秒數帶在 query string，倒數 UI 由
        登入頁自己用 setInterval 算，伺服器端不依賴 JS 才能完成節流——
        沒有 JS 時使用者看到的是「請等 N 秒」的靜態文字加 disabled 按鈕，
        是漸進增強而非依賴。login_page_endpoint 由呼叫端指定要重導回
        /login 還是 /admin/login。
        """
        ip = _client_ip()
        n_fine, last_fine = login_attempt_store.count_fine(ip, form_department)
        n_coarse, last_coarse = login_attempt_store.count_coarse(ip)
        delay_fine = _remaining_delay(n_fine, last_fine, n_threshold=1, n_offset=0)
        delay_coarse = _remaining_delay(n_coarse, last_coarse, n_threshold=20, n_offset=19)
        delay = max(delay_fine, delay_coarse)
        if delay > 0:
            return redirect(url_for(login_page_endpoint, throttled=delay))
        return None

    def _do_login(form_department: str, password: str, admin: bool) -> Optional[str]:
        """PLAN 第 2 節：登入路徑在入口就分岔，不做 fallthrough。
        admin=False 時用於一般部門登入（僅走部門 pw_hash）；
        admin=True 時用於管理員登入（含 __super__ 分岔）。

        呼叫前提：_check_login_throttle() 已確認不在節流窗口內
        （2.2.4 節第 3 種情況由呼叫端在呼叫這個函式之前攔截，這裡
        面只處理「格式不合法」與「部門不存在」兩種情況的記錄）。
        """
        ip = _client_ip()

        if admin and form_department == SUPER_DEPT_SENTINEL:
            ok = hmac.compare_digest(password, os.environ.get("SUPERADMIN_PASSWORD", ""))
            login_attempt_store.record(ip, SUPER_DEPT_SENTINEL, ok)
            if not ok:
                return None  # 不 fallthrough 到部門密碼
            session.clear()
            session.update(auth=True, admin=True, superadmin=True, department=None)
            return "superadmin"

        if not DEPT_ID_RE.match(form_department):
            # 2.2.4 節情況 1：格式不合法，不查 DB、不寫入 login_attempts
            check_password_hash(_DUMMY_HASH, password)
            return None

        dept = department_store.get_by_id(form_department)
        if dept is None or not dept.get("active"):
            # 2.2.4 節情況 2：格式合法但部門不存在（或已停用），仍要記錄，
            # 且必須計入 N_ip（粗網），這是有價值的稽核訊號
            check_password_hash(_DUMMY_HASH, password)  # 2.2.2 節：枚舉防護，消耗相同時間
            login_attempt_store.record(ip, form_department, False)
            return None
        pw_field = "admin_pw_hash" if admin else "pw_hash"
        ok = check_password_hash(dept[pw_field], password)
        login_attempt_store.record(ip, form_department, ok)
        if not ok:
            return None
        session.clear()
        session.update(
            auth=True, admin=admin, superadmin=False,
            department=dept["id"],
            dept_session_version=dept["session_version"],
        )
        return "admin" if admin else "user"

    @app.get("/admin/login")
    @public_endpoint
    def admin_login_page():
        if is_admin():
            return redirect("/admin")
        return send_from_directory(FRONTEND, "admin-login.html")

    @app.post("/admin/login")
    @public_endpoint
    def admin_login_submit():
        pw = (request.form.get("password") or "").strip()
        form_department = (request.form.get("department") or "").strip()

        # 守衛條件只看環境，不看表單內容——理由同 login_submit()
        if _use_supabase():
            if not form_department:
                return redirect(url_for("admin_login_page", error=1))
            throttled = _check_login_throttle(form_department, login_page_endpoint="admin_login_page")
            if throttled is not None:
                return throttled
            role = _do_login(form_department, pw, admin=True)
            if role is None:
                return redirect(url_for("admin_login_page", error=1))
            return redirect("/admin")

        # 本機/測試模式 fallback：.env 明文比對（只有 _use_supabase()=False 才會到這裡）
        if pw == os.environ.get("ADMIN_PASSWORD", ""):
            session.clear()
            session["admin"] = True
            session["auth"] = True
            session["department"] = "local"
            return redirect("/admin")
        return redirect(url_for("admin_login_page", error=1))

    @app.get("/admin/logout")
    @public_endpoint
    def admin_logout():
        # session.clear()，不只 pop admin/superadmin（外部審查發現：只 pop
        # 這兩個鍵會殘留 auth=True／department，對超管而言 department 是
        # None，登出後台後 scope_department() 會一律 401，前台永久壞掉
        # 直到重新登入）。比照 /logout 直接清空整個 session。
        session.clear()
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
        scope, dept = scope_department()
        q = request.args.get("q", "").strip().lower()
        device = request.args.get("device", "").strip()
        severity = request.args.get("severity", "").strip()
        missing_local = request.args.get("missing_local", "").strip().lower() == "true"
        items = alarms_store.load(department=(dept if scope == DeptScope.DEPT else None))

        def match(a: dict) -> bool:
            if device and a.get("device_model") != device:
                return False
            if severity and a.get("severity") != severity:
                return False
            if missing_local and (a.get("local_solution") or "").strip():
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

    @app.get("/api/alarms/<department>/<device_model>/<code>")
    @login_required
    def get_alarm(department: str, device_model: str, code: str):
        target = resolve_target_department(department)
        for a in alarms_store.load(department=target):
            if a["code"] == code and a.get("device_model") == device_model:
                return jsonify(a)
        abort(404, "找不到此警報代碼")

    @app.get("/api/devices")
    @login_required
    def list_devices():
        scope, dept = scope_department()
        return jsonify(devices_store.load(department=(dept if scope == DeptScope.DEPT else None)))

    @app.post("/api/devices/<department>")
    @admin_required
    def create_device(department: str):
        target = resolve_target_department(department)
        body = request.get_json(silent=True) or {}
        _check_body_department_conflict(body, target)
        model = (body.get("model") or body.get("device_model") or "").strip()
        if not model:
            abort(400, "model 為必填")
        items = devices_store.load(department=target)
        if any(d.get("model") == model for d in items):
            abort(409, "機種已存在")
        new_id = (body.get("id") or "").strip() or f"{target}-{model}"
        device = devices_store.upsert_one(
            {"id": new_id, "model": model,
             "category": (body.get("category") or "").strip(),
             "line": (body.get("line") or "").strip()},
            department=target,
            on_conflict="department,model",
        )
        return jsonify(device), 201

    @app.get("/api/devices/<department>/<device_model>")
    @login_required
    def get_device(department: str, device_model: str):
        target = resolve_target_department(department)
        for d in devices_store.load(department=target):
            if d.get("model") == device_model:
                return jsonify(d)
        abort(404, "找不到此機種")

    @app.put("/api/devices/<department>/<device_model>")
    @admin_required
    def update_device(department: str, device_model: str):
        target = resolve_target_department(department)
        body = request.get_json(silent=True) or {}
        _check_body_department_conflict(body, target)
        items = devices_store.load(department=target)
        existing = next((d for d in items if d.get("model") == device_model), None)
        if existing is None:
            abort(404, "找不到此機種")
        device = devices_store.upsert_one(
            {"id": existing["id"],
             "model": (body.get("model") or body.get("device_model") or device_model).strip(),
             "category": (body.get("category") or existing.get("category") or "").strip(),
             "line": (body.get("line") or existing.get("line") or "").strip()},
            department=target,
            on_conflict="department,model",
        )
        return jsonify(device)

    @app.delete("/api/devices/<department>/<device_model>")
    @admin_required
    def delete_device(department: str, device_model: str):
        target = resolve_target_department(department)
        items = devices_store.load(department=target)
        if not any(d.get("model") == device_model for d in items):
            abort(404, "找不到此機種")
        devices_store.delete_one(department=target, match={"model": device_model})
        return "", 204

    @app.get("/api/server-url")
    @public_endpoint
    def server_url():
        """Render 的 healthCheckPath 指向這裡（render.yaml），不能加
        login_required——健康檢查請求不會帶 session，加了會讓 Render
        誤判服務不健康。

        本機/內網開發時回內網 IP 是刻意功能（方便同網段的平板/手機
        連線測試，不用手動查 IP）。但這個 fallback 不該在正式環境
        觸發——即使漏設 RENDER_EXTERNAL_URL/PUBLIC_URL，只要偵測到
        production 環境就不该把內網 IP 回給任何未驗證的呼叫端（外部
        審查發現：這個端點是 public，正式環境若忘記設定這兩個變數，
        會把內網拓樸資訊洩漏給任何人）。"""
        public = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_URL")
        if public:
            return jsonify({"url": public.rstrip("/") + "/"})
        if is_production:
            abort(500, "PUBLIC_URL 或 RENDER_EXTERNAL_URL 未設定")
        host = request.host or ""
        port = host.split(":", 1)[1] if ":" in host else "5001"
        return jsonify({"url": f"http://{_lan_ip()}:{port}/"})

    # ── Write API (需要管理員) ───────────────────────────────────────

    @app.post("/api/alarms/<department>")
    @admin_required
    def create_alarm(department: str):
        target = resolve_target_department(department)
        raw_body = request.get_json(silent=True) or {}
        _check_body_department_conflict(raw_body, target)  # 必須用原始 body——
        # normalize() 只保留 ALARM_FIELDS 白名單，不含 department，過濾後
        # 這個檢查永遠不會觸發（外部審查發現的死碼）。
        body = normalize(raw_body)
        items = alarms_store.load(department=target)
        if any(a["code"] == body["code"] and a.get("device_model") == body.get("device_model") for a in items):
            abort(409, "代碼已存在")
        row = alarms_store.upsert_one(body, department=target, on_conflict="department,device_model,code")
        audit_logger.log("CREATE", department=target, new_data=row)
        return jsonify(row), 201

    @app.put("/api/alarms/<department>/<device_model>/<code>")
    @admin_required
    def update_alarm(department: str, device_model: str, code: str):
        target = resolve_target_department(department)
        raw_body = request.get_json(silent=True) or {}
        _check_body_department_conflict(raw_body, target)  # 見 create_alarm 同樣的說明
        body = normalize(raw_body, require_code=False)
        body["code"] = code
        body["device_model"] = device_model
        items = alarms_store.load(department=target)
        old = next((a for a in items if a["code"] == code and a.get("device_model") == device_model), None)
        if old is None:
            abort(404, "找不到此警報代碼")
        row = alarms_store.upsert_one(body, department=target, on_conflict="department,device_model,code")
        audit_logger.log("UPDATE", department=target, new_data=row, old_data=old)
        return jsonify(row)

    @app.delete("/api/alarms/<department>/<device_model>/<code>")
    @admin_required
    def delete_alarm(department: str, device_model: str, code: str):
        target = resolve_target_department(department)
        items = alarms_store.load(department=target)
        old = next((a for a in items if a["code"] == code and a.get("device_model") == device_model), None)
        if old is None:
            abort(404, "找不到此警報代碼")
        alarms_store.delete_one(department=target, match={"device_model": device_model, "code": code})
        audit_logger.log("DELETE", department=target, old_data=old)
        return "", 204

    # ── 現場處置做法 local_solution（PLAN_local_solution.md）─────────

    LOCAL_EDITABLE = {"local_solution", "local_reason"}

    @app.put("/api/alarms/<department>/<device_model>/<code>/local")
    @login_required
    def update_local_solution(department: str, device_model: str, code: str):
        """任何登入者皆可直接編輯現場做法，不再限管理員（PLAN_local_solution.md
        審核路徑停用決策記錄）。原本規劃「一般使用者提交建議、管理員審核」，
        但部門共用密碼、無個人帳號，提交者與審核者無法區分，且
        alarm_suggestions.submitted_by 記的只是「某個知道密碼的人」，審核者
        判斷不了建議可不可信——審核在此模型下摩擦為真、把關為假。改為所有
        登入者直接編輯，以 alarm_history 的 local_update 紀錄與前台的
        「最後修改」標示作為追溯機制，這不依賴身分模型。

        只接受 local_solution/local_reason，其餘欄位一律忽略——這是防止
        原廠欄位（solution）被覆寫的最後一道（PLAN_local_solution.md 4.3
        節），與權限層級無關，不能省。department 必須用
        resolve_target_department() 的目標部門，不能沿用 _confirmed_by()
        （那個服務的是無路徑部門段的端點，語意不同，見 4.4 節）。"""
        target = resolve_target_department(department)
        body = request.get_json(silent=True) or {}
        patch = {k: v for k, v in body.items() if k in LOCAL_EDITABLE}
        if not patch:
            abort(400, "沒有可更新的欄位")
        role = "superadmin" if is_superadmin() else ("admin" if is_admin() else "user")
        patch["local_updated_by"] = f"{target}/{role}"
        patch["local_updated_at"] = datetime.now(timezone.utc).isoformat()
        old_row = alarms_store.get_one(department=target, match={"device_model": device_model, "code": code})
        if old_row is None:
            abort(404, "找不到此警報代碼")
        row = alarms_store.patch_one(department=target,
                                     match={"device_model": device_model, "code": code}, patch=patch)
        if row is None:
            # patch_one() 打不到任何列——警報在上面 get_one() 查完之後被
            # 刪除的競態，或 PostgREST 過濾條件沒匹配到（不該發生，但
            # PATCH 打空不報錯，靜默回 None 比讓前端以為改成功了更安全，見
            # 外部審查提醒：這類「看似合理的預設值」是這個系統反覆踩過的坑）
            abort(404, "找不到此警報代碼")
        audit_logger.log("local_update", department=target, new_data=row, old_data=old_row)
        return jsonify(row)

    @app.get("/api/alarms/<department>/<device_model>/<code>/history")
    @login_required
    def alarm_local_history(department: str, device_model: str, code: str):
        """單筆警報的本廠做法變更紀錄。審核路徑停用後，這是追溯機制的
        核心——歷史紀錄不依賴身分模型，前台詳情卡片的「最後修改」標示與
        這裡的完整紀錄，取代的是「這筆內容可不可信」的判斷依據（PLAN
        審核路徑停用決策記錄）。

        只回 operation=local_update 的紀錄——一般使用者不需要看到批次
        匯入、機種變更這類技術性軌跡，那些留在後台的 /api/audit（維持
        admin_required 不動，不為了這個功能把整個部門的稽核軌跡開放給
        一般使用者）。

        唯讀。alarm_history 只有寫入路徑（AuditLogger.log），沒有
        update/delete 端點——稽核軌跡能被修改就沒有稽核價值。"""
        target = resolve_target_department(department)  # 跨部門一律 404
        if alarms_store.get_one(department=target, match={"device_model": device_model, "code": code}) is None:
            abort(404, "找不到此警報代碼")
        rows = audit_logger.list_for_alarm(
            department=target, device_model=device_model, code=code,
            operation="local_update", limit=20,
        )
        return jsonify(rows)

    # ── alarm_suggestions（審核路徑，目前停用，見下方三支端點與 storage.py
    #    的 AlarmSuggestionStore）─────────────────────────────────────
    #
    # 這三支端點目前無前端呼叫端。審核路徑已停用：部門共用密碼、無個人
    # 帳號，提交者與審核者無法區分，且 alarm_suggestions.submitted_by
    # 記的只是「某個知道密碼的人」，審核者判斷不了建議可不可信——審核
    # 在此模型下摩擦為真、把關為假（PLAN_local_solution.md 審核路徑
    # 停用決策記錄）。
    #
    # 表與端點保留、不刪除：這套機制已對正式 Supabase 環境端到端驗證
    # 過（提交 → 待審 → 接受 → alarms.local_solution 生效，全部正確），
    # 技術上是完好的。刪除成本（改 storage.py、改 app.py、改白名單、
    # 跑 migration、更新測試）遠高於保留成本（一張空表 + 三支無呼叫端
    # 的端點）。待個人帳號功能完成、提交者與審核者可以真正區分時，
    # 直接重新啟用即可，不需要重寫或重新驗證。

    @app.post("/api/alarms/<department>/<device_model>/<code>/suggestions")
    @login_required
    def submit_local_suggestion(department: str, device_model: str, code: str):
        """一般使用者提交現場做法建議，寫進待審表，不直接改 alarms
        （PLAN_local_solution.md 2.2 節）。department 一律來自 session，
        沒有路徑部門段的寫入語意在這裡不適用——但這個端點確實有路徑段，
        所以仍走 resolve_target_department()，跟其他三段式路由一致，
        超管走這個端點沒有意義（不屬於任何部門），維持既有配套規則。"""
        target = resolve_target_department(department)
        body = request.get_json(silent=True) or {}
        suggestion = (body.get("suggestion") or "").strip()
        if not suggestion:
            abort(400, "suggestion 為必填")
        reason = (body.get("reason") or "").strip() or None
        if alarms_store.get_one(department=target, match={"device_model": device_model, "code": code}) is None:
            abort(404, "找不到此警報代碼")
        # 防止同一筆警報已有待審建議時又重複提交——不區分是不是同一人
        # 提的，因為部門共用密碼追不到個人（見 PLAN_local_solution.md 9
        # 節已知限制），只要這筆警報已經有人在排隊審核，就不該再疊一筆。
        # 這裡只是給友善 409 訊息的第一線，真正防競態（兩人同時提交、
        # 同一人連點兩下）的是資料庫的部分唯一索引（005 遷移），兩者
        # 不衝突——外部審查第四輪指出應用層檢查有 check-then-insert
        # 的競態視窗，資料庫層才是真正的保險。
        if alarm_suggestion_store.has_pending(target, device_model, code):
            abort(409, "這筆警報已有待審核的建議，請等管理員處理後再提交")
        role = "superadmin" if is_superadmin() else ("admin" if is_admin() else "user")
        try:
            row = alarm_suggestion_store.create(
                department=target, device_model=device_model, code=code,
                suggestion=suggestion, reason=reason, submitted_by=f"{target}/{role}",
            )
        except urllib.error.HTTPError as e:
            if e.code == 409:
                # has_pending() 已經檢查過，這裡仍撞到 005 遷移的部分唯一
                # 索引，代表競態確實發生（兩人同時提交，或 has_pending()
                # 查完之後、create() 送出之前有別人搶先送出）——資料庫層
                # 的保險生效了，接住轉成乾淨的應用層 409，不要讓 PostgREST
                # 的原始 HTTPError 往上炸成未預期的 500。
                abort(409, "這筆警報已有待審核的建議，請等管理員處理後再提交")
            raise
        return jsonify(row), 201

    @app.get("/api/admin/suggestions")
    @admin_required
    def list_suggestions():
        scope, dept = scope_department()
        rows = alarm_suggestion_store.list_pending(department=(dept if scope == DeptScope.DEPT else None))
        return jsonify(rows)

    @app.put("/api/admin/suggestions/<int:suggestion_id>")
    @admin_required
    def review_suggestion(suggestion_id: int):
        """接受寫入 local_solution（沿用 update_local_solution 同一支
        patch_one() 呼叫，稽核軌跡同樣記 local_update）；退回只更新
        alarm_suggestions 本身的狀態，不動 alarms。"""
        row = alarm_suggestion_store.get_by_id(suggestion_id)
        if row is None:
            abort(404, "找不到此建議")
        scope, dept = scope_department()
        if scope == DeptScope.DEPT and row["department"] != dept:
            abort(404, NOT_FOUND_MSG)  # 不透露其他部門的建議存在（同三段式路由 404 而非 403 的一貫做法）
        if row["status"] != "pending":
            abort(409, "這筆建議已經審核過了")
        body = request.get_json(silent=True) or {}
        action = body.get("action")
        if action not in ("accept", "reject"):
            abort(400, "action 必須是 accept 或 reject")
        role = "superadmin" if is_superadmin() else "admin"
        # 一律用建議所屬的部門（row["department"]），不是審核者的檢視範圍
        # （scope_department() 的 dept）——這次寫入實際影響的是建議所屬
        # 部門，超管在 ?dept= 模式下審核時兩者可能不同，稽核軌跡要記對
        # 象而非操作者當下的檢視狀態（同 PLAN_local_solution.md 4.4 節
        # local_updated_by 的原則）。
        reviewer = f"{row['department']}/{role}"
        note = (body.get("review_note") or "").strip() or None
        if action == "accept":
            patch = {"local_solution": row["suggestion"], "local_updated_by": reviewer,
                     "local_updated_at": datetime.now(timezone.utc).isoformat()}
            if row.get("reason"):
                patch["local_reason"] = row["reason"]
            old = alarms_store.load(department=row["department"])
            old_row = next((a for a in old if a["code"] == row["code"]
                            and a.get("device_model") == row["device_model"]), None)
            new_row = alarms_store.patch_one(department=row["department"],
                                             match={"device_model": row["device_model"], "code": row["code"]},
                                             patch=patch)
            if new_row is None:
                # 建議指向的警報在待審期間被刪除——alarm_suggestions 的外鍵
                # 有 on delete cascade，理論上這筆建議會跟著消失，走不到這裡；
                # 但 patch_one() 打空不報錯，保守起見仍要擋，不留靜默寫入 None
                abort(404, "找不到此建議對應的警報，可能已被刪除")
            audit_logger.log("local_update", department=row["department"], new_data=new_row, old_data=old_row)
            updated = alarm_suggestion_store.review(suggestion_id, "accepted", reviewer, note)
        else:
            updated = alarm_suggestion_store.review(suggestion_id, "rejected", reviewer, note)
        return jsonify(updated)

    @app.post("/api/feedback")
    @login_required
    def submit_feedback():
        if is_superadmin():
            abort(400, "請以部門帳號操作")
        dept = session.get("department")
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
        feedback_store.append(entry, department=dept)
        return jsonify({"ok": True}), 201

    @app.get("/api/feedback/stats")
    @login_required
    def feedback_stats():
        scope, dept = scope_department()
        return jsonify(feedback_store.stats(department=(dept if scope == DeptScope.DEPT else None)))

    @app.post("/api/view")
    @login_required
    def record_view():
        if is_superadmin():
            abort(400, "請以部門帳號操作")
        dept = session.get("department")
        body = request.get_json(silent=True) or {}
        code = body.get("code", "").strip()
        device_model = body.get("device_model", "").strip()
        if not code:
            abort(400, "code 為必填")
        view_store.append({
            "code": code,
            "device_model": device_model,
            "viewed_at": datetime.now(timezone.utc).isoformat(),
        }, department=dept)
        return jsonify({"ok": True}), 201

    @app.get("/api/view/stats")
    @login_required
    def view_stats():
        scope, dept = scope_department()
        return jsonify(view_store.stats(department=(dept if scope == DeptScope.DEPT else None)))

    @app.post("/api/analyze")
    @login_required
    def analyze_image():
        if is_superadmin():
            abort(400, "請以部門帳號操作")
        body = request.get_json(silent=True) or {}
        image_b64 = body.get("image")
        mime_type = body.get("mime_type", "image/jpeg")
        known_model = (body.get("model") or "").strip() or None
        if not image_b64:
            abort(400, "image (base64) 為必填")
        try:
            from ai import run_pipeline
            return jsonify(run_pipeline(image_b64, mime_type, known_model=known_model,
                                        department=session.get("department")))
        except ImportError:
            app.logger.exception("AI 模組未安裝")
            abort(503, "AI 模組未安裝")
        except KeyError:
            app.logger.exception("AI 分析缺少環境變數")
            abort(503, "AI 服務設定不完整，請聯絡管理員")
        except Exception:
            app.logger.exception("AI 分析失敗")
            abort(500, "AI 分析失敗，請稍後再試或聯絡管理員")

    def _confirmed_by() -> str:
        """PLAN 5.2.2 節：用寫入的目標部門＋真實角色組成，取代信任前端傳值。
        依配套規則 c，超管本來就不會走到 analyze/confirm/correct，此處僅為文件正確性保險。"""
        target = session.get("department") or "unknown"
        role = "superadmin" if is_superadmin() else ("admin" if is_admin() else "user")
        return f"{target}/{role}"

    @app.post("/api/confirm")
    @login_required
    def confirm_scan():
        """操作員確認 AI 結果正確（未修改），補寫 source=confirmed 記錄。"""
        if is_superadmin():
            abort(400, "請以部門帳號操作")
        body = request.get_json(silent=True) or {}
        scan_id = (body.get("scan_id") or "").strip()
        model = (body.get("model") or "").strip()
        if not scan_id or not model:
            abort(400, "scan_id、model 為必填")
        try:
            from ai import run_confirmation
            raw_conf = body.get("model_conf")
            result = run_confirmation(
                scan_id=scan_id,
                model=model,
                alarms=body.get("alarms", []),
                model_conf=int(raw_conf) if raw_conf is not None else None,
                original_model=body.get("original_model"),
                original_analyzer=body.get("original_analyzer"),
                confirmed_by=_confirmed_by(),
                department=session.get("department"),
            )
            return jsonify(result), 201
        except Exception:
            app.logger.exception("確認記錄失敗")
            abort(500, "確認記錄失敗，請稍後再試或聯絡管理員")

    @app.post("/api/correct")
    @login_required
    def correct_scan():
        """操作員修正 AI 辨識結果（MEM-002）。"""
        if is_superadmin():
            abort(400, "請以部門帳號操作")
        body = request.get_json(silent=True) or {}
        scan_id = (body.get("scan_id") or "").strip()
        corrected_model = (body.get("corrected_model") or "").strip()
        if not scan_id or not corrected_model:
            abort(400, "scan_id、corrected_model 為必填")
        try:
            from ai import run_correction
            raw_conf = body.get("model_conf")
            result = run_correction(
                scan_id=scan_id,
                original_model=body.get("original_model"),
                corrected_model=corrected_model,
                original_codes=body.get("original_codes", []),
                corrected_codes=body.get("corrected_codes", []),
                model_conf=int(raw_conf) if raw_conf is not None else None,
                original_analyzer=body.get("original_analyzer"),
                confirmed_by=_confirmed_by(),
                department=session.get("department"),
            )
            return jsonify(result), 201
        except Exception:
            app.logger.exception("修正記錄失敗")
            abort(500, "修正記錄失敗，請稍後再試或聯絡管理員")

    @app.get("/api/audit")
    @admin_required
    def list_audit():
        scope, dept = scope_department()
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 500))
        except ValueError:
            abort(400, "limit 必須是數字")
        device_model = (request.args.get("device_model") or "").strip() or None
        # 純日期（YYYY-MM-DD），不是完整 ISO timestamp——前端快速選項
        # 只需要日期粒度。to 補到當天結束，否則「今天」會漏掉當天的異動
        # （gte today 00:00 加 lte today 00:00 只會篩到剛好那一刻）。
        from_raw = (request.args.get("from") or "").strip()
        to_raw = (request.args.get("to") or "").strip()
        from_dt = to_dt = None
        try:
            if from_raw:
                from_dt = datetime.fromisoformat(from_raw).replace(tzinfo=timezone.utc).isoformat()
            if to_raw:
                to_dt = (datetime.fromisoformat(to_raw) + timedelta(days=1)).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            abort(400, "from/to 必須是 YYYY-MM-DD 格式")
        items, truncated = audit_logger.load(
            limit, department=(dept if scope == DeptScope.DEPT else None),
            device_model=device_model, from_dt=from_dt, to_dt=to_dt,
        )
        return jsonify({"items": items, "truncated": truncated, "limit": limit})

    # ── Admin dashboard (AI 掃描統計) ─────────────────────────────────

    def _parse_dt(iso: str):
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except Exception:
            return None

    @app.get("/api/admin/scan-stats")
    @admin_required
    def scan_stats():
        scope, dept = scope_department()
        scans = ai_scan_store.load_scans(department=(dept if scope == DeptScope.DEPT else None))
        now = datetime.now(timezone.utc)
        today = now.date()
        week_start = today.fromordinal(today.toordinal() - today.weekday())

        today_count = 0
        week_count = 0
        fail_count = 0
        total = len(scans)
        for s in scans:
            dt = _parse_dt(s.get("created_at", ""))
            if dt is not None:
                d = dt.astimezone(timezone.utc).date()
                if d == today:
                    today_count += 1
                if d >= week_start:
                    week_count += 1
            if s.get("tier") in ("failure", "low_confidence"):
                fail_count += 1

        fail_rate = round(fail_count / total * 100, 1) if total else 0.0
        return jsonify({
            "today_count": today_count,
            "week_count": week_count,
            "total_count": total,
            "fail_count": fail_count,
            "fail_rate": fail_rate,
        })

    def _parse_alarms(raw):
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return []
        return raw or []

    @app.get("/api/admin/scan-recent")
    @admin_required
    def scan_recent():
        scope, dept = scope_department()
        limit = min(int(request.args.get("limit", 50)), 200)
        scans = ai_scan_store.load_scans(limit=limit, department=(dept if scope == DeptScope.DEPT else None))
        result = [{
            "scan_id": s.get("scan_id"),
            "scanned_at": s.get("created_at"),
            "model": s.get("model"),
            "model_conf": s.get("model_conf"),
            "tier": s.get("tier"),
            "source": s.get("source"),
            "corrected": s.get("source") == "corrected",
            "alarms": _parse_alarms(s.get("alarms")),
        } for s in scans]
        return jsonify(result)

    @app.get("/api/admin/scan-ranking")
    @admin_required
    def scan_ranking():
        scope, dept = scope_department()
        scans = ai_scan_store.load_scans(department=(dept if scope == DeptScope.DEPT else None))
        counts: dict = {}
        for s in scans:
            model = s.get("model") or "未知"
            if model not in counts:
                counts[model] = {"model": model, "count": 0, "fail_count": 0}
            counts[model]["count"] += 1
            if s.get("tier") in ("failure", "low_confidence"):
                counts[model]["fail_count"] += 1
        ranking = sorted(counts.values(), key=lambda x: -x["count"])
        for r in ranking:
            r["fail_rate"] = round(r["fail_count"] / r["count"] * 100, 1) if r["count"] else 0.0
        return jsonify(ranking)

    @app.get("/api/admin/ai-logs")
    @admin_required
    def ai_logs():
        scope, dept = scope_department()
        limit = min(int(request.args.get("limit", 100)), 500)
        return jsonify(ai_scan_store.load_logs(limit=limit, department=(dept if scope == DeptScope.DEPT else None)))

    @app.post("/api/admin/cleanup-expired")
    @superadmin_required
    def cleanup_expired():
        try:
            from ai.ai_memory import RETENTION
        except ImportError as e:
            abort(503, f"AI 模組未安裝：{e}")
        removed = ai_scan_store.cleanup_expired(RETENTION)
        total = sum(n for n in removed.values() if n > 0)
        login_attempts_removed = login_attempt_store.cleanup_expired(days=90)
        return jsonify({
            "removed_by_tier": removed,
            "total_removed": total,
            "login_attempts_removed": login_attempts_removed,
        })

    # ── 4.5 節：部門管理端點（superadmin_required）───────────────────

    @app.get("/api/admin/departments")
    @superadmin_required
    def list_departments():
        return jsonify(department_store.list())

    @app.post("/api/admin/departments")
    @superadmin_required
    def create_department():
        body = request.get_json(silent=True) or {}
        dept_id = (body.get("id") or "").strip()
        name = (body.get("name") or "").strip()
        password = body.get("password") or ""
        admin_password = body.get("admin_password") or ""
        if not DEPT_ID_RE.match(dept_id):
            abort(400, "id 必須符合 ^[a-z0-9_]{1,32}$")
        if not name or not password or not admin_password:
            abort(400, "name、password、admin_password 為必填")
        _validate_dept_name(name)
        super_pw = os.environ.get("SUPERADMIN_PASSWORD", "")
        if super_pw and (password == super_pw or admin_password == super_pw):
            abort(400, "密碼不可與總管理員密碼相同")
        dept = department_store.create(
            dept_id, name,
            generate_password_hash(password, method="pbkdf2:sha256"),
            generate_password_hash(admin_password, method="pbkdf2:sha256"),
            hidden=bool(body.get("hidden", False)),
            purgeable=bool(body.get("purgeable", False)),
        )
        dept.pop("pw_hash", None)
        dept.pop("admin_pw_hash", None)
        return jsonify(dept), 201

    @app.put("/api/admin/departments/<dept_id>")
    @superadmin_required
    def rename_department(dept_id: str):
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            abort(400, "name 為必填")
        _validate_dept_name(name)
        department_store.update_name(dept_id, name)
        return jsonify({"ok": True})

    @app.put("/api/admin/departments/<dept_id>/reset-password")
    @superadmin_required
    def reset_department_password(dept_id: str):
        body = request.get_json(silent=True) or {}
        password = body.get("password")
        admin_password = body.get("admin_password")
        if not password and not admin_password:
            abort(400, "password 或 admin_password 至少一個必填")
        super_pw = os.environ.get("SUPERADMIN_PASSWORD", "")
        if super_pw and (password == super_pw or admin_password == super_pw):
            abort(400, "密碼不可與總管理員密碼相同")
        department_store.update_password(
            dept_id,
            pw_hash=generate_password_hash(password, method="pbkdf2:sha256") if password else None,
            admin_pw_hash=generate_password_hash(admin_password, method="pbkdf2:sha256") if admin_password else None,
        )
        _invalidate_dept_cache(dept_id)
        return jsonify({"ok": True})

    @app.put("/api/admin/departments/<dept_id>/active")
    @superadmin_required
    def set_department_active(dept_id: str):
        body = request.get_json(silent=True) or {}
        active = body.get("active")
        if active is None:
            abort(400, "active 為必填")
        department_store.set_active(dept_id, bool(active))
        _invalidate_dept_cache(dept_id)
        return jsonify({"ok": True})

    @app.get("/api/admin/departments/<dept_id>/impact")
    @superadmin_required
    def department_impact(dept_id: str):
        """刪除部門前的確認端點：各表筆數統計（PLAN 5 節前端刪除流程第一步，
        外部審查發現此端點原本不存在，設計稿的刪除確認畫面依賴它）。"""
        dept = department_store.get_by_id(dept_id)
        if dept is None:
            abort(404, "部門不存在")
        counts = department_store.count_impact(dept_id)
        return jsonify({"department": dept_id, "counts": counts})

    @app.delete("/api/admin/departments/<dept_id>")
    @superadmin_required
    def purge_department(dept_id: str):
        body = request.get_json(silent=True) or {}
        confirm_id = body.get("confirm_id")
        try:
            removed = department_store.purge(dept_id, confirm_id)
        except PermissionError as e:
            abort(403, str(e))
        except ValueError as e:
            abort(400, str(e))
        _invalidate_dept_cache(dept_id)
        return jsonify({"ok": True, "removed": removed})

    # ── 4.6/4.7 節：公開端點 ──────────────────────────────────────────

    @app.get("/api/whoami")
    @public_endpoint
    def whoami():
        dept_id = session.get("department")
        dept_name = None
        if dept_id and _use_supabase():
            dept = _dept_cached(dept_id)
            dept_name = dept.get("name") if dept else None
        return jsonify({
            "auth": is_logged_in(),
            "admin": is_admin(),
            "superadmin": is_superadmin(),
            "department": dept_id,
            "department_name": dept_name,
        })

    @app.get("/api/departments/public")
    @public_endpoint
    def departments_public():
        if not _use_supabase():
            return jsonify([])
        return jsonify(department_store.list_public())

    # ── Pages ───────────────────────────────────────────────────────

    def _no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/ping")
    @public_endpoint
    def ping():
        try:
            # 只確認連得到資料庫，不搬資料列——department=None 的 load() 會撈
            # 整張表（外部審查發現：Render cron 每 10 分鐘打一次，白白浪費）
            alarms_store.probe()
            return {"status": "ok"}, 200
        except Exception as e:
            return {"status": "db_error", "msg": str(e)}, 200

    @app.get("/")
    @public_endpoint
    def root_redirect():
        return redirect("/app")

    @app.get("/app")
    @public_endpoint
    def index():
        return _no_cache(send_from_directory(FRONTEND, "index.html"))

    @app.get("/admin")
    @admin_required
    def admin():
        return _no_cache(send_from_directory(FRONTEND, "dashboard.html"))

    @app.get("/admin/dashboard")
    @admin_required
    def admin_dashboard():
        return redirect("/admin")

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
