"""
守住「create_alarm/update_alarm 的 body 若帶了跟路徑不符的 department，
要被拒絕」這條規則真的生效。

背景：_check_body_department_conflict() 曾經在 normalize() 之後才被
呼叫，但 normalize() 只保留 ALARM_FIELDS 白名單（不含 department），
body 裡的 department 早被濾掉，檢查永遠不會觸發，是外部審查發現的
死碼。已改成用 request 的原始 body（normalize() 之前）呼叫這個檢查。
"""


def test_create_alarm_rejects_conflicting_body_department(client):
    r = client.post(
        "/api/alarms/local",
        json={
            "code": "E999", "device_model": "CNC-A100",
            "severity": "警告", "description": "test",
            "department": "other-dept",
        },
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "department 與路徑不符"


def test_update_alarm_rejects_conflicting_body_department(client):
    r = client.put(
        "/api/alarms/local/CNC-A100/E001",
        json={"description": "test", "department": "other-dept"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "department 與路徑不符"


def test_create_alarm_allows_matching_body_department(client):
    """body 帶的 department 若跟路徑一致，不該被擋——這條規則只擋衝突，
    不是禁止 body 出現這個欄位。"""
    r = client.post(
        "/api/alarms/local",
        json={
            "code": "E998", "device_model": "CNC-A100",
            "severity": "警告", "description": "test",
            "department": "local",
        },
    )
    assert r.status_code == 201
