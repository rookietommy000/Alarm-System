"""
AI 多重驗證 / 二次確認（VAL 層）。

標籤系統：
  VAL-001  多重驗證 / 二次確認

觸發條件：
  - 信心度落在灰色地帶（VAL_GREY_LOW ~ VAL_GREY_HIGH）
  - 機種與歷史記錄不符
  - 代碼從未在該機種出現過

結果：
  needs_reconfirm: True  → 前端顯示人工確認頁
  needs_reconfirm: False → 直接放行
"""

from .ai_config import VAL as CFG, CONF_PROFILE
from .ai_logger import log_validation


# ── VAL-001：觸發判斷 ────────────────────────────────────────────────────────

def check_validation(post_result: dict, corrections: list = []) -> dict:
    """
    [VAL-001] 判斷是否需要觸發二次確認。

    post_result: apply_post_rules() 的回傳值
    corrections: load_corrections() 的修正歷史

    回傳：
      {
        "needs_reconfirm": bool,
        "reasons": ["觸發原因說明", ...]
      }
    """
    reasons = []

    # [VAL-001] 信心度灰色地帶
    reasons += _check_grey_zone(post_result)

    # [VAL-001] 與修正歷史不符
    if CFG["check_history_mismatch"]:
        reasons += _check_history_mismatch(post_result, corrections)

    triggered = len(reasons) > 0
    log_validation("VAL-001", triggered, {
        "model": post_result.get("model"),
        "reasons": reasons,
    })

    return {
        "needs_reconfirm": triggered,
        "reasons": reasons,
    }


# ── 各條件判斷 ───────────────────────────────────────────────────────────────

def _check_grey_zone(post_result: dict) -> list:
    """
    [VAL-001] 機種信心度落在灰色地帶（pass ~ high 之間）。
    下限從 CONF_PROFILE 取 pass（和 POST 層過濾用同一個值），
    上限從 CFG grey_zone_high 取（可獨立調整）。
    已需要手選（needs_model_selection）時不重複觸發。
    conf=None 時跳過（語意不明）。
    """
    conf = post_result.get("model_conf")

    if post_result.get("needs_model_selection"):
        return []
    if conf is None:
        return []

    # 灰色地帶下限跟 POST 的 pass 對齊（依 analyzer 校準）
    analyzer_name = (post_result.get("analyzer") or {}).get("name")
    profile = CONF_PROFILE.get(analyzer_name or "", {})
    low = profile.get("pass", CFG["grey_zone_low"])
    high = CFG["grey_zone_high"]

    if low <= conf < high:
        return [f"機種信心度 {conf}% 落在灰色地帶（{low}%~{high}%），建議確認"]
    return []


def _extract_codes(items) -> set:
    """相容新舊格式：[{'code': ..., 'conf': ...}, ...] 或 ['0514', ...]。"""
    return {i["code"] if isinstance(i, dict) else i for i in (items or [])}


def _check_history_mismatch(post_result: dict, corrections: list) -> list:
    """
    [VAL-001] 這次辨識的代碼，在該機種的修正歷史中曾被改過。
    表示 AI 可能又犯同樣的錯。
    """
    if not corrections:
        return []

    model = post_result.get("model")
    current_codes = {a["code"] for a in post_result.get("alarms", [])}

    reasons = []
    for corr in corrections:
        if corr.get("corrected_model") != model:
            continue
        overlap = current_codes & _extract_codes(corr.get("original_codes"))
        if overlap:
            reasons.append(
                f"代碼 {', '.join(overlap)} 曾在此機種被使用者修正過，請再次確認"
            )
            break

    return reasons
