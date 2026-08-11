"""
AI 分析主流程（Pipeline）。

所有 AI 層的串接邏輯集中在這裡，app.py 只需呼叫這三個入口：

  run_pipeline()     → 純 AI 分析（source=ai）
  run_confirmation() → 操作員確認 AI 正確（source=confirmed）
  run_correction()   → 操作員修正 AI 錯誤（source=corrected）

流程（run_pipeline）：
  1. Gemini 辨識（ai_analyzer）
  2. POST 層：正規化、過濾、白名單（ai_rules）
  3. VAL 層：二次確認判斷（ai_validation）
  4. MEM 層：記錄本次掃描（ai_memory）
  5. ALERT 層：警報觸發（ai_alert）
  6. LOG 層：記錄決策路徑（ai_logger）
"""

from typing import Optional

from .ai_analyzer import get_analyzer
from .ai_rules import apply_post_rules, load_valid_models
from .ai_memory import (
    record_scan,
    record_confirmation as mem_record_confirmation,
    record_correction as mem_record_correction,
    _load_records,
)
from .ai_alert import check_alerts
from .ai_validation import check_validation
from .ai_logger import log_scan, log_confirmation, log_correction


def _codes_only(items: list) -> list:
    """
    統一轉成純字串 code 列表，供 LOG 使用。
    相容新格式 [{"code": ..., "conf": ...}] 和舊格式 ["0514"]。
    """
    return [i["code"] if isinstance(i, dict) else i for i in (items or [])]


def run_pipeline(image_b64: str, mime_type: str = "image/jpeg", known_model: str = None) -> dict:
    """
    執行完整 AI 分析流程。

    回傳：
      {
        scan_id,
        model, model_conf, model_valid, model_warning,
        alarms, rejected_alarms, needs_model_selection,
        validation: { needs_reconfirm, reasons },
        alerts: [{ code, level, message, block }],
        tier,
        analyzer: { name, model, prompt_version },
      }

    Analyzer 失敗時回傳 pipeline_error，MEM / LOG 仍會寫入，不會全部 500。
    """
    analyzer = get_analyzer()

    # 1. Analyzer 辨識（獨立 try，失敗時降級回傳，不影響 MEM/LOG）
    try:
        raw = analyzer.analyze(image_b64, mime_type)
    except Exception as exc:
        analyzer_meta = {
            "name": getattr(analyzer, "analyzer_name", "unknown"),
            "model": getattr(analyzer, "analyzer_model", "unknown"),
            "prompt_version": None,
        }
        # MEM 也要記（連續斷線才會觸發 ALERT_LOW_CONF）
        scan_record = record_scan(None, None, [], [], source="ai", analyzer=analyzer_meta)
        log_scan(
            level="ERROR",
            model=None,
            model_conf=None,
            alarms=[],
            rejected_alarms=[],
            model_warning=None,
            needs_model_selection=True,
            analyzer=analyzer_meta,
            extra={"pipeline_error": str(exc), "scan_id": scan_record["scan_id"]},
        )
        return {
            "scan_id":             scan_record["scan_id"],
            "model":               None,
            "model_conf":          None,
            "model_valid":         False,
            "model_warning":       None,
            "alarms":              [],
            "rejected_alarms":     [],
            "needs_model_selection": True,
            "validation":          {"needs_reconfirm": False, "reasons": []},
            "alerts":              [],
            "tier":                "failure",
            "analyzer":            analyzer_meta,
            "pipeline_error":      str(exc),
        }

    # 2. POST 層（若操作員已選機種，覆蓋 AI 辨識結果）
    valid_models = load_valid_models()
    if known_model:
        raw["model"] = known_model
        raw["model_conf"] = 100
    result = apply_post_rules(raw, valid_models)

    # 3. VAL 層
    model = result.get("model")
    corrections = _load_records("corrections", model) if model else []
    val = check_validation(result, corrections)

    # 4. MEM 層（先寫，讓 ALERT 能算到「本次」）
    scan_record = record_scan(
        model=model,
        model_conf=result.get("model_conf"),
        alarms=result.get("alarms", []),
        rejected_alarms=result.get("rejected_alarms", []),
        analyzer=raw.get("analyzer"),
    )

    # 5. ALERT 層（含本次 scan_record）
    scan_history = _load_records("history", model or "_unknown")
    alerts = check_alerts(result, scan_history)

    # 6. LOG 層
    analyzer_meta = raw.get("analyzer")
    has_block = any(a.get("block") for a in alerts)
    log_scan(
        level="ERROR" if has_block else ("WARN" if alerts or val["needs_reconfirm"] else "INFO"),
        model=model,
        model_conf=result.get("model_conf"),
        alarms=result.get("alarms", []),
        rejected_alarms=result.get("rejected_alarms", []),
        model_warning=result.get("model_warning"),
        needs_model_selection=result.get("needs_model_selection", False),
        analyzer=analyzer_meta,
        extra={"scan_id": scan_record["scan_id"], "alerts": [a["code"] for a in alerts], "val_triggered": val["needs_reconfirm"]},
    )

    return {
        **result,
        "scan_id":   scan_record["scan_id"],
        "validation": val,
        "alerts":    alerts,
        "tier":      scan_record["tier"],
        "analyzer":  analyzer_meta,
    }


def run_confirmation(
    scan_id: str,
    model: str,
    alarms: list,
    model_conf: Optional[int],
    original_model: Optional[str],
    original_analyzer: Optional[dict],
    confirmed_by: str,
) -> dict:
    """
    操作員確認 AI 結果正確（未修改），補寫 source="confirmed" 記錄。

    scan_id:         原始 AI scan 的 id（確認頁帶回來）
    model:           操作員確認的最終機種
    alarms:          [{"code": str, "conf": int | None}, ...]（從原始 scan 帶回，含 conf）
    model_conf:      AI 原始的機種信心度
    original_model:  AI 原始辨識的機種（供機種層錯誤率計算）
    original_analyzer: 原始 analyzer metadata
    confirmed_by:    操作者身分（GMP 稽核用）
    """
    rec = mem_record_confirmation(
        scan_id=scan_id,
        model=model,
        alarms=alarms,
        model_conf=model_conf,
        original_model=original_model,
        original_analyzer=original_analyzer,
        confirmed_by=confirmed_by,
    )
    log_confirmation(
        scan_id=scan_id,
        model=model,
        original_model=original_model,
        alarms=alarms,
        model_conf=model_conf,
        confirmed_by=confirmed_by,
    )
    return {"ok": True, "scan_id": scan_id, "tier": rec["tier"]}


def run_correction(
    scan_id: str,
    original_model: Optional[str],
    corrected_model: str,
    original_codes: list,
    corrected_codes: list,
    model_conf: Optional[int],
    original_analyzer: Optional[dict] = None,
    confirmed_by: Optional[str] = None,
) -> dict:
    """
    操作員修正 AI 辨識錯誤，補寫 source="corrected" 記錄（MEM-002 + LOG）。

    scan_id:         原始 AI scan 的 id（確認頁帶回來）
    original_model:  AI 原始辨識的機種
    corrected_model: 操作員選擇的正確機種
    original_codes:  [{"code": str, "conf": int | None}, ...]（AI 原始，含 conf）
    corrected_codes: [{"code": str, "conf": int | None}, ...]（操作員確認，含 conf）
    original_analyzer: 原始 analyzer metadata（計算 per-model 錯誤率用）
    confirmed_by:    操作者身分（GMP 稽核用）
    """
    rec = mem_record_correction(
        scan_id=scan_id,
        original_model=original_model,
        corrected_model=corrected_model,
        original_codes=original_codes,
        corrected_codes=corrected_codes,
        model_conf=model_conf,
        original_analyzer=original_analyzer,
        confirmed_by=confirmed_by,
    )
    log_correction(
        scan_id=scan_id,
        original_model=original_model,
        corrected_model=corrected_model,
        original_codes=_codes_only(original_codes),
        corrected_codes=_codes_only(corrected_codes),
        model_conf=model_conf,
        confirmed_by=confirmed_by,
    )
    return {"ok": True, "scan_id": scan_id, "tier": rec["tier"]}
