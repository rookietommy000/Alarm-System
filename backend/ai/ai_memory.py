"""
AI 記憶庫（MEM 層）。

標籤系統：
  MEM-001  拍照歷史（分級保留）
  MEM-002  修正記憶（source: corrected，不覆蓋原始）

儲存策略：
  短期：本地 JSON（按機種分檔）
  中期：搬到 Supabase ← 待接

Tier 定義（MEM-001）：
  success_high   成功 + 高信心（≥ profile.high）  → 365 天
  success        成功 + 普通信心（≥ profile.pass） → 90 天
  low_confidence 有拍到代碼但全被信心過濾           → 30 天
  no_alarm       機種辨識成功但畫面確實無警報        → 7 天
  failure        機種完全辨識不出 / analyzer 斷線    → 30 天
  confirmed      操作員人工確認 AI 結果正確          → 長期（9999 天）
  corrected      使用者修正 AI 辨識錯誤              → 長期（9999 天）

Source 語意：
  "ai"        純 AI 輸出，尚未人工背書
  "confirmed" 操作員確認 AI 正確（未改動）
  "corrected" 操作員修正 AI 錯誤

錯誤率計算：
  機種層：original_model != model（有人工背書的 scan）
  代碼層：被改過的 code / 出現過的 code（依 conf 區間）
  → 使用 load_confirmed_history()，不用未確認的 AI 猜測回餵 AI
"""

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .ai_config import MEM as _CFG, CONF_PROFILE

# 模組級別鎖，_append_record 的 read-modify-write 全程鎖住，避免並發掉紀錄
_write_lock = threading.Lock()

# 檔名字元白名單：只留英數、底線、連字號，防路徑注入
_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_\-]")

# ── 設定 ────────────────────────────────────────────────────────────────────

MEM_DIR = Path(os.environ.get(
    "AI_MEM_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "data" / "ai_memory")
))

# [MEM-001] 保留天數分級（從 ai_config 讀，不寫死）
RETENTION = {
    "success_high":   _CFG["retain_success_high"],
    "success":        _CFG["retain_success"],
    "low_confidence": _CFG["retain_low_confidence"],
    "no_alarm":       _CFG["retain_no_alarm"],
    "failure":        _CFG["retain_failure"],
    "confirmed":      _CFG["retain_confirmed"],
    "corrected":      _CFG["retain_corrected"],
}

# 預設門檻（當 analyzer 不在 CONF_PROFILE 時使用）
_DEFAULT_HIGH = _CFG["high_conf"]
_DEFAULT_PASS = _CFG["min_conf"]


def _get_profile_thresholds(analyzer_name: Optional[str]) -> tuple:
    """回傳 (high_threshold, pass_threshold) 依 analyzer CONF_PROFILE 校準。"""
    profile = CONF_PROFILE.get(analyzer_name or "", {})
    return (
        profile.get("high", _DEFAULT_HIGH),
        profile.get("pass", _DEFAULT_PASS),
    )


# ── MEM-001：拍照歷史 ────────────────────────────────────────────────────────

def record_scan(
    model: Optional[str],
    model_conf: Optional[int],
    alarms: list,
    rejected_alarms: list,
    source: str = "ai",
    analyzer: Optional[dict] = None,
    confirmed_by: Optional[str] = None,
    original_model: Optional[str] = None,
    scan_id: Optional[str] = None,
    *,
    department: Optional[str],
) -> dict:
    """
    [MEM-001] 記錄一次拍照辨識結果。

    參數：
      model           最終機種（None 表示辨識失敗）
      model_conf      機種信心度（None 表示模型未回信心度）
      alarms          通過 POST 規則的警報列表（每筆帶 conf）
      rejected_alarms 被過濾掉的警報列表（每筆帶 conf 和 error 原因）
      source          "ai"（純 AI）| "confirmed"（人工確認）| "corrected"（人工修正）
      analyzer        追溯資訊 {name, model, prompt_version}
      confirmed_by    操作者身分（GMP 稽核用，source!=ai 時必填）
      original_model  AI 原始辨識值（source=confirmed/corrected 時帶入，供錯誤率計算）
      scan_id         外部傳入 scan_id（省略時自動產生）
      department      寫入的部門，None 是明確選擇（無部門情境），不是預設值——
                       呼叫端必須每次主動決定（PLAN 3.6 節）

    回傳寫入的紀錄 dict（含 scan_id，供後續確認/修正帶回）。
    """
    analyzer_name = (analyzer or {}).get("name")
    high_conf, pass_conf = _get_profile_thresholds(analyzer_name)

    model_identified = bool(model)
    has_alarms = len(alarms) > 0

    # conf=None 的代碼排除在平均計算之外（None 語意：模型沒回信心度）
    confs = [a["conf"] for a in alarms if a.get("conf") is not None]
    avg_conf: Optional[float] = sum(confs) / len(confs) if confs else None

    if source in ("confirmed", "corrected"):
        tier = source
    elif not model_identified:
        tier = "failure"
    elif not has_alarms and any(r.get("error") == "ERR_LOW_CONF" for r in rejected_alarms):
        tier = "low_confidence"
    elif not has_alarms:
        tier = "no_alarm"
    elif avg_conf is None:
        tier = "success"
    elif model_conf is not None and model_conf >= high_conf and avg_conf >= high_conf:
        tier = "success_high"
    elif avg_conf >= pass_conf:
        tier = "success"
    else:
        tier = "low_confidence"

    retention_days = RETENTION[tier]
    now = datetime.now(timezone.utc)
    record = {
        "scan_id":        scan_id or uuid.uuid4().hex,
        "source":         source,
        "tier":           tier,
        "model":          model,
        "original_model": original_model,
        "model_conf":     model_conf,
        "alarms":         [{"code": a["code"], "conf": a.get("conf")} for a in alarms],
        "rejected":       [{"code": a["code"], "conf": a.get("conf"), "error": a.get("error")} for a in rejected_alarms],
        "analyzer":       analyzer,
        "confirmed_by":   confirmed_by,
        "scanned_at":     now.isoformat(),
        "expires_at":     (now + timedelta(days=retention_days)).isoformat(),
        "department":     department,
    }

    _append_record("history", model or "_unknown", record, department=department)
    return record


def record_confirmation(
    scan_id: str,
    model: str,
    alarms: list,
    model_conf: Optional[int],
    original_model: Optional[str],
    original_analyzer: Optional[dict],
    confirmed_by: str,
    *,
    department: Optional[str],
) -> dict:
    """
    [MEM-001] 操作員確認 AI 結果正確（未修改），補寫一筆 source="confirmed"。

    scan_id: 原始 AI scan 的 id，確認頁帶回來
    alarms:  [{"code": str, "conf": int | None}, ...]（含 conf，從原始 scan 帶回）
    original_model: AI 原始辨識的機種（供機種層錯誤率計算用）
    department: 見 record_scan() 的說明（None 為明確選擇，非預設值）
    """
    return record_scan(
        model=model,
        model_conf=model_conf,
        alarms=alarms,
        rejected_alarms=[],
        source="confirmed",
        analyzer=original_analyzer,
        confirmed_by=confirmed_by,
        original_model=original_model,
        scan_id=scan_id,
        department=department,
    )


# ── MEM-002：修正記憶 ────────────────────────────────────────────────────────

def record_correction(
    scan_id: str,
    original_model: Optional[str],
    corrected_model: str,
    original_codes: list,
    corrected_codes: list,
    model_conf: Optional[int],
    original_analyzer: Optional[dict] = None,
    confirmed_by: Optional[str] = None,
    *,
    department: Optional[str],
) -> dict:
    """
    [MEM-002] 記錄使用者的修正行為。
    不覆蓋原始紀錄，新增一筆 source="corrected"。

    scan_id:         原始 AI scan 的 id（確認頁帶回來）
    original_model:  AI 原始辨識的機種
    corrected_model: 使用者選擇的正確機種
    original_codes:  [{"code": str, "conf": int | None}, ...]（含 conf）
    corrected_codes: [{"code": str, "conf": int | None}, ...]（含 conf）
    model_conf:      AI 原始的機種信心度
    original_analyzer: 原判斷的 analyzer 資訊（計算 per-model 錯誤率用）
    confirmed_by:    操作者身分（GMP 稽核用）
    department:      見 record_scan() 的說明（None 為明確選擇，非預設值）
    """
    now = datetime.now(timezone.utc)
    record = {
        "scan_id":         scan_id,
        "corrected_at":    now.isoformat(),
        "expires_at":      (now + timedelta(days=RETENTION["corrected"])).isoformat(),
        "source":          "corrected",
        "original_model":  original_model,
        "corrected_model": corrected_model,
        "original_codes":  original_codes,   # [{"code", "conf"}, ...]
        "corrected_codes": corrected_codes,  # [{"code", "conf"}, ...]
        "model_conf":      model_conf,
        "original_analyzer": original_analyzer,
        "confirmed_by":    confirmed_by,
        "department":      department,
    }

    _append_record("corrections", corrected_model, record, department=department)

    # 同時在歷史裡補一筆 corrected 記錄（帶 scan_id 和 original_model 追溯）
    return record_scan(
        model=corrected_model,
        model_conf=model_conf,
        alarms=corrected_codes,
        rejected_alarms=[],
        source="corrected",
        analyzer=original_analyzer,
        confirmed_by=confirmed_by,
        original_model=original_model,
        scan_id=scan_id,
        department=department,
    )


def load_corrections(model: str, *, department: Optional[str]) -> list:
    """[MEM-002] 讀取某機種的所有修正記錄，限縮在呼叫者的部門範圍內
    （PLAN 3.5 節：不限縮會讓 A 部門的辨識歷史混進 B 部門候選建議清單）。"""
    return _load_records("corrections", model, department=department)


def load_confirmed_history(model: str, *, department: Optional[str]) -> list:
    """
    [MEM-001] 只回傳人工確認過的歷史（source in confirmed/corrected），
    且限縮在呼叫者的部門範圍內（PLAN 3.5 節）。

    用於任何「以歷史回饋 AI」的邏輯（錯誤率計算、比對建議…）。
    直接用 _load_records 會把未確認的 AI 猜測一起帶入，讓 AI 拿
    自己的推測當依據造成偏移。需要用確認資料的邏輯一律呼叫這裡。
    """
    return [
        r for r in _load_records("history", model, department=department)
        if r.get("source") in ("confirmed", "corrected")
    ]


# ── 自動清理 ─────────────────────────────────────────────────────────────────

def cleanup_expired() -> int:
    """
    掃描所有記憶檔，移除已過期的紀錄。
    回傳刪除筆數。建議定期呼叫（例如每天一次）。
    """
    now = datetime.now(timezone.utc)
    removed = 0

    for subdir in ["history", "corrections"]:
        folder = MEM_DIR / subdir
        if not folder.exists():
            continue
        for f in folder.glob("*.json"):
            with _write_lock:
                records = _read_file(f)
                before = len(records)
                records = [r for r in records if _not_expired(r, now)]
                removed += before - len(records)
                _write_file_unlocked(f, records)

    return removed


# ── Supabase helpers ─────────────────────────────────────────────────────────

def _use_supabase() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


def _sb_insert(table: str, row: dict):
    try:
        import urllib.request, urllib.error
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        data = json.dumps(row).encode()
        req = urllib.request.Request(
            f"{base}/rest/v1/{table}",
            data=data,
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            method="POST",
        )
        urllib.request.urlopen(req)
    except Exception:
        pass


def _sb_query(table: str, filters: dict) -> list:
    try:
        import urllib.request, urllib.parse
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_KEY"]
        qs = "&".join(f"{k}=eq.{urllib.parse.quote(str(v))}" for k, v in filters.items() if v is not None)
        req = urllib.request.Request(
            f"{base}/rest/v1/{table}?select=*&{qs}&limit=1000",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            method="GET",
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except Exception:
        return []


# ── 內部工具 ─────────────────────────────────────────────────────────────────

def _append_record(subdir: str, key: str, record: dict, department: Optional[str]):
    if _use_supabase():
        if subdir == "history":
            _sb_insert("ai_scans", {
                "scan_id":        record.get("scan_id"),
                "model":          record.get("model"),
                "model_conf":     record.get("model_conf"),
                "tier":           record.get("tier"),
                "source":         record.get("source"),
                "alarms":         json.dumps(record.get("alarms", [])),
                "rejected_alarms": json.dumps(record.get("rejected", [])),
                "analyzer":       json.dumps(record.get("analyzer")),
                "confirmed_by":   record.get("confirmed_by"),
                "original_model": record.get("original_model"),
                "department":     department,
            })
        elif subdir == "corrections":
            _sb_insert("ai_corrections", {
                "scan_id":          record.get("scan_id"),
                "original_model":   record.get("original_model"),
                "corrected_model":  record.get("corrected_model"),
                "original_codes":   json.dumps(record.get("original_codes", [])),
                "corrected_codes":  json.dumps(record.get("corrected_codes", [])),
                "model_conf":       record.get("model_conf"),
                "original_analyzer": json.dumps(record.get("original_analyzer")),
                "confirmed_by":     record.get("confirmed_by"),
                "department":       department,
            })
        return

    folder = MEM_DIR / subdir
    folder.mkdir(parents=True, exist_ok=True)
    safe_key = (_SAFE_KEY_RE.sub("_", key) or "_unknown")[:64]
    path = folder / f"{safe_key}.json"
    with _write_lock:
        records = _read_file(path)
        records.append(record)
        _write_file_unlocked(path, records)


def _load_records(subdir: str, key: str, department: Optional[str]) -> list:
    """department=None 時不加過濾條件（PLAN 3.7 節：DeptScope.ALL 不得加任何
    department 相關過濾，含看似無害的 .not.is.null；_sb_query() 的 filters
    已在 None 值時自動跳過該欄位，天然滿足這個約束）。"""
    if _use_supabase():
        if subdir == "history":
            return _sb_query("ai_scans", {"model": key, "department": department})
        elif subdir == "corrections":
            return _sb_query("ai_corrections", {"corrected_model": key, "department": department})
        return []

    safe_key = (_SAFE_KEY_RE.sub("_", key) or "_unknown")[:64]
    path = MEM_DIR / subdir / f"{safe_key}.json"
    now = datetime.now(timezone.utc)
    return [r for r in _read_file(path) if _not_expired(r, now)]


def _not_expired(r: dict, now: datetime) -> bool:
    exp = r.get("expires_at")
    if not exp:
        return True
    try:
        return datetime.fromisoformat(exp) > now
    except ValueError:
        return True


def _read_file(path: Path) -> list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_file_unlocked(path: Path, records: list):
    """呼叫端必須已持有 _write_lock。"""
    tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
