"""GeminiAnalyzer.analyze() 的用量統計 outcome 分類與 usage 保留行為。

背景：Gemini 只要回應了就已經計費，若讀 response.text/_parse_response
中途出錯，之前寫法要等這些步驟都成功才讀 usage，會讓已經發生的花費
完全沒有記錄。修正後優先讀 usage_metadata，再處理可能失敗的步驟；
外部審查另外要求區分五種結果類型（outcome），因為 timeout/http_error/
safety/parse_fail 這幾種情境 Gemini 是否計費 Google 官方文件沒有明確
保證，先記錄分類，未來用實際帳單反推誤差。

驗證方式：mock google.genai 的 Client 與例外類型，不觸發真實 API 呼叫。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")


def _new_analyzer():
    from ai.ai_analyzer import GeminiAnalyzer
    analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)  # 跳過 __init__ 的真實 genai.Client 建立
    analyzer.analyzer_model = "gemini-test"
    analyzer._client = MagicMock()
    return analyzer


def _usage_metadata(**kwargs):
    from google.genai import types
    return types.GenerateContentResponseUsageMetadata(**kwargs)


def test_usage_preserved_when_response_text_access_fails(gemini_env):
    """parse_fail：Gemini 有回應（usage_metadata 正常），但 response.text
    存取本身拋出未預期例外——usage 要被保留、outcome 標記為 parse_fail。"""
    analyzer = _new_analyzer()
    response = MagicMock()
    response.candidates = []  # 明確設定，避免 MagicMock 預設屬性造成誤判
    response.usage_metadata = _usage_metadata(
        prompt_token_count=100, candidates_token_count=50, total_token_count=150,
    )
    type(response).text = PropertyMock(side_effect=RuntimeError("simulated response.text failure"))
    analyzer._client.models.generate_content.return_value = response

    with pytest.raises(RuntimeError) as exc_info:
        analyzer.analyze("ZmFrZQ==", "image/jpeg")

    assert exc_info.value.usage_outcome == "parse_fail"
    usage = exc_info.value.usage
    assert usage == {
        "prompt_token_count": 100, "candidates_token_count": 50,
        "thoughts_token_count": None, "total_token_count": 150,
    }


def test_usage_attached_normally_on_success(gemini_env):
    """ok：正常路徑（解析成功）usage 跟 usage_outcome="ok" 都要出現在
    回傳結果裡，不是只有失敗路徑才有這些欄位。"""
    analyzer = _new_analyzer()
    response = MagicMock()
    response.candidates = []
    response.usage_metadata = _usage_metadata(
        prompt_token_count=20, candidates_token_count=10, total_token_count=30,
    )
    response.text = '{"model": null, "model_conf": null, "alarms": []}'
    analyzer._client.models.generate_content.return_value = response

    result = analyzer.analyze("ZmFrZQ==", "image/jpeg")
    assert result["usage"] == {
        "prompt_token_count": 20, "candidates_token_count": 10,
        "thoughts_token_count": None, "total_token_count": 30,
    }
    assert result["usage_outcome"] == "ok"


def test_timeout_has_no_usage_and_correct_outcome(gemini_env):
    """timeout：我方主動斷線，完全沒有回應可讀，usage 必須是 None
    （不是空字典或假造的零值），outcome 標記為 timeout。"""
    import httpx
    analyzer = _new_analyzer()
    analyzer._client.models.generate_content.side_effect = httpx.TimeoutException("simulated timeout")

    with pytest.raises(httpx.TimeoutException) as exc_info:
        analyzer.analyze("ZmFrZQ==", "image/jpeg")

    assert exc_info.value.usage_outcome == "timeout"
    assert exc_info.value.usage is None


def test_http_error_has_no_usage_and_correct_outcome(gemini_env):
    """http_error：Gemini 明確回 4xx/5xx（APIError），沒有 usage 可讀，
    outcome 標記為 http_error。"""
    from google.genai import errors
    analyzer = _new_analyzer()
    api_error = errors.ClientError(code=400, response_json={"error": {"message": "bad request"}})
    analyzer._client.models.generate_content.side_effect = api_error

    with pytest.raises(errors.ClientError) as exc_info:
        analyzer.analyze("ZmFrZQ==", "image/jpeg")

    assert exc_info.value.usage_outcome == "http_error"
    assert exc_info.value.usage is None


def test_safety_finish_reason_preserves_usage_and_outcome(gemini_env):
    """safety：finish_reason=SAFETY 時 Gemini 通常仍會回 usage_metadata
    （模型已經跑完，只是內容被擋），這裡驗證 usage 資料確實被讀到並
    保留，outcome 標記為 safety，不會被誤判成 parse_fail。"""
    from google.genai import types
    analyzer = _new_analyzer()

    candidate = MagicMock()
    candidate.finish_reason = types.FinishReason.SAFETY
    response = MagicMock()
    response.candidates = [candidate]
    response.usage_metadata = _usage_metadata(
        prompt_token_count=80, candidates_token_count=0, total_token_count=80,
    )
    analyzer._client.models.generate_content.return_value = response

    with pytest.raises(RuntimeError) as exc_info:
        analyzer.analyze("ZmFrZQ==", "image/jpeg")

    assert exc_info.value.usage_outcome == "safety"
    usage = exc_info.value.usage
    assert usage == {
        "prompt_token_count": 80, "candidates_token_count": 0,
        "thoughts_token_count": None, "total_token_count": 80,
    }


def test_safety_not_confused_with_normal_finish_reason(gemini_env):
    """對照組：finish_reason=STOP（正常結束）不該被誤判成 safety。"""
    from google.genai import types
    analyzer = _new_analyzer()

    candidate = MagicMock()
    candidate.finish_reason = types.FinishReason.STOP
    response = MagicMock()
    response.candidates = [candidate]
    response.usage_metadata = _usage_metadata(
        prompt_token_count=20, candidates_token_count=10, total_token_count=30,
    )
    response.text = '{"model": null, "model_conf": null, "alarms": []}'
    analyzer._client.models.generate_content.return_value = response

    result = analyzer.analyze("ZmFrZQ==", "image/jpeg")
    assert result["usage_outcome"] == "ok"
