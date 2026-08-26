"""
AI 分析模組 — 從圖片辨識機種與警報代碼。

目前實作：Gemini (google-genai)
換地端：新增一個 class 繼承 BaseAnalyzer，在 get_analyzer() 中根據 ANALYZER 環境變數切換。
"""

import base64
import json
import os
import re

# prompt 版本號，改動 prompt 時遞增，讓歷史記錄可回溯比較
PROMPT_VERSION = "v1"


# ── 抽象介面 ────────────────────────────────────────────────────────────────

class BaseAnalyzer:
    # 子類別必須宣告，用於追溯記錄
    analyzer_name: str = "unknown"
    analyzer_model: str = "unknown"

    def analyze(self, image_b64: str, mime_type: str = "image/jpeg") -> dict:
        """
        回傳格式：
        {
            "model": "PILM004",          # 辨識到的機種，None 表示無法辨識
            "model_conf": 85,            # 0-100，None 表示模型未回信心度
            "alarms": [
                { "code": "E-514", "conf": 92, "note": "清晰可見" }
            ],
            "raw": "...",                # 原始 AI 回應，供 debug
            "analyzer": {
                "name": "gemini",        # 追溯用：哪個 analyzer
                "model": "gemini-flash-latest",
                "prompt_version": "v1",
            }
        }
        """
        raise NotImplementedError


# ── Prompt（集中管理，版本號跟 PROMPT_VERSION 同步）───────────────────────
# 修改 prompt 時同步遞增 PROMPT_VERSION，歷史記錄才能回溯比較哪個版本的判斷

_ALARM_PROMPT = """你是工廠設備警報辨識系統。請分析這張圖片並以 JSON 格式回應，不要加任何 markdown。

格式：
{
  "model": "機種型號或null",
  "model_conf": 0到100的整數,
  "alarms": [
    {"code": "警報代碼", "conf": 0到100的整數, "note": "簡短說明"}
  ]
}

規則：
- model: 圖片中可見的機台型號（如 PILM004、TFM002），看不到就填 null
- model_conf: 對機種辨識的信心度（整數，0~100）
- alarms: 圖片中所有可見的警報代碼，可能有多個
- code: 原始警報代碼，保留原始格式（不要自行修改）
- conf: 對該代碼的 OCR 信心度（整數，0~100）
- note: 一句話說明（中文），描述代碼在圖片中的狀態
- 若圖片完全沒有警報代碼，alarms 填空陣列 []
- 校準標準：畫面清晰可完整辨識 → conf 85~100；部分模糊或遮擋 → 50~84；幾乎看不清 → 50 以下
- 機種型號請精確抄錄，不要猜測或補全"""


# ── Gemini 實作 ─────────────────────────────────────────────────────────────

class GeminiAnalyzer(BaseAnalyzer):
    analyzer_name = "gemini"

    def __init__(self):
        import warnings
        from google import genai
        self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.analyzer_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
        if self.analyzer_model.endswith("-latest"):
            warnings.warn(
                f"GEMINI_MODEL='{self.analyzer_model}' 是浮動 alias，"
                "追溯記錄將無法精確對應版本，正式環境請改用釘死版本號（如 gemini-2.0-flash）。",
                stacklevel=2,
            )

    def analyze(self, image_b64: str, mime_type: str = "image/jpeg") -> dict:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self.analyzer_model,
            contents=[
                types.Part.from_bytes(data=base64.b64decode(image_b64), mime_type=mime_type),
                _ALARM_PROMPT,
            ],
            # 比照 LocalAnalyzer 的 timeout=30（秒）；SDK 的 timeout 單位是毫秒。
            # 原本沒有設定，Gemini API 請求掛住時會無限期卡住整個 worker。
            config=types.GenerateContentConfig(http_options=types.HttpOptions(timeout=30000)),
        )
        raw = response.text.strip()
        result = _parse_response(raw, self)
        # AI 用量統計階段 1：只記錄，不做上限管控（跟 /api/analyze 的呼叫
        # 頻率節流是分開的機制）。usage_metadata 在 SDK 回應裡可能是
        # None（例如 API 本身未回傳），這裡不強求一定有值。只有 Gemini
        # 才有這份資料，LocalAnalyzer 走 Ollama 沒有對應概念，所以放在
        # GeminiAnalyzer 這層附加，不改動共用的 _parse_response() 簽名。
        usage = response.usage_metadata
        result["usage"] = {
            "prompt_token_count": getattr(usage, "prompt_token_count", None),
            "candidates_token_count": getattr(usage, "candidates_token_count", None),
            "thoughts_token_count": getattr(usage, "thoughts_token_count", None),
            "total_token_count": getattr(usage, "total_token_count", None),
        } if usage is not None else None
        return result


# ── 地端模型佔位（之後實作）────────────────────────────────────────────────

class LocalAnalyzer(BaseAnalyzer):
    analyzer_name = "local"

    def __init__(self):
        self._endpoint = os.environ.get("LOCAL_AI_ENDPOINT", "http://localhost:11434")
        self.analyzer_model = os.environ.get("LOCAL_AI_MODEL", "llava")

    def analyze(self, image_b64: str, mime_type: str = "image/jpeg") -> dict:
        import urllib.request
        payload = json.dumps({
            "model": self.analyzer_model,
            "prompt": _ALARM_PROMPT,
            "images": [image_b64],
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{self._endpoint}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.loads(r.read())["response"].strip()
        return _parse_response(raw, self)


# ── 共用解析 ────────────────────────────────────────────────────────────────

def _parse_response(raw: str, analyzer: "BaseAnalyzer | None" = None) -> dict:
    _analyzer_meta = {
        "name": getattr(analyzer, "analyzer_name", "unknown"),
        "model": getattr(analyzer, "analyzer_model", "unknown"),
        "prompt_version": PROMPT_VERSION,
    }

    text = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group())
        else:
            return {"model": None, "model_conf": None, "alarms": [], "raw": raw, "analyzer": _analyzer_meta}

    def _int_or_none(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    raw_model_conf = data.get("model_conf")
    model_conf = _int_or_none(raw_model_conf)  # None = 模型未回信心度

    alarms = []
    for a in data.get("alarms", []):
        if not a.get("code"):
            continue
        alarm_conf = _int_or_none(a.get("conf"))  # None = 模型未回信心度
        alarms.append({
            "code": str(a["code"]).strip(),
            "conf": alarm_conf,
            "note": str(a.get("note", "")),
        })

    return {
        "model": data.get("model") or None,
        "model_conf": model_conf,
        "alarms": alarms,
        "raw": raw,
        "analyzer": _analyzer_meta,
    }


# ── 工廠函式 ────────────────────────────────────────────────────────────────

def get_analyzer() -> BaseAnalyzer:
    """根據環境變數 ANALYZER 選擇實作，預設 gemini。"""
    name = os.environ.get("ANALYZER", "gemini").lower()
    if name == "local":
        return LocalAnalyzer()
    return GeminiAnalyzer()
