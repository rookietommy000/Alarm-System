import json
import os
import urllib.parse
import urllib.request
import urllib.error
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
    def __init__(self, table: str, pk: str = "code"):
        self.table = table
        self.pk = pk
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
        return self._req("GET", f"{self.table}?select=*&order={self.pk}")

    def save(self, items: list) -> None:
        # Step 1: upsert all items in the new list — never deletes, so safe if network drops
        if items:
            self._req("POST", self.table, items,
                      extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

        # Step 2: delete only rows whose PK is no longer in the list
        new_pks = {str(item[self.pk]) for item in items}
        existing = self._req("GET", f"{self.table}?select={self.pk}")
        to_delete = [row[self.pk] for row in existing if str(row[self.pk]) not in new_pks]
        if to_delete:
            encoded = ",".join(urllib.parse.quote(str(pk), safe="") for pk in to_delete)
            self._req("DELETE",
                      f"{self.table}?{self.pk}=in.({encoded})",
                      extra_headers={"Prefer": "return=minimal"})


def _use_supabase() -> bool:
    if os.environ.get("ALARM_DATA_DIR"):
        return False  # test isolation mode → always use JsonStore
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


if _use_supabase():
    alarms_store = SupabaseStore("alarms", pk="code")
    devices_store = SupabaseStore("devices", pk="id")
else:
    alarms_store = JsonStore("alarms.json")
    devices_store = JsonStore("devices.json")
