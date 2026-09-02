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
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "claim", lambda *a, **kw: {"id": 5, "status": "approved"})

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
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "claim", lambda *a, **kw: {"id": 6, "status": "approved"})

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
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "claim", lambda *a, **kw: {"id": 7, "status": "approved"})
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
    """reject 只更新 pending_alarm_imports 本身狀態（透過 claim()，同
    accept 的 CAS 保護），完全不呼叫 upsert_one()——退回不該對 alarms
    表有任何寫入動作。"""
    import storage as storage_mod

    row = _pending_row(id=8)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)
    claim_calls = []
    monkeypatch.setattr(
        storage_mod.pending_alarm_import_store, "claim",
        lambda row_id, **kw: claim_calls.append(kw["to_status"]) or {"id": row_id, "status": kw["to_status"]},
    )
    upsert_calls = []
    monkeypatch.setattr(
        storage_mod.alarms_store, "upsert_one",
        lambda *a, **kw: upsert_calls.append(1),
    )

    r = client.put("/api/admin/pending-alarm-imports/8", json={"action": "reject"})

    assert r.status_code == 200
    assert claim_calls == ["rejected"]
    assert upsert_calls == []


def test_reject_returns_409_when_claim_fails(client, monkeypatch):
    """reject 分支也要有 CAS 保護——claim() 回 None 代表這筆已經被
    別人審核過，要 409，不能讓 reject 落成「accept 受保護、reject
    沒受保護」的不對稱。"""
    import storage as storage_mod

    row = _pending_row(id=15)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "claim", lambda *a, **kw: None)

    r = client.put("/api/admin/pending-alarm-imports/15", json={"action": "reject"})

    assert r.status_code == 409


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


def test_accept_calls_claim_before_upsert_not_review(client, monkeypatch):
    """accept 分支必須呼叫 claim()（CAS 搶佔）而不是 review()——
    CLAUDE.md「狀態轉換鐵則」：狀態轉換要在寫入 alarms 之前完成，
    review() 是舊的、最後才更新狀態的寫法，這支端點從一開始就不該用它。"""
    import storage as storage_mod

    row = _pending_row(id=12)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)

    claim_calls = []
    monkeypatch.setattr(
        storage_mod.pending_alarm_import_store, "claim",
        lambda row_id, **kw: claim_calls.append((row_id, kw)) or {"id": row_id, "status": "approved"},
    )
    review_calls = []
    monkeypatch.setattr(
        storage_mod.pending_alarm_import_store, "review",
        lambda *a, **kw: review_calls.append(1),
    )
    monkeypatch.setattr(storage_mod.alarms_store, "upsert_one", lambda item, department, on_conflict: {**item})

    r = client.put("/api/admin/pending-alarm-imports/12", json={"action": "accept"})

    assert r.status_code == 200
    assert len(claim_calls) == 1
    row_id, kw = claim_calls[0]
    assert row_id == 12
    assert kw["from_status"] == "pending"
    assert kw["to_status"] == "approved"
    assert review_calls == []  # accept 分支完全不該碰 review()


def test_accept_returns_409_when_claim_fails(client, monkeypatch):
    """claim() 回 None 代表這筆已被別人搶先審核過（CAS 條件不符），
    要直接 409、完全不呼叫 upsert_one()——不能繼續往下寫 alarms。"""
    import storage as storage_mod

    row = _pending_row(id=13)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "claim", lambda *a, **kw: None)
    upsert_calls = []
    monkeypatch.setattr(
        storage_mod.alarms_store, "upsert_one",
        lambda *a, **kw: upsert_calls.append(1),
    )

    r = client.put("/api/admin/pending-alarm-imports/13", json={"action": "accept"})

    assert r.status_code == 409
    assert upsert_calls == []


def test_accept_releases_claim_when_upsert_fails(client, monkeypatch):
    """claim 成功後若 upsert_one() 拋錯（例如網路中斷），必須呼叫
    release() 把狀態退回 pending，不能留下「已標記 approved 但 alarms
    沒真的新增」的孤兒記錄（CLAUDE.md「狀態轉換鐵則」的 release 步驟）。
    這裡驗證的是端點層有沒有接住例外並呼叫 release，不驗證 release()
    本身對真實 Supabase 是否生效（PendingAlarmImportStore 只服務
    Supabase，pytest 環境測不到那個層級）。"""
    import storage as storage_mod

    row = _pending_row(id=14)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "claim", lambda *a, **kw: {"id": 14, "status": "approved"})
    release_calls = []
    monkeypatch.setattr(
        storage_mod.pending_alarm_import_store, "release",
        lambda row_id, **kw: release_calls.append(row_id),
    )

    def _boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(storage_mod.alarms_store, "upsert_one", _boom)

    # TESTING=True 讓 Flask 把例外原樣往外拋（不吞成 500 回應），這裡要
    # 驗證的是 release() 真的被呼叫到，例外本身有沒有被轉成 500 不是
    # 這個測試的重點。
    import pytest
    with pytest.raises(RuntimeError, match="network down"):
        client.put("/api/admin/pending-alarm-imports/14", json={"action": "accept"})

    assert release_calls == [14]


def test_accept_returns_400_with_clear_consequence_when_pk_field_empty(client, monkeypatch):
    """upsert_one() 的主鍵欄位空值保護（storage.py，外部審查
    2026-09-02）拋出的 ValueError 要被接住轉成明確 400——不能讓這個
    ValueError 落到 test_accept_releases_claim_when_upsert_fails 驗證
    的「泛用例外往外拋」分支（那樣會變成 500，技術性訊息直接洩漏給
    前端）。錯誤訊息要講清楚實際後果（核准後無法透過管理介面刪除，
    需要工程師介入），不能只寫「不建議核准」這種含糊措辭（老師方案2
    的具體要求）。release 仍要被呼叫，同其他失敗分支一致。"""
    import storage as storage_mod

    row = _pending_row(id=16)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "claim", lambda *a, **kw: {"id": 16, "status": "approved"})
    release_calls = []
    monkeypatch.setattr(
        storage_mod.pending_alarm_import_store, "release",
        lambda row_id, **kw: release_calls.append(row_id),
    )

    def _reject_empty_pk(*a, **kw):
        raise ValueError("alarms: 主鍵欄位 device_model 不可為空")

    monkeypatch.setattr(storage_mod.alarms_store, "upsert_one", _reject_empty_pk)

    r = client.put("/api/admin/pending-alarm-imports/16", json={"action": "accept"})

    assert r.status_code == 400
    body = r.get_json()
    assert "無法核准寫入" in body["error"]
    assert "無法刪除" in body["error"]
    assert "工程師" in body["error"]
    assert release_calls == [16]


def test_review_invalid_action_returns_400(client, monkeypatch):
    import storage as storage_mod

    row = _pending_row(id=11)
    monkeypatch.setattr(storage_mod.pending_alarm_import_store, "get_by_id", lambda import_id: row)

    r = client.put("/api/admin/pending-alarm-imports/11", json={"action": "delete"})

    assert r.status_code == 400
