import json
import os
import time
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
        # variant 加入 alarms 主鍵後這裡要跟著加，否則本機/測試模式下
        # 同一 code 的多個變體會被判成「同一筆」互相覆蓋（PLAN_variant，
        # 這個坑先在 SupabaseStore.pk_fields 補過一次，JsonStore 這邊
        # 是獨立的判斷邏輯，容易漏改——實測發現匯入 114 筆多變體資料
        # 被壓縮成 28 筆，就是這裡漏掉 variant 導致）。
        return ["model"] if self.is_devices else ["device_model", "code", "variant"]

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

    def get_one(self, department: Optional[str] = None, match: dict = None) -> Optional[dict]:
        """介面對齊 SupabaseStore.get_one()。JsonStore 沒有分頁成本問題，
        單純 load() 後線性找一筆即可，不需要另外的查詢路徑。

        row.get(k, "") 而非 row.get(k)：既有測試種子資料（PLAN_variant
        前建立）沒有 variant 欄位，讀出來是 None，但呼叫端傳入比對的
        variant 值是正規化過的空字串——None != ""，會讓既有機種（不帶
        variant 概念）在本機/測試模式下查不到自己。Supabase 端不會有
        這個落差（DDL 已將 variant 設為 not null default ''）。"""
        match = match or {}
        for row in self.load(department):
            if all(row.get(k, "") == v for k, v in match.items()):
                return row
        return None

    def find_by_code(self, department: Optional[str], device_model: str, code: str) -> list:
        """不要求完整主鍵（不知道 variant）——用於 AI 辨識後拿正規化過的
        code 反查 DB 裡實際存在哪些 variant，可能 0/1/多筆，跟 get_one()
        要求精確定位單一筆是不同的用途（拍照辨識故障修復：AI 辨識完全
        不知道 variant 概念，只能靠這個方法把猜到的 code 對應回 DB
        實際存在的紀錄）。直接複用 load() 後過濾，不另開查詢路徑。"""
        return [row for row in self.load(department)
                if row.get("device_model") == device_model and row.get("code") == code]

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
                # row.get(f, "")：見 get_one() 同樣的說明（既有列可能沒有
                # variant 欄位，讀出 None，跟新寫入值的空字串比對要視為相等）。
                if all(row.get(f, "") == write_item.get(f, "") for f in fields):
                    # 部分合併，不是整列取代（外部審查發現：批次匯入的
                    # OPTIONAL_FIELDS 保護機制——見 alarm_ingest/commit.py
                    # 的 _to_payload()——只在 SupabaseStore 成立，因為
                    # PostgREST 的 merge-duplicates upsert 對缺席欄位是
                    # 保留舊值；這裡若直接 raw[i] = write_item，payload
                    # 缺的欄位會從這一列完全消失，不是保留舊值，讓本機/
                    # 測試環境跟正式環境行為分岔）。write_item 本身若已
                    # 含完整欄位（既有呼叫端 create_alarm/update_alarm
                    # 皆如此），{**row, **write_item} 等同整列取代，
                    # 行為不變。
                    raw[i] = {**row, **write_item}
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
            # row.get(k, "")：見 get_one() 同樣的說明（既有種子資料沒有
            # variant 欄位時讀出 None，跟正規化後的空字串比對不相等）。
            remaining = [row for row in raw if not all(row.get(k, "") == v for k, v in match.items())]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(remaining, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)

    def patch_one(self, department: Optional[str] = None, match: dict = None,
                  patch: dict = None) -> Optional[dict]:
        match = match or {}
        patch = patch or {}
        with self._lock:
            raw = []
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
            updated = None
            for row in raw:
                # row.get(k, "")：見 get_one() 同樣的說明。
                if all(row.get(k, "") == v for k, v in match.items()):
                    row.update(patch)
                    updated = row
                    break
            if updated is None:
                return None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)
        return updated


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
    # load() 快取 TTL（秒）。只在建構時傳入 cache_ttl > 0 才啟用——目前只有
    # alarms_store 開啟（PLAN 效能優化第 4 項：/api/alarms 全表掃描+分頁，
    # mf4d 部門 1759 筆實測約 1.6 秒）。devices/其他表資料量小、變動頻率
    # 不同，維持預設不快取，不用因為這次改動被迫承擔額外的快取失效風險。
    def __init__(self, table: str, pk: str = "code", pk_fields: list = None,
                 is_devices: bool = False, cache_ttl: int = 0):
        self.table = table
        self.pk = pk
        self.pk_fields = pk_fields or [pk]
        self.is_devices = is_devices
        self._base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self._key = os.environ.get("SUPABASE_KEY", "")
        self._cache_ttl = cache_ttl
        self._cache_lock = Lock()
        self._cache: dict = {}  # department（None 正規化成 "__all__"）-> (expires_at, items)

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
        不是「忘記傳」的預設值——呼叫端必須每次主動決定（PLAN 3.6 節）。

        快取只在 department 有值時生效（cache_ttl > 0 才啟用，見
        __init__ 說明）——department=None 代表總管跨部門查詢，範圍不
        固定、快取鍵不該用一個特殊值（例如 "__all__"）去代表「沒有
        邊界的查詢」，那會讓快取鍵設計本身承擔部門隔離風險，直接跳過
        快取最單純：總管本來就是少數、低頻的操作，不快取不影響一般
        使用者體感的效能改善（PLAN 效能優化第 4 項驗收條件 a：快取鍵
        必須包含 department，這是安全邊界層級問題，不是效能小事）。
        """
        if self._cache_ttl > 0 and department is not None:
            now = time.time()
            with self._cache_lock:
                cached = self._cache.get(department)
                if cached is not None and cached[0] > now:
                    return cached[1]
            result = self._load_uncached(department)
            with self._cache_lock:
                self._cache[department] = (now + self._cache_ttl, result)
            return result
        return self._load_uncached(department)

    def _load_uncached(self, department: Optional[str]) -> list:
        qs = "select=*"
        if department is not None:
            qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
        result = self._paginated_get(qs, self.pk_fields)
        if self.is_devices:
            result = [_row_to_device(row) for row in result]
        return result

    def _invalidate_cache(self, department: Optional[str]) -> None:
        """寫入方法（upsert_one/delete_one/patch_one/save）內部呼叫，
        寫完該部門的資料後讓對應的快取項目失效，下一次 load() 會重新
        查詢。內建在 store 層而非要求呼叫端各自記得補——目前 alarms_store
        的寫入呼叫點有 9 處（app.py 6 處、alarm_ingest/commit.py 3 處），
        不能要求 9 處各自記得加（PLAN 效能優化第 4 項驗收條件 c）。

        department=None 時清空整個快取（沒有精確的部門邊界可失效，例如
        save() 在 department=None 時是全表級操作，寧可保守清空也不要
        漏掉該失效卻沒失效、讓使用者看到過期資料）。"""
        if self._cache_ttl <= 0:
            return
        with self._cache_lock:
            if department is None:
                self._cache.clear()
            else:
                self._cache.pop(department, None)

    def _require_full_pk_match(self, match: dict) -> None:
        """match 必須包含除 department 外的完整主鍵欄位，否則 PostgREST
        在條件不足時會回多列，get_one()/patch_one()/delete_one() 目前的
        寫法是靜默取第一筆／全部命中中的一筆——順序不保證，看起來完全
        正常但其實是任意一筆。variant 加入主鍵後這個風險是真實的：呼叫
        端若忘記帶 variant，在多變體機種上會打到不確定是哪一筆。

        漏帶時直接 ValueError（開發階段就炸），不是讓查詢默默用不完整
        的條件跑下去——跟 department 參數必填、漏傳就報錯是同一個原則
        （那次抓到了 /ping 漏傳 department 的問題）。這層檢查自動涵蓋
        未來新增的所有呼叫點，不需要另外維護一份端點清單。"""
        missing = set(self.pk_fields) - {"department"} - set(match)
        if missing:
            raise ValueError(
                f"{self.table} 的 match 缺少主鍵欄位：{sorted(missing)}。"
                f"條件不足會取到任意一列，不允許執行。"
            )

    def get_one(self, department: str, match: dict) -> Optional[dict]:
        """單筆精確查詢，不分頁、不撈整個部門（外部審查第四輪發現：
        alarms 有 1759 筆，load() 兩次 HTTP 往返只為了取一列做稽核用的
        old_data 或存在性檢查，隨部門警報數線性變慢）。match 為精確比對
        條件，同 delete_one()/patch_one() 的定位方式，必須含完整主鍵。"""
        self._require_full_pk_match(match)
        qs_parts = [f"select=*", f"department=eq.{urllib.parse.quote(department, safe='')}"]
        for k, v in match.items():
            qs_parts.append(f"{k}=eq.{urllib.parse.quote(str(v), safe='')}")
        result = self._req("GET", f"{self.table}?{'&'.join(qs_parts)}&limit=1")
        if not result:
            return None
        return _row_to_device(result[0]) if self.is_devices else result[0]

    def find_by_code(self, department: str, device_model: str, code: str) -> list:
        """不要求完整主鍵（不知道 variant）——用於 AI 辨識後拿正規化過的
        code 反查 DB 裡實際存在哪些 variant，可能 0/1/多筆，跟 get_one()
        要求精確定位單一筆是不同的用途（拍照辨識故障修復：AI 辨識完全
        不知道 variant 概念，只能靠這個方法把猜到的 code 對應回 DB
        實際存在的紀錄）。用目標查詢而非 load()：同 get_one() 的理由，
        alarms 有 1759 筆，不該為了找同一個 code 底下最多幾筆 variant
        就整個部門撈下來。variant 欄位本身數量少（同一 code 頂多幾個
        variant），不需要分頁。"""
        qs_parts = [
            "select=*",
            f"department=eq.{urllib.parse.quote(department, safe='')}",
            f"device_model=eq.{urllib.parse.quote(device_model, safe='')}",
            f"code=eq.{urllib.parse.quote(code, safe='')}",
        ]
        return self._req("GET", f"{self.table}?{'&'.join(qs_parts)}")

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
        self._invalidate_cache(department)

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
        self._invalidate_cache(department)
        return _row_to_device(row) if self.is_devices else row

    def delete_one(self, department: str, match: dict) -> None:
        """單筆刪除，match 為 {欄位: 值} 的精確比對條件，一律含 department，
        且必須含完整主鍵（見 _require_full_pk_match）。"""
        self._require_full_pk_match(match)
        qs_parts = [f"department=eq.{urllib.parse.quote(department, safe='')}"]
        for k, v in match.items():
            qs_parts.append(f"{k}=eq.{urllib.parse.quote(str(v), safe='')}")
        qs = "&".join(qs_parts)
        self._req("DELETE", f"{self.table}?{qs}", extra_headers={"Prefer": "return=minimal"})
        self._invalidate_cache(department)

    def patch_one(self, department: str, match: dict, patch: dict) -> Optional[dict]:
        """單筆部分更新（PLAN_local_solution.md 4.3 節：只改呼叫端明確給的
        欄位，其餘欄位不動）。match 為精確比對條件、一律含 department，
        且必須含完整主鍵（見 _require_full_pk_match），跟 delete_one()
        同樣的定位方式；不用 upsert_one()，因為那是整筆覆蓋語意，這裡
        只想動 patch 裡列出的欄位，不想動到其他欄位（也不需要呼叫端
        先讀出整筆再合併）。找不到符合 match 的列時回傳 None。"""
        self._require_full_pk_match(match)
        qs_parts = [f"department=eq.{urllib.parse.quote(department, safe='')}"]
        for k, v in match.items():
            qs_parts.append(f"{k}=eq.{urllib.parse.quote(str(v), safe='')}")
        qs = "&".join(qs_parts)
        result = self._req("PATCH", f"{self.table}?{qs}", patch,
                           extra_headers={"Prefer": "return=representation"})
        self._invalidate_cache(department)
        return result[0] if result else None


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

    def purge(self, dept_id: str, confirm_id: str, acknowledge_counts: dict) -> dict:
        """硬刪除一個部門與其所有關聯資料。僅限 purgeable=true 的部門，
        正式部門一律用 set_active()（PLAN 3.4 節）。

        acknowledge_counts：呼叫端（前端）在使用者確認畫面上顯示過、
        使用者已經看過並按下確認的筆數快照，通常來自稍早呼叫
        count_impact() 的回應原封不動帶回來。這裡拿到之後**不當真值
        使用**，而是重新呼叫 self.count_impact(dept_id) 現場算出
        actual，兩者逐表比對——如果使用者確認畫面顯示的筆數，跟現在
        真正要刪除之前重新算出來的筆數對不上，代表確認之後、真正動手
        刪除之前這段時間資料又變動了（例如另一個人在同時匯入資料），
        這正是最該停下來、不能悶著頭刪的時刻，不能假設「使用者按過確認
        了就代表可以刪」——按確認當下看到的數字可能早就不是事實。
        不符時 raise ValueError 附上兩邊的實際筆數，讓呼叫端能顯示
        清楚的重新確認訊息，不是含糊的「數量不符」。"""
        dept = self.get_by_id(dept_id)
        if dept is None or not dept.get("purgeable"):
            raise PermissionError("此部門不可硬刪除")
        if confirm_id != dept_id:
            raise ValueError("二次確認的部門 id 不相符")

        actual = self.count_impact(dept_id)
        if actual != acknowledge_counts:
            mismatched = {
                table: {"confirmed": acknowledge_counts.get(table), "actual": actual[table]}
                for table in actual
                if acknowledge_counts.get(table) != actual[table]
            }
            raise ValueError(
                f"確認的筆數與目前實際資料不符（確認後資料已變動），請重新確認：{mismatched}"
            )

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

    def load(self, limit: int, department: Optional[str], device_model: Optional[str] = None,
              from_dt: Optional[str] = None, to_dt: Optional[str] = None) -> tuple:
        # department=None 是明確選擇（DeptScope.ALL），呼叫端一律主動傳入
        # （PLAN 3.6 節）。JsonStore fallback（本機/測試）內部不使用這個值，
        # device_model/from_dt/to_dt 同理——本機模式資料量小，不需要篩選。
        #
        # 多要一筆（limit+1）來偵測「是否被截斷」，不用額外的 count 查詢：
        # 若拿回的筆數超過呼叫端要的 limit，代表資料庫裡還有更多、被截斷了
        # （見 /api/audit 的 truncated 回傳，避免使用者誤以為某段時間沒有
        # 異動——實際上只是被截斷沒撈到，這類「看似合理的空結果」正是這個
        # 系統反覆在防的問題）。
        if _use_supabase():
            rows = self._load_supabase(limit + 1, department, device_model, from_dt, to_dt)
        else:
            rows = self._load_json(limit + 1)
        truncated = len(rows) > limit
        return rows[:limit], truncated

    def list_for_alarm(self, department: str, device_model: str, code: str,
                        operation: str, limit: int, variant: str = "") -> list:
        """單筆警報的變更紀錄，department 為必填（PLAN 3.6 節：跨部門查詢
        一律要求呼叫端主動決定範圍，不提供「不過濾」的隱式預設）。

        只回傳指定 operation（現場處置做法場景下固定傳 local_update）——
        一般使用者不需要看到批次匯入、機種變更這類技術性軌跡，那些留在
        後台的 /api/audit（PLAN_local_solution.md 3.2 節）。

        variant 必須一併過濾（PLAN_variant）：code 不再是機種底下唯一的
        識別，同一 code 可能有多個 variant，不過濾會把不同變體的變更
        歷史混在一起顯示，讓使用者以為某段內容是「這個變體」的異動，
        實際上是另一個變體的。"""
        if not _use_supabase():
            return []
        return self._load_supabase_for_alarm(department, device_model, code, operation, limit, variant)

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

    def _load_supabase(self, limit: int, department: Optional[str] = None,
                        device_model: Optional[str] = None,
                        from_dt: Optional[str] = None, to_dt: Optional[str] = None) -> list:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            qs = f"select=*&order=changed_at.desc&limit={limit}"
            if department is not None:
                qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
            if device_model:
                # DELETE 只有 old_data（new_data 為 null），但這裡跟
                # list_for_alarm() 同樣的判斷：一般 CREATE/UPDATE/
                # local_update 用 new_data 過濾即可，機種本身不會被
                # local_update 改動；DELETE 記錄用 new_data 過濾會
                # 因為 new_data 是 null 而篩不到，這是可接受的取捨
                # ——被刪除的警報本來就不會出現在「當前機種」的清單裡。
                qs += f"&new_data->>device_model=eq.{urllib.parse.quote(device_model, safe='')}"
            if from_dt:
                qs += f"&changed_at=gte.{urllib.parse.quote(from_dt, safe='')}"
            if to_dt:
                qs += f"&changed_at=lte.{urllib.parse.quote(to_dt, safe='')}"
            req = urllib.request.Request(
                f"{base}/rest/v1/alarm_history?{qs}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []

    def _load_supabase_for_alarm(self, department: str, device_model: str, code: str,
                                  operation: str, limit: int, variant: str = "") -> list:
        """alarm_history 沒有獨立的 device_model/variant 欄位（只有 code/
        department/operation 是一般欄位），兩者都存在 new_data/old_data
        這兩個 JSON 欄位裡。用 PostgREST 的 JSON 路徑運算子 ->> 過濾
        new_data->>device_model 與 new_data->>variant（local_update 這個
        operation 只改 local_solution/local_reason，不會改 device_model/
        variant，用 new_data 過濾足夠，不需要同時比對 old_data）。"""
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            qs = (
                "select=*"
                f"&department=eq.{urllib.parse.quote(department, safe='')}"
                f"&code=eq.{urllib.parse.quote(code, safe='')}"
                f"&operation=eq.{urllib.parse.quote(operation, safe='')}"
                f"&new_data->>device_model=eq.{urllib.parse.quote(device_model, safe='')}"
                f"&new_data->>variant=eq.{urllib.parse.quote(variant, safe='')}"
                f"&order=changed_at.desc&limit={limit}"
            )
            req = urllib.request.Request(
                f"{base}/rest/v1/alarm_history?{qs}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []


class ImportSnapshotStore:
    """批次匯入的整批復原機制（規劃第 5 階段，跟 AI 無關的保底機制）。

    commit_rows() 是逐筆 upsert，對已存在的列是 merge（覆蓋舊值），
    不是新增——真正的復原必須記下「寫入前的值」，不能只記「這次新增
    了哪些 code」（否則覆蓋掉的舊值就回不來了）。這裡的策略是 commit
    每一筆之前先用 alarms_store.get_one() 讀出當前值（不存在則為
    None）存進快照，undo 時逐筆回寫該值或刪除。

    只在 Supabase 模式運作（JsonStore fallback 沒有 import_snapshots
    這張表，本機/測試模式不提供復原——批次匯入本身在 JsonStore 模式
    下影響範圍小，直接改資料檔即可）。JsonStore fallback 下 save_snapshot
    回傳 None、list_snapshots/undo 回空結果，呼叫端據此判斷不可用。
    """

    def is_available(self) -> bool:
        """探測 import_snapshots 表是否真的存在（migration 007 是否已在
        正式環境執行）。save_snapshot() 本身是 fail-open 設計——表不存在
        時 POST 失敗會被吞掉、印一行 stderr、回傳 None，呼叫端若沒有
        主動檢查這個 None，會在完全沒有復原保護的情況下繼續往下寫入
        正式表（語意審核「採用並寫入」曾經就是這樣，見 update_semantic_
        review() 的防呆修復）。這裡用 limit=0 輕量 GET 探測，不寫入
        任何資料；HTTPError 明確判斷是不是 404（表不存在）而不是網路
        層問題，兩者不該混為一談——404 代表 migration 007 還沒執行，
        其他錯誤（逾時、憑證問題）不代表表不存在，但保守起見一律視為
        不可用，因為呼叫端要用這個結果決定要不要開放寫入正式表的高風險
        操作，寧可誤判成「不可用」擋下操作，也不要誤判成「可用」放行
        一個實際上救不回來的寫入。"""
        if not _use_supabase():
            return False
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        try:
            req = urllib.request.Request(
                f"{base}/rest/v1/import_snapshots?select=id&limit=0",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            urllib.request.urlopen(req)
            return True
        except Exception as e:
            # fail-closed 方向不變（表不存在/查詢失敗一律視為不可用，
            # 寧可誤擋高風險寫入），但「表真的不存在」跟「網路暫時故障/
            # 憑證問題」是完全不同的故障，混在一起會讓之後除錯難以
            # 分辨是哪一種——留一行痕跡（CLAUDE.md 例外處理判準）。
            import sys as _sys
            print(f"[ImportSnapshotStore] is_available() 探測失敗（視為不可用，"
                  f"擋下高風險寫入）：{type(e).__name__}: {e}", file=_sys.stderr)
            return False

    def save_snapshot(self, department: str, device_models: list, rows_before: list,
                       total_rows: int, import_mode: str) -> Optional[int]:
        """rows_before：[{"device_model", "code", "variant", "before_data"}, ...]，
        before_data 為 None 代表這筆在 commit 前不存在。回傳新建快照的 id，
        JsonStore fallback 下回傳 None（不支援）。"""
        if not _use_supabase():
            return None
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            snap_req = urllib.request.Request(
                f"{base}/rest/v1/import_snapshots",
                data=json.dumps({
                    "department": department,
                    "device_models": ",".join(sorted(set(device_models))),
                    "total_rows": total_rows,
                    "import_mode": import_mode,
                }).encode(),
                headers={**headers, "Prefer": "return=representation"},
                method="POST",
            )
            with urllib.request.urlopen(snap_req) as r:
                snapshot = json.loads(r.read().decode())[0]
            snapshot_id = snapshot["id"]

            if rows_before:
                row_payload = [
                    {"snapshot_id": snapshot_id, "device_model": r["device_model"],
                     "code": r["code"], "variant": r.get("variant", ""), "before_data": r["before_data"]}
                    for r in rows_before
                ]
                rows_req = urllib.request.Request(
                    f"{base}/rest/v1/import_snapshot_rows",
                    data=json.dumps(row_payload).encode(),
                    headers={**headers, "Prefer": "return=minimal"},
                    method="POST",
                )
                urllib.request.urlopen(rows_req)
            return snapshot_id
        except Exception as e:
            # 快照寫入失敗不得中斷匯入本身——復原是保底機制，不是匯入的
            # 必要條件；沒有快照只是這次不能 undo，不代表資料沒寫進去。
            # 但完全吞掉例外會讓「表還沒建（007 migration 沒執行）」跟
            # 「網路抖動」這兩種情況都靜默變成 snapshot_id=None，運維
            # 端毫無線索可查——外部審查發現的問題：正式環境若忘記跑
            # migration，匯入會一直「成功但沒有復原保底」卻沒有任何
            # 警示。印一行 stderr，不中斷主流程，但至少留下痕跡。
            import sys as _sys
            print(f"[import_snapshot_store] save_snapshot 失敗（不影響本次匯入寫入結果）："
                  f"{type(e).__name__}: {e}", file=_sys.stderr)
            return None

    def list_snapshots(self, department: str, limit: int = 50) -> list:
        if not _use_supabase():
            return []
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        try:
            qs = (f"select=*&department=eq.{urllib.parse.quote(department, safe='')}"
                  f"&order=created_at.desc&limit={limit}")
            req = urllib.request.Request(
                f"{base}/rest/v1/import_snapshots?{qs}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read().decode())
        except Exception:
            return []

    def get_snapshot(self, snapshot_id: int, department: str) -> Optional[dict]:
        """回傳 {"snapshot": {...}, "rows": [...]}，department 必須相符
        （越權查詢一律當作不存在，不外洩其他部門是否有這筆快照）。"""
        if not _use_supabase():
            return None
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        try:
            snap_req = urllib.request.Request(
                f"{base}/rest/v1/import_snapshots?id=eq.{snapshot_id}"
                f"&department=eq.{urllib.parse.quote(department, safe='')}",
                headers=headers, method="GET",
            )
            with urllib.request.urlopen(snap_req) as r:
                snaps = json.loads(r.read().decode())
            if not snaps:
                return None
            rows_req = urllib.request.Request(
                f"{base}/rest/v1/import_snapshot_rows?snapshot_id=eq.{snapshot_id}",
                headers=headers, method="GET",
            )
            with urllib.request.urlopen(rows_req) as r:
                rows = json.loads(r.read().decode())
            return {"snapshot": snaps[0], "rows": rows}
        except Exception:
            return None

    def mark_undone(self, snapshot_id: int, result: dict) -> None:
        if not _use_supabase():
            return
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        try:
            req = urllib.request.Request(
                f"{base}/rest/v1/import_snapshots?id=eq.{snapshot_id}",
                data=json.dumps({
                    "undone_at": datetime.now(timezone.utc).isoformat(),
                    "undone_result": result,
                }).encode(),
                headers={"apikey": key, "Authorization": f"Bearer {key}",
                         "Content-Type": "application/json", "Prefer": "return=minimal"},
                method="PATCH",
            )
            urllib.request.urlopen(req)
        except Exception:
            pass


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
        except Exception as e:
            # 寫入失敗不可中斷主流程，但完全吞掉例外會讓「表結構跟預期
            # 不符」跟「網路抖動」都靜默變成「什麼都沒發生」——同
            # ai_logger.py _write() 的既有修法：印一行 stderr，不中斷，
            # 至少留下痕跡。
            import sys as _sys
            print(f"[FeedbackStore] _append_supabase 寫入 feedback 失敗（不影響主流程）："
                  f"{type(e).__name__}: {e}", file=_sys.stderr)

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
        except Exception as e:
            import sys as _sys
            print(f"[ViewStore] _append_supabase 寫入 alarm_views 失敗（不影響主流程）："
                  f"{type(e).__name__}: {e}", file=_sys.stderr)

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
        except Exception as e:
            # 唯讀 dashboard 統計用途，查詢失敗回空 list 不影響主流程
            # （沒有安全邊界問題），但完全吞掉例外會讓查詢失敗跟真的
            # 沒有資料在畫面上長得一模一樣——印一行 stderr 至少留下痕跡。
            import sys as _sys
            print(f"[AiScanStore] _get({table}) 查詢失敗（回退空清單）："
                  f"{type(e).__name__}: {e}", file=_sys.stderr)
            return []

    def _dept_qs(self, department: Optional[str]) -> str:
        if department is None:
            return ""
        return f"&department=eq.{urllib.parse.quote(department, safe='')}"

    def load_scans(self, department: Optional[str], limit: int = 5000) -> list:
        return self._get("ai_scans",
                         f"select=*&order=created_at.desc&limit={limit}{self._dept_qs(department)}")

    def count_recent(self, department: str, since_minutes: int) -> int:
        """/api/analyze 節流用：該部門在最近 since_minutes 分鐘內已成功寫入
        ai_scans 的筆數（PostgREST count，不搬資料）。以資料庫為唯一真實
        來源、無狀態，理由同 LoginAttemptStore——多 worker/重啟下次數才
        不會被稀釋。JsonStore fallback（非 Supabase）不啟用節流，回傳 0，
        與登入節流既有的單租戶定位一致。

        用既有的 ai_scans 表而非新開一張節流專用表：這張表本來就是每次
        AI 分析成功後才寫入的記錄，拿來算「最近呼叫過幾次」語意上是
        近似值（若 Gemini 呼叫失敗未寫入，不計入節流），不是精確的
        請求層級計數器，但足以擋住這次要防的濫用情境（正常操作不會
        在短時間內大量呼叫），且不需要新增表／migration，改動範圍
        限縮在純程式碼層級。"""
        if not _use_supabase():
            return 0
        since = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        try:
            req = urllib.request.Request(
                f"{base}/rest/v1/ai_scans?select=id"
                f"&department=eq.{urllib.parse.quote(department, safe='')}"
                f"&created_at=gt.{urllib.parse.quote(since, safe='')}",
                headers={
                    "apikey": key, "Authorization": f"Bearer {key}",
                    "Prefer": "count=exact", "Range": "0-0",
                },
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                content_range = r.headers.get("Content-Range", "")
                # 格式 "0-0/N"，N 是總筆數
                return int(content_range.split("/")[-1]) if "/" in content_range else 0
        except Exception:
            # 節流查詢失敗不可擋住正常分析請求——fail-open，理由同
            # LoginAttemptStore.record() 的既有原則：節流是防濫用機制，
            # 不是核心功能，查詢本身出錯不該讓使用者完全無法使用 AI 分析。
            return 0

    def load_corrections(self, department: Optional[str], limit: int = 5000) -> list:
        return self._get("ai_corrections",
                         f"select=*&order=created_at.desc&limit={limit}{self._dept_qs(department)}")

    def load_logs(self, department: Optional[str], limit: int = 5000) -> list:
        return self._get("ai_logs",
                         f"select=*&order=created_at.desc&limit={limit}{self._dept_qs(department)}")

    def usage_stats(self, department: Optional[str]) -> dict:
        """AI 用量統計（本月），department=None 時回傳全部部門的總計＋
        按部門分列（總管視角）；帶 department 時只回該部門的總計，
        by_department 為空（跟既有 scan_stats() 的 scope 慣例一致，
        分列清單只在跨部門視角才有意義）。

        跟 count_recent() 一樣走「撈原始列表、Python 端聚合」而非
        PostgREST 層聚合——這個 repo 目前所有統計端點（scan_stats/
        scan_ranking）都是這個風格，維持一致比另外學一套聚合語法
        風險更低。

        只計入 data.usage 有值的記錄（真正成功拿到 Gemini token 用量
        的呼叫），不含分析失敗、LocalAnalyzer（沒有 usage 概念）、或
        usage_metadata 本身為 None 的記錄——這是使用者裁決的統計口徑：
        次數要反映「實際花了多少 token」，不是「嘗試了幾次」。

        已知限制（誠實揭露，不保證 100% 精確反映 Google 實際帳單）：
        Google 官方文件沒有明確保證以下兩種灰色地帶的計費狀況——
        (a) 我們主動 30 秒 timeout 斷線時，Gemini 那端可能已經處理完
            並計費，但我們這裡連 usage_metadata 都沒收到，這種花費
            不會出現在這份統計裡；
        (b) 內容被 Gemini 安全過濾機制擋下的情況，是否計費不確定。
        這份統計只能反映「我們這邊有紀錄到的部分」，是估算參考，
        不是精確帳單來源——實際費用請以 Google Cloud/AI Studio 的
        帳單為準。"""
        if not _use_supabase():
            return {
                "month_count": 0, "month_total_tokens": 0,
                "month_prompt_tokens": 0, "month_candidates_tokens": 0,
                "by_department": [],
            }
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        query = (
            f"select=department,data&event=eq.scan"
            f"&created_at=gt.{urllib.parse.quote(month_start, safe='')}"
            f"{self._dept_qs(department)}&limit=10000"
        )
        try:
            req = urllib.request.Request(
                f"{base}/rest/v1/ai_logs?{query}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                rows = json.loads(r.read().decode())
        except Exception as e:
            import sys as _sys
            print(f"[AiScanStore] usage_stats 查詢 ai_logs 失敗（回退空清單，統計數字會偏低）："
                  f"{type(e).__name__}: {e}", file=_sys.stderr)
            rows = []

        totals = {"count": 0, "total": 0, "prompt": 0, "candidates": 0}
        by_dept: dict = {}
        for row in rows:
            data = row.get("data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    continue
            usage = (data or {}).get("usage")
            # usage 現在一律有 outcome 欄位（見 ai_pipeline.py），但只有
            # timeout/http_error 這類「完全沒拿到回應」的情況 usage 才會
            # 是 None；safety/parse_fail 即使解析失敗，usage 字典仍可能
            # 存在（只是沒有 outcome 以外的 token 欄位，因為 Gemini 那邊
            # 也沒回傳 usage_metadata）。統計口徑只認真正有 token 數字的
            # 記錄，不能被「usage 字典存在但只有 outcome、沒有 token 欄位」
            # 誤判成有效用量。
            if not usage or usage.get("total_token_count") is None:
                continue
            total_tokens = usage.get("total_token_count") or 0
            prompt_tokens = usage.get("prompt_token_count") or 0
            candidates_tokens = usage.get("candidates_token_count") or 0

            totals["count"] += 1
            totals["total"] += total_tokens
            totals["prompt"] += prompt_tokens
            totals["candidates"] += candidates_tokens

            dept_key = row.get("department") or "未知"
            if dept_key not in by_dept:
                by_dept[dept_key] = {"department": dept_key, "count": 0, "total_tokens": 0}
            by_dept[dept_key]["count"] += 1
            by_dept[dept_key]["total_tokens"] += total_tokens

        return {
            "month_count": totals["count"],
            "month_total_tokens": totals["total"],
            "month_prompt_tokens": totals["prompt"],
            "month_candidates_tokens": totals["candidates"],
            "by_department": sorted(by_dept.values(), key=lambda x: -x["total_tokens"]) if department is None else [],
        }

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


class AlarmSuggestionStore:
    """alarm_suggestions 表的讀寫（PLAN_local_solution.md 3.2/4.2 節）。

    只服務 Supabase——這張表本身就是多部門功能（一般使用者提交建議、
    管理員依部門審核），JsonStore 環境測不到跨部門隔離，這裡不假裝
    支援，比照 AiScanStore/LoginAttemptStore 的既有模式：
    _use_supabase()=False 時讀回空清單、寫入 no-op。

    目前無前端呼叫端。審核路徑已停用：部門共用密碼、無個人帳號，提交者
    與審核者無法區分，審核在此模型下摩擦為真、把關為假（PLAN_local_
    solution.md 審核路徑停用決策記錄）。已對正式 Supabase 端到端驗證過
    （提交 → 待審 → 接受 → alarms.local_solution 生效），技術上完好，
    保留供個人帳號功能完成後重新評估啟用，不刪除。
    """

    _TABLE = "alarm_suggestions"

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

    def create(self, department: str, device_model: str, code: str,
               suggestion: str, reason: Optional[str], submitted_by: str, variant: str = "") -> dict:
        """一般使用者提交建議，狀態固定為 pending，不直接寫入 alarms
        （PLAN_local_solution.md 2.2 節：一個人改、全部門立刻看到，
        工廠環境風險過高，所以只能建議、由管理員審核後才生效）。"""
        if not _use_supabase():
            return {}
        body = {
            "department": department, "device_model": device_model, "code": code,
            "variant": variant, "suggestion": suggestion, "reason": reason, "submitted_by": submitted_by,
        }
        result = self._req("POST", f"{self._TABLE}",
                           [{k: v for k, v in body.items() if v is not None}],
                           extra_headers={"Prefer": "return=representation"})
        return result[0] if result else {}

    def list_pending(self, department: Optional[str]) -> list:
        """待審清單，依 scope_department() 過濾（department=None 是總管
        不過濾的明確選擇，見 PLAN_department_isolation.md 3.6 節同一原則）。

        用 PostgREST resource embedding 帶出對應 alarms 列的
        solution/local_solution/local_reason/description，一次查詢完成
        （外部審查指出：審核者判斷的是「建議 vs 目前現值」的差異，不是
        建議文字本身，沒有現值對照無法判斷這是從無到有還是覆蓋既有內容；
        且外鍵已是複合鍵 (department, device_model, code)，逐筆查詢或
        前端另外打 API 都是 N+1，這裡用單一往返的 embedding 查詢）。"""
        if not _use_supabase():
            return []
        qs = ("select=*,alarms(solution,local_solution,local_reason,description)"
              "&status=eq.pending&order=submitted_at.desc")
        if department is not None:
            qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
        return self._req("GET", f"{self._TABLE}?{qs}")

    def has_pending(self, department: str, device_model: str, code: str, variant: str = "") -> bool:
        """單筆存在性檢查，不撈整個部門的待審清單（外部審查第四輪發現：
        submit_local_suggestion() 原本呼叫 list_pending() 只為了檢查某一筆
        有沒有待審建議，隨部門待審筆數增加會越來越浪費）。用
        Range: 0-0 只確認有沒有列，不搬資料——同 DepartmentStore._count()
        的既有模式，且同樣必須帶 Prefer: count=exact，否則 Content-Range
        只回 "0-0/*"（未知筆數）而非真正的總數，導致這裡永遠判斷為
        False（第一版漏帶這個 header 的實測結果：資料庫確實擋下重複
        提交、回 409，但這個函式卻回報「沒有 pending」，讓 create() 繼續
        往下打，PostgREST 的 409 沒被接住，變成未預期的 500 而非乾淨的
        應用層 409——同一個「except/防線失效時不出聲」的模式）。應用層
        檢查是給友善 409 訊息的第一線，資料庫的部分唯一索引（005 遷移）
        才是真正防競態的保險，兩者不衝突。"""
        if not _use_supabase():
            return False
        base, key = self._base_key()
        qs = (f"select=id&status=eq.pending"
              f"&department=eq.{urllib.parse.quote(department, safe='')}"
              f"&device_model=eq.{urllib.parse.quote(device_model, safe='')}"
              f"&code=eq.{urllib.parse.quote(code, safe='')}"
              f"&variant=eq.{urllib.parse.quote(variant, safe='')}")
        req = urllib.request.Request(
            f"{base}/rest/v1/{self._TABLE}?{qs}",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"},
            method="GET",
        )
        with urllib.request.urlopen(req) as r:
            content_range = r.headers.get("Content-Range", "")
            if "/" not in content_range:
                return False
            total = content_range.rsplit("/", 1)[-1]
            return total.isdigit() and int(total) > 0

    def get_by_id(self, suggestion_id: int) -> Optional[dict]:
        if not _use_supabase():
            return None
        result = self._req("GET", f"{self._TABLE}?id=eq.{suggestion_id}&select=*")
        return result[0] if result else None

    def review(self, suggestion_id: int, status: str, reviewed_by: str,
               review_note: Optional[str]) -> Optional[dict]:
        """接受或退回一筆建議，status 只接受 accepted/rejected（由呼叫端
        app.py 驗證，這裡不重複檢查，維持 store 層單純負責存取）。"""
        if not _use_supabase():
            return None
        patch = {
            "status": status, "reviewed_by": reviewed_by,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        if review_note is not None:
            patch["review_note"] = review_note
        result = self._req("PATCH", f"{self._TABLE}?id=eq.{suggestion_id}", patch,
                           extra_headers={"Prefer": "return=representation"})
        return result[0] if result else None


class LoginAttemptStore:
    """login_attempts 表的讀寫（PLAN 2.2.1~2.2.4 節）。以資料庫為唯一真實
    來源，不使用行程內狀態——多 worker/重啟/擴容下退避次數才不會被稀釋。

    本機/測試模式（非 Supabase）不記錄，節流形同不啟用（與正式環境的
    JsonStore 單租戶定位一致，PLAN 3.2 節）。

    Supabase 查詢失敗時的降級策略（外部專家 + 使用者裁決）：fail-open，
    降級為行程內計數，不是完全不節流、也不是 fail-closed 擋下所有人。

    這不是因為「密碼夠長所以節流不重要」——只有 zztest 哨兵部門的密碼是
    reset_sentinel_password.py 產生的 20 字元隨機字串，正式部門密碼是
    超管手動輸入、沒有強度驗證，機密性這條防線本來就不算牢固。選擇
    fail-open 是**誠實的可用性優先取捨**：這裡的節流主要防的是「暴力
    嘗試把 worker 資源佔滿」這種可用性攻擊，不是最後一道機密性防線；
    資料庫查詢本身不穩時，讓所有正常使用者（不只攻擊者）被完全鎖在
    登入頁外，代價比「降級成一個比較寬鬆的行程內節流」更高。

    行程內計數依賴目前的單 worker 部署假設（render.yaml 的 startCommand
    沒有指定 `-w`，用 gunicorn 預設值）。未來若改成多 worker，各 worker
    的計數彼此獨立、互不同步，降級模式下的節流精確度會下降（實際能
    通過的請求數可能是「每 worker 各自的上限」而非全域上限），但不會
    因此出錯或崩潰——只是防護變得更鬆，這個取捨在多 worker 化時需要
    重新評估，不是這次修正要解決的範圍。
    """

    _TABLE = "login_attempts"

    # 降級節流的窗口/門檻（外部專家建議值，使用者裁決採用）：
    # Supabase 查詢連續失敗時，5 分鐘內同一 IP 超過此次數才擋，比正常
    # 模式（_remaining_delay 的指數退避，N=1 就開始）寬鬆得多——降級
    # 模式本來就該比正常模式寬鬆，這是刻意的，不是疏漏。
    _FALLBACK_WINDOW_SECONDS = 300
    _FALLBACK_LIMIT = 20

    def __init__(self):
        self._fallback_lock = Lock()
        self._fallback_hits: dict = {}  # ip -> [timestamp, ...]（僅記錄「查詢失敗當下」的請求時間，不是失敗登入次數）
        self.degraded = False  # /ping、後台橫幅讀這個旗標

    def _fallback_count(self, ip: str, record: bool = True) -> int:
        """行程內計數：清掉窗口外的舊記錄，回傳目前窗口內的次數。
        不是「登入失敗次數」，是「Supabase 查詢失敗、退回行程內計數的
        次數」——降級期間用來判斷是否還要繼續放行。

        record=True 時會多記一筆本次呼叫的時間戳。【修正既有計數語意，
        非併行化引入】fine/coarse 兩支查詢原本各自在查詢失敗時都呼叫
        一次這個方法並各記一筆，等於一次登入嘗試（同時觸發兩支查詢）
        會被計成 2 次降級命中，把原本設計成寬鬆的降級門檻
        （_FALLBACK_LIMIT=20）實質砍半。這個問題在併行化之前就存在
        （count_fine/count_coarse 循序呼叫時就已經各記一筆），併行化
        只是讓這次一併修正——呼叫端（_check_login_throttle）改為兩支
        查詢各自只用 record=False 讀目前次數，等兩支都拿到結果後，若
        任一降級，由呼叫端統一呼叫一次 record=True 才真正記錄，確保
        一次登入嘗試最多只計一次降級命中。"""
        now = time.time()
        cutoff = now - self._FALLBACK_WINDOW_SECONDS
        with self._fallback_lock:
            hits = [t for t in self._fallback_hits.get(ip, []) if t > cutoff]
            if record:
                hits.append(now)
                self._fallback_hits[ip] = hits
            return len(hits)

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
                                   scope_by_department: bool = True,
                                   label: str = "") -> tuple:
        """該 (ip[, department]) 組合在最近一次成功登入之後、15 分鐘窗口內的
        連續失敗次數（PLAN 2.2.1/2.2.3 節的 N 定義），以及最後一次失敗的時間。

        回傳 (N, last_failure_at, degraded)：delay 是相對 last_failure_at 算的
        倒數計時，不是「只要 N>=1 就永久節流」——過了 2**N 秒窗口，同一個 N
        就不再節流，直到下一次失敗才會再次觸發（且 N 會遞增）。

        degraded 由呼叫端負責寫回 self.degraded（不在這裡直接寫共享屬性）：
        count_fine/count_coarse 併行執行時，若各自查詢失敗直接寫
        self.degraded，後寫入的那支會蓋掉先寫入的結果，讓 /ping、後台
        橫幅讀到的降級狀態失真。呼叫端等兩支都跑完，OR 合併後才寫一次
        （PLAN 效能優化第 3 項驗收條件 a）。
        """
        if not _use_supabase():
            return (0, None, False)
        window_start = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        dept_qs = f"&department=eq.{urllib.parse.quote(department, safe='')}" if (scope_by_department and department is not None) else ""
        import sys as _sys
        t0 = time.monotonic()
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
            elapsed_ms = (time.monotonic() - t0) * 1000
            # urllib.request.urlopen() 是連線建立+送出查詢+收回應一次完成的
            # 黑盒呼叫，沒有 API 能單獨量出「連線建立」這一段（要拆的話得換
            # http.client 手動 connect()/request()，影響面會擴及其他 store
            # 類別的 _req()，這次先只量總耗時，數字若顯示有必要再另外評估）。
            print(f"throttle_timing[{label}]: {elapsed_ms:.0f}ms "
                  f"（連線建立+查詢合計，urllib 無法單獨拆分連線建立耗時）", file=_sys.stderr)
            return (n, last_failure_at, False)
        except Exception as e:
            # 查詢失敗不可靜默偽裝成「N=0，沒有任何失敗記錄」——那等於
            # 節流在資料庫不穩時直接失效且毫無警示。改為 fail-open 降級：
            # 捕捉例外、記錄、退回行程內計數（見 class docstring 的取捨
            # 說明），不是完全不節流、也不是讓所有正常使用者被鎖在外面。
            #
            # 【修正既有計數語意，非併行化引入】這裡只「讀」目前窗口內的
            # 命中次數（record=False），不在這裡記一筆——一次登入嘗試會
            # 同時觸發 count_fine + count_coarse 兩支查詢，若兩支查詢失敗
            # 時都各自在這裡記一筆，等於一次登入嘗試被計成 2 次降級命中，
            # 把原本設計成寬鬆的降級門檻（_FALLBACK_LIMIT=20）實質砍半。
            # 真正記錄的時機交給呼叫端（_check_login_throttle）：兩支查詢
            # 都跑完後，只要任一降級，整個登入嘗試只記一次
            # （見 LoginAttemptStore.note_fallback_hit()）。
            print(f"throttle_degraded[{label}]: LoginAttemptStore 查詢失敗，退回行程內計數："
                  f"{type(e).__name__}: {e}", file=_sys.stderr)
            fallback_n = self._fallback_count(ip, record=False)
            if fallback_n > self._FALLBACK_LIMIT:
                # 沿用「N、最後失敗時間」這個既有回傳形狀，讓呼叫端
                # _remaining_delay() 不需要另外處理降級模式的分支——
                # 直接讓它算出一個非零延遲，效果等同節流生效。
                return (fallback_n, datetime.now(timezone.utc).isoformat(), True)
            return (0, None, True)

    def note_fallback_hit(self, ip: str) -> int:
        """一次登入嘗試若有任一支查詢（fine/coarse）降級，呼叫端統一
        呼叫這個方法記一筆，取代兩支查詢各自記錄——避免一次登入嘗試
        被重複計成 2 次降級命中（見 _count_since_last_success 的說明）。
        回傳記錄後的目前次數，供呼叫端需要時使用。"""
        return self._fallback_count(ip, record=True)

    def count_fine(self, ip: str, department: str) -> tuple:
        """細網：(ip, department) 組合的連續失敗數 N_ip_dept 與最後失敗時間。
        回傳 (N, last_failure_at, degraded)——degraded 由呼叫端負責寫回
        self.degraded（見 _count_since_last_success 的說明）。"""
        return self._count_since_last_success(ip, department, scope_by_department=True, label="fine")

    def count_coarse(self, ip: str) -> tuple:
        """粗網：只看 ip 的連續失敗數 N_ip 與最後失敗時間（PLAN 2.2.3 節，防止換部門繞過細網）。
        回傳 (N, last_failure_at, degraded)——degraded 由呼叫端負責寫回
        self.degraded（見 _count_since_last_success 的說明）。"""
        return self._count_since_last_success(ip, None, scope_by_department=False, label="coarse")

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


class VariantTranslationStore:
    """variant 英文原文 -> 中文翻譯查找表（拍照辨識故障修復 Q3 延伸）。

    跟 department 無關：翻譯只跟文字本身有關，不比照 alarms 帶
    department 欄位。本機/測試模式讀 data/variant_translations.json
    （JsonStore 慣例，_load_json 找不到檔案時回傳空 dict，fail-open——
    翻譯只是顯示用的加值資訊，找不到就退回顯示英文原文，不應該讓
    整個拍照辨識流程掛掉）。
    """

    def load_all(self) -> dict:
        """回傳 {original_text: {"zh": ..., "status": ...}}。"""
        if _use_supabase():
            return self._load_supabase()
        return self._load_json()

    def _load_json(self) -> dict:
        path = _data_dir() / "variant_translations.json"
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return {
            text: {"zh": entry.get("zh", ""), "status": entry.get("status", "")}
            for text, entry in raw.items()
        }

    def _load_supabase(self) -> dict:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            qs = "select=original_text,translated_text,review_status&limit=5000"
            req = urllib.request.Request(
                f"{base}/rest/v1/variant_translations?{qs}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                rows = json.loads(r.read().decode())
            return {
                row["original_text"]: {"zh": row["translated_text"], "status": row["review_status"]}
                for row in rows
            }
        except Exception as e:
            # fail-open：翻譯查無資料時前端退回顯示英文原文，不影響
            # 主流程（拍照辨識/variant 選擇本身跟翻譯是否可用無關），
            # 但例外發生過這件事本身要留痕（CLAUDE.md 例外處理判準），
            # 不能完全空白吞掉。
            import sys as _sys
            print(f"[VariantTranslationStore] _load_supabase 查詢失敗（退回空dict，"
                  f"前端顯示英文原文）：{type(e).__name__}: {e}", file=_sys.stderr)
            return {}


class SemanticReviewStore:
    """全庫語意品質審核清單（303 筆 AI 語意疑慮發現，見 migration
    009_add_semantic_review_findings.sql）。跟 department 無關——既有
    API（app.py 的 list_semantic_review()）本來就不依 department 過濾，
    回傳整份清單給任何呼叫的部門看，這裡忠實保留既有行為。

    介面刻意跟 app.py 原本的 _load_semantic_review()/_save_semantic_review()
    保持相容（load_all() 回傳 list、save_all(findings) 整批覆蓋寫回），
    這樣 update_semantic_review() 的「讀出整個 list、改一筆、存回整個
    list」邏輯完全不用改，呼叫端不用感知底層是 JSON 檔案還是 DB 表。
    順序穩定很重要：前端用陣列 index 當這筆審核項目的識別碼（不是用
    device_model/code），load_all() 必須每次回傳同一個順序，這裡固定
    用 created_at 排序，不能讓 Supabase 的預設回傳順序（無 order 時
    不保證）打亂既有的 index 契約。

    本機/測試模式讀 data/semantic_scan_fixes.json（既有格式，fail-open：
    找不到檔案回空清單，這代表「還沒跑過離線掃描工具」不是系統壞了，
    行為完全複製自原本 app.py 的 _load_semantic_review()）。
    """

    def load_all(self) -> list:
        if _use_supabase():
            return self._load_supabase()
        return self._load_json()

    def save_all(self, findings: list) -> None:
        if _use_supabase():
            self._save_supabase(findings)
        else:
            self._save_json(findings)

    def _path(self):
        return _data_dir() / "semantic_scan_fixes.json"

    def _load_json(self) -> list:
        path = self._path()
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        findings = data.get("findings", data) if isinstance(data, dict) else data
        for f in findings:
            f.setdefault("status", "pending")
        return findings

    def _save_json(self, findings: list) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({"findings": findings}, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    _FIELDS = ["device_model", "code", "description", "issue", "confidence",
               "suggested_zh", "suggested_description"]

    def _row_to_finding(self, row: dict) -> dict:
        finding = {k: row[k] for k in self._FIELDS}
        finding["status"] = row["review_status"]
        if row.get("final_zh") is not None:
            finding["final_zh"] = row["final_zh"]
        if row.get("snapshot_id") is not None:
            finding["snapshot_id"] = row["snapshot_id"]
        return finding

    def _finding_to_row(self, finding: dict) -> dict:
        row = {k: finding[k] for k in self._FIELDS}
        row["review_status"] = finding.get("status", "pending")
        row["final_zh"] = finding.get("final_zh")
        row["snapshot_id"] = finding.get("snapshot_id")
        return row

    def _load_supabase(self) -> list:
        try:
            base = os.environ.get("SUPABASE_URL", "").rstrip("/")
            key = os.environ.get("SUPABASE_KEY", "")
            qs = "select=*&order=created_at.asc&limit=5000"
            req = urllib.request.Request(
                f"{base}/rest/v1/semantic_review_findings?{qs}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET",
            )
            with urllib.request.urlopen(req) as r:
                rows = json.loads(r.read().decode())
            return [self._row_to_finding(row) for row in rows]
        except Exception as e:
            # fail-open：跟既有 _load_semantic_review() 對「檔案不存在」
            # 的處理一致——查詢失敗時如實回空清單，不是報錯，但例外
            # 本身要留痕（CLAUDE.md 例外處理判準），不能完全空白吞掉。
            import sys as _sys
            print(f"[SemanticReviewStore] _load_supabase 查詢失敗（退回空清單）："
                  f"{type(e).__name__}: {e}", file=_sys.stderr)
            return []

    def _save_supabase(self, findings: list) -> None:
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_KEY", "")
        data = json.dumps([self._finding_to_row(f) for f in findings]).encode()
        req = urllib.request.Request(
            f"{base}/rest/v1/semantic_review_findings?on_conflict=device_model,code",
            data=data,
            headers={
                "apikey": key, "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            method="POST",
        )
        # 這裡刻意不 catch——跟 variant_translations/save_snapshot 那類
        # 「失敗不影響主流程」的加值功能不同，這是審核動作本身唯一的
        # 寫入路徑：accept/reject 這筆狀態如果沒真的存進去，使用者會
        # 以為操作成功但下次載入時狀態消失，比拋錯讓 update_semantic_
        # review() 的呼叫端得到 500 更誤導人。
        urllib.request.urlopen(req)


class PendingAlarmImportStore:
    """異常匯入資料的待審清單（見 migration 010_add_pending_alarm_
    imports.sql）。比照 AlarmSuggestionStore 的既有模式（storage.py
    AlarmSuggestionStore 類別）：只服務 Supabase，_use_supabase()=False
    時讀回空清單、寫入 no-op；review() 只單純更新這張表本身的狀態，
    不負責把資料寫進 alarms——跨 store 的編排（組裝完整 alarm dict、
    呼叫 alarms_store.upsert_one()、稽核軌跡）放在 app.py 端點層，
    理由同 AlarmSuggestionStore 的既有分工：跨 store 協調本來就該在
    應用層，不是 store 層，避免 store 之間互相耦合。

    跟 alarm_suggestions 的關鍵差異：這張表**不對 alarms 設外鍵**——
    待審資料指向的 (department, device_model, code, variant) 在 alarms
    表裡本來就還不存在，這正是「異常/新資料要先審核」的本質，若照抄
    alarm_suggestions 的外鍵約束會直接擋住所有正常的待審資料寫入
    （見 migration 010 開頭的完整說明）。
    """

    _TABLE = "pending_alarm_imports"

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

    def list_pending(self, department: Optional[str]) -> list:
        """待審清單，依 scope_department() 過濾（department=None 是總管
        不過濾的明確選擇，同 alarms_store.load() 一致的既有原則）。"""
        if not _use_supabase():
            return []
        qs = "select=*&status=eq.pending&order=submitted_at.desc"
        if department is not None:
            qs += f"&department=eq.{urllib.parse.quote(department, safe='')}"
        return self._req("GET", f"{self._TABLE}?{qs}")

    def get_by_id(self, import_id: int) -> Optional[dict]:
        if not _use_supabase():
            return None
        result = self._req("GET", f"{self._TABLE}?id=eq.{import_id}&select=*")
        return result[0] if result else None

    def review(self, import_id: int, status: str, reviewed_by: str,
               review_note: Optional[str]) -> Optional[dict]:
        """更新這張表本身的審核狀態，status 只接受 approved/rejected
        （由呼叫端 app.py 驗證，這裡不重複檢查，維持 store 層單純負責
        存取——同 AlarmSuggestionStore.review() 的既有分工）。不負責
        把資料寫進 alarms，那是呼叫端在確認這裡更新成功後另外呼叫
        alarms_store.upsert_one() 的責任。"""
        if not _use_supabase():
            return None
        patch = {
            "status": status, "reviewed_by": reviewed_by,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        if review_note is not None:
            patch["review_note"] = review_note
        result = self._req("PATCH", f"{self._TABLE}?id=eq.{import_id}", patch,
                           extra_headers={"Prefer": "return=representation"})
        return result[0] if result else None


_ALARMS_CACHE_TTL_SECONDS = 60  # PLAN 效能優化第 4 項：mf4d 部門 1759 筆分頁查詢實測約 1.6 秒

if _use_supabase():
    alarms_store = SupabaseStore("alarms", pk="code", pk_fields=["department", "device_model", "code", "variant"],
                                  cache_ttl=_ALARMS_CACHE_TTL_SECONDS)
    devices_store = SupabaseStore("devices", pk="id", pk_fields=["department", "model"], is_devices=True)
else:
    alarms_store = JsonStore("alarms.json")
    devices_store = JsonStore("devices.json", is_devices=True)

department_store = DepartmentStore()
login_attempt_store = LoginAttemptStore()
alarm_suggestion_store = AlarmSuggestionStore()
feedback_store = FeedbackStore()
view_store = ViewStore()
audit_logger = AuditLogger()
ai_scan_store = AiScanStore()
import_snapshot_store = ImportSnapshotStore()
variant_translation_store = VariantTranslationStore()
semantic_review_store = SemanticReviewStore()
pending_alarm_import_store = PendingAlarmImportStore()
