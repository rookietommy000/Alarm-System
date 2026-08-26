"""全庫語意品質審核端點的整合測試（規劃第 1c 項）。

清單檔案（data/semantic_scan_fixes.json）是離線工具的產物，不進版控。
測試用 monkeypatch 直接控制 _load_semantic_review()/_save_semantic_review()
背後的檔案路徑（走 conftest 的 ALARM_DATA_DIR 隔離），不依賴真實跑過
scan_semantic_quality.py。
"""
import json

import pytest


def _write_review_file(tmp_path_dir, findings):
    import os
    data_dir = os.environ["ALARM_DATA_DIR"]
    path = __import__("pathlib").Path(data_dir) / "semantic_scan_fixes.json"
    path.write_text(json.dumps({"findings": findings}, ensure_ascii=False), encoding="utf-8")


SAMPLE_FINDING = {
    "code": "0003", "device_model": "CNC-A100",
    "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
    "issue": "units 被誤譯為單位", "confidence": "high",
    "suggested_zh": "單元開啟/檢測停用",
    "suggested_description": "OPEN UNITS/CHECK DISABLE 單元開啟/檢測停用",
}


def test_list_requires_admin(anon_client):
    r = anon_client.get("/api/admin/semantic-review/local")
    assert r.status_code in (302, 401, 403)


def test_list_empty_when_no_file(client):
    """還沒跑過掃描工具時如實回空清單，不是報錯。"""
    r = client.get("/api/admin/semantic-review/local")
    assert r.status_code == 200
    assert r.get_json() == {"findings": []}


def test_list_returns_findings_with_default_pending_status(client, tmp_path):
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.get("/api/admin/semantic-review/local")
    body = r.get_json()
    assert len(body["findings"]) == 1
    assert body["findings"][0]["status"] == "pending"


def test_update_requires_admin(anon_client):
    r = anon_client.put("/api/admin/semantic-review/local/0", json={"action": "reject"})
    assert r.status_code in (302, 401, 403)


def test_update_index_out_of_range_404(client, tmp_path):
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.put("/api/admin/semantic-review/local/5", json={"action": "reject"})
    assert r.status_code == 404


def test_update_invalid_action_400(client, tmp_path):
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.put("/api/admin/semantic-review/local/0", json={"action": "delete"})
    assert r.status_code == 400


def test_reject_marks_status_without_writing_alarm(client, tmp_path):
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.put("/api/admin/semantic-review/local/0", json={"action": "reject"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "rejected"

    listed = client.get("/api/alarms").get_json()
    assert not any(a["code"] == "0003" and a.get("device_model") == "CNC-A100" for a in listed)


def test_accept_without_existing_alarm_404(client, tmp_path):
    """審核清單裡的 device_model/code 若資料庫已經找不到（可能已被刪除），
    採用時要如實回報，不能安靜略過或寫出一筆孤兒資料。"""
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
    assert r.status_code == 404


def test_accept_writes_new_description_and_marks_accepted(client, tmp_path):
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "0003", "device_model": "CNC-A100", "variant": "", "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
         "severity": "", "cause": "", "solution": "", "keywords": []},
        department="local",
    )
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])

    r = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "accepted"
    assert body["final_zh"] == "單元開啟/檢測停用"

    written = alarms_store.get_one(department="local", match={"device_model": "CNC-A100", "code": "0003", "variant": ""})
    assert written["description"] == "OPEN UNITS/CHECK DISABLE 單元開啟/檢測停用"


def test_accept_with_custom_final_zh_overrides_suggestion(client, tmp_path):
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "0003", "device_model": "CNC-A100", "variant": "", "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
         "severity": "", "cause": "", "solution": "", "keywords": []},
        department="local",
    )
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])

    r = client.put("/api/admin/semantic-review/local/0", json={"action": "accept", "final_zh": "審核者自己改的文字"})
    assert r.status_code == 200
    written = alarms_store.get_one(department="local", match={"device_model": "CNC-A100", "code": "0003", "variant": ""})
    assert written["description"] == "OPEN UNITS/CHECK DISABLE 審核者自己改的文字"


def test_double_processing_returns_409(client, tmp_path):
    from storage import alarms_store
    alarms_store.upsert_one(
        {"code": "0003", "device_model": "CNC-A100", "variant": "", "description": "OPEN UNITS/CHECK DISABLE 打開單位/檢查禁用",
         "severity": "", "cause": "", "solution": "", "keywords": []},
        department="local",
    )
    _write_review_file(tmp_path, [dict(SAMPLE_FINDING)])
    r1 = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
    assert r1.status_code == 200
    r2 = client.put("/api/admin/semantic-review/local/0", json={"action": "accept"})
    assert r2.status_code == 409


def test_accept_rejects_nonexistent_department_via_resolve_target(client):
    r = client.put("/api/admin/semantic-review/does-not-exist-dept/0", json={"action": "reject"})
    assert r.status_code in (403, 404)
