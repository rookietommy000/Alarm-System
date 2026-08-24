import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "devices.json").write_text(
        json.dumps([{"id": "M-1", "model": "CNC-A100", "category": "車床"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "alarms.json").write_text(
        json.dumps(
            [
                {
                    "code": "E001",
                    "device_model": "CNC-A100",
                    "severity": "嚴重",
                    "description": "主軸過載",
                    "cause": "負荷過大",
                    "solution": "降低進給",
                    "keywords": ["主軸"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ALARM_DATA_DIR", str(data_dir))

    for mod in ("app", "storage"):
        sys.modules.pop(mod, None)
    from app import create_app

    monkeypatch.setenv("LOGIN_PASSWORD", "test-pw")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    # Establish admin session (covers both general + admin access)
    client.post("/admin/login", data={"password": "test-admin-pw"})
    return client


def test_list_alarms(client):
    r = client.get("/api/alarms")
    assert r.status_code == 200
    assert len(r.get_json()) == 1


def test_search_by_keyword(client):
    assert len(client.get("/api/alarms?q=主軸").get_json()) == 1
    assert len(client.get("/api/alarms?q=不存在").get_json()) == 0


def test_filter_by_device_and_severity(client):
    assert len(client.get("/api/alarms?device=CNC-A100").get_json()) == 1
    assert len(client.get("/api/alarms?device=OTHER").get_json()) == 0
    assert len(client.get("/api/alarms?severity=嚴重").get_json()) == 1
    assert len(client.get("/api/alarms?severity=警告").get_json()) == 0


def test_get_single_alarm(client):
    r = client.get("/api/alarms/local/CNC-A100/E001")
    assert r.status_code == 200
    assert r.get_json()["description"] == "主軸過載"
    assert client.get("/api/alarms/local/CNC-A100/NOPE").status_code == 404


def test_create_alarm(client):
    payload = {
        "code": "E999",
        "device_model": "CNC-A100",
        "severity": "警告",
        "description": "測試",
        "cause": "",
        "solution": "",
        "keywords": ["test"],
    }
    r = client.post("/api/alarms/local", json=payload)
    assert r.status_code == 201
    assert len(client.get("/api/alarms").get_json()) == 2


def test_create_duplicate_rejected(client):
    payload = {"code": "E001", "device_model": "CNC-A100", "description": "dup"}
    assert client.post("/api/alarms/local", json=payload).status_code == 409


def test_same_code_different_device_allowed(client):
    payload = {"code": "E001", "device_model": "OTHER-MACHINE", "severity": "警告", "description": "不同機型相同碼", "cause": "", "solution": "", "keywords": []}
    assert client.post("/api/alarms/local", json=payload).status_code == 201


def test_create_missing_code_rejected(client):
    assert client.post("/api/alarms/local", json={"description": "x"}).status_code == 400


def test_create_invalid_severity_rejected(client):
    assert client.post("/api/alarms/local", json={"code": "X1", "severity": "致命"}).status_code == 400


def test_update_alarm(client):
    r = client.put("/api/alarms/local/CNC-A100/E001", json={"description": "更新後"})
    assert r.status_code == 200
    assert client.get("/api/alarms/local/CNC-A100/E001").get_json()["description"] == "更新後"


def test_update_missing(client):
    assert client.put("/api/alarms/local/CNC-A100/NOPE", json={"description": "x"}).status_code == 404


def test_delete_alarm(client):
    assert client.delete("/api/alarms/local/CNC-A100/E001").status_code == 204
    assert client.get("/api/alarms/local/CNC-A100/E001").status_code == 404


def test_devices(client):
    r = client.get("/api/devices")
    assert r.status_code == 200
    assert r.get_json()[0]["model"] == "CNC-A100"
    assert r.get_json()[0]["device_model"] == "CNC-A100"


def test_keywords_string_normalized(client):
    r = client.post("/api/alarms/local", json={"code": "K1", "keywords": "a, b ,c"})
    assert r.status_code == 201
    assert r.get_json()["keywords"] == ["a", "b", "c"]


# ── missing_local 篩選（PLAN_local_solution.md 階段 6）──────────────────

def test_missing_local_excludes_alarm_with_local_solution(client):
    """E001 種子資料原本沒有 local_solution，補上後應該從 missing_local=true 排除。"""
    assert len(client.get("/api/alarms?missing_local=true").get_json()) == 1
    client.put("/api/alarms/local/CNC-A100/E001/local",
               json={"local_solution": "降速運轉", "local_reason": ""})
    assert len(client.get("/api/alarms?missing_local=true").get_json()) == 0


def test_missing_local_treats_empty_string_as_missing(client):
    """local_solution 是空字串（不是 null）時仍要算「缺」——既有資料
    兩種狀態都有，只判斷 null 會漏掉一半（PLAN 第九節查證同一個坑）。"""
    client.put("/api/alarms/local/CNC-A100/E001/local",
               json={"local_solution": "先前寫過的內容", "local_reason": ""})
    assert len(client.get("/api/alarms?missing_local=true").get_json()) == 0
    client.put("/api/alarms/local/CNC-A100/E001/local",
               json={"local_solution": "", "local_reason": ""})
    assert len(client.get("/api/alarms?missing_local=true").get_json()) == 1


def test_missing_local_absent_or_non_true_behaves_like_unfiltered(client):
    """missing_local 缺席、或給非 "true" 的值（例如 "1"、"false"），
    行為都要跟不帶這個參數一樣，不能被意外當成 true 觸發過濾。"""
    baseline = len(client.get("/api/alarms").get_json())
    assert len(client.get("/api/alarms?missing_local=1").get_json()) == baseline
    assert len(client.get("/api/alarms?missing_local=false").get_json()) == baseline
    assert len(client.get("/api/alarms?missing_local=TRUE").get_json()) == baseline  # 大小寫不敏感，見 4.5 節


def test_missing_local_combines_with_other_filters(client):
    """missing_local 要能跟既有的 device/severity 篩選疊加，不是互斥的獨立端點。"""
    client.post("/api/alarms/local", json={
        "code": "E002", "device_model": "CNC-A100", "severity": "警告",
        "description": "第二筆，缺處置", "cause": "", "solution": "", "keywords": [],
    })
    r = client.get("/api/alarms?missing_local=true&device=CNC-A100")
    codes = {a["code"] for a in r.get_json()}
    assert codes == {"E001", "E002"}
    r2 = client.get("/api/alarms?missing_local=true&severity=警告")
    assert {a["code"] for a in r2.get_json()} == {"E002"}


# ── /api/audit 分類與時間篩選（後台操作歷史紀錄優化）────────────────────

def test_audit_response_shape(client):
    """回應改成 {items, truncated, limit} 物件，不是裸陣列——truncated
    要能讓前端明說「只顯示最新 N 筆」，不能讓截斷變成沉默的不完整資料。"""
    client.put("/api/alarms/local/CNC-A100/E001", json={"description": "觸發一筆稽核紀錄"})
    r = client.get("/api/audit")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body.keys()) == {"items", "truncated", "limit"}
    assert isinstance(body["items"], list)
    assert body["truncated"] is False
    assert body["limit"] == 100


def test_audit_limit_invalid_value_rejected(client):
    """limit 給非數字要明確 400，不能讓 int() 拋出的 ValueError 變成 500。"""
    assert client.get("/api/audit?limit=abc").status_code == 400


def test_audit_limit_clamped_to_valid_range(client):
    """limit 給 0 或負數要夾到至少 1，不能讓 0/負數原樣傳給底層查詢。"""
    r = client.get("/api/audit?limit=0")
    assert r.status_code == 200
    assert r.get_json()["limit"] == 1
    r2 = client.get("/api/audit?limit=-5")
    assert r2.get_json()["limit"] == 1
    r3 = client.get("/api/audit?limit=9999")
    assert r3.get_json()["limit"] == 500


def test_audit_from_to_invalid_format_rejected(client):
    """from/to 給不是 YYYY-MM-DD 的格式要明確 400，不能讓底層 fromisoformat 拋例外。"""
    assert client.get("/api/audit?from=not-a-date").status_code == 400
    assert client.get("/api/audit?to=2026/08/24").status_code == 400


# ── SupabaseStore._require_full_pk_match（PLAN_variant 第三層防護）──────
# variant 加入主鍵後，get_one()/patch_one()/delete_one() 若收到不完整的
# match（缺 variant），PostgREST 條件不足會回多列，靜默取第一筆是任意的
# ——這支測試釘住「漏帶主鍵欄位時直接 ValueError，不讓查詢默默用不完整
# 條件跑下去」，跟 department 參數必填漏傳就報錯是同一個原則。純邏輯
# 測試，不需要真實 Supabase 連線。

def test_require_full_pk_match_rejects_missing_field():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from storage import SupabaseStore

    store = SupabaseStore("alarms", pk="code", pk_fields=["department", "device_model", "code", "variant"])
    with pytest.raises(ValueError, match="variant"):
        store._require_full_pk_match({"device_model": "ACM001", "code": "0001"})


def test_require_full_pk_match_accepts_complete_match():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from storage import SupabaseStore

    store = SupabaseStore("alarms", pk="code", pk_fields=["department", "device_model", "code", "variant"])
    store._require_full_pk_match({"device_model": "ACM001", "code": "0001", "variant": ""})  # 不應拋例外
