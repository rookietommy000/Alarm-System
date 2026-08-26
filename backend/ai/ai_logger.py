"""
AI 決策日誌（LOG 層）。

標籤系統：
  LOG-001  完整操作與決策日誌

每次辨識、過濾、修正、放行的完整決策路徑都記錄在這裡，
方便事後追溯與調參。

日誌分級：
  INFO    正常流程
  WARN    灰色地帶（低信心、白名單外放行等）
  ERROR   失敗或異常
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import threading

from .ai_config import LOG as _LOG_CFG

# ── 設定 ────────────────────────────────────────────────────────────────────

LOG_DIR = Path(os.environ.get(
    "AI_LOG_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "data" / "ai_logs")
))

LOG_RETENTION_DAYS = _LOG_CFG["retain_days"]

_lock = threading.Lock()


# ── LOG-001：決策日誌 ────────────────────────────────────────────────────────

def log_scan(
    level: str,
    model: Optional[str],
    model_conf: Optional[int],
    alarms: list,
    rejected_alarms: list,
    model_warning: Optional[str],
    needs_model_selection: bool,
    analyzer: Optional[dict] = None,
    usage: Optional[dict] = None,
    extra: Optional[dict] = None,
):
    """
    [LOG-001] 記錄一次完整辨識決策路徑。

    level: "INFO" | "WARN" | "ERROR"
    analyzer: {name, model, prompt_version} 追溯用
    usage: Gemini 的 token 用量（prompt_token_count 等）＋outcome 分類，
    純記錄用途，不做額度上限管控（AI 用量統計階段 1）。outcome 標示
    這次呼叫的結果類型："ok"（正常拿到 usage）/"timeout"（我方 30 秒
    逾時主動斷線）/"http_error"（Gemini 回 4xx/5xx）/"safety"（Gemini
    安全過濾擋下，finish_reason=SAFETY）/"parse_fail"（Gemini 有回應
    但我方解析失敗）——這幾種情境下 Gemini 是否計費 Google 官方文件
    沒有明確保證，先記錄 outcome 分類，未來用實際帳單反推各類情況的
    誤差，不用現在就查出確切計費規則。只有 GeminiAnalyzer 會提供，
    LocalAnalyzer/Analyzer 完全沒呼叫到（例如 import 失敗）時為 None，
    寫入 data JSONB 的 usage 子欄位，不放進頂層 event（頂層 event
    固定是 "scan"，usage 只是這筆掃描記錄底下的附屬資料，不是獨立
    事件類型）。
    """
    _write({
        "event": "scan",
        "level": level,
        "model": model,
        "model_conf": model_conf,
        "model_warning": model_warning,
        "needs_model_selection": needs_model_selection,
        "alarm_count": len(alarms),
        "rejected_count": len(rejected_alarms),
        "alarms": [{"code": a["code"], "conf": a.get("conf")} for a in alarms],
        "rejected": [{"code": a["code"], "error": a.get("error")} for a in rejected_alarms],
        "analyzer": analyzer,
        "usage": usage,
        **(extra or {}),
    })


def log_confirmation(
    scan_id: str,
    model: str,
    original_model: Optional[str],
    alarms: list,
    model_conf: Optional[int],
    confirmed_by: str,
):
    """
    [LOG-001] 操作員確認 AI 結果正確（GMP 稽核必填）。

    alarms 格式：[{"code": str, "conf": int | None}, ...]
    scan_id 可與 MEM history 的同名欄位交叉比對。
    """
    _write({
        "event": "confirmation",
        "level": "INFO",
        "scan_id": scan_id,
        "model": model,
        "original_model": original_model,
        "alarms": alarms,
        "model_conf": model_conf,
        "confirmed_by": confirmed_by,
    })


def log_correction(
    scan_id: str,
    original_model: Optional[str],
    corrected_model: str,
    original_codes: list,
    corrected_codes: list,
    model_conf: Optional[int],
    confirmed_by: Optional[str] = None,
):
    """
    [LOG-001] 記錄使用者修正行為。confirmed_by 為 GMP 稽核欄位。

    注意：original_codes / corrected_codes 此處存純字串（供人閱讀）。
    MEM corrections 存帶 conf 的 dict 格式（供錯誤率計算）。
    勿拿 LOG 的 codes 欄位做統計，應使用 MEM。
    """
    _write({
        "event": "correction",
        "level": "INFO",
        "scan_id": scan_id,
        "original_model": original_model,
        "corrected_model": corrected_model,
        "original_codes": original_codes,
        "corrected_codes": corrected_codes,
        "model_conf": model_conf,
        "confirmed_by": confirmed_by,
    })


def log_alert(alert_code: str, level: str, detail: dict):
    """[LOG-001] 記錄警報觸發事件（供 ALERT 層呼叫）。"""
    _write({
        "event": "alert",
        "level": level,
        "alert_code": alert_code,
        **detail,
    })


def log_validation(val_code: str, triggered: bool, detail: dict):
    """[LOG-001] 記錄二次驗證觸發事件（供 VAL 層呼叫）。"""
    _write({
        "event": "validation",
        "level": "WARN" if triggered else "INFO",
        "val_code": val_code,
        "triggered": triggered,
        **detail,
    })


def load_logs(limit: int = 100, event: Optional[str] = None) -> list:
    """讀取最近的日誌，可依 event 類型過濾。"""
    path = _today_log_path()
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
        if event:
            records = [r for r in records if r.get("event") == event]
        return records[-limit:]
    except Exception:
        return []


# ── 內部工具 ─────────────────────────────────────────────────────────────────

def _use_supabase() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


def _write(record: dict):
    record["timestamp"] = datetime.now(timezone.utc).isoformat()

    if _use_supabase():
        try:
            import urllib.request
            base = os.environ["SUPABASE_URL"].rstrip("/")
            key = os.environ["SUPABASE_KEY"]
            row = {
                "event":     record.get("event"),
                "level":     record.get("level"),
                "scan_id":   record.get("scan_id") or record.get("extra", {}).get("scan_id"),
                "model":     record.get("model"),
                "model_conf": record.get("model_conf"),
                "data":      json.dumps({k: v for k, v in record.items()
                                         if k not in ("event", "level", "scan_id", "model", "model_conf")}),
            }
            data = json.dumps(row).encode()
            req = urllib.request.Request(
                f"{base}/rest/v1/ai_logs",
                data=data,
                headers={"apikey": key, "Authorization": f"Bearer {key}",
                         "Content-Type": "application/json", "Prefer": "return=minimal"},
                method="POST",
            )
            urllib.request.urlopen(req)
        except Exception as e:
            # 寫入失敗不可中斷主流程（LOG 層是輔助記錄，不是核心功能），
            # 但完全吞掉例外會讓「ai_logs 表結構跟預期不符」跟「網路
            # 抖動」都靜默變成一樣的「什麼都沒發生」，沒有任何線索可查
            # ——同 storage.py ImportSnapshotStore.save_snapshot() 的
            # 既有修法：印一行 stderr，不中斷，但至少留下痕跡。
            import sys as _sys
            print(f"[ai_logger] _write 寫入 ai_logs 失敗（不影響主流程）："
                  f"{type(e).__name__}: {e}", file=_sys.stderr)
        return

    path = _today_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            records = []
        records.append(record)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _today_log_path() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"{today}.json"
