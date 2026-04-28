import json
import os
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional


def _data_dir() -> Path:
    env = os.environ.get("ALARM_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data"


class JsonStore:
    def __init__(self, filename: str):
        self.filename = filename
        self._lock = Lock()

    @property
    def path(self) -> Path:
        return _data_dir() / self.filename

    def load(self) -> list:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, items: list) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)


class SupabaseStore:
    def __init__(self, table: str, pk: str = "code", pk_fields: list = None):
        self.table = table
        self.pk = pk
        self.pk_fields = pk_fields or [pk]
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

    def load(self) -> list:
        page_size = 1000
        result = []
        offset = 0
        while True:
            batch = self._req("GET", f"{self.table}?select=*&order={self.pk}&limit={page_size}&offset={offset}")
            result.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return result

    def _row_key(self, row: dict) -> tuple:
        return tuple(str(row.get(f, "")) for f in self.pk_fields)

    def save(self, items: list) -> None:
        # Step 1: upsert all items in the new list — never deletes, so safe if network drops
        if items:
            self._req("POST", self.table, items,
                      extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

        # Step 2: delete only rows whose PK is no longer in the list
        new_keys = {self._row_key(item) for item in items}
        select_fields = ",".join(self.pk_fields)
        existing = self._req("GET", f"{self.table}?select={select_fields}")
        to_delete = [row for row in existing if self._row_key(row) not in new_keys]
        for row in to_delete:
            qs = "&".join(f"{f}=eq.{urllib.parse.quote(str(row[f]), safe='')}" for f in self.pk_fields)
            self._req("DELETE", f"{self.table}?{qs}",
                      extra_headers={"Prefer": "return=minimal"})


def _use_supabase() -> bool:
    if os.environ.get("ALARM_DATA_DIR"):
        return False  # test isolation mode → always use JsonStore
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


class AuditLogger:
    _MAX = 500

    def __init__(self):
        self._lock = Lock()

    def log(self, operation: str, new_data: dict = None, old_data: dict = None) -> None:
        entry = {
            "operation": operation,
            "code": (new_data or old_data or {}).get("code", ""),
            "old_data": old_data,
            "new_data": new_data if operation != "DELETE" else None,
            "changed_at": datetime.now(timezone.utc).isoformat(),
        }
        if _use_supabase():
            self._log_supabase(entry)
        else:
            self._log_json(entry)

    def load(self, limit: int = 100) -> list:
        if _use_supabase():
            return self._load_supabase(limit)
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

    def _load_supabase(self, limit: int) -> list:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            req = urllib.request.Request(
                f"{base}/rest/v1/alarm_history?select=*&order=changed_at.desc&limit={limit}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []


class FeedbackStore:
    """Append-only store for user feedback entries."""

    def append(self, entry: dict) -> None:
        if _use_supabase():
            self._append_supabase(entry)
        else:
            self._append_json(entry)

    def load(self) -> list:
        if _use_supabase():
            return self._load_supabase()
        return self._load_json()

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

    def _load_supabase(self) -> list:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            req = urllib.request.Request(
                f"{base}/rest/v1/feedback?select=*&order=created_at.desc&limit=5000",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []


class ViewStore:
    """Append-only store for alarm view events."""

    def append(self, entry: dict) -> None:
        if _use_supabase():
            self._append_supabase(entry)
        else:
            self._append_json(entry)

    def load(self) -> list:
        if _use_supabase():
            return self._load_supabase()
        return self._load_json()

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

    def _load_supabase(self) -> list:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            req = urllib.request.Request(
                f"{base}/rest/v1/alarm_views?select=device_model,code&limit=50000",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []


if _use_supabase():
    alarms_store = SupabaseStore("alarms", pk="code", pk_fields=["device_model", "code"])
    devices_store = SupabaseStore("devices", pk="id")
else:
    alarms_store = JsonStore("alarms.json")
    devices_store = JsonStore("devices.json")

feedback_store = FeedbackStore()
view_store = ViewStore()
audit_logger = AuditLogger()
