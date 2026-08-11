"""
AI 參數可配置化（CFG 層）。

標籤系統：
  CFG-001  所有 AI 相關參數集中管理

所有閾值、保留期限、行為開關都在這裡定義。
其他模組從這裡讀取，不自己寫死。
環境變數可覆寫任何設定。
"""

import os

# ── CFG-001：POST 層參數 ─────────────────────────────────────────────────────

POST = {
    # [POST-002] 信心度門檻
    "conf_threshold": int(os.environ.get("AI_CONF_THRESHOLD", "70")),

    # [POST-003] 機種不在白名單但信心極高時放行的門檻
    "model_bypass_conf": int(os.environ.get("AI_MODEL_BYPASS_CONF", "95")),

    # [POST-001] 代碼位數（補零）
    "code_pad": int(os.environ.get("AI_CODE_PAD", "4")),
}

# [CFG-001] Analyzer 邊界校準（CONF_PROFILE）
# 不同模型的信心度數字語意不同，這裡定義每個 analyzer 的校準邊界。
# 換模型時只改這裡，POST / MEM / VAL 的業務門檻不用動。
#
# 格式：{ "analyzer_name": { "high": int, "bypass": int, "pass": int } }
#   high   → tier=success_high 門檻（≥ 此值才記為高信心）
#   bypass → 白名單外機種放行門檻（≥ 此值才 ERR_MODEL_BYPASS，安全方向，應 ≥ high）
#   pass   → 代碼過濾門檻（≥ 此值放行，< 此值 ERR_LOW_CONF）
#
# 若 analyzer 不在此表，回退到 POST / MEM 的固定值。
CONF_PROFILE: dict = {
    "gemini": {"high": 90, "bypass": 95, "pass": 70},
    "local":  {"high": 85, "bypass": 92, "pass": 65},
}

# ── CFG-001：MEM 層參數 ─────────────────────────────────────────────────────

MEM = {
    # [MEM-001] 保留天數分級（對應 ai_memory.RETENTION）
    "retain_success_high":   int(os.environ.get("MEM_RETAIN_SUCCESS_HIGH",   "365")),
    "retain_success":        int(os.environ.get("MEM_RETAIN_SUCCESS",         "90")),
    "retain_low_confidence": int(os.environ.get("MEM_RETAIN_LOW_CONF",        "30")),
    "retain_no_alarm":       int(os.environ.get("MEM_RETAIN_NO_ALARM",         "7")),
    "retain_failure":        int(os.environ.get("MEM_RETAIN_FAILURE",         "30")),
    "retain_corrected":      int(os.environ.get("MEM_RETAIN_CORRECTED",     "9999")),
    "retain_confirmed":      int(os.environ.get("MEM_RETAIN_CONFIRMED",     "9999")),

    # tier 判斷門檻（ai_memory 從這裡讀，不再寫死）
    "high_conf": int(os.environ.get("MEM_HIGH_CONF", "90")),
    "min_conf":  int(os.environ.get("MEM_MIN_CONF",  "70")),
}

# ── CFG-001：VAL 層參數 ─────────────────────────────────────────────────────

VAL = {
    # [VAL-001] 灰色地帶信心度範圍（此區間觸發二次確認）
    # 注意：grey_zone_high 必須 > POST.conf_threshold（預設 70），否則永遠不觸發
    # 因為 conf < 70 時 needs_model_selection=True，_check_grey_zone 直接 return []
    "grey_zone_low":  int(os.environ.get("VAL_GREY_LOW",  "70")),
    "grey_zone_high": int(os.environ.get("VAL_GREY_HIGH", "85")),

    # 與歷史不符時是否觸發二次確認
    "check_history_mismatch": os.environ.get("VAL_CHECK_HISTORY", "true").lower() == "true",
}

# ── CFG-001：ALERT 層參數 ───────────────────────────────────────────────────

ALERT = {
    # [ALERT-001] 連續低信心幾次才升級警報
    "consecutive_low_conf_limit": int(os.environ.get("ALERT_CONSEC_LOW", "3")),

    # 格式錯誤比例超過多少觸發警報（0~1）
    "format_error_ratio": float(os.environ.get("ALERT_FORMAT_ERR_RATIO", "0.5")),

    # 各等級是否阻擋流程
    "level_low_block":    os.environ.get("ALERT_LOW_BLOCK",    "false").lower() == "true",
    "level_medium_block": os.environ.get("ALERT_MEDIUM_BLOCK", "false").lower() == "true",
    "level_high_block":   os.environ.get("ALERT_HIGH_BLOCK",   "true").lower()  == "true",

    # 同一警報冷卻秒數（避免重複發）
    "cooldown_seconds": int(os.environ.get("ALERT_COOLDOWN_SEC", "600")),
}

# ── CFG-001：LOG 層參數 ─────────────────────────────────────────────────────

LOG = {
    "retain_days": int(os.environ.get("AI_LOG_RETAIN_DAYS", "90")),
}
