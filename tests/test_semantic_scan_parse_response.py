"""scan_semantic_quality.py / suggest_semantic_fixes.py 的 _parse_response()
路徑測試（語意掃描靜默跳過修正的回歸測試）。

背景：suggest_semantic_fixes.py 在合併 _apply_fix() 到 quality.py 時
不慎連帶刪掉了 import re，但 _parse_response() 仍用到 re.sub()——這條
路徑先前完全沒有測試涵蓋，實際執行時每筆都會拋 NameError，被外層
except Exception 吞掉、誤記為批次失敗，QA/老師各自重現才抓到。這支
測試直接呼叫 _parse_response()（不需要 mock AI client），釘住兩支
腳本這條路徑最基本的「能跑」與「格式錯誤時正確拋例外」行為。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "variant"))

import scan_semantic_quality
import suggest_semantic_fixes


@pytest.mark.parametrize("module", [scan_semantic_quality, suggest_semantic_fixes])
class TestParseResponse:
    def test_plain_json_array(self, module):
        assert module._parse_response('[{"index": 0, "issue": "x"}]') == [{"index": 0, "issue": "x"}]

    def test_empty_array_means_no_findings(self, module):
        assert module._parse_response("[]") == []

    def test_wrapped_in_markdown_fence_is_stripped(self, module):
        raw = '```json\n[{"index": 0}]\n```'
        assert module._parse_response(raw) == [{"index": 0}]

    def test_non_array_json_raises(self, module):
        with pytest.raises(ValueError):
            module._parse_response('{"not": "an array"}')

    def test_malformed_json_raises(self, module):
        with pytest.raises(Exception):
            module._parse_response("not json at all")
