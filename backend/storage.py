import json
import os
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Optional


def _data_dir() -> Path:
    env = os.environ.get("ALARM_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data"


class JsonStore:
    """單租戶 store，僅服務本機開發／pytest（PLAN 3.2 節）。

    department 參數一律接受但忽略——JsonStore 不承擔多部門角色，
    多租戶真正的風險點（PostgREST eq 語意、NULL 不匹配、save() 整表
    刪除掃描、on_conflict）在這裡不存在，測通不代表任何保證。

    upsert_one()/delete_one() 只是為了讓 app.py 的單筆 CRUD 呼叫端
    介面一致（不用 if isinstance(store, SupabaseStore) 分支），內部
    仍是 load-modify-save，不模擬 PostgREST 的 on_conflict 語意。
    """

    def __init__(self, filename: str, is_devices: bool = False):
        self.filename = filename
        self.is_devices = is_devices
        self._lock = Lock()

    @property
    def path(self) -> Path:
        return _data_dir() / self.filename

    def _match_fields(self) -> list:
        return ["model"] if self.is_devices else ["device_model", "code"]

    def probe(self) -> None:
        """健康檢查專用，介面對齊 SupabaseStore.probe()。JsonStore 是本機檔案，
        沒有網路往返成本，直接確認檔案路徑存在即可，不需要真的讀取內容。"""
        _ = self.path.exists()

    def load(self, department: Optional[str] = None) -> list:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            items = json.load(f)
        if self.is_devices:
            items = [_row_to_device(row) for row in items]
        return items

    def save(self, items: list, department: Optional[str] = None, on_conflict: Optional[str] = None) -> None:
        with self._lock:
            write_items = [_device_payload_to_row(i) for i in items] if self.is_devices else items
            if self.is_devices:
                for row, original in zip(write_items, items):
                    if "id" in original:
                        row["id"] = original["id"]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(write_items, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)

    def upsert_one(self, item: dict, department: Optional[str] = None,
                    on_conflict: Optional[str] = None) -> dict:
        with self._lock:
            raw = []
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
            write_item = _device_payload_to_row(item) if self.is_devices else dict(item)
            if self.is_devices and "id" in item:
                write_item["id"] = item["id"]
            fields = self._match_fields()
            replaced = False
            for i, row in enumerate(raw):
                if all(row.get(f) == write_item.get(f) for f in fields):
                    raw[i] = write_item
                    replaced = True
                    break
            if not replaced:
                raw.append(write_item)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)
        return _row_to_device(write_item) if self.is_devices else write_item

    def delete_one(self, department: Optional[str] = None, match: dict = None) -> None:
        match = match or {}
        with self._lock:
            raw = []
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
            remaining = [row for row in raw if not all(row.get(k) == v for k, v in match.items())]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(remaining, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)


def _row_to_device(row: dict) -> dict:
    """devices 表的欄位叫 model，但 API 對外同時提供兩個 key。

    這不是過渡措施，是最終設計（PLAN 3.1.1 節，第十九輪定案）：
    - `model`        既有前端使用中（index.html 40 處、dashboard.html 36 處）
    - `device_model` 與 alarms 表的欄位名一致，新程式碼一律使用這個

    兩者永遠指向同一個值，在此處統一產生，不會有不同步的可能。
    轉換只能在這一個函式發生——app.py、路由層、前端、驗證腳本全部
    只看得到轉換後的結果，只有這裡知道 devices 表欄位實際叫 model。
    """
    model = row.get("model")
    return {
        "id":           row.get("id"),
        "model":        model,          # 既有前端讀這個
        "device_model": model,          # 新程式碼讀這個
        "category":     row.get("category"),
        "line":         row.get("line"),
        "department":   row.get("department"),
    }


def _device_payload_to_row(body: dict) -> dict:
    """寫入方向的對稱處理：body 兩個 key（model/device_model）都接受，
    內部統一轉成 model 欄位名（PLAN 3.1.1 節配套規則二）。"""
    model = (body.get("model") or body.get("device_model") or "").strip()
    return {
        "model":    model,
        "category": (body.get("category") or "").strip(),
        "line":     (body.get("line") or "").strip(),
    }


class SupabaseStore:
    def __init__(self, table: str, pk: str = "code", pk_fields: list = None,
                 is_devices: bool = False):
        self.table = table
        self.pk = pk
        self.pk_fields = pk_fields or [pk]
        self.is_devices = is_devices
        self._base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self._key = os.environ.get("SUPABASE_KEY", "")

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    def _req(self, method: str, path: str, body=None, extra_headers: Optional[dict] = None):
        url = f"{self._base}/rest/v1/{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data,
                                     headers=self._headers(extra_headers),
                                     method=method)
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else []

    def _paginated_get(self, qs: str, order_fields: list) -> list:
        """PostgREST 單次 GET 有列數上限（Supabase 預設 1000），任何可能掃到
        整張表的查詢都必須走這裡分頁，不能只發一次 GET（外部審查發現：save()
        的刪除掃描先前沒分頁，資料表超過 1000 筆時 replace 模式會安靜少刪）。

        order_fields 必須是完整的唯一鍵（pk_fields），不能只給單一非唯一欄位——
        並列（tie）時 ORDER BY 不保證跨多次請求（每次 offset 各自獨立查詢計畫）
        的相對順序一致，會導致漏列或重複列（第二輪外部審查發現：alarms 的主鍵
        是 (department, device_model, code) 三欄，只用 code 或 department 排序
        會有大量並列，1759 筆超過 1000 分頁門檻時即會觸發）。
        """
        page_size = 1000
        result = []
        offset = 0
        order_clause = ",".join(order_fields)
        full_qs = f"{qs}&order={order_clause}&limit={page_size}"
        while True:
            batch = self._req("GET", f"{self.table}?{full_qs}&offset={offset}")
            result.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return result

    def probe(self) -> None:
        """健康檢查專用：只確認連得到資料庫，不搬任何資料列（外部審查發現：
        /ping 原本呼叫 load(department=None)，department=None 代表不過濾，
        等於整張表 1759 筆都撈下來，Render cron 每 10 分鐘打一次，白白浪費
        兩次分頁 HTTP 往返）。用 Range: 0-0 只要拿到回應就代表連線正常，
        不判斷回傳筆數，出錯就讓例外往上拋（呼叫端 /ping 自行接住）。"""
        req = urllib.request.Request(
            f"{self._base}/rest/v1/{self.table}?select={self.pk_fields[0]}",
            headers=self._headers({"Range-Unit": "items", "Range": "0-0"}),
            method="GET",
        )
        with urllib.request.urlopen(req) as r:
            r.read()

    def load(self, department: Optional[str]) -> list:
        """department=None 是 DeptScope.ALL 的明確選擇（總管不過濾），
        不是「忘記傳」的預設值——呼叫端必須每次主動決定（PLAN 3.6 節）。"""
        qs = "select=*"
        if department is not None:
            qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
        result = self._paginated_get(qs, self.pk_fields)
        if self.is_devices:
            result = [_row_to_device(row) for row in result]
        return result

    def _row_key(self, row: dict) -> tuple:
        return tuple(str(row.get(f, "")) for f in self.pk_fields)

    def save(self, items: list, department: Optional[str], on_conflict: Optional[str] = None) -> None:
        """整批取代語意。department=None 是明確選擇（不限定部門範圍），不是預設值
        （PLAN 3.6 節）。若 department 有值，刪除掃描比對只在該部門範圍內進行
        （PLAN 3.1 節：避免存 A 部門資料時把 B 部門資料誤刪）。

        用於 devices_store 的批次匯入／管理頁全量儲存，以及 alarms 的批次匯入
        （第 6 節新工具）。單筆 CRUD 一律改走 upsert_one()/delete_one()。

        on_conflict：同 upsert_one() 的理由（PostgREST upsert 預設取主鍵，不會
        自動用新建的 unique index），未指定時 fallback 用 pk_fields 組成，跟
        upsert_one() 的呼叫端保持一致的簽名（外部審查發現：先前 save() 完全
        沒帶這個參數，devices 表主鍵是 id，會打錯約束）。
        """
        write_items = items
        if self.is_devices:
            write_items = [_device_payload_to_row(item) for item in items]
            for row, original in zip(write_items, items):
                if "id" in original:
                    row["id"] = original["id"]
            if department is not None:
                for row in write_items:
                    row["department"] = department

        # Step 1: upsert all items in the new list — never deletes, so safe if network drops
        if write_items:
            conflict_target = on_conflict or ",".join(self.pk_fields)
            self._req("POST", f"{self.table}?on_conflict={conflict_target}", write_items,
                      extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

        # Step 2: delete only rows whose PK is no longer in the list
        new_keys = {self._row_key(item) for item in write_items}
        select_fields = ",".join(self.pk_fields)
        scan_qs = f"select={select_fields}"
        if department is not None:
            scan_qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
        existing = self._paginated_get(scan_qs, self.pk_fields)
        to_delete = [row for row in existing if self._row_key(row) not in new_keys]
        for row in to_delete:
            qs = "&".join(f"{f}=eq.{urllib.parse.quote(str(row[f]), safe='')}" for f in self.pk_fields)
            if department is not None:
                qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
            self._req("DELETE", f"{self.table}?{qs}",
                      extra_headers={"Prefer": "return=minimal"})

    def upsert_one(self, item: dict, department: str, on_conflict: str) -> dict:
        """單筆 upsert，明確指定 on_conflict 目標（PLAN 3.1 節第四輪審查補強）。

        PostgREST 做 upsert 時衝突目標預設取主鍵，不會自動使用新建的 unique
        index；在中間部署狀態下會安靜打到錯誤約束，因此一律在 URL 明確帶上
        欄位組合，不使用 ON CONFLICT ON CONSTRAINT <名稱>（約束名稱可能變動）。
        """
        write_item = dict(item)
        if self.is_devices:
            row = _device_payload_to_row(item)
            if "id" in item:
                row["id"] = item["id"]
            write_item = row
        write_item["department"] = department
        path = f"{self.table}?on_conflict={on_conflict}"
        result = self._req("POST", path, [write_item],
                           extra_headers={"Prefer": "resolution=merge-duplicates,return=representation"})
        row = result[0] if result else write_item
        return _row_to_device(row) if self.is_devices else row

    def delete_one(self, department: str, match: dict) -> None:
        """單筆刪除，match 為 {欄位: 值} 的精確比對條件，一律含 department。"""
        qs_parts = [f"department=eq.{urllib.parse.quote(department, safe='')}"]
        for k, v in match.items():
            qs_parts.append(f"{k}=eq.{urllib.parse.quote(str(v), safe='')}")
        qs = "&".join(qs_parts)
        self._req("DELETE", f"{self.table}?{qs}", extra_headers={"Prefer": "return=minimal"})


def _use_supabase() -> bool:
    if os.environ.get("ALARM_DATA_DIR"):
        return False  # test isolation mode → always use JsonStore
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


class DepartmentStore:
    """部門帳號管理（PLAN 3.3、3.4 節）。本機/測試模式（非 Supabase）不提供
    真正的多部門功能，登入 fallback 交由 app.py 走 .env 明文比對。"""

    _TABLE = "departments"

    def _base_key(self):
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        return base, key

    def _req(self, method: str, path: str, body=None, extra_headers: Optional[dict] = None):
        base, key = self._base_key()
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{base}/rest/v1/{path}", data=data,
                                     headers=headers, method=method)
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else []

    def _count(self, table: str, filter_qs: str) -> int:
        """用 Prefer: count=exact + Range: 0-0 取得筆數，不搬移實際資料列
        （PLAN 5 節前端刪除確認流程用，表可能有上千筆，不該整批 GET 下來數）。

        select=department 而非 select=id：alarms 表沒有 id 欄位（複合主鍵是
        (department, device_model, code)），department 欄位則是這批表全部
        都有的共同欄位，避免每張表要記各自的主鍵長相。
        """
        base, key = self._base_key()
        req = urllib.request.Request(
            f"{base}/rest/v1/{table}?select=department&{filter_qs}",
            headers={
                "apikey": key, "Authorization": f"Bearer {key}",
                "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0",
            },
            method="GET",
        )
        with urllib.request.urlopen(req) as r:
            content_range = r.headers.get("Content-Range", "")
            # 格式："0-0/N"（零列時是 "*/0"，"/" in 判斷涵蓋得到）。
            # 拿不到筆數時必須拋出而不是回 0——這支方法唯一的呼叫端是刪除前
            # 確認畫面（外部審查發現：解析失敗回 0 會讓「將刪除 0 筆」這種
            # 看起來安全的訊息顯示出來，實際上資料還在，使用者會放心按下
            # 刪除鍵。拿不到影響範圍就不該讓人刪，寧可讓端點回 500。
            if "/" not in content_range:
                raise RuntimeError(f"{table} 筆數查詢未回傳 Content-Range")
            total = content_range.rsplit("/", 1)[-1]
            if not total.isdigit():
                raise RuntimeError(f"{table} 筆數格式異常：{content_range}")
            return int(total)

    _PUBLIC_FIELDS = "id,name,active,hidden,purgeable,session_version,created_at"

    def list(self, active_only: bool = False) -> list:
        """絕不回傳密碼雜湊（pw_hash/admin_pw_hash）。"""
        qs = f"select={self._PUBLIC_FIELDS}&order=id"
        if active_only:
            qs += "&active=eq.true"
        return self._req("GET", f"{self._TABLE}?{qs}")

    def list_public(self) -> list:
        """給 /api/departments/public 用：只回傳 active=true 且 hidden=false 的
        id/name（PLAN 4.7 節）。"""
        qs = "select=id,name&active=eq.true&hidden=eq.false&order=name"
        return self._req("GET", f"{self._TABLE}?{qs}")

    def get_by_id(self, dept_id: str) -> Optional[dict]:
        """回傳需含 session_version、active（assert_session_valid() 依賴這兩個
        欄位），以及 pw_hash/admin_pw_hash（登入比對用，內部呼叫端才會拿到）。"""
        qs = f"select=*&id=eq.{urllib.parse.quote(dept_id, safe='')}"
        rows = self._req("GET", f"{self._TABLE}?{qs}")
        return rows[0] if rows else None

    def create(self, dept_id: str, name: str, pw_hash: str, admin_pw_hash: str,
               hidden: bool = False, purgeable: bool = False) -> dict:
        body = {
            "id": dept_id, "name": name,
            "pw_hash": pw_hash, "admin_pw_hash": admin_pw_hash,
            "hidden": hidden, "purgeable": purgeable,
        }
        result = self._req("POST", self._TABLE, [body],
                           extra_headers={"Prefer": "return=representation"})
        return result[0] if result else body

    def update_name(self, dept_id: str, name: str) -> None:
        qs = f"id=eq.{urllib.parse.quote(dept_id, safe='')}"
        self._req("PATCH", f"{self._TABLE}?{qs}", {"name": name},
                  extra_headers={"Prefer": "return=minimal"})

    def update_password(self, dept_id: str, pw_hash: str = None,
                         admin_pw_hash: str = None) -> None:
        """連帶 session_version += 1，讓既有 session 失效（PLAN 2.1 節）。"""
        current = self.get_by_id(dept_id)
        if current is None:
            raise ValueError(f"部門不存在：{dept_id}")
        body = {"session_version": current["session_version"] + 1}
        if pw_hash is not None:
            body["pw_hash"] = pw_hash
        if admin_pw_hash is not None:
            body["admin_pw_hash"] = admin_pw_hash
        qs = f"id=eq.{urllib.parse.quote(dept_id, safe='')}"
        self._req("PATCH", f"{self._TABLE}?{qs}", body,
                  extra_headers={"Prefer": "return=minimal"})

    def set_active(self, dept_id: str, active: bool) -> None:
        qs = f"id=eq.{urllib.parse.quote(dept_id, safe='')}"
        self._req("PATCH", f"{self._TABLE}?{qs}", {"active": active},
                  extra_headers={"Prefer": "return=minimal"})

    def count_impact(self, dept_id: str) -> dict:
        """唯讀版的 purge() 統計，供刪除前確認用（PLAN 5 節前端刪除流程第一步，
        外部審查發現：設計稿的刪除流程依賴這個端點顯示「將刪除 N 台機種、
        M 筆警報」，先前不存在）。表清單與 purge() 保持一致，避免兩處各自
        維護一份、日後漏改其中一邊。用 count=exact + limit=0 取得筆數，
        不搬移實際資料列（部分表可能有上千筆）。"""
        counts = {}
        dept_qs = f"department=eq.{urllib.parse.quote(dept_id, safe='')}"
        for table in ("alarms", "ai_scans", "ai_corrections", "ai_logs",
                      "feedback", "alarm_views", "alarm_history", "devices"):
            counts[table] = self._count(table, dept_qs)
        return counts

    def purge(self, dept_id: str, confirm_id: str) -> dict:
        """硬刪除一個部門與其所有關聯資料。僅限 purgeable=true 的部門，
        正式部門一律用 set_active()（PLAN 3.4 節）。"""
        dept = self.get_by_id(dept_id)
        if dept is None or not dept.get("purgeable"):
            raise PermissionError("此部門不可硬刪除")
        if confirm_id != dept_id:
            raise ValueError("二次確認的部門 id 不相符")

        removed = {}
        dept_qs = f"department=eq.{urllib.parse.quote(dept_id, safe='')}"
        for table in ("alarms", "ai_scans", "ai_corrections", "ai_logs",
                      "feedback", "alarm_views", "alarm_history", "devices"):
            deleted = self._req("DELETE", f"{table}?{dept_qs}",
                                extra_headers={"Prefer": "return=representation"})
            removed[table] = len(deleted) if isinstance(deleted, list) else 0

        id_qs = f"id=eq.{urllib.parse.quote(dept_id, safe='')}"
        self._req("DELETE", f"{self._TABLE}?{id_qs}",
                  extra_headers={"Prefer": "return=minimal"})
        return removed


class AuditLogger:
    _MAX = 500

    def __init__(self):
        self._lock = Lock()

    def log(self, operation: str, department: Optional[str],
            new_data: dict = None, old_data: dict = None) -> None:
        entry = {
            "operation": operation,
            "code": (new_data or old_data or {}).get("code", ""),
            "old_data": old_data,
            "new_data": new_data if operation != "DELETE" else None,
            "changed_at": datetime.now(timezone.utc).isoformat(),
        }
        if department is not None:
            entry["department"] = department
        if _use_supabase():
            self._log_supabase(entry)
        else:
            self._log_json(entry)

    def load(self, limit: int, department: Optional[str]) -> list:
        # department=None 是明確選擇（DeptScope.ALL），呼叫端一律主動傳入
        # （PLAN 3.6 節）。JsonStore fallback（本機/測試）內部不使用這個值。
        if _use_supabase():
            return self._load_supabase(limit, department)
        return self._load_json(limit)

    # ── JSON backend ────────────────────────────────────────────────

    def _log_json(self, entry: dict) -> None:
        with self._lock:
            path = _data_dir() / "audit_log.json"
            logs = []
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    logs = json.load(f)
            logs.append(entry)
            logs = logs[-self._MAX:]
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
            tmp.replace(path)

    def _load_json(self, limit: int) -> list:
        path = _data_dir() / "audit_log.json"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            logs = json.load(f)
        return list(reversed(logs[-limit:]))

    # ── Supabase backend ────────────────────────────────────────────

    def _log_supabase(self, entry: dict) -> None:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            data = json.dumps(entry).encode()
            req = urllib.request.Request(
                f"{base}/rest/v1/alarm_history",
                data=data,
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                method="POST",
            )
            urllib.request.urlopen(req)
        except Exception:
            pass  # log failure must never crash the main operation

    def _load_supabase(self, limit: int, department: Optional[str] = None) -> list:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            qs = f"select=*&order=changed_at.desc&limit={limit}"
            if department is not None:
                qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
            req = urllib.request.Request(
                f"{base}/rest/v1/alarm_history?{qs}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []


class FeedbackStore:
    """Append-only store for user feedback entries."""

    def append(self, entry: dict, department: Optional[str]) -> None:
        if department is not None:
            entry = {**entry, "department": department}
        if _use_supabase():
            self._append_supabase(entry)
        else:
            self._append_json(entry)

    def load(self, department: Optional[str]) -> list:
        if _use_supabase():
            return self._load_supabase(department)
        return self._load_json()

    def stats(self, department: Optional[str]) -> list:
        records = self.load(department)
        stats: dict = {}
        for r in records:
            key = (r.get("code", ""), r.get("device_model", ""))
            if key not in stats:
                stats[key] = {"code": key[0], "device_model": key[1], "effective": 0, "total": 0}
            stats[key]["total"] += 1
            if r.get("result") == "effective":
                stats[key]["effective"] += 1
        return list(stats.values())

    def _append_json(self, entry: dict) -> None:
        path = _data_dir() / "feedback.json"
        with Lock():
            records = []
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    records = json.load(f)
            records.append(entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            tmp.replace(path)

    def _load_json(self) -> list:
        path = _data_dir() / "feedback.json"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _append_supabase(self, entry: dict) -> None:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            data = json.dumps(entry).encode()
            req = urllib.request.Request(
                f"{base}/rest/v1/feedback",
                data=data,
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                method="POST",
            )
            urllib.request.urlopen(req)
        except Exception:
            pass

    def _load_supabase(self, department: Optional[str] = None) -> list:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            qs = "select=*&order=created_at.desc&limit=5000"
            if department is not None:
                qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
            req = urllib.request.Request(
                f"{base}/rest/v1/feedback?{qs}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []


class ViewStore:
    """Append-only store for alarm view events."""

    def append(self, entry: dict, department: Optional[str]) -> None:
        if department is not None:
            entry = {**entry, "department": department}
        if _use_supabase():
            self._append_supabase(entry)
        else:
            self._append_json(entry)

    def load(self, department: Optional[str]) -> list:
        if _use_supabase():
            return self._load_supabase(department)
        return self._load_json()

    def top(self, department: Optional[str], limit: int = 10) -> list:
        records = self.load(department)
        counts: dict = {}
        for r in records:
            key = (r.get("code", ""), r.get("device_model", ""))
            counts[key] = counts.get(key, 0) + 1
        result = [{"code": k[0], "device_model": k[1], "count": v}
                  for k, v in sorted(counts.items(), key=lambda x: -x[1])]
        return result[:limit]

    def stats(self, department: Optional[str]) -> list:
        return self.top(department, limit=len(self.load(department)) or 1)

    def _append_json(self, entry: dict) -> None:
        path = _data_dir() / "views.json"
        with Lock():
            records = []
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    records = json.load(f)
            records.append(entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            tmp.replace(path)

    def _load_json(self) -> list:
        path = _data_dir() / "views.json"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _append_supabase(self, entry: dict) -> None:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            data = json.dumps(entry).encode()
            req = urllib.request.Request(
                f"{base}/rest/v1/alarm_views",
                data=data,
                headers={"apikey": key, "Authorization": f"Bearer {key}",
                         "Content-Type": "application/json", "Prefer": "return=minimal"},
                method="POST",
            )
            urllib.request.urlopen(req)
        except Exception:
            pass

    def _load_supabase(self, department: Optional[str] = None) -> list:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            qs = "select=device_model,code&limit=50000"
            if department is not None:
                qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
            req = urllib.request.Request(
                f"{base}/rest/v1/alarm_views?{qs}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []


class AiScanStore:
    """Read-only access to ai_scans / ai_corrections for dashboard stats."""

    def _get(self, table: str, query: str) -> list:
        if not _use_supabase():
            return []
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            req = urllib.request.Request(
                f"{base}/rest/v1/{table}?{query}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []

    def _dept_qs(self, department: Optional[str]) -> str:
        if department is None:
            return ""
        return f"&department=eq.{urllib.parse.quote(department, safe='')}"

    def load_scans(self, department: Optional[str], limit: int = 5000) -> list:
        return self._get("ai_scans",
                         f"select=*&order=created_at.desc&limit={limit}{self._dept_qs(department)}")

    def load_corrections(self, department: Optional[str], limit: int = 5000) -> list:
        return self._get("ai_corrections",
                         f"select=*&order=created_at.desc&limit={limit}{self._dept_qs(department)}")

    def load_logs(self, department: Optional[str], limit: int = 5000) -> list:
        return self._get("ai_logs",
                         f"select=*&order=created_at.desc&limit={limit}{self._dept_qs(department)}")

    def cleanup_expired(self, retention_days: dict) -> dict:
        """全庫清理，只有總管能按（PLAN 4.4 節：cleanup-expired 改
        superadmin_required，不依部門過濾，語意不變）。"""
        if not _use_supabase():
            return {}
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        now = datetime.now(timezone.utc)
        removed = {}
        for tier, days in retention_days.items():
            cutoff = (now - timedelta(days=days)).isoformat()
            try:
                qs = f"tier=eq.{urllib.parse.quote(tier)}&created_at=lt.{urllib.parse.quote(cutoff)}"
                req = urllib.request.Request(
                    f"{base}/rest/v1/ai_scans?{qs}",
                    headers={"apikey": key, "Authorization": f"Bearer {key}",
                             "Prefer": "return=representation"},
                    method="DELETE",
                )
                with urllib.request.urlopen(req) as r:
                    deleted = json.loads(r.read().decode())
                    removed[tier] = len(deleted)
            except Exception:
                removed[tier] = -1
        return removed


class LoginAttemptStore:
    """login_attempts 表的讀寫（PLAN 2.2.1~2.2.4 節）。以資料庫為唯一真實
    來源，不使用行程內狀態——多 worker/重啟/擴容下退避次數才不會被稀釋。

    本機/測試模式（非 Supabase）不記錄，節流形同不啟用（與正式環境的
    JsonStore 單租戶定位一致，PLAN 3.2 節）。
    """

    _TABLE = "login_attempts"

    def _base_key(self):
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        return base, key

    def _req(self, method: str, path: str, body=None, extra_headers: Optional[dict] = None):
        base, key = self._base_key()
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{base}/rest/v1/{path}", data=data,
                                     headers=headers, method=method)
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else []

    def record(self, ip: str, department: Optional[str], success: bool) -> None:
        """2.2.4 節第 2、3 情況：格式合法但部門不存在時仍要記錄；已在節流窗口
        內的請求由呼叫端在呼叫這個方法之前就攔截掉，不會走到這裡。"""
        if not _use_supabase():
            return
        try:
            self._req("POST", self._TABLE,
                      {"ip": ip, "department": department, "success": success},
                      extra_headers={"Prefer": "return=minimal"})
        except Exception:
            pass  # 節流記錄失敗不可影響登入主流程

    def _count_since_last_success(self, ip: str, department: Optional[str] = None,
                                   scope_by_department: bool = True) -> tuple:
        """該 (ip[, department]) 組合在最近一次成功登入之後、15 分鐘窗口內的
        連續失敗次數（PLAN 2.2.1/2.2.3 節的 N 定義），以及最後一次失敗的時間。

        回傳 (N, last_failure_at)：delay 是相對 last_failure_at 算的倒數計時，
        不是「只要 N>=1 就永久節流」——過了 2**N 秒窗口，同一個 N 就不再節流，
        直到下一次失敗才會再次觸發（且 N 會遞增）。
        """
        if not _use_supabase():
            return (0, None)
        window_start = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        dept_qs = f"&department=eq.{urllib.parse.quote(department, safe='')}" if (scope_by_department and department is not None) else ""
        try:
            success_rows = self._req(
                "GET",
                f"{self._TABLE}?select=attempted_at&ip=eq.{urllib.parse.quote(ip, safe='')}{dept_qs}"
                f"&success=eq.true&order=attempted_at.desc&limit=1",
            )
            since = success_rows[0]["attempted_at"] if success_rows else None
            lower_bound = max(since, window_start) if since else window_start
            count_rows = self._req(
                "GET",
                f"{self._TABLE}?select=attempted_at&ip=eq.{urllib.parse.quote(ip, safe='')}{dept_qs}"
                f"&success=eq.false&attempted_at=gt.{urllib.parse.quote(lower_bound, safe='')}"
                f"&order=attempted_at.desc",
            )
            n = len(count_rows)
            last_failure_at = count_rows[0]["attempted_at"] if count_rows else None
            return (n, last_failure_at)
        except Exception:
            return (0, None)

    def count_fine(self, ip: str, department: str) -> tuple:
        """細網：(ip, department) 組合的連續失敗數 N_ip_dept 與最後失敗時間。"""
        return self._count_since_last_success(ip, department, scope_by_department=True)

    def count_coarse(self, ip: str) -> tuple:
        """粗網：只看 ip 的連續失敗數 N_ip 與最後失敗時間（PLAN 2.2.3 節，防止換部門繞過細網）。"""
        return self._count_since_last_success(ip, None, scope_by_department=False)

    def cleanup_expired(self, days: int = 90) -> int:
        """PLAN 2.2.1 節：併入 cleanup-expired 端點，90 天保留期。"""
        if not _use_supabase():
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            deleted = self._req(
                "DELETE",
                f"{self._TABLE}?attempted_at=lt.{urllib.parse.quote(cutoff, safe='')}",
                extra_headers={"Prefer": "return=representation"},
            )
            return len(deleted) if isinstance(deleted, list) else 0
        except Exception:
            return -1


if _use_supabase():
    alarms_store = SupabaseStore("alarms", pk="code", pk_fields=["department", "device_model", "code"])
    devices_store = SupabaseStore("devices", pk="id", pk_fields=["department", "model"], is_devices=True)
else:
    alarms_store = JsonStore("alarms.json")
    devices_store = JsonStore("devices.json", is_devices=True)

department_store = DepartmentStore()
login_attempt_store = LoginAttemptStore()
feedback_store = FeedbackStore()
view_store = ViewStore()
audit_logger = AuditLogger()
ai_scan_store = AiScanStore()
