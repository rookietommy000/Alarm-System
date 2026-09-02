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

import sys
import time
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


_variant_translations_cache: Optional[dict] = None


def _load_variant_translations() -> dict:
    """variant 中文翻譯對照表（拍照辨識故障修復 Q3）：mf4c 的 variant
    是完整英文語意描述（非短編號），現場人員不一定看得懂，顧問裁決
    先用 AI 批次翻譯一版、標記「待人工校對」狀態，之後由人工校對。
    找不到翻譯（新代碼或翻譯檔案缺筆）時回 None，前端只顯示英文原文，
    不強行拼湊看起來像翻譯但其實沒有的文字——這跟 load_valid_models()
    的 fail-closed 精神一致：缺翻譯要讓呼叫端知道「沒有」，不能用空
    字串或猜測值掩蓋。快取整份資料在記憶體，103 筆資料量小，不需要
    TTL 過期機制，人工校對更新後重啟服務即可生效。

    翻譯資料存放位置本機/測試模式讀 data/variant_translations.json，
    production 走 Supabase 的 variant_translations 表（variant_translations
    這批資料會持續增加/校對，改存 DB 才不用每次校對都走 commit+部署
    流程，見 008_add_variant_translations.sql）——雙軌邏輯已收斂進
    storage.VariantTranslationStore，這裡不重複判斷 _use_supabase()。"""
    global _variant_translations_cache
    if _variant_translations_cache is not None:
        return _variant_translations_cache
    from storage import variant_translation_store
    _variant_translations_cache = variant_translation_store.load_all()
    return _variant_translations_cache


def _resolve_alarm_codes(alarms: list, department: Optional[str], model: Optional[str]) -> list:
    """拍照辨識故障修復（Q2）：AI 辨識+正規化後的 code 只是猜測值，且
    完全不知道 variant 概念（圖片辨識看不出閥門位置這類差異）。這裡用
    正規化過的 code 反查 DB 實際存在的紀錄，把猜測值換成資料庫裡真正
    存在的 code/variant，前端不用再自己查一次 DB、自己做一次 normalize
    比對（原本 frontend index.html 的 normalize 是碰撞更嚴重的第二個
    問題根源，見 QATEST01/0001/ZZC001 誤判事故，這次直接移除）。

    每筆 alarm 補上：
      db_matched: bool                  是否在 DB 找到至少一筆
      variant: str | None                唯一命中時的實際 variant，多筆或
                                          零筆時為 None
      candidates: list[dict] | None      多筆命中（同 code 不同 variant）
                                          時的完整候選列表，每筆候選帶
                                          variant_zh/translation_status
                                          （見 _load_variant_translations()），
                                          交給前端 Q3 的分組選擇 UI；
                                          否則為 None

    找不到（0 筆）時保留 AI 原始猜測值不變，只標記 db_matched=False——
    不在這裡做模糊比對候選，那是分開的、需要使用者確認的獨立步驟
    （見 ai_rules._normalize_code 的說明），現在只做精確比對。"""
    if model is None:
        return [{**a, "db_matched": False, "variant": None, "candidates": None} for a in alarms]

    from storage import alarms_store
    translations = _load_variant_translations()

    def _with_translation(row: dict) -> dict:
        variant_text = row.get("variant") or ""
        entry = translations.get(variant_text)
        return {
            "variant": variant_text,
            "code": row["code"],
            "variant_zh": entry["zh"] if entry else None,
            "translation_status": entry["status"] if entry else None,
        }

    resolved = []
    for alarm in alarms:
        rows = alarms_store.find_by_code(department, model, alarm["code"])
        if not rows:
            resolved.append({**alarm, "db_matched": False, "variant": None, "candidates": None})
        elif len(rows) == 1:
            resolved.append({
                **alarm, "code": rows[0]["code"], "db_matched": True,
                "variant": rows[0].get("variant"), "candidates": None,
            })
        else:
            resolved.append({
                **alarm, "db_matched": True, "variant": None,
                "candidates": [_with_translation(r) for r in rows],
            })
    return resolved


def _codes_only(items: list) -> list:
    """
    統一轉成純字串 code 列表，供 LOG 使用。
    相容新格式 [{"code": ..., "conf": ...}] 和舊格式 ["0514"]。
    """
    return [i["code"] if isinstance(i, dict) else i for i in (items or [])]


def run_pipeline(image_b64: str, mime_type: str = "image/jpeg", known_model: str = None,
                  *, department: Optional[str]) -> dict:
    """
    執行完整 AI 分析流程。

    department: 寫入 ai_scans.department，且 MEM 層的歷史/候選查詢限縮在此
    部門範圍內（PLAN 3.5 節：不限縮會讓 A 部門的辨識歷史混進 B 部門候選建議
    清單，同時是正確性問題與資料洩漏問題）。None 為明確選擇，非預設值
    （PLAN 3.6/4.8 節）。

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

    結構性保證（拍照辨識故障修復 PR-2）：不管哪個步驟（analyzer/
    valid_models/post_rule/verify...）失敗，MEM 層（record_scan 寫
    ai_scans）跟 LOG 層（log_scan 寫 ai_logs）都會寫入一筆記錄，不會
    因為例外從中途冒出去就完全沒有任何痕跡——修復前只有 analyzer 這
    一步失敗有獨立 try 保護，其餘步驟（POST/VAL/ALERT）失敗時例外會
    一路冒到 app.py 變成 500，MEM/LOG 兩邊都不會寫，這次故障排查時
    完全查不到任何線索就是這個結構性缺口。

    做法：用 try/except Exception + re-raise 包住整個流程（不是
    try/finally——這裡只需要攔 Exception，不需要連 BaseException
    也一起處理，用 finally 字面上會讓人誤以為連 KeyboardInterrupt
    這類也會觸發下面的補寫，實際上不會）。任何步驟失敗都一律用
    model=None、alarms=[]、rejected_alarms=[] 呼叫 record_scan()——不是
    「盡量帶上已經算到一半的部分結果」，因為部分結果可能來自尚未通過
    完整驗證的中間狀態（例如 POST 層已經算出 model，但 VAL 層才失敗，
    這次掃描仍然沒有走完整條流程，不該被記成「成功辨識」）。用固定的
    失敗參數呼叫 record_scan()，讓它內部依這組參數自動算出 tier=failure
    （見 ai_memory.py:124-139），不自己發明另一套 tier 判斷邏輯，避免
    兩個地方各自維護一份「失敗時 tier 該是什麼」的規則、之後容易改一邊
    忘記改另一邊。只有 analyzer/usage 這些「已經確定拿到、不隨後續步驟
    成敗而變動」的追溯資訊才保留下來一併記錄。
    """
    analyzer = get_analyzer()
    _state = {
        "analyzer_meta": {
            "name": getattr(analyzer, "analyzer_name", "unknown"),
            "model": getattr(analyzer, "analyzer_model", "unknown"),
            "prompt_version": None,
        },
        "usage_with_outcome": None,
        "logged": False,  # 正常路徑（步驟 6）已經寫過 LOG 就不再重複補寫
    }

    def _write_failure_record(exc: Exception) -> dict:
        """任何步驟失敗時的統一收尾：MEM 先寫（拿到 scan_id），LOG 再寫。
        record_scan() 本身若寫入失敗，不能整個吞掉（要大聲記 log），但
        也不能讓「記錄失敗」蓋過原本 pipeline 的例外——原本的例外還是
        要繼續往外拋，讓 app.py 的既有 500 處理接手。"""
        try:
            scan_record = record_scan(
                None, None, [], [],
                source="ai", analyzer=_state["analyzer_meta"], department=department,
            )
            scan_id = scan_record["scan_id"]
        except Exception as mem_exc:
            import logging
            logging.getLogger("app").error(
                f"run_pipeline: record_scan() 寫入失敗（例外仍會繼續往外拋）："
                f"{type(mem_exc).__name__}: {mem_exc}", exc_info=mem_exc,
            )
            scan_id = None

        log_scan(
            level="ERROR",
            model=None,
            model_conf=None,
            alarms=[],
            rejected_alarms=[],
            model_warning=None,
            needs_model_selection=True,
            analyzer=_state["analyzer_meta"],
            usage=_state["usage_with_outcome"],
            extra={"pipeline_error": str(exc), "scan_id": scan_id},
        )
        _state["logged"] = True
        return {
            "scan_id":             scan_id,
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
            "analyzer":            _state["analyzer_meta"],
            "pipeline_error":      str(exc),
        }

    def _log_timing(segment: str, elapsed_ms: float) -> None:
        # 比照 storage.py 的 throttle_timing 格式（同一套可觀測性慣例）。
        # 純觀測用，不影響任何行為邏輯——29 秒的拍照辨識耗時目前完全
        # 沒有分段數據，只能靠推測，這裡先量出五段真實分佈再決定要不要
        # 動手優化（PLAN 拍照辨識效能優化：先量測，不要沒有數據就猜）。
        print(f"pipeline_timing[{segment}]: {elapsed_ms:.0f}ms", file=sys.stderr)

    try:
        # 1. Analyzer 辨識
        t0 = time.monotonic()
        try:
            raw = analyzer.analyze(image_b64, mime_type)
        except Exception as exc:
            _log_timing("analyzer", (time.monotonic() - t0) * 1000)
            # 解析回應失敗/timeout/http_error/safety 這幾種情況，
            # GeminiAnalyzer 會把 usage_outcome 分類跟已讀到的 usage
            # 資料（若有）掛在例外上（見 ai_analyzer.py），這裡撈出來
            # 一併記錄——outcome 標示這次呼叫的結果類型，即使 usage
            # 資料本身是 None（timeout/http_error 沒有任何回應可讀），
            # outcome 仍要被記下來，未來才能用實際 Google 帳單反推各類
            # 情況的計費誤差，不用現在就查出確切計費規則。
            exc_usage = getattr(exc, "usage", None)
            _state["usage_with_outcome"] = {**(exc_usage or {}), "outcome": getattr(exc, "usage_outcome", "unknown")}
            return _write_failure_record(exc)
        _log_timing("analyzer", (time.monotonic() - t0) * 1000)

        _state["analyzer_meta"] = raw.get("analyzer") or _state["analyzer_meta"]
        raw_usage = raw.get("usage")
        _state["usage_with_outcome"] = (
            {**raw_usage, "outcome": raw.get("usage_outcome", "ok")} if raw_usage is not None else None
        )

        # 2. POST 層（若操作員已選機種，覆蓋 AI 辨識結果）
        valid_models = load_valid_models()
        if known_model:
            raw["model"] = known_model
            raw["model_conf"] = 100
        result = apply_post_rules(raw, valid_models, department=department)

        # 2.5 DB 比對層（拍照辨識故障修復 Q2）：把 POST 層正規化過的
        # code 反查 DB 實際存在的紀錄，換成真正的 code/variant，前端
        # 不用再自己查一次 DB、自己做一次容易碰撞的 normalize 比對。
        t0 = time.monotonic()
        model = result.get("model")
        result["alarms"] = _resolve_alarm_codes(result.get("alarms", []), department, model)
        _log_timing("resolve", (time.monotonic() - t0) * 1000)

        # 3. VAL 層
        t0 = time.monotonic()
        corrections = _load_records("corrections", model, department=department) if model else []
        val = check_validation(result, corrections)
        _log_timing("val", (time.monotonic() - t0) * 1000)

        # 4. MEM 層（先寫，讓 ALERT 能算到「本次」）
        t0 = time.monotonic()
        scan_record = record_scan(
            model=model,
            model_conf=result.get("model_conf"),
            alarms=result.get("alarms", []),
            rejected_alarms=result.get("rejected_alarms", []),
            analyzer=raw.get("analyzer"),
            department=department,
        )
        _log_timing("mem", (time.monotonic() - t0) * 1000)

        # 5. ALERT 層（含本次 scan_record）
        t0 = time.monotonic()
        scan_history = _load_records("history", model or "_unknown", department=department)
        alerts = check_alerts(result, scan_history)
        _log_timing("alert", (time.monotonic() - t0) * 1000)

        # 6. LOG 層
        t0 = time.monotonic()
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
            usage=_state["usage_with_outcome"],
            extra={"scan_id": scan_record["scan_id"], "alerts": [a["code"] for a in alerts], "val_triggered": val["needs_reconfirm"]},
        )
        _log_timing("log", (time.monotonic() - t0) * 1000)
        _state["logged"] = True

        return {
            **result,
            "scan_id":   scan_record["scan_id"],
            "validation": val,
            "alerts":    alerts,
            "tier":      scan_record["tier"],
            "analyzer":  analyzer_meta,
        }
    except Exception as exc:
        if _state["logged"]:
            # 已經正常走完步驟 6（例如 alerts/val 之後、return 之前的
            # 純資料組裝萬一意外拋錯——理論上不會，但 finally 的設計
            # 前提就是不能預設「後面一定不出事」），不要再補寫第二筆，
            # 否則同一次 scan 會產生兩筆記錄，混淆真正的次數統計。
            raise
        _write_failure_record(exc)
        raise


def run_confirmation(
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
    操作員確認 AI 結果正確（未修改），補寫 source="confirmed" 記錄。

    scan_id:         原始 AI scan 的 id（確認頁帶回來）
    model:           操作員確認的最終機種
    alarms:          [{"code": str, "conf": int | None}, ...]（從原始 scan 帶回，含 conf）
    model_conf:      AI 原始的機種信心度
    original_model:  AI 原始辨識的機種（供機種層錯誤率計算）
    original_analyzer: 原始 analyzer metadata
    confirmed_by:    操作者身分（GMP 稽核用）
    department:      見 run_pipeline() 的說明（None 為明確選擇，非預設值）
    """
    rec = mem_record_confirmation(
        scan_id=scan_id,
        model=model,
        alarms=alarms,
        model_conf=model_conf,
        original_model=original_model,
        original_analyzer=original_analyzer,
        confirmed_by=confirmed_by,
        department=department,
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
    *,
    department: Optional[str],
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
    department:      見 run_pipeline() 的說明（None 為明確選擇，非預設值）
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
        department=department,
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
