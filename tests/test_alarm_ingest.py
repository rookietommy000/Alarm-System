"""alarm_ingest/ 共用模組單元測試（PLAN 批次匯入 UI 階段 1）。

parse.py 是純函式，不需要 fixture。validate.py/commit.py 依賴
storage.devices_store/alarms_store/audit_logger，這些是模組層級
singleton，決定於 import 當下的 ALARM_DATA_DIR（見 conftest.py 說明），
所以跟 test_api.py 一樣需要「重載模組」隔離。
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


# ── parse.py：純函式，不需要 fixture ─────────────────────────────────

def test_normalize_variant_collapses_whitespace():
    from alarm_ingest import normalize_variant
    assert normalize_variant("  a   b  ") == "a b"


def test_normalize_variant_unifies_dashes_and_fullwidth():
    from alarm_ingest import normalize_variant
    assert normalize_variant("V46403–valve") == "V46403-valve"
    assert normalize_variant("V46403—valve") == "V46403-valve"
    assert normalize_variant("位置（未到達）") == "位置(未到達)"
    assert normalize_variant("A／B") == "A/B"


def test_normalize_variant_keeps_case():
    from alarm_ingest import normalize_variant
    assert normalize_variant("Position NOT Reached") == "Position NOT Reached"


def test_normalize_variant_empty_input():
    from alarm_ingest import normalize_variant
    assert normalize_variant("") == ""
    assert normalize_variant(None) == ""


# ── 契約測試：alarm_ingest 與 tools/variant/parse_alarms.py 部分共用
# （read_tabular/_detect_columns/split_code 已改為 import 共用，見
# backend/alarm_ingest/detect.py 開頭說明），但 normalize_variant/
# decide_variant_mode 仍各自一份（規模小、分岔風險低），用固定案例
# 釘住同步。tools/variant/ 已進版控，理論上一定存在，但仍保留 skip
# 條件而非直接 import——避免測試對檔案系統佈局做過強假設。

VARIANT_DIR = Path(__file__).resolve().parent.parent / "tools" / "variant"
PARSE_ALARMS_PATH = VARIANT_DIR / "parse_alarms.py"

pytestmark_variant_available = pytest.mark.skipif(
    not PARSE_ALARMS_PATH.exists(),
    reason="tools/variant/parse_alarms.py 不存在",
)


def _load_parse_alarms_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_parse_alarms_contract", PARSE_ALARMS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


NORMALIZE_VARIANT_CASES = [
    ("  a   b  ", "a b"),
    ("V46403–valve", "V46403-valve"),   # en dash
    ("V46403—valve", "V46403-valve"),   # em dash
    ("位置（未到達）", "位置(未到達)"),
    ("A／B", "A/B"),
    ("", ""),
]


@pytestmark_variant_available
def test_normalize_variant_contract_matches_parse_alarms():
    """兩邊各自維護一份 normalize_variant，任一方改動都必須同步——
    不同步的後果是 CLI 產出的 variant 跟後端查詢時正規化出的 variant
    對不上，patch_one() 打空回 404，看起來像資料不存在。"""
    from alarm_ingest import normalize_variant as ingest_normalize
    parse_alarms = _load_parse_alarms_module()

    for src, expected in NORMALIZE_VARIANT_CASES:
        assert ingest_normalize(src) == expected, f"alarm_ingest 版本對 {src!r} 的結果跟預期不符"
        assert parse_alarms.normalize_variant(src) == expected, f"parse_alarms.py 版本對 {src!r} 的結果跟預期不符"


DECIDE_VARIANT_MODE_CASES = [
    # (rows 的 code 列表, mode, 預期 use_variant)
    (["E1", "E2", "E3"], "auto", False),
    (["E1", "E1", "E2"], "auto", True),
    (["E1", "E2"], "always", True),
    (["E1", "E1"], "never", False),
]


@pytestmark_variant_available
def test_decide_variant_mode_contract_matches_parse_alarms():
    """decide_variant_mode 的 (bool, reason) 介面兩邊也要同步——後台
    批次匯入固定傳 auto，判定結果要跟 CLI 一致，管理員在後台看到的
    「系統判定」才不會跟你用 CLI 跑出來的結果對不上。"""
    from alarm_ingest import decide_variant_mode as ingest_decide
    parse_alarms = _load_parse_alarms_module()

    for codes, mode, expected in DECIDE_VARIANT_MODE_CASES:
        rows = [{"code": c} for c in codes]
        ingest_result, ingest_reason = ingest_decide(rows, mode)
        parse_result, parse_reason = parse_alarms.decide_variant_mode(rows, mode)
        assert ingest_result == expected, f"alarm_ingest 版本對 {codes!r}/{mode!r} 判定錯誤"
        assert parse_result == expected, f"parse_alarms.py 版本對 {codes!r}/{mode!r} 判定錯誤"
        assert ingest_result == parse_result
        assert isinstance(ingest_reason, str) and ingest_reason
        assert isinstance(parse_reason, str) and parse_reason


def test_row_to_alarm_requires_code_and_device_model():
    from alarm_ingest import row_to_alarm
    with pytest.raises(ValueError, match="code"):
        row_to_alarm({"device_model": "M1"})
    with pytest.raises(ValueError, match="device_model"):
        row_to_alarm({"code": "E1"})


def test_row_to_alarm_rejects_invalid_severity():
    from alarm_ingest import row_to_alarm
    with pytest.raises(ValueError, match="severity"):
        row_to_alarm({"code": "E1", "device_model": "M1", "severity": "致命"})


def test_row_to_alarm_normalizes_variant_and_defaults():
    from alarm_ingest import row_to_alarm
    row = row_to_alarm({"code": "E1", "device_model": "M1", "variant": "  V1—A  "})
    assert row["variant"] == "V1-A"
    assert row["keywords"] == []
    assert row["sol_steps"] == {}


def test_row_to_alarm_parses_keywords_and_sol_steps():
    from alarm_ingest import row_to_alarm
    row = row_to_alarm({
        "code": "E1", "device_model": "M1",
        "keywords": "主軸;過載",
        "sol_steps": '{"1": "關機"}',
    })
    assert row["keywords"] == ["主軸", "過載"]
    assert row["sol_steps"] == {"1": "關機"}


def test_row_to_alarm_rejects_invalid_sol_steps_json():
    from alarm_ingest import row_to_alarm
    with pytest.raises(ValueError, match="sol_steps"):
        row_to_alarm({"code": "E1", "device_model": "M1", "sol_steps": "{not json"})


CSV_HEADER = "code,device_model,variant,description,cause,solution,local_solution"


def test_load_csv_reports_line_number_on_error(tmp_path):
    from alarm_ingest import load_csv
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text(f"{CSV_HEADER}\nE1,M1,,,,,\n,M2,,,,,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="第 3 行"):
        load_csv(csv_path)


def test_load_csv_missing_required_header_rejected(tmp_path):
    from alarm_ingest import load_csv
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("code,device_model\nE1,M1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="缺少欄位"):
        load_csv(csv_path)


def test_load_csv_missing_header_error_shows_detected_headers(tmp_path):
    """缺欄位的錯誤訊息要顯示偵測到的原始表頭，讓使用者能判斷是自己
    打錯字還是真的漏欄——只說「缺少欄位」不夠，看不出來哪裡對不上。"""
    from alarm_ingest import load_csv
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("code,device_model,cuase\nE1,M1,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="偵測到的表頭.*cuase"):
        load_csv(csv_path)


def test_load_csv_header_tolerates_case_and_whitespace(tmp_path):
    """使用者從 Excel 複製表頭容易帶到大小寫不一致或多餘空白（例如
    "Code"、" cause "），這些差異不影響「這一欄是什麼」的判斷，不該
    被判定成缺欄位；比對後仍要能用正規化後的欄名正確抓到值。"""
    from alarm_ingest import load_csv
    csv_path = tmp_path / "good.csv"
    csv_path.write_text(
        "CODE, Device_Model ,Variant,Description,CAUSE,Solution,Local_Solution\n"
        "E1,M1,,主軸過載,負荷過大,降低進給,\n",
        encoding="utf-8",
    )
    rows = load_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["code"] == "E1"
    assert rows[0]["device_model"] == "M1"
    assert rows[0]["cause"] == "負荷過大"


def test_load_csv_full_template_parses_correctly(tmp_path):
    from alarm_ingest import load_csv
    csv_path = tmp_path / "good.csv"
    csv_path.write_text(
        f"{CSV_HEADER}\nE1,M1,,主軸過載,負荷過大,降低進給,\n", encoding="utf-8"
    )
    rows = load_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["code"] == "E1"
    assert rows[0]["description"] == "主軸過載"


def test_load_json_reports_row_number_on_error(tmp_path):
    from alarm_ingest import load_json
    json_path = tmp_path / "bad.json"
    json_path.write_text(json.dumps([
        {"code": "E1", "device_model": "M1"},
        {"code": "", "device_model": "M2"},
    ]), encoding="utf-8")
    with pytest.raises(ValueError, match="第 2 筆"):
        load_json(json_path)


def test_load_json_rejects_non_list_content(tmp_path):
    from alarm_ingest import load_json
    json_path = tmp_path / "bad.json"
    json_path.write_text(json.dumps({"code": "E1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="陣列"):
        load_json(json_path)


def test_load_file_dispatches_by_suffix_and_rejects_unknown(tmp_path):
    from alarm_ingest import load_file
    unknown = tmp_path / "data.txt"
    unknown.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="不支援的檔案格式"):
        load_file(unknown)


# ── validate.py / commit.py：需要 ALARM_DATA_DIR 隔離 ──────────────────

@pytest.fixture
def ingest_env(tmp_path, monkeypatch):
    """比照 conftest.py 的重載機制，但額外重載 alarm_ingest 底下的子
    模組——它們在 import 當下就從 storage.py 抓走 devices_store/
    alarms_store/audit_logger 參照，不重載的話會抓到上一個測試殘留的
    singleton，指向錯的 ALARM_DATA_DIR。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "devices.json").write_text(
        json.dumps([{"id": "d1", "model": "CNC-A100", "category": ""}], ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "alarms.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("ALARM_DATA_DIR", str(data_dir))

    for mod in list(sys.modules):
        if mod == "storage" or mod.startswith("alarm_ingest"):
            sys.modules.pop(mod)

    import alarm_ingest
    return alarm_ingest


def test_validate_devices_exist_reports_missing(ingest_env):
    rows = [
        {"device_model": "CNC-A100", "code": "E1"},
        {"device_model": "GHOST-1", "code": "E2"},
        {"device_model": "GHOST-1", "code": "E3"},
    ]
    missing, counts = ingest_env.validate_devices_exist(rows, department="local")
    assert missing == {"GHOST-1": 2}
    assert counts == {"CNC-A100": 1, "GHOST-1": 2}


def test_validate_devices_exist_empty_when_all_known(ingest_env):
    rows = [{"device_model": "CNC-A100", "code": "E1"}]
    missing, _ = ingest_env.validate_devices_exist(rows, department="local")
    assert missing == {}


# ── check_variant_consistency ───────────────────────────────────────

def test_variant_consistency_no_existing_data_is_always_consistent(ingest_env):
    """機種在該部門尚無既有資料：沒有既有狀態可比對，不視為不一致
    ——第一批匯入本來就是在定義這個機種的 variant 狀態。"""
    rows = [{"code": "E1"}, {"code": "E1"}]  # 重複 code，會被判為 use_variant=True
    errors = ingest_env.check_variant_consistency(rows, department="local", device_model="CNC-A100")
    assert errors == []


def test_variant_consistency_flags_mismatch_existing_has_variant(ingest_env):
    """既有資料已啟用 variant（有非空 variant 列），但新來源代碼全部
    唯一、判定為不啟用 variant → 不一致，應回報 error。"""
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "E1", "device_model": "CNC-A100", "variant": "V1",
         "severity": "", "description": "", "cause": "", "solution": "",
         "local_solution": "", "keywords": [], "sol_steps": {}},
        department="local", on_conflict="department,device_model,code,variant",
    )
    rows = [{"code": "E9"}]  # 唯一 code → use_variant=False
    errors = ingest_env.check_variant_consistency(rows, department="local", device_model="CNC-A100")
    assert len(errors) == 1
    assert errors[0]["existing_has_variant"] is True
    assert errors[0]["new_has_variant"] is False
    assert "CNC-A100" in errors[0]["reason"]


def test_variant_consistency_flags_mismatch_existing_no_variant(ingest_env):
    """既有資料 variant 全空（未啟用），新來源代碼重複 → 判定啟用
    variant → 不一致。"""
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "E1", "device_model": "CNC-A100", "variant": "",
         "severity": "", "description": "", "cause": "", "solution": "",
         "local_solution": "", "keywords": [], "sol_steps": {}},
        department="local", on_conflict="department,device_model,code,variant",
    )
    rows = [{"code": "E9"}, {"code": "E9"}]  # 重複 code → use_variant=True
    errors = ingest_env.check_variant_consistency(rows, department="local", device_model="CNC-A100")
    assert len(errors) == 1
    assert errors[0]["existing_has_variant"] is False
    assert errors[0]["new_has_variant"] is True


def test_variant_consistency_allows_matching_state(ingest_env):
    """既有資料與新來源的 variant 啟用狀態一致時，不回報。"""
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "E1", "device_model": "CNC-A100", "variant": "V1",
         "severity": "", "description": "", "cause": "", "solution": "",
         "local_solution": "", "keywords": [], "sol_steps": {}},
        department="local", on_conflict="department,device_model,code,variant",
    )
    rows = [{"code": "E9"}, {"code": "E9"}]  # 重複 code → use_variant=True，跟既有一致
    errors = ingest_env.check_variant_consistency(rows, department="local", device_model="CNC-A100")
    assert errors == []


def test_variant_consistency_accepts_precomputed_use_variant(ingest_env):
    """呼叫端若已用 decide_variant_mode() 算過 use_variant，可以直接
    傳進來，這裡不再重算——避免兩處各自用同一個公式算，容易在改動時
    只改一邊而不同步。傳入的判定結果即使跟 rows 實際內容矛盾也要被
    採信（呼叫端負責算對，這裡只負責比對）。"""
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "E1", "device_model": "CNC-A100", "variant": "",
         "severity": "", "description": "", "cause": "", "solution": "",
         "local_solution": "", "keywords": [], "sol_steps": {}},
        department="local", on_conflict="department,device_model,code,variant",
    )
    # rows 本身 code 唯一（自然計算應為 use_variant=False），但強制傳入 True
    rows = [{"code": "E9"}]
    errors = ingest_env.check_variant_consistency(
        rows, department="local", device_model="CNC-A100", use_variant=True,
    )
    assert len(errors) == 1  # 既有 False vs 傳入 True → 不一致
    assert errors[0]["new_has_variant"] is True


def test_variant_consistency_scoped_to_device_model(ingest_env):
    """一致性檢查只比對同一 device_model 的既有資料，不同機種的
    variant 狀態不該互相影響判斷。"""
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "E1", "device_model": "OTHER-MODEL", "variant": "V1",
         "severity": "", "description": "", "cause": "", "solution": "",
         "local_solution": "", "keywords": [], "sol_steps": {}},
        department="local", on_conflict="department,device_model,code,variant",
    )
    rows = [{"code": "E9"}]  # CNC-A100 在該部門尚無既有資料
    errors = ingest_env.check_variant_consistency(rows, department="local", device_model="CNC-A100")
    assert errors == []


def test_dedupe_check_flags_duplicate_device_code_variant(ingest_env):
    rows = [
        {"device_model": "M1", "code": "E1", "variant": ""},
        {"device_model": "M1", "code": "E1", "variant": ""},
    ]
    dupes = ingest_env.dedupe_check(rows)
    assert dupes == [("M1", "E1", "")]


def test_dedupe_check_allows_same_code_different_variant(ingest_env):
    rows = [
        {"device_model": "M1", "code": "E1", "variant": "A"},
        {"device_model": "M1", "code": "E1", "variant": "B"},
    ]
    assert ingest_env.dedupe_check(rows) == []


def test_completeness_report_counts_nonempty_fields(ingest_env):
    rows = [
        {"cause": "x", "solution": "", "local_solution": "y"},
        {"cause": "", "solution": "", "local_solution": ""},
    ]
    report = ingest_env.completeness_report(rows)
    assert report == {"cause": 1, "solution": 0, "local_solution": 1}


def test_completeness_report_empty_rows(ingest_env):
    assert ingest_env.completeness_report([]) == {}


def test_commit_rows_all_succeed(ingest_env):
    rows = [
        {"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "", "description": "",
         "cause": "", "solution": "", "local_solution": "", "keywords": [], "sol_steps": {}},
        {"code": "E2", "device_model": "CNC-A100", "variant": "", "severity": "", "description": "",
         "cause": "", "solution": "", "local_solution": "", "keywords": [], "sol_steps": {}},
    ]
    result = ingest_env.commit_rows(rows, department="local", import_mode="upsert")
    # snapshot_id 另外驗證（JsonStore fallback 下必為 None，見
    # test_commit_rows_snapshot_id_none_on_json_store）——這裡整批復原
    # 尚未在 pytest 環境可測（見 storage.ImportSnapshotStore 說明：只在
    # Supabase 模式運作），不納入這個既有斷言以免耦合兩件事。
    result.pop("snapshot_id")
    assert result == {
        "committed": True, "partial": False, "succeeded": 2, "failed": 0,
        "first_failure": None, "recovery": "全部寫入成功。",
    }

    from storage import alarms_store
    written = alarms_store.load(department="local")
    assert {r["code"] for r in written} == {"E1", "E2"}


def test_commit_rows_snapshot_id_none_on_json_store(ingest_env):
    """整批復原（ImportSnapshotStore）只在 Supabase 模式運作——pytest
    走 JsonStore fallback，snapshot_id 必為 None，呼叫端（前端）據此
    判斷這次匯入不提供復原入口，不是快照寫入失敗。"""
    rows = [{"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "",
             "description": "", "cause": "", "solution": "", "local_solution": "",
             "keywords": [], "sol_steps": {}}]
    result = ingest_env.commit_rows(rows, department="local", import_mode="upsert")
    assert result["snapshot_id"] is None


# ── OPTIONAL_FIELDS 保護：批次匯入是 upsert 語意，不是整列取代 ────────
#
# 這組測試驗的是 JsonStore（本機/pytest 環境）的部分更新行為。
#
# ⚠️ 這組測試守不到什麼：SupabaseStore（正式環境）的對應行為 pytest
# 測不到（沒有真實 Supabase 連線）。這個修法的前提——PostgREST 的
# merge-duplicates upsert 對 payload 缺席欄位保留舊值——已於 2026-08-24
# 對 zztest 部門手動實測確認：送一筆含 keywords=['重要','關鍵字'] 的
# 資料，再送一次不含 keywords 的 payload（solution 有送並確認被更新），
# 查回來 keywords 維持 ['重要','關鍵字'] 不變。若日後 PostgREST 行為
# 變動、或這個修法被後續改動破壞，這組 pytest 不會發現，需要重跑那次
# 手動實測（已寫進 sentinel_pack/verify_isolation.sh T-27）。

def test_row_to_alarm_present_tracks_source_fields(ingest_env):
    """_present 只記錄來源實際提供的鍵（且限定在 ALARM_FIELDS 內），
    用於 commit_rows() 決定哪些 OPTIONAL_FIELDS 要保護。"""
    row = ingest_env.row_to_alarm({"code": "E1", "device_model": "M1", "keywords": "a;b"})
    assert "keywords" in row["_present"]
    assert "severity" not in row["_present"]
    assert "sol_steps" not in row["_present"]
    assert isinstance(row["_present"], list)  # 不是 set——見 parse.py 的說明


def test_commit_rows_preserves_absent_optional_field(ingest_env):
    """批次匯入更新既有列時，來源沒有 keywords 欄，既有 keywords 應該
    保留，不被清空成 []——這是本輪修正的核心行為。"""
    from storage import alarms_store

    alarms_store.upsert_one(
        {"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "",
         "description": "舊描述", "cause": "", "solution": "", "local_solution": "",
         "keywords": ["重要", "關鍵字"], "sol_steps": {}},
        department="local", on_conflict="department,device_model,code,variant",
    )

    # 模擬來源只有必要表頭、沒有 keywords 欄（row_to_alarm 的 _present
    # 因此不含 "keywords"）
    row = ingest_env.row_to_alarm({
        "code": "E1", "device_model": "CNC-A100", "variant": "",
        "description": "新描述", "cause": "", "solution": "更新後的方案", "local_solution": "",
    })
    assert "keywords" not in row["_present"]

    result = ingest_env.commit_rows([row], department="local", import_mode="upsert")
    assert result["succeeded"] == 1

    after = alarms_store.load(department="local")
    updated = next(a for a in after if a["code"] == "E1")
    assert updated["description"] == "新描述"
    assert updated["solution"] == "更新後的方案"
    assert updated["keywords"] == ["重要", "關鍵字"]  # 保留，不是被清空


def test_commit_rows_overwrites_optional_field_when_source_provides_it(ingest_env):
    """反例：來源確實有填 keywords 時，要正常覆蓋——OPTIONAL_FIELDS
    的保護只在「缺席」時生效，不是「keywords 永遠不會被更新」。"""
    from storage import alarms_store

    alarms_store.upsert_one(
        {"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "",
         "description": "d", "cause": "", "solution": "", "local_solution": "",
         "keywords": ["舊關鍵字"], "sol_steps": {}},
        department="local", on_conflict="department,device_model,code,variant",
    )

    row = ingest_env.row_to_alarm({
        "code": "E1", "device_model": "CNC-A100", "variant": "",
        "description": "d", "cause": "", "solution": "", "local_solution": "",
        "keywords": "新關鍵字",
    })
    assert "keywords" in row["_present"]

    ingest_env.commit_rows([row], department="local", import_mode="upsert")

    after = alarms_store.load(department="local")
    updated = next(a for a in after if a["code"] == "E1")
    assert updated["keywords"] == ["新關鍵字"]


def test_jsonstore_upsert_preserves_absent_fields_for_non_ingest_callers(ingest_env):
    """JsonStore 必須與 SupabaseStore 的 merge-duplicates 行為一致：
    payload 缺席的欄位保留舊值，不清空也不刪除。

    原本 JsonStore 是整列取代（raw[i] = write_item），缺欄位時該欄會
    完全消失——比覆蓋成空值更糟，而且 pytest 全過、正式環境行為不同。
    這是「JsonStore 與 SupabaseStore 分歧」那類問題的又一個實例（見
    CLAUDE.md 已知的其他分歧：assert_session_valid() 非 Supabase 模式
    提早 return、登入節流 pytest 不執行）。

    這條不透過 _to_payload()（那是批次匯入專用），直接測 JsonStore
    本身，確認任何呼叫端送部分 payload 給 upsert_one() 都會得到一致
    行為，不只是批次匯入這一條路徑。
    """
    from storage import alarms_store

    alarms_store.upsert_one(
        {"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "嚴重",
         "description": "d", "cause": "c", "solution": "s", "local_solution": "l",
         "keywords": ["a", "b"], "sol_steps": {"1": "x"}},
        department="local", on_conflict="department,device_model,code,variant",
    )

    # 只送必要欄位（主鍵 + description），不送 keywords/sol_steps/severity
    alarms_store.upsert_one(
        {"code": "E1", "device_model": "CNC-A100", "variant": "", "description": "新描述"},
        department="local", on_conflict="department,device_model,code,variant",
    )

    after = alarms_store.load(department="local")
    updated = next(a for a in after if a["code"] == "E1")
    assert updated["description"] == "新描述"
    assert updated["keywords"] == ["a", "b"]
    assert updated["sol_steps"] == {"1": "x"}
    assert updated["severity"] == "嚴重"
    assert updated["cause"] == "c"  # 完全沒提到的欄位同樣要保留


def test_commit_rows_stops_at_first_failure(ingest_env, monkeypatch):
    """遇錯即停：模擬第 2 筆寫入失敗，驗證第 3 筆不會被嘗試、
    succeeded 只計入實際寫入的筆數。"""
    from storage import alarms_store

    calls = []
    original_upsert = alarms_store.upsert_one

    def flaky_upsert(item, department, on_conflict):
        calls.append(item["code"])
        if item["code"] == "E2":
            raise RuntimeError("simulated PostgREST failure")
        return original_upsert(item, department=department, on_conflict=on_conflict)

    monkeypatch.setattr(alarms_store, "upsert_one", flaky_upsert)

    rows = [
        {"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "", "description": "",
         "cause": "", "solution": "", "local_solution": "", "keywords": [], "sol_steps": {}},
        {"code": "E2", "device_model": "CNC-A100", "variant": "", "severity": "", "description": "",
         "cause": "", "solution": "", "local_solution": "", "keywords": [], "sol_steps": {}},
        {"code": "E3", "device_model": "CNC-A100", "variant": "", "severity": "", "description": "",
         "cause": "", "solution": "", "local_solution": "", "keywords": [], "sol_steps": {}},
    ]
    result = ingest_env.commit_rows(rows, department="local", import_mode="upsert")

    assert calls == ["E1", "E2"]  # E3 從未被嘗試
    assert result["partial"] is True
    assert result["succeeded"] == 1
    assert result["failed"] == 2
    assert result["first_failure"]["row"] == 2
    assert result["first_failure"]["code"] == "E2"
    assert "重新匯入同一份檔案即可補齊" in result["recovery"]


def test_commit_rows_append_mode_recovery_warns_not_idempotent(ingest_env, monkeypatch):
    from storage import alarms_store

    def always_fail(item, department, on_conflict):
        raise RuntimeError("boom")

    monkeypatch.setattr(alarms_store, "upsert_one", always_fail)

    rows = [{"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "", "description": "",
             "cause": "", "solution": "", "local_solution": "", "keywords": [], "sol_steps": {}}]
    result = ingest_env.commit_rows(rows, department="local", import_mode="append")

    assert result["partial"] is True
    assert "撞上重複主鍵" in result["recovery"]


def test_commit_rows_partial_failure_writes_audit_log(ingest_env, monkeypatch):
    from storage import alarms_store, audit_logger

    def always_fail(item, department, on_conflict):
        raise RuntimeError("boom")

    monkeypatch.setattr(alarms_store, "upsert_one", always_fail)

    logged = []
    monkeypatch.setattr(audit_logger, "log", lambda operation, **kw: logged.append((operation, kw)))

    rows = [{"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "", "description": "",
             "cause": "", "solution": "", "local_solution": "", "keywords": [], "sol_steps": {}}]
    ingest_env.commit_rows(rows, department="local", import_mode="upsert")

    assert len(logged) == 1
    operation, kw = logged[0]
    assert operation == "bulk_import_partial"
    assert kw["department"] == "local"
    assert "成功 0 / 1" in kw["new_data"]["code"]


def test_commit_rows_no_audit_log_when_all_succeed(ingest_env, monkeypatch):
    from storage import audit_logger

    logged = []
    monkeypatch.setattr(audit_logger, "log", lambda operation, **kw: logged.append((operation, kw)))

    rows = [{"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "", "description": "",
             "cause": "", "solution": "", "local_solution": "", "keywords": [], "sol_steps": {}}]
    ingest_env.commit_rows(rows, department="local", import_mode="upsert")

    assert logged == []


def test_commit_rows_truncates_long_error_reason(ingest_env, monkeypatch):
    from storage import alarms_store

    def always_fail(item, department, on_conflict):
        raise RuntimeError("x" * 500)

    monkeypatch.setattr(alarms_store, "upsert_one", always_fail)

    rows = [{"code": "E1", "device_model": "CNC-A100", "variant": "", "severity": "", "description": "",
             "cause": "", "solution": "", "local_solution": "", "keywords": [], "sol_steps": {}}]
    result = ingest_env.commit_rows(rows, department="local", import_mode="upsert")

    assert len(result["first_failure"]["reason"]) <= 200


# ── quality.py：clean / decide_variant_mode / split_code / dedup ───────

def test_clean_collapses_whitespace_and_treats_na_as_empty():
    from alarm_ingest import clean
    assert clean("  a\n  b ") == "a b"
    assert clean("N/A") == ""
    assert clean("#N/A") == ""
    assert clean(None) == ""
    assert clean(123) == "123"


def test_decide_variant_mode_always_never_override_auto_detection():
    from alarm_ingest import decide_variant_mode
    rows = [{"code": "E1"}, {"code": "E1"}]  # 重複，auto 會判 True
    use, reason = decide_variant_mode(rows, "never")
    assert use is False
    assert "手動指定" in reason


def test_decide_variant_mode_auto_unique_codes():
    from alarm_ingest import decide_variant_mode
    rows = [{"code": "E1"}, {"code": "E2"}]
    use, reason = decide_variant_mode(rows, "auto")
    assert use is False
    assert "全部唯一" in reason


def test_decide_variant_mode_auto_duplicate_codes():
    from alarm_ingest import decide_variant_mode
    rows = [{"code": "E1"}, {"code": "E1"}, {"code": "E2"}]
    use, reason = decide_variant_mode(rows, "auto")
    assert use is True
    assert "重複" in reason


def test_split_code_extracts_leading_number():
    import re
    from alarm_ingest import split_code
    code_re = re.compile(r"^\s*(\d{3,6})\s*[-–—:：]?\s*(.*)$")
    code, variant = split_code("31033 Operation active weigh-in filling 1", code_re)
    assert code == "31033"
    assert variant == "Operation active weigh-in filling 1"


def test_split_code_no_match_returns_none_code():
    import re
    from alarm_ingest import split_code
    code_re = re.compile(r"^\s*(\d{3,6})\s*[-–—:：]?\s*(.*)$")
    code, variant = split_code("沒有代碼的文字", code_re)
    assert code is None
    assert variant == "沒有代碼的文字"


def test_dedup_keeps_most_complete_row_and_counts_conflicts():
    from alarm_ingest import dedup
    rows = [
        {"code": "E1", "variant": "", "cause": "短", "action": ""},
        {"code": "E1", "variant": "", "cause": "比較完整的原因", "action": "動作"},
        {"code": "E2", "variant": "", "cause": "x", "action": "y"},
    ]
    result, conflicts = dedup(rows)
    assert len(result) == 2
    e1 = next(r for r in result if r["code"] == "E1")
    assert e1["cause"] == "比較完整的原因"
    assert conflicts == 1


def test_dedup_no_conflict_when_single_row_per_key():
    from alarm_ingest import dedup
    rows = [{"code": "E1", "variant": "", "cause": "x", "action": "y"}]
    result, conflicts = dedup(rows)
    assert len(result) == 1
    assert conflicts == 0


# ── load_excel：固定範本（第一個工作表、第一列表頭，不做智慧偵測）───

EXCEL_HEADERS = ["code", "device_model", "variant", "description", "cause", "solution", "local_solution"]


def _write_excel(path, headers, data_rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in data_rows:
        ws.append(row)
    wb.save(path)


def test_load_excel_parses_first_sheet_with_fixed_headers(tmp_path):
    from alarm_ingest import load_excel
    xlsx_path = tmp_path / "good.xlsx"
    _write_excel(xlsx_path, EXCEL_HEADERS, [
        ["E1", "M1", "", "主軸過載", "負荷過大", "降低進給", ""],
        ["E2", "M1", "V1", "警報二", "", "", "現場處置"],
    ])
    rows = load_excel(xlsx_path)
    assert len(rows) == 2


def test_load_excel_handles_int_typed_code(tmp_path):
    """openpyxl 回傳的儲存格型別取決於儲存格格式，不保證是字串——
    數字型儲存格轉字串要正常運作，不能崩潰或產生非預期格式。"""
    from alarm_ingest import load_excel
    xlsx_path = tmp_path / "int_code.xlsx"
    _write_excel(xlsx_path, EXCEL_HEADERS, [
        [31033, "M1", "", "整數型代碼", "", "", ""],
    ])
    rows = load_excel(xlsx_path)
    assert rows[0]["code"] == "31033"


def test_cell_to_str_normalizes_integer_valued_float():
    """_cell_to_str() 是 load_excel() 型別轉換的核心：「整數值但 float
    型別」的儲存格（公式結果、複製貼上運算、pandas 匯出等常見來源，
    非 openpyxl 自己寫入時容易重現，但確實是真實使用者檔案會出現的
    型態）若直接 str() 會產生 "24.0" 這種帶小數尾碼的錯誤代碼——這個
    情境不會崩潰，是安靜產生錯的代碼，比 AttributeError 更難發現。
    float 若無小數部分先轉 int 再轉字串；有小數部分（儲存格真的存了
    非整數）保留原樣，讓後續驗證（row_to_alarm 的必填/格式檢查）去
    處理這種本來就不像有效代碼格式的輸入。"""
    from alarm_ingest.parse import _cell_to_str
    assert _cell_to_str(24.0) == "24"
    assert _cell_to_str(31033.0) == "31033"
    assert _cell_to_str(31033.5) == "31033.5"  # 真的有小數：保留原樣
    assert _cell_to_str(24) == "24"            # int 型別：不受影響
    assert _cell_to_str("0024") == "0024"      # str 型別：前導零保留
    assert _cell_to_str(None) == ""


def test_load_excel_missing_required_header_rejected(tmp_path):
    from alarm_ingest import load_excel
    xlsx_path = tmp_path / "bad.xlsx"
    _write_excel(xlsx_path, ["code", "device_model"], [["E1", "M1"]])
    with pytest.raises(ValueError, match="缺少欄位"):
        load_excel(xlsx_path)


def test_load_excel_header_tolerates_case_and_whitespace(tmp_path):
    from alarm_ingest import load_excel
    xlsx_path = tmp_path / "good.xlsx"
    _write_excel(
        xlsx_path,
        ["CODE", " Device_Model ", "Variant", "Description", "CAUSE", "Solution", "Local_Solution"],
        [["E1", "M1", "", "d", "負荷過大", "", ""]],
    )
    rows = load_excel(xlsx_path)
    assert len(rows) == 1
    assert rows[0]["code"] == "E1"
    assert rows[0]["cause"] == "負荷過大"


def test_load_excel_reports_row_number_on_error(tmp_path):
    from alarm_ingest import load_excel
    xlsx_path = tmp_path / "bad.xlsx"
    _write_excel(xlsx_path, EXCEL_HEADERS, [
        ["E1", "M1", "", "", "", "", ""],
        ["", "M2", "", "", "", "", ""],  # 第 3 列缺 code
    ])
    with pytest.raises(ValueError, match="第 3 列"):
        load_excel(xlsx_path)


def test_load_excel_skips_blank_trailing_rows(tmp_path):
    """Excel 常見的尾端完全空白列（人工編輯留下的）不該被當成資料列，
    也不該觸發「code 為必填」錯誤——那不是使用者想匯入的一列，是
    儲存格格式殘留。"""
    from alarm_ingest import load_excel
    xlsx_path = tmp_path / "trailing_blank.xlsx"
    _write_excel(xlsx_path, EXCEL_HEADERS, [
        ["E1", "M1", "", "desc", "", "", ""],
        [None, None, None, None, None, None, None],
    ])
    rows = load_excel(xlsx_path)
    assert len(rows) == 1
    assert rows[0]["code"] == "E1"


def test_load_excel_empty_file_rejected(tmp_path):
    from alarm_ingest import load_excel
    xlsx_path = tmp_path / "empty.xlsx"
    import openpyxl
    wb = openpyxl.Workbook()
    wb.save(xlsx_path)
    with pytest.raises(ValueError, match="表頭"):
        load_excel(xlsx_path)


def test_load_file_dispatches_xlsx_to_load_excel(tmp_path):
    from alarm_ingest import load_file
    xlsx_path = tmp_path / "good.xlsx"
    _write_excel(xlsx_path, EXCEL_HEADERS, [["E1", "M1", "", "d", "", "", ""]])
    rows = load_file(xlsx_path)
    assert len(rows) == 1
    assert rows[0]["code"] == "E1"


# ── undo_snapshot()：整批復原核心邏輯 ──────────────────────────────
#
# ImportSnapshotStore 只在 Supabase 模式運作（JsonStore fallback 下
# get_snapshot() 固定回 None），所以這裡不用 ingest_env，改用
# monkeypatch 直接替換 import_snapshot_store 的方法來測 undo_snapshot()
# 本身逐筆回寫/刪除的邏輯——跟 Supabase 是否真的連得上無關。

def test_undo_snapshot_not_found(monkeypatch):
    import storage
    from alarm_ingest.commit import undo_snapshot

    monkeypatch.setattr(storage.import_snapshot_store, "get_snapshot", lambda sid, dept: None)
    result = undo_snapshot(1, department="local")
    assert result == {"found": False}


def test_undo_snapshot_already_undone(monkeypatch):
    import storage
    from alarm_ingest.commit import undo_snapshot

    monkeypatch.setattr(
        storage.import_snapshot_store, "get_snapshot",
        lambda sid, dept: {"snapshot": {"undone_at": "2026-08-01T00:00:00Z"}, "rows": []},
    )
    result = undo_snapshot(1, department="local")
    assert result == {"found": True, "already_undone": True}


def test_undo_snapshot_restores_previous_value_and_deletes_new_row(monkeypatch, tmp_path):
    """rows 有兩筆：E1 在 commit 前已存在（before_data 非 None，undo
    要把舊值寫回）；E2 在 commit 前不存在（before_data 為 None，undo
    要刪除）。"""
    import json as json_mod
    import sys as sys_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "devices.json").write_text(
        json_mod.dumps([{"id": "d1", "model": "CNC-A100", "category": ""}], ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "alarms.json").write_text(
        json_mod.dumps([
            {"code": "E1", "device_model": "CNC-A100", "variant": "", "description": "新值（要被復原掉）"},
            {"code": "E2", "device_model": "CNC-A100", "variant": "", "description": "commit 時新增的"},
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    import os
    os.environ["ALARM_DATA_DIR"] = str(data_dir)
    for mod in list(sys_mod.modules):
        if mod == "storage" or mod.startswith("alarm_ingest"):
            sys_mod.modules.pop(mod)

    import storage
    from alarm_ingest.commit import undo_snapshot

    snapshot_data = {
        "snapshot": {"undone_at": None, "device_models": "CNC-A100"},
        "rows": [
            {"device_model": "CNC-A100", "code": "E1", "variant": "",
             "before_data": {"code": "E1", "device_model": "CNC-A100", "variant": "", "description": "舊值"}},
            {"device_model": "CNC-A100", "code": "E2", "variant": "", "before_data": None},
        ],
    }
    marked = {}
    monkeypatch.setattr(storage.import_snapshot_store, "get_snapshot", lambda sid, dept: snapshot_data)
    monkeypatch.setattr(storage.import_snapshot_store, "mark_undone", lambda sid, result: marked.update(result))

    result = undo_snapshot(1, department="local")
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert marked["succeeded"] == 2

    written = storage.alarms_store.load(department="local")
    assert {r["code"] for r in written} == {"E1"}
    assert written[0]["description"] == "舊值"
