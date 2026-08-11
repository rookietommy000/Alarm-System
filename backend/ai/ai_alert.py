"""
AI 警報觸發與升級機制（ALERT 層）。

標籤系統：
  ALERT-001  警報觸發條件與升級機制

警報等級（可在 ai_config.ALERT 調整）：
  LOW     提示性，不影響流程，block=False
  MEDIUM  需要注意，記錄並顯示警示，block=False
  HIGH    需要人工介入，block=True

警報代碼：
  ALERT_FORMAT_ERR     格式錯誤比例過高
  ALERT_UNKNOWN_MODEL  機種不在白名單
  ALERT_LOW_CONF       連續低信心
  ALERT_BYPASS         機種白名單外但放行

scan_history 每筆格式（來自 ai_memory.record_scan 回傳值）：
  {
    "tier": "success_high" | "success" | "low_confidence" | "no_alarm" | "failure" | "corrected",
    "alarms": [{"code": str, "conf": int | None}],
    "model": str | None,
    "scanned_at": ISO8601 str,
  }
"""

import time
from .ai_config import ALERT as CFG
from .ai_logger import log_alert

# ── 冷卻快取（in-memory，重啟後重置）───────────────────────────────────────
# key: (alert_code, model)，value: 最後觸發的 timestamp
_cooldown_cache: dict = {}


# ── 等級設定（從 CFG 讀，預設值內建）───────────────────────────────────────
_LEVEL_BLOCK = {
    "LOW":    CFG.get("level_low_block",    False),
    "MEDIUM": CFG.get("level_medium_block", False),
    "HIGH":   CFG.get("level_high_block",   True),
}

_COOLDOWN_SECONDS = CFG.get("cooldown_seconds", 600)  # 預設 10 分鐘


# ── ALERT-001：主入口 ────────────────────────────────────────────────────────

def check_alerts(post_result: dict, scan_history: list = []) -> list:
    """
    [ALERT-001] 根據 POST 結果和歷史掃描記錄，回傳需要觸發的警報列表。

    post_result: apply_post_rules() 的回傳值
    scan_history: 最近幾次 record_scan() 的回傳值列表

    回傳:
      [{
        "code":    "ALERT_xxx",
        "level":   "LOW | MEDIUM | HIGH",
        "message": "說明",
        "block":   bool,   # True 表示需要人工介入才能繼續
      }]
    """
    model = post_result.get("model") or "_unknown"
    raw_alerts = []

    # [ALERT-001] 格式錯誤比例過高
    raw_alerts += _check_format_error_ratio(post_result)

    # [ALERT-001] 機種辨識失敗 / 不在白名單
    raw_alerts += _check_unknown_model(post_result)

    # [ALERT-001] 機種白名單外但放行（bypass）
    raw_alerts += _check_model_bypass(post_result)

    # [ALERT-001] 連續低信心
    raw_alerts += _check_consecutive_low_conf(scan_history)

    # 套用冷卻機制 + 補 block 欄位
    # 冷卻只抑制通知/log，block=True 的警報照常回傳（避免裸奔）
    alerts = []
    now = time.time()
    for a in raw_alerts:
        block = _LEVEL_BLOCK.get(a["level"], False)
        cache_key = (a["code"], model)
        last = _cooldown_cache.get(cache_key, 0)
        in_cooldown = now - last < _COOLDOWN_SECONDS

        if in_cooldown and not block:
            continue  # 冷卻中且不阻擋流程，安全跳過

        alert = {**a, "block": block}
        alerts.append(alert)

        if not in_cooldown:
            # 只在真正通知時才刷新時間戳，保持稽核計數準確
            _cooldown_cache[cache_key] = now
            log_alert(alert["code"], alert["level"], {
                "model": model,
                "message": alert["message"],
                "block": alert["block"],
            })

    return alerts


# ── 各條件判斷 ───────────────────────────────────────────────────────────────

def _check_format_error_ratio(post_result: dict) -> list:
    """
    格式錯誤的代碼佔總辨識代碼比例過高時觸發。
    最小樣本數 2，避免單次只拍到 1 個就誤報。
    """
    alarms = post_result.get("alarms", [])
    rejected = post_result.get("rejected_alarms", [])
    format_errors = [r for r in rejected if r.get("error") == "ERR_CODE_FORMAT"]

    total = len(alarms) + len(rejected)
    if total < 2:
        return []

    ratio = len(format_errors) / total
    if ratio >= CFG["format_error_ratio"]:
        return [{
            "code": "ALERT_FORMAT_ERR",
            "level": "MEDIUM",
            "message": f"格式錯誤代碼佔比 {ratio:.0%}（{len(format_errors)}/{total}），可能拍到非警報畫面",
        }]
    return []


def _check_unknown_model(post_result: dict) -> list:
    """
    機種辨識問題分兩種：
    - 完全辨識不出（model=None）→ LOW，請使用者手選
    - 辨識出但不在白名單且未達 bypass 門檻 → MEDIUM
    """
    model = post_result.get("model")
    model_valid = post_result.get("model_valid")
    model_warning = post_result.get("model_warning")

    if not model:
        return [{
            "code": "ALERT_UNKNOWN_MODEL",
            "level": "LOW",
            "message": "無法辨識機種，需要使用者手動選擇",
        }]

    if not model_valid and model_warning != "ERR_MODEL_BYPASS":
        return [{
            "code": "ALERT_UNKNOWN_MODEL",
            "level": "MEDIUM",
            "message": f"機種 {model} 辨識出但不在白名單，且信心度不足以放行",
        }]

    return []


def _check_model_bypass(post_result: dict) -> list:
    """機種不在白名單但因信心極高而放行。"""
    if post_result.get("model_warning") == "ERR_MODEL_BYPASS":
        return [{
            "code": "ALERT_BYPASS",
            "level": "MEDIUM",
            "message": f"機種 {post_result.get('model')} 不在白名單但信心度極高，已放行，請確認是否需要加入白名單",
        }]
    return []


def _check_consecutive_low_conf(scan_history: list) -> list:
    """
    最近連續幾次都是 low_confidence 或 failure，觸發升級警報。
    no_alarm（正常機台）和 corrected 不計入。

    scan_history 每筆需包含：
      tier: "success_high"|"success"|"low_confidence"|"no_alarm"|"failure"|"corrected"
    """
    limit = CFG["consecutive_low_conf_limit"]
    if len(scan_history) < limit:
        return []

    recent = scan_history[-limit:]

    low_tiers = {"low_confidence", "failure"}
    # no_alarm = 機台正常沒警報，不算辨識失敗，不納入連續低信心計算

    if all(r.get("tier") in low_tiers for r in recent):
        return [{
            "code": "ALERT_LOW_CONF",
            "level": "HIGH",
            "message": f"連續 {limit} 次辨識失敗或低信心，請確認拍照環境或聯絡管理員",
        }]
    return []
