"""
AI pipeline 迴歸測試。

涵蓋這幾輪修過的所有邏輯錯誤，改 prompt / 換 analyzer / 調門檻時
跑一次就知道有沒有把規則弄成死碼。

所有被測函式都是純函式或只碰 tmp 目錄，測起來無需網路、無需 API key。
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# ── 讓 import backend.ai.* 找得到 ─────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════════════════════════════════════════════
# ai_rules
# ═══════════════════════════════════════════════════════════════════════════════

class TestFilterByConf:
    def setup_method(self):
        from backend.ai.ai_rules import _filter_by_conf
        self._fn = _filter_by_conf

    def test_conf_none_passes_through(self):
        """conf=None 的代碼不被過濾（模型沒回信心度，交下游人工確認）。"""
        alarms = [{"code": "0001", "conf": None}]
        passed, rejected = self._fn(alarms, threshold=70)
        assert len(passed) == 1
        assert len(rejected) == 0

    def test_low_conf_rejected(self):
        passed, rejected = self._fn([{"code": "0001", "conf": 50}], threshold=70)
        assert len(passed) == 0
        assert rejected[0]["error"] == "ERR_LOW_CONF"

    def test_exact_threshold_passes(self):
        passed, _ = self._fn([{"code": "0001", "conf": 70}], threshold=70)
        assert len(passed) == 1


class TestValidateModel:
    def setup_method(self):
        from backend.ai.ai_rules import _validate_model
        self._fn = _validate_model

    def test_bypass_uses_bypass_key_not_high(self):
        """
        CONF_PROFILE 的 bypass=95，high=90。
        conf=91 在 high 之上但在 bypass 之下，不應放行。
        """
        from backend.ai.ai_config import CONF_PROFILE
        bypass = CONF_PROFILE["gemini"]["bypass"]   # 95
        high   = CONF_PROFILE["gemini"]["high"]     # 90
        assert bypass > high, "前提：bypass 必須比 high 嚴"

        _, valid, warning = self._fn("UNKNOWN", model_conf=high, valid_models=[], bypass_threshold=bypass)
        assert not valid
        assert warning == "ERR_MODEL_UNKNOWN"

    def test_bypass_above_threshold_warns(self):
        from backend.ai.ai_config import CONF_PROFILE
        bypass = CONF_PROFILE["gemini"]["bypass"]
        _, valid, warning = self._fn("UNKNOWN", model_conf=bypass, valid_models=[], bypass_threshold=bypass)
        assert valid
        assert warning == "ERR_MODEL_BYPASS"

    def test_bypass_sets_needs_model_selection(self):
        """bypass 放行後 apply_post_rules 必須把 needs_model_selection 設 True。"""
        from backend.ai.ai_rules import apply_post_rules
        raw = {
            "model": "FAKE_MODEL",
            "model_conf": 97,
            "alarms": [],
            "analyzer": {"name": "gemini", "model": "gemini-2.0-flash", "prompt_version": "v1"},
        }
        r = apply_post_rules(raw, valid_models=["PILM004"])
        assert r["needs_model_selection"] is True

    def test_analyzer_forwarded_in_result(self):
        """apply_post_rules 回傳值必須帶 analyzer，VAL 層才能取 profile。"""
        from backend.ai.ai_rules import apply_post_rules
        analyzer = {"name": "gemini", "model": "gemini-2.0-flash", "prompt_version": "v1"}
        raw = {"model": "PILM004", "model_conf": 92, "alarms": [], "analyzer": analyzer}
        r = apply_post_rules(raw, valid_models=["PILM004"])
        assert r.get("analyzer") == analyzer


# ═══════════════════════════════════════════════════════════════════════════════
# ai_memory
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def mem(tmp_path, monkeypatch):
    """每個測試用獨立的 tmp MEM_DIR，不污染 data/。"""
    monkeypatch.setenv("AI_MEM_DIR", str(tmp_path))
    import importlib
    import backend.ai.ai_memory as m
    importlib.reload(m)
    return tmp_path, m


class TestRecordScanFormat:
    def test_scan_id_generated(self, mem):
        """每次 record_scan 都應產生非空 scan_id。"""
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [])
        assert rec.get("scan_id") and len(rec["scan_id"]) > 0

    def test_scan_id_stable_when_provided(self, mem):
        """外部傳入 scan_id 時原樣保留。"""
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], scan_id="abc123")
        assert rec["scan_id"] == "abc123"

    def test_alarms_keep_conf(self, mem):
        """alarms 欄位一律保留 conf（含 None）。"""
        _, m = mem
        alarms = [{"code": "0001", "conf": None}, {"code": "0002", "conf": 85}]
        rec = m.record_scan("PILM004", 90, alarms, [])
        assert rec["alarms"][0] == {"code": "0001", "conf": None}
        assert rec["alarms"][1] == {"code": "0002", "conf": 85}

    def test_rejected_keep_conf_and_error(self, mem):
        """rejected 欄位一律保留 conf 和 error。"""
        _, m = mem
        rejected = [{"code": "0001", "conf": 50, "error": "ERR_LOW_CONF"}]
        rec = m.record_scan("PILM004", 90, [], rejected)
        assert rec["rejected"][0] == {"code": "0001", "conf": 50, "error": "ERR_LOW_CONF"}

    def test_original_model_stored(self, mem):
        """original_model 欄位寫入記錄，供錯誤率計算。"""
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], original_model="PILM003")
        assert rec["original_model"] == "PILM003"

    def test_source_ai_by_default(self, mem):
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [])
        assert rec["source"] == "ai"


class TestHistoryMismatchCompatibility:
    """_check_history_mismatch 需相容新舊 original_codes 格式。"""

    def _post(self, codes):
        return {
            "model": "PILM004",
            "model_conf": 90,
            "needs_model_selection": False,
            "alarms": [{"code": c} for c in codes],
            "analyzer": {"name": "gemini", "model": "x", "prompt_version": "v1"},
        }

    def test_new_format_dict_codes_no_type_error(self):
        """original_codes 為 dict 格式時不應 TypeError（主路徑 500 的修復）。"""
        from backend.ai.ai_validation import check_validation
        corrections = [{
            "corrected_model": "PILM004",
            "original_codes": [{"code": "0001", "conf": 80}],
        }]
        # 只要不 crash 就算過
        val = check_validation(self._post(["0001"]), corrections)
        assert val["needs_reconfirm"] is True

    def test_old_format_str_codes_still_works(self):
        """original_codes 為舊字串格式仍正確匹配。"""
        from backend.ai.ai_validation import check_validation
        corrections = [{"corrected_model": "PILM004", "original_codes": ["0001"]}]
        val = check_validation(self._post(["0001"]), corrections)
        assert val["needs_reconfirm"] is True

    def test_no_overlap_no_trigger(self):
        from backend.ai.ai_validation import check_validation
        corrections = [{"corrected_model": "PILM004", "original_codes": [{"code": "9999", "conf": 80}]}]
        val = check_validation(self._post(["0001"]), corrections)
        assert val["needs_reconfirm"] is False


class TestRecordScanTier:
    def test_all_rejected_by_low_conf_gives_low_confidence(self, mem):
        """所有代碼被 ERR_LOW_CONF 過濾 → tier=low_confidence。"""
        _, m = mem
        rec = m.record_scan(
            "PILM004", 85, [],
            [{"code": "0001", "conf": 50, "error": "ERR_LOW_CONF"}],
        )
        assert rec["tier"] == "low_confidence"

    def test_no_alarms_no_rejected_gives_no_alarm(self, mem):
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [])
        assert rec["tier"] == "no_alarm"

    def test_alarms_all_conf_none_gives_success(self, mem):
        """avg_conf=None 不 crash，tier=success。"""
        _, m = mem
        rec = m.record_scan("PILM004", 90, [{"code": "0001", "conf": None}], [])
        assert rec["tier"] == "success"

    def test_high_conf_gives_success_high(self, mem):
        from backend.ai.ai_config import CONF_PROFILE
        high = CONF_PROFILE["gemini"]["high"]
        _, m = mem
        rec = m.record_scan(
            "PILM004", high, [{"code": "0001", "conf": high}], [],
            analyzer={"name": "gemini", "model": "gemini-2.0-flash", "prompt_version": "v1"},
        )
        assert rec["tier"] == "success_high"

    def test_model_none_gives_failure(self, mem):
        _, m = mem
        rec = m.record_scan(None, None, [], [])
        assert rec["tier"] == "failure"

    def test_confirmed_source_gives_confirmed_tier(self, mem):
        """source=confirmed 直接走 confirmed tier，不走 AI 邏輯。"""
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], source="confirmed", confirmed_by="op01")
        assert rec["tier"] == "confirmed"
        assert rec["source"] == "confirmed"

    def test_corrected_source_gives_corrected_tier(self, mem):
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], source="corrected", confirmed_by="op01")
        assert rec["tier"] == "corrected"


class TestRecordConfirmation:
    def test_confirmation_writes_confirmed_record(self, mem):
        """record_confirmation 寫入 source=confirmed、tier=confirmed 的記錄。"""
        _, m = mem
        rec = m.record_confirmation(
            scan_id="orig001",
            model="PILM004",
            alarms=[{"code": "0001", "conf": 90}],
            model_conf=88,
            original_model="PILM004",
            original_analyzer=None,
            confirmed_by="op01",
        )
        assert rec["tier"] == "confirmed"
        assert rec["scan_id"] == "orig001"
        assert rec["confirmed_by"] == "op01"

    def test_confirmation_alarms_keep_conf(self, mem):
        _, m = mem
        rec = m.record_confirmation(
            scan_id="orig002",
            model="PILM004",
            alarms=[{"code": "0001", "conf": None}],
            model_conf=None,
            original_model=None,
            original_analyzer=None,
            confirmed_by="op01",
        )
        assert rec["alarms"][0]["conf"] is None


class TestLoadRecordsSafety:
    def test_missing_expires_at_does_not_raise(self, mem):
        """缺 expires_at 的舊格式記錄不拋錯，視為未過期保留。"""
        tmp_path, m = mem
        bad = {"tier": "success", "model": "PILM004"}
        folder = tmp_path / "history"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "PILM004.json").write_text(json.dumps([bad]), encoding="utf-8")
        assert m._load_records("history", "PILM004") == [bad]

    def test_invalid_expires_at_does_not_raise(self, mem):
        tmp_path, m = mem
        bad = {"tier": "success", "expires_at": "NOT_A_DATE"}
        folder = tmp_path / "history"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "PILM004.json").write_text(json.dumps([bad]), encoding="utf-8")
        assert m._load_records("history", "PILM004") == [bad]

    def test_expired_record_filtered(self, mem):
        tmp_path, m = mem
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        record = {"tier": "success", "expires_at": past}
        folder = tmp_path / "history"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "PILM004.json").write_text(json.dumps([record]), encoding="utf-8")
        assert m._load_records("history", "PILM004") == []


class TestLoadConfirmedHistory:
    def test_only_confirmed_and_corrected_returned(self, mem):
        """load_confirmed_history 只回傳 source in (confirmed, corrected)。"""
        _, m = mem
        m.record_scan("PILM004", 90, [], [], source="ai")
        m.record_scan("PILM004", 90, [], [], source="confirmed", confirmed_by="op01")
        m.record_scan("PILM004", 90, [], [], source="corrected", confirmed_by="op01")
        confirmed = m.load_confirmed_history("PILM004")
        assert len(confirmed) == 2
        assert all(r["source"] in ("confirmed", "corrected") for r in confirmed)

    def test_ai_source_excluded(self, mem):
        _, m = mem
        m.record_scan("PILM004", 90, [], [], source="ai")
        assert m.load_confirmed_history("PILM004") == []


# ═══════════════════════════════════════════════════════════════════════════════
# ai_validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestGreyZone:
    def _post(self, conf, analyzer_name="gemini"):
        return {
            "model": "PILM004",
            "model_conf": conf,
            "needs_model_selection": False,
            "alarms": [],
            "analyzer": {"name": analyzer_name, "model": "x", "prompt_version": "v1"},
        }

    def test_gemini_grey_zone_triggers(self):
        """conf=75，gemini pass=70 ~ grey_zone_high=85 → 觸發。"""
        from backend.ai.ai_validation import check_validation
        val = check_validation(self._post(75), [])
        assert val["needs_reconfirm"] is True

    def test_gemini_below_pass_no_grey_zone(self):
        """needs_model_selection=True 時灰色地帶不重複觸發。"""
        from backend.ai.ai_validation import check_validation
        post = self._post(65)
        post["needs_model_selection"] = True
        val = check_validation(post, [])
        assert val["needs_reconfirm"] is False

    def test_local_grey_zone_triggers(self):
        """local profile pass=65，conf=67 應觸發（這條是修過的洞）。"""
        from backend.ai.ai_validation import check_validation
        val = check_validation(self._post(67, analyzer_name="local"), [])
        assert val["needs_reconfirm"] is True

    def test_conf_none_skips_grey_zone(self):
        """conf=None 語意不明，不觸發灰色地帶。"""
        from backend.ai.ai_validation import check_validation
        val = check_validation(self._post(None), [])
        assert val["needs_reconfirm"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# ai_alert
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=False)
def clear_cooldown():
    from backend.ai import ai_alert
    ai_alert._cooldown_cache.clear()
    yield
    ai_alert._cooldown_cache.clear()


def _make_history(tiers: list) -> list:
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=30)).isoformat()
    return [
        {"tier": t, "scanned_at": now.isoformat(), "expires_at": future}
        for t in tiers
    ]


class TestConsecutiveFailureAlert:
    def test_three_failures_triggers_alert(self, clear_cooldown):
        """連續 3 筆 failure → ALERT_LOW_CONF，block=True。"""
        from backend.ai.ai_alert import check_alerts
        history = _make_history(["failure", "failure", "failure"])
        post_result = {"model": None, "model_conf": None, "alarms": [], "rejected_alarms": []}
        alerts = check_alerts(post_result, history)
        codes = [a["code"] for a in alerts]
        assert "ALERT_LOW_CONF" in codes
        block_alert = next(a for a in alerts if a["code"] == "ALERT_LOW_CONF")
        assert block_alert["block"] is True

    def test_two_failures_no_alert(self, clear_cooldown):
        from backend.ai.ai_alert import check_alerts
        history = _make_history(["failure", "failure"])
        post_result = {"model": None, "model_conf": None, "alarms": [], "rejected_alarms": []}
        alerts = check_alerts(post_result, history)
        assert "ALERT_LOW_CONF" not in [a["code"] for a in alerts]

    def test_no_alarm_tier_not_counted_as_failure(self, clear_cooldown):
        """no_alarm（正常機台）不算連續失敗。"""
        from backend.ai.ai_alert import check_alerts
        history = _make_history(["no_alarm", "no_alarm", "no_alarm"])
        post_result = {"model": "PILM004", "model_conf": 90, "alarms": [], "rejected_alarms": []}
        alerts = check_alerts(post_result, history)
        assert "ALERT_LOW_CONF" not in [a["code"] for a in alerts]


class TestCooldown:
    def test_cooldown_does_not_suppress_block_alerts(self, clear_cooldown):
        """冷卻期內再次呼叫，block=True 的警報仍必須出現在回傳值中。"""
        from backend.ai.ai_alert import check_alerts
        history = _make_history(["failure", "failure", "failure"])
        post_result = {"model": None, "model_conf": None, "alarms": [], "rejected_alarms": []}
        first  = check_alerts(post_result, history)
        second = check_alerts(post_result, history)
        assert any(a.get("block") for a in first),  "第一次應有 block 警報"
        assert any(a.get("block") for a in second), "冷卻期內 block 警報不能被吃掉"

    def test_cooldown_timestamp_not_refreshed_during_cooldown(self, clear_cooldown):
        """冷卻期間多次呼叫，不應一直刷新時間戳（否則永遠在冷卻中）。"""
        from backend.ai import ai_alert
        from backend.ai.ai_alert import check_alerts
        history = _make_history(["failure", "failure", "failure"])
        post_result = {"model": None, "model_conf": None, "alarms": [], "rejected_alarms": []}
        check_alerts(post_result, history)
        ts_after_first = dict(ai_alert._cooldown_cache)
        check_alerts(post_result, history)
        ts_after_second = dict(ai_alert._cooldown_cache)
        assert ts_after_first == ts_after_second


# ═══════════════════════════════════════════════════════════════════════════════
# ai_pipeline 整合（MEM 入口）
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunConfirmation:
    def test_confirmation_returns_ok_and_scan_id(self, mem):
        from backend.ai.ai_pipeline import run_confirmation
        result = run_confirmation(
            scan_id="scan001",
            model="PILM004",
            alarms=[{"code": "0001", "conf": 90}],
            model_conf=88,
            original_model="PILM004",
            original_analyzer=None,
            confirmed_by="op01",
        )
        assert result["ok"] is True
        assert result["scan_id"] == "scan001"
        assert result["tier"] == "confirmed"

    def test_confirmation_record_queryable(self, mem):
        """確認後可在 load_confirmed_history 查到。"""
        _, m = mem
        from backend.ai.ai_pipeline import run_confirmation
        run_confirmation(
            scan_id="scan002",
            model="PILM004",
            alarms=[{"code": "0002", "conf": 85}],
            model_conf=88,
            original_model=None,
            original_analyzer=None,
            confirmed_by="op02",
        )
        history = m.load_confirmed_history("PILM004")
        assert any(r["scan_id"] == "scan002" for r in history)


class TestRunCorrection:
    def test_correction_returns_ok_and_scan_id(self, mem):
        from backend.ai.ai_pipeline import run_correction
        result = run_correction(
            scan_id="scan010",
            original_model="PILM003",
            corrected_model="PILM004",
            original_codes=[{"code": "0001", "conf": 80}],
            corrected_codes=[{"code": "0002", "conf": 90}],
            model_conf=75,
            confirmed_by="op03",
        )
        assert result["ok"] is True
        assert result["scan_id"] == "scan010"
        assert result["tier"] == "corrected"

    def test_correction_written_to_corrections_file(self, mem):
        """修正記錄寫進 corrections/PILM004.json。"""
        tmp_path, m = mem
        from backend.ai.ai_pipeline import run_correction
        run_correction(
            scan_id="scan011",
            original_model="PILM003",
            corrected_model="PILM004",
            original_codes=[{"code": "0001", "conf": 80}],
            corrected_codes=[{"code": "0002", "conf": 90}],
            model_conf=75,
            confirmed_by="op03",
        )
        corrections = m.load_corrections("PILM004")
        assert any(r.get("scan_id") == "scan011" for r in corrections)
