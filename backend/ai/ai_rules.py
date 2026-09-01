"""
AI 辨識後處理規則集。

標籤系統：
  POST-xxx  辨識後處理（格式、過濾、驗證）
  MEM-xxx   記憶庫（歷史、學習）   ← 保留待議
  BEH-xxx   行為邏輯（流程決策）   ← 待實作

使用方式：
    from ai_rules import apply_post_rules
    result = apply_post_rules(gemini_raw, valid_models)
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from .ai_config import POST as _POST_CFG, CONF_PROFILE

# ── 設定（可透過環境變數覆寫）───────────────────────────────────────────────

# [POST-002] 信心度門檻，低於此值丟棄
CONF_THRESHOLD = _POST_CFG["conf_threshold"]

# [POST-003] 機種信心度極高時即使不在白名單也放行的門檻
MODEL_BYPASS_CONF = _POST_CFG["model_bypass_conf"]

# [POST-001] 代碼補零位數
CODE_PAD = _POST_CFG["code_pad"]

# [POST-001] 各機種的代碼正規化規則（預設：純數字補零4位）
# 格式：{ "機種": { "strip_prefix": "E-", "pad": 4 } }
# 若機種不在此表，套用預設規則
NORMALIZE_RULES: dict = {}


# ── 錯誤碼 ───────────────────────────────────────────────────────────────────

ERR_CODE_FORMAT  = "ERR_CODE_FORMAT"   # [POST-001] 代碼無法解析為合法格式
ERR_LOW_CONF     = "ERR_LOW_CONF"      # [POST-002] 信心度不足
ERR_MODEL_UNKNWN = "ERR_MODEL_UNKNOWN" # [POST-003] 機種不在白名單
ERR_MODEL_BYPASS = "ERR_MODEL_BYPASS"  # [POST-003] 機種不在白名單但信心度極高，警示放行


# ── 主入口 ───────────────────────────────────────────────────────────────────

def _get_pass_threshold(analyzer_name: Optional[str]) -> int:
    """
    [CFG-001] 依 analyzer 的 CONF_PROFILE 取「通過」門檻。
    未設定時回退到 CONF_THRESHOLD。
    """
    profile = CONF_PROFILE.get(analyzer_name or "", {})
    return profile.get("pass", CONF_THRESHOLD)


def _get_bypass_threshold(analyzer_name: Optional[str]) -> int:
    """
    [CFG-001] 依 analyzer 的 CONF_PROFILE 取「白名單外機種放行」門檻。
    注意：取 bypass key，而非 high——兩者語意不同，bypass ≥ high。
    未設定時回退到 MODEL_BYPASS_CONF。
    """
    profile = CONF_PROFILE.get(analyzer_name or "", {})
    return profile.get("bypass", MODEL_BYPASS_CONF)


def apply_post_rules(raw: dict, valid_models: list) -> dict:
    """
    對 Gemini 原始輸出套用所有 POST 規則，回傳乾淨結果。

    raw 格式（來自 ai_analyzer._parse_response）：
      { model, model_conf, alarms: [{code, conf, note}], raw }

    回傳格式：
      {
        model: str | None,
        model_conf: int,
        model_valid: bool,
        model_warning: str | None,      # 有警示時說明原因
        alarms: [{code, conf, note}],
        rejected_alarms: [{code, conf, note, error}],  # 被過濾的代碼及原因
        needs_model_selection: bool
      }
    """
    model = raw.get("model") or None
    raw_conf = raw.get("model_conf")
    # None = 模型未回信心度；沿用 None 語意，不強轉 0，以免掩蓋模型差異
    model_conf = int(raw_conf) if raw_conf is not None else None
    alarms = raw.get("alarms") or []
    analyzer_name = (raw.get("analyzer") or {}).get("name")

    # [POST-001] 代碼格式正規化
    normalized, rejected = _normalize_alarms(alarms, model)

    # [POST-002] 信心度過濾（使用 analyzer 校準後的門檻）
    pass_threshold = _get_pass_threshold(analyzer_name)
    passed, low_conf = _filter_by_conf(normalized, pass_threshold)
    rejected += low_conf

    # [POST-003] 機種白名單驗證（使用 analyzer 校準後的 bypass 門檻）
    bypass_threshold = _get_bypass_threshold(analyzer_name)
    model, model_valid, model_warning = _validate_model(model, model_conf, valid_models, bypass_threshold)

    # model_conf=None 或白名單外 bypass 放行，都須人工確認
    needs_model_selection = (
        (not model_valid)
        or (model_conf is None)
        or (model_conf < pass_threshold)
        or (model_warning == ERR_MODEL_BYPASS)
    )

    return {
        "model": model,
        "model_conf": model_conf,
        "model_valid": model_valid,
        "model_warning": model_warning,
        "alarms": passed,
        "rejected_alarms": rejected,
        "needs_model_selection": needs_model_selection,
        "analyzer": raw.get("analyzer"),   # VAL / MEM 需要這個取 profile
    }


# ── [POST-001] 代碼格式正規化 ────────────────────────────────────────────────

def _normalize_alarms(alarms: list, model: Optional[str]) -> tuple:
    """
    將每個警報代碼正規化。
    回傳 (通過列表, 失敗列表)，失敗列表帶 error 欄位說明原因。
    """
    rule = NORMALIZE_RULES.get(model or "", {})
    passed, failed = [], []

    for alarm in alarms:
        result, err = _normalize_code(alarm, rule)
        if err:
            failed.append({**alarm, "error": err})
        else:
            passed.append(result)

    return passed, failed


def _normalize_code(alarm: dict, rule: dict) -> tuple:
    """
    單一代碼正規化。回傳 (正規化後alarm, 錯誤碼或None)。
    規則：先去掉前綴（如 E-），再提取數字，補零到指定位數。
    """
    code = str(alarm.get("code", "")).strip()
    pad = rule.get("pad", CODE_PAD)  # 機種規則優先，否則用全域 CODE_PAD

    # 去掉機種設定的前綴
    prefix = rule.get("strip_prefix", "")
    if prefix and code.upper().startswith(prefix.upper()):
        code = code[len(prefix):]

    digits = re.sub(r"\D", "", code)
    if not digits:
        return alarm, ERR_CODE_FORMAT

    normalized = digits.zfill(pad)
    return {**alarm, "code": normalized}, None


# ── [POST-002] 信心度過濾 ────────────────────────────────────────────────────

def _filter_by_conf(alarms: list, threshold: int = CONF_THRESHOLD) -> tuple:
    """
    低於 threshold 的代碼移到 rejected，帶 ERR_LOW_CONF 錯誤碼。
    conf=None 表示模型未回信心度，視為通過（不擋，交給下游人工確認）。
    threshold 由呼叫端依 CONF_PROFILE 決定。
    回傳 (通過列表, 過濾列表)。
    """
    passed, rejected = [], []
    for a in alarms:
        conf = a.get("conf")
        if conf is None or conf >= threshold:
            passed.append(a)
        else:
            rejected.append({**a, "error": ERR_LOW_CONF})
    return passed, rejected


# ── [POST-003] 機種白名單驗證 ────────────────────────────────────────────────

def _validate_model(
    model: Optional[str],
    model_conf: Optional[int],
    valid_models: list,
    bypass_threshold: int = MODEL_BYPASS_CONF,
) -> tuple:
    """
    確認機種是否在白名單。
    bypass_threshold 由呼叫端依 CONF_PROFILE 決定。
    回傳 (model, is_valid, warning_message)。
    """
    if not model:
        return None, False, ERR_MODEL_UNKNWN

    # 精確比對
    if model in valid_models:
        return model, True, None

    # 大小寫不敏感比對
    model_upper = model.upper()
    for v in valid_models:
        if v.upper() == model_upper:
            return v, True, None

    # 不在白名單，看信心度是否極高（conf=None 不放行）
    if model_conf is not None and model_conf >= bypass_threshold:
        return model, True, ERR_MODEL_BYPASS

    return None, False, ERR_MODEL_UNKNWN


# ── 工具函式 ─────────────────────────────────────────────────────────────────

class ValidModelsUnavailable(Exception):
    """load_valid_models() 讀取失敗時拋出——白名單消失會讓 AI 辨識結果
    全部被判定為不在白名單而遭拒絕，不能用空 list 蒙混成「這台機種真的
    不存在」，呼叫端必須能區分「暫時性讀取故障」跟「機種真的沒登記」。
    """


# [POST-004] 機種清單變動極少（新增/停用機種是管理員手動操作），加短
# TTL 記憶體快取：一次讀取失敗不必每次呼叫都重新觸發檔案/資料庫 I/O，
# 也讓短暫抖動（例如檔案系統瞬斷）不會被使用者感知到；快取時間夠短
# （預設 5 分鐘），機種變更後不會讓使用者等太久才生效。
_VALID_MODELS_CACHE_TTL = int(os.environ.get("VALID_MODELS_CACHE_TTL", "300"))
_valid_models_cache: dict = {"models": None, "expires_at": 0.0}


def load_valid_models() -> set:
    """讀取合法機種列表（只含 active 機種），透過 devices_store 走
    JsonStore（本機/測試）或 SupabaseStore（正式環境），不再自己直接
    讀 devices.json 檔案——跟 app.py 讀 devices 資料走同一個入口，
    避免正式環境資料實際存在 Supabase，這裡卻讀本機檔案系統看到空的
    白名單（拍照辨識故障修復 PR-3）。

    department=None 呼叫 devices_store.load()：這是「不加過濾條件，
    取全部部門的機種」，不是「查 department 為 NULL」——PostgREST 的
    department=None 語意跟本專案 storage.py 全部 store 類別一致（見
    SupabaseStore.load() 的同款判斷），白名單本來就該涵蓋所有部門
    的機種，不限單一部門。

    回傳型別改用 set：呼叫端（_validate_model()）只用 `in`/`for` 走訪，
    不會索引存取，set 對成員檢查更直接對應這裡的用途（機種名單去重
    比對，不在意順序）。

    讀取失敗時 fail-closed，拋出 ValidModelsUnavailable，不回傳空
    set——空集合在 apply_post_rules() 眼中等同「沒有任何合法機種」，
    會讓所有辨識結果被判定為不在白名單而全數拒絕，且錯誤訊息（見
    app.py 的 /api/analyze）容易被誤判成「這台機種真的不在系統裡」，
    而不是「白名單暫時讀不到」這種故障。

    「過期快取優於中斷」：查詢失敗時，只要有任何一次成功讀過的快取
    （即使已經過期），優先沿用舊值並記一行 warning log，不直接中斷；
    完全沒有任何快取（從未成功讀過）才真的 fail-closed。這是改用
    Supabase 後新增的行為——原本讀本機檔案幾乎不會失敗，現在多了
    網路這個全新的失效路徑，一次瞬斷不該讓正在使用中的辨識功能
    整個掛掉。
    """
    now = time.monotonic()
    if _valid_models_cache["models"] is not None and now < _valid_models_cache["expires_at"]:
        return _valid_models_cache["models"]

    try:
        from storage import devices_store
        devices = devices_store.load(department=None)
        # [POST-003] 只回傳 active != false 的機種（無此欄位視為有效）
        models = {d["device_model"] for d in devices if d.get("device_model") and d.get("active", True)}
    except Exception as e:
        if _valid_models_cache["models"] is not None:
            # 過期快取優於中斷：查詢失敗但曾經成功讀過，寧可回傳稍舊的
            # 白名單也不要讓整條辨識路徑掛掉。
            import sys as _sys
            print(f"[ai_rules] load_valid_models() 查詢失敗，沿用過期快取："
                  f"{type(e).__name__}: {e}", file=_sys.stderr)
            return _valid_models_cache["models"]
        raise ValidModelsUnavailable(
            f"機種清單暫時無法讀取，請稍後再試（若持續發生請聯絡管理員）：{type(e).__name__}: {e}"
        ) from e

    _valid_models_cache["models"] = models
    _valid_models_cache["expires_at"] = now + _VALID_MODELS_CACHE_TTL
    return models
