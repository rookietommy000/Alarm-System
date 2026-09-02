"""異常匯入資料待審核端點（PendingAlarmImportStore，見 migration
010_add_pending_alarm_imports.sql）。

PendingAlarmImportStore 沒有 JsonStore fallback（跟 AlarmSuggestionStore
同款，只服務 Supabase），這裡用 monkeypatch 讓它不真的打 HTTP，測的是
app.py 端點層的組裝邏輯（四欄主鍵是否正確、稽核軌跡是否記對、accept/
reject 分工是否正確），不是 PendingAlarmImportStore 對真實 Supabase 是否
真的生效——兩者是不同等級的結論（CLAUDE.md「測試的能力邊界」）。
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def _pending_row(**overrides) -> dict:
    row = {
        "id": 1, "department": "local", "device_model": "CNC-A100", "code": "0099",
        "variant": "", "description": "測試待審描述", "severity": None, "cause": None,
        "solution": None, "keywords": None, "sol_steps": None, "status": "pending",
        "source": "bulk_import", "flagged_reason": "格式不符", "raw_source_text": "0099.0",
        "confidence": None, "submitted_by": "tester", "reviewed_by": None,
        "reviewed_at": None, "review_note": None,
    }
    row.update(overrides)
    return row


def test_list_pending_alarm_imports_scoped_to_department(client, monkeypatch):
    import storage as storage_mod

    calls = []
    monkeypatch.setattr(
        storage_mod.pending_alarm_import_store, "list_pending",
        lambda department: calls.append(department) or [_pending_row()],
    )

    r = client.get("/api/admin/pending-alarm-imports")

    assert r.status_code == 200
    assert calls == ["local"]  # 本機模式 client fixture 登入的是 local 部門


def test_accept_upserts_full_four_field_primary_key(client, monkeypatch):
    """accept 時 upsert_one() 收到的 item 必須明確含 department/
    device_model/code/variant 四欄，variant 缺席時（row 裡沒有這個
    key、或值是 None）要 fallback 成空字串，不能讓 None 被送進去。"""
    import storage as storage_mod

    row = _pending_row(id=5, variant=None)  # 模擬 variant 為 None 的情境
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "review", lambda *a, **kw: {"id": 5, "status": "approved"})

    captured = {}

    def _fake_upsert(item, department, on_conflict):
        captured["item"] = item
        captured["department"] = department
        captured["on_conflict"] = on_conflict
        return {**item}

    monkeypatch.setattr(storage_mod.alarms_store, "upsert_one", _fake_upsert)

    r = client.put("/api/admin/pending-alarm-imports/5", json={"action": "accept"})

    assert r.status_code == 200
    assert captured["item"]["department"] == "local"
    assert captured["item"]["device_model"] == "CNC-A100"
    assert captured["item"]["code"] == "0099"
    assert captured["item"]["variant"] == ""  # None 必須 fallback 成空字串，不是 None
    assert captured["on_conflict"] == "department,device_model,code,variant"


def test_accept_only_includes_optional_fields_present_in_row(client, monkeypatch):
    """severity/cause/solution/keywords/sol_steps 若在待審資料裡是
    None（沒填），不該以 None 值送進 upsert_one() 的 item——比照
    commit.py 的 OPTIONAL_FIELDS 精神，缺席就不送這個 key，不是送
    None 去覆蓋掉。這裡只驗證 severity 有值、cause 沒值的情境。"""
    import storage as storage_mod

    row = _pending_row(id=6, severity="警告", cause=None, solution=None)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "review", lambda *a, **kw: {"id": 6, "status": "approved"})

    captured = {}
    monkeypatch.setattr(
        storage_mod.alarms_store, "upsert_one",
        lambda item, department, on_conflict: captured.update(item) or item,
    )

    r = client.put("/api/admin/pending-alarm-imports/6", json={"action": "accept"})

    assert r.status_code == 200
    assert captured["severity"] == "警告"
    assert "cause" not in captured
    assert "solution" not in captured


def test_accept_logs_audit_as_create_with_no_old_data(client, monkeypatch):
    """稽核軌跡記 CREATE（同 create_alarm() 端點慣例），old_data 一律
    None——這是新增全新一列，不是改既有列，沒有舊值可對照。"""
    import storage as storage_mod

    row = _pending_row(id=7)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "review", lambda *a, **kw: {"id": 7, "status": "approved"})
    monkeypatch.setattr(storage_mod.alarms_store, "upsert_one", lambda item, department, on_conflict: {**item})

    audit_calls = []
    monkeypatch.setattr(
        storage_mod.audit_logger, "log",
        lambda action, **kw: audit_calls.append((action, kw)),
    )

    r = client.put("/api/admin/pending-alarm-imports/7", json={"action": "accept"})

    assert r.status_code == 200
    assert len(audit_calls) == 1
    action, kw = audit_calls[0]
    assert action == "CREATE"
    # old_data 沒有被傳（同 create_alarm() 端點的既有慣例，見 app.py:727
    # 只傳 new_data），不是被顯式傳了 None——AuditLogger.log() 本身
    # old_data 參數有預設值 None，呼叫端不傳等同新增沒有舊值可對照。
    assert kw.get("old_data") is None
    assert kw["department"] == "local"


def test_reject_does_not_touch_alarms_store(client, monkeypatch):
    """reject 只更新 pending_alarm_imports 本身狀態，完全不呼叫
    upsert_one()——退回不該對 alarms 表有任何寫入動作。"""
    import storage as storage_mod

    row = _pending_row(id=8)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)
    review_calls = []
    monkeypatch.setattr(
        storage_mod.pending_alarm_import_store, "review",
        lambda import_id, status, reviewed_by, review_note: review_calls.append(status) or {"id": import_id, "status": status},
    )
    upsert_calls = []
    monkeypatch.setattr(
        storage_mod.alarms_store, "upsert_one",
        lambda *a, **kw: upsert_calls.append(1),
    )

    r = client.put("/api/admin/pending-alarm-imports/8", json={"action": "reject"})

    assert r.status_code == 200
    assert review_calls == ["rejected"]
    assert upsert_calls == []


def test_review_endpoint_returns_uniform_404_when_department_mismatches(client, monkeypatch):
    """測的是 app.py review_pending_alarm_import() 裡
    `if scope == DeptScope.DEPT and row["department"] != dept: abort(404)`
    這個純邏輯分支（不透露「這筆存在但不是你的部門」，同
    review_suggestion() 的既有原則），不是驗證跨部門隔離機制本身——
    真正的隔離驗證只在 sentinel_pack/verify_isolation.sh 對真實
    Supabase 執行，見 CLAUDE.md「測試的能力邊界」。"""
    from app import NOT_FOUND_MSG
    import storage as storage_mod

    row = _pending_row(id=9, department="other-dept")
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)

    r = client.put("/api/admin/pending-alarm-imports/9", json={"action": "accept"})

    assert r.status_code == 404
    assert r.get_json()["error"] == NOT_FOUND_MSG


def test_review_already_reviewed_returns_409(client, monkeypatch):
    import storage as storage_mod

    row = _pending_row(id=10, status="approved")
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)

    r = client.put("/api/admin/pending-alarm-imports/10", json={"action": "accept"})

    assert r.status_code == 409


def test_review_missing_row_returns_404(client, monkeypatch):
    import storage as storage_mod

    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: None)

    r = client.put("/api/admin/pending-alarm-imports/999", json={"action": "accept"})

    assert r.status_code == 404


def test_review_invalid_action_returns_400(client, monkeypatch):
    import storage as storage_mod

    row = _pending_row(id=11)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)

    r = client.put("/api/admin/pending-alarm-imports/11", json={"action": "delete"})

    assert r.status_code == 400
