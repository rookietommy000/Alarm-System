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
        rec = m.record_scan("PILM004", 90, [], [], department="test_dept")
        assert rec.get("scan_id") and len(rec["scan_id"]) > 0

    def test_scan_id_stable_when_provided(self, mem):
        """外部傳入 scan_id 時原樣保留。"""
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], scan_id="abc123", department="test_dept")
        assert rec["scan_id"] == "abc123"

    def test_alarms_keep_conf(self, mem):
        """alarms 欄位一律保留 conf（含 None）。"""
        _, m = mem
        alarms = [{"code": "0001", "conf": None}, {"code": "0002", "conf": 85}]
        rec = m.record_scan("PILM004", 90, alarms, [], department="test_dept")
        assert rec["alarms"][0] == {"code": "0001", "conf": None}
        assert rec["alarms"][1] == {"code": "0002", "conf": 85}

    def test_rejected_keep_conf_and_error(self, mem):
        """rejected 欄位一律保留 conf 和 error。"""
        _, m = mem
        rejected = [{"code": "0001", "conf": 50, "error": "ERR_LOW_CONF"}]
        rec = m.record_scan("PILM004", 90, [], rejected, department="test_dept")
        assert rec["rejected"][0] == {"code": "0001", "conf": 50, "error": "ERR_LOW_CONF"}

    def test_original_model_stored(self, mem):
        """original_model 欄位寫入記錄，供錯誤率計算。"""
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], original_model="PILM003", department="test_dept")
        assert rec["original_model"] == "PILM003"

    def test_source_ai_by_default(self, mem):
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], department="test_dept")
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
            department="test_dept",
        )
        assert rec["tier"] == "low_confidence"

    def test_no_alarms_no_rejected_gives_no_alarm(self, mem):
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], department="test_dept")
        assert rec["tier"] == "no_alarm"

    def test_alarms_all_conf_none_gives_success(self, mem):
        """avg_conf=None 不 crash，tier=success。"""
        _, m = mem
        rec = m.record_scan("PILM004", 90, [{"code": "0001", "conf": None}], [], department="test_dept")
        assert rec["tier"] == "success"

    def test_high_conf_gives_success_high(self, mem):
        from backend.ai.ai_config import CONF_PROFILE
        high = CONF_PROFILE["gemini"]["high"]
        _, m = mem
        rec = m.record_scan(
            "PILM004", high, [{"code": "0001", "conf": high}], [],
            analyzer={"name": "gemini", "model": "gemini-2.0-flash", "prompt_version": "v1"},
            department="test_dept",
        )
        assert rec["tier"] == "success_high"

    def test_model_none_gives_failure(self, mem):
        _, m = mem
        rec = m.record_scan(None, None, [], [], department="test_dept")
        assert rec["tier"] == "failure"

    def test_confirmed_source_gives_confirmed_tier(self, mem):
        """source=confirmed 直接走 confirmed tier，不走 AI 邏輯。"""
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], source="confirmed", confirmed_by="op01", department="test_dept")
        assert rec["tier"] == "confirmed"
        assert rec["source"] == "confirmed"

    def test_corrected_source_gives_corrected_tier(self, mem):
        _, m = mem
        rec = m.record_scan("PILM004", 90, [], [], source="corrected", confirmed_by="op01", department="test_dept")
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
            department="test_dept",
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
            department="test_dept",
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
        assert m._load_records("history", "PILM004", department=None) == [bad]

    def test_invalid_expires_at_does_not_raise(self, mem):
        tmp_path, m = mem
        bad = {"tier": "success", "expires_at": "NOT_A_DATE"}
        folder = tmp_path / "history"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "PILM004.json").write_text(json.dumps([bad]), encoding="utf-8")
        assert m._load_records("history", "PILM004", department=None) == [bad]

    def test_expired_record_filtered(self, mem):
        tmp_path, m = mem
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        record = {"tier": "success", "expires_at": past}
        folder = tmp_path / "history"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "PILM004.json").write_text(json.dumps([record]), encoding="utf-8")
        assert m._load_records("history", "PILM004", department=None) == []


class TestLoadConfirmedHistory:
    def test_only_confirmed_and_corrected_returned(self, mem):
        """load_confirmed_history 只回傳 source in (confirmed, corrected)。"""
        _, m = mem
        m.record_scan("PILM004", 90, [], [], source="ai", department="test_dept")
        m.record_scan("PILM004", 90, [], [], source="confirmed", confirmed_by="op01", department="test_dept")
        m.record_scan("PILM004", 90, [], [], source="corrected", confirmed_by="op01", department="test_dept")
        confirmed = m.load_confirmed_history("PILM004", department="test_dept")
        assert len(confirmed) == 2
        assert all(r["source"] in ("confirmed", "corrected") for r in confirmed)

    def test_ai_source_excluded(self, mem):
        _, m = mem
        m.record_scan("PILM004", 90, [], [], source="ai", department="test_dept")
        assert m.load_confirmed_history("PILM004", department="test_dept") == []

    def test_department_value_correctly_threaded_into_record(self, mem):
        """department 參數確實被寫入記錄的 department 欄位，不會在傳遞過程中
        被吃掉或搞混（PLAN 3.5 節跨部門隔離的前提：值本身要先傳對）。

        誠實的範圍聲明：這條測試驗證不到「查詢時是否真的按部門過濾」，因為
        JsonStore fallback（本機/測試，PLAN 3.2 節）刻意不實作多部門查詢過濾，
        `_load_records()` 在非 Supabase 分支完全不使用 department 參數做篩選。
        真正的查詢隔離只發生在 Supabase 路徑（_sb_query 依 department 過濾），
        這裡測不到、也不該假裝測到——那樣的斷言反而會製造「有測試守著」的
        錯覺，比沒有斷言更危險。這條測試能守住的，是「department 有沒有從
        record_scan() 一路正確傳進最終寫入的記錄」這個更小、但仍然重要的環節：
        少了它，日後有人在 record_scan() 內部改動時，就算把 department 從
        record dict 裡漏寫掉，也不會被任何測試抓到。"""
        _, m = mem
        rec_a = m.record_scan("PILM004", 90, [], [], source="confirmed", confirmed_by="op_a", department="dept_a")
        rec_b = m.record_scan("PILM004", 90, [], [], source="confirmed", confirmed_by="op_b", department="dept_b")
        assert rec_a["department"] == "dept_a"
        assert rec_b["department"] == "dept_b"


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
# ai_pipeline run_pipeline() 結構性保證（拍照辨識故障修復 PR-2）
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeAnalyzer:
    """run_pipeline() 內部呼叫 get_analyzer().analyze(...)，這裡給一個
    永遠成功的假 analyzer，讓測試能聚焦在「某個下游步驟失敗」這件事，
    不用真的打 Gemini。"""
    analyzer_name = "fake"
    analyzer_model = "fake-model-v1"

    def analyze(self, image_b64, mime_type="image/jpeg"):
        return {
            "model": "PILM004",
            "model_conf": 90,
            "alarms": [{"code": "0001", "conf": 92, "note": "測試"}],
            "raw": "{}",
            "analyzer": {"name": self.analyzer_name, "model": self.analyzer_model, "prompt_version": "v1"},
            "usage": None,
            "usage_outcome": "ok",
        }


class TestRunPipelineFailureRecording:
    """不管哪個步驟失敗，MEM（record_scan 寫 ai_scans）跟 LOG（log_scan
    寫 ai_logs）都必須寫入一筆記錄——修復前只有 analyzer 這一步有保護，
    其餘步驟失敗時例外一路冒到 app.py 變成 500，兩邊完全沒有任何痕跡，
    這批參數化測試逐一涵蓋每個步驟，之後新增 pipeline 步驟時只要照
    同樣的 pattern 加一組 case，測試會自動涵蓋到，不用每次手動記得
    另外加保護。

    _reset_valid_models_cache 比照 tests/test_ai_rules_valid_models.py
    的既有 autouse fixture 模式，避免 valid_models 步驟失敗那個 case
    的模擬被 TTL 快取蓋掉、或污染到其他測試。
    """

    @pytest.fixture(autouse=True)
    def _reset_valid_models_cache(self):
        from backend.ai import ai_rules
        ai_rules._valid_models_cache["models"] = None
        ai_rules._valid_models_cache["expires_at"] = 0.0
        yield
        ai_rules._valid_models_cache["models"] = None
        ai_rules._valid_models_cache["expires_at"] = 0.0

    @pytest.fixture
    def pipeline_mem(self, tmp_path, monkeypatch):
        """跟現有的 mem fixture 邏輯相同，但額外讓
        backend.ai.ai_pipeline 內已經 import 進來的 record_scan/log_scan
        指向重新載入後的新模組——ai_pipeline.py 是用
        `from .ai_memory import record_scan` 這種具名匯入，模組級變數
        重新賦值不會自動同步到已經 import 過的呼叫端，需要手動 patch
        回去，否則測試寫入的 tmp 目錄跟 ai_pipeline 實際呼叫到的函式
        用的是兩個不同時期的模組物件。"""
        monkeypatch.setenv("AI_MEM_DIR", str(tmp_path / "mem"))
        monkeypatch.setenv("AI_LOG_DIR", str(tmp_path / "log"))
        import importlib
        import backend.ai.ai_memory as mem_mod
        import backend.ai.ai_logger as log_mod
        import backend.ai.ai_pipeline as pipeline_mod
        importlib.reload(mem_mod)
        importlib.reload(log_mod)
        monkeypatch.setattr(pipeline_mod, "record_scan", mem_mod.record_scan)
        monkeypatch.setattr(pipeline_mod, "log_scan", log_mod.log_scan)
        monkeypatch.setattr(pipeline_mod, "_load_records", mem_mod._load_records)
        return tmp_path, mem_mod, log_mod, pipeline_mod

    def _write_devices(self, data_dir, devices):
        import json
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "devices.json").write_text(json.dumps(devices, ensure_ascii=False), encoding="utf-8")

    @pytest.mark.parametrize("failing_step", ["analyzer", "valid_models", "post_rule", "verify"])
    def test_step_failure_writes_mem_and_log_record(self, pipeline_mem, monkeypatch, tmp_path, failing_step):
        tmp_path_mem, mem_mod, log_mod, pipeline_mod = pipeline_mem
        monkeypatch.setattr(pipeline_mod, "get_analyzer", lambda: _FakeAnalyzer())

        devices_dir = tmp_path / "devices_data"
        self._write_devices(devices_dir, [{"model": "PILM004", "active": True}])
        monkeypatch.setenv("ALARM_DATA_DIR", str(devices_dir))

        if failing_step == "analyzer":
            def _boom_analyze(self, image_b64, mime_type="image/jpeg"):
                raise RuntimeError("模擬 analyzer 失敗")
            monkeypatch.setattr(_FakeAnalyzer, "analyze", _boom_analyze)
        elif failing_step == "valid_models":
            def _boom_load_valid_models(*a, **k):
                from backend.ai.ai_rules import ValidModelsUnavailable
                raise ValidModelsUnavailable("模擬白名單讀取失敗")
            monkeypatch.setattr(pipeline_mod, "load_valid_models", _boom_load_valid_models)
        elif failing_step == "post_rule":
            def _boom_apply_post_rules(*a, **k):
                raise RuntimeError("模擬 POST 規則套用失敗")
            monkeypatch.setattr(pipeline_mod, "apply_post_rules", _boom_apply_post_rules)
        elif failing_step == "verify":
            def _boom_check_validation(*a, **k):
                raise RuntimeError("模擬 VAL 層失敗")
            monkeypatch.setattr(pipeline_mod, "check_validation", _boom_check_validation)

        # analyzer 失敗是既有的降級路徑：run_pipeline() 回傳一個帶
        # pipeline_error 欄位的降級 dict，不 re-raise（app.py 的
        # /api/analyze 前端流程本來就預期這種情況是 200 + pipeline_error，
        # 不是 500——見 frontend/index.html 的 apiResult.pipeline_error
        # 判斷）。其餘步驟（valid_models/post_rule/verify）目前沒有
        # 對應的降級語意，例外會繼續往外拋讓 app.py 既有的
        # except ValidModelsUnavailable / except Exception 接手決定
        # 對外回應（PR-1 已保證這些路徑會回合法 JSON 而不是裸 HTML）。
        if failing_step == "analyzer":
            result = pipeline_mod.run_pipeline("ZmFrZQ==", department="test_dept")
            assert result.get("pipeline_error"), "analyzer 失敗時應回傳降級 dict，帶 pipeline_error"
        else:
            with pytest.raises(Exception):
                pipeline_mod.run_pipeline("ZmFrZQ==", department="test_dept")

        history = mem_mod._load_records("history", "PILM004", department="test_dept") \
            + mem_mod._load_records("history", "_unknown", department="test_dept")
        assert len(history) == 1, f"{failing_step} 失敗時 MEM 層應寫入恰好一筆記錄"
        assert history[0]["tier"] == "failure"

        logs = log_mod.load_logs(limit=50, event="scan")
        assert len(logs) == 1, f"{failing_step} 失敗時 LOG 層應寫入恰好一筆記錄"
        assert logs[0]["level"] == "ERROR"

    def test_success_path_writes_exactly_one_record_not_two(self, pipeline_mem, monkeypatch, tmp_path):
        """正常成功路徑（沒有任何步驟失敗）只能有一筆記錄——這條測試
        守住 finally 分支的「_state['logged'] 已為 True 就不再補寫」
        這個判斷，避免正常路徑意外被算成失敗又多寫一筆。"""
        tmp_path_mem, mem_mod, log_mod, pipeline_mod = pipeline_mem
        monkeypatch.setattr(pipeline_mod, "get_analyzer", lambda: _FakeAnalyzer())

        devices_dir = tmp_path / "devices_data"
        self._write_devices(devices_dir, [{"model": "PILM004", "active": True}])
        monkeypatch.setenv("ALARM_DATA_DIR", str(devices_dir))

        result = pipeline_mod.run_pipeline("ZmFrZQ==", department="test_dept")
        assert result["model"] == "PILM004"

        history = mem_mod._load_records("history", "PILM004", department="test_dept")
        assert len(history) == 1
        assert history[0]["tier"] != "failure"

        logs = log_mod.load_logs(limit=50, event="scan")
        assert len(logs) == 1

    def test_success_path_logs_all_five_timing_segments(self, pipeline_mem, monkeypatch, tmp_path, capsys):
        """效能優化前置：先量測、不要沒有數據就猜（PLAN 拍照辨識效能
        優化）。這裡驗證五段計時 log 真的有輸出，不驗證數字本身（純觀測
        用途，不影響行為邏輯，測太細反而在鎖死格式字串）。"""
        tmp_path_mem, mem_mod, log_mod, pipeline_mod = pipeline_mem
        monkeypatch.setattr(pipeline_mod, "get_analyzer", lambda: _FakeAnalyzer())

        devices_dir = tmp_path / "devices_data"
        self._write_devices(devices_dir, [{"model": "PILM004", "active": True}])
        monkeypatch.setenv("ALARM_DATA_DIR", str(devices_dir))

        pipeline_mod.run_pipeline("ZmFrZQ==", department="test_dept")
        stderr = capsys.readouterr().err
        for segment in ("analyzer", "val", "mem", "alert", "log"):
            assert f"pipeline_timing[{segment}]:" in stderr, f"缺少 {segment} 段的計時 log"


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
            department="test_dept",
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
            department="test_dept",
        )
        history = m.load_confirmed_history("PILM004", department="test_dept")
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
            department="test_dept",
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
            department="test_dept",
        )
        corrections = m.load_corrections("PILM004", department="test_dept")
        assert any(r.get("scan_id") == "scan011" for r in corrections)
