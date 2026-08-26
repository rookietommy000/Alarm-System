"""批次匯入 preview/commit 兩支端點的整合測試（PLAN 批次匯入 UI）。

preview 與 commit 跑完全相同的驗證 pipeline，這組測試涵蓋兩者的
一致性、multipart 上傳、errors 擋 commit、部分寫入等情境。純邏輯層
（alarm_ingest 各函式）已在 test_alarm_ingest.py 涵蓋，這裡只測
端點層的組裝與 HTTP 行為。
"""
import io
import json

import pytest


CSV_HEADER = "code,device_model,variant,description,cause,solution,local_solution"


def _csv_file(content: str, filename: str = "import.csv"):
    return (io.BytesIO(content.encode("utf-8")), filename)


def test_preview_requires_admin(anon_client):
    r = anon_client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": _csv_file(f"{CSV_HEADER}\nE1,CNC-A100,,d,c,s,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code in (302, 401, 403)


def test_preview_missing_file_rejected(client):
    r = client.post("/api/admin/bulk-import/local/preview", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_preview_unsupported_extension_rejected(client):
    r = client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_preview_parses_and_reports_counts(client):
    r = client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": _csv_file(f"{CSV_HEADER}\nE001,CNC-A100,,更新描述,,,\nE999,CNC-A100,,新代碼,,,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["row_count"] == 2
    # E001 是 conftest 種子資料既有代碼 → will_update；E999 是新的 → will_create
    assert body["will_update"] == 1
    assert body["will_create"] == 1
    assert body["errors"] == []


def test_preview_does_not_write(client):
    r = client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": _csv_file(f"{CSV_HEADER}\nE999,CNC-A100,,新代碼,,,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    listed = client.get("/api/alarms").get_json()
    assert not any(a["code"] == "E999" for a in listed)


def test_preview_missing_device_reported_as_error(client):
    r = client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": _csv_file(f"{CSV_HEADER}\nE1,GHOST-MODEL,,d,,,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    body = r.get_json()
    types = {e["type"] for e in body["errors"]}
    assert "missing_device" in types


def test_preview_duplicate_rows_reported_as_error(client):
    r = client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": _csv_file(f"{CSV_HEADER}\nE9,CNC-A100,,a,,,\nE9,CNC-A100,,b,,,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    body = r.get_json()
    types = {e["type"] for e in body["errors"]}
    assert "duplicate" in types


def test_preview_low_completeness_reported_as_warning_not_error(client):
    r = client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": _csv_file(f"{CSV_HEADER}\nE9,CNC-A100,,d,,,\n")},  # solution 空
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["errors"] == []
    types = {w["type"] for w in body["warnings"]}
    assert "low_completeness" in types
    assert body["requires_accept_incomplete"] is True


def test_preview_high_completeness_does_not_require_accept(client):
    r = client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": _csv_file(f"{CSV_HEADER}\nE9,CNC-A100,,d,,有solution,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.get_json()["requires_accept_incomplete"] is False


# ── 完整度硬攔截：只看 solution，比照 CLI 的 --accept-incomplete ───────

def test_commit_low_completeness_without_reason_rejected(client):
    """solution 完整度低於門檻且沒有提供 accept_incomplete 理由時，
    commit 必須拒絕——這是唯一能擋住「半年後看到一批空 solution，沒人
    記得是當初就沒有、抽取失敗、還是漏匯」的機制，後台使用者比 CLI
    使用者更不容易判斷「覆蓋率低」代表什麼，這道防線更不能只存在於
    CLI 那條路徑。"""
    r = client.post(
        "/api/admin/bulk-import/local/commit",
        data={"file": _csv_file(f"{CSV_HEADER}\nE9,CNC-A100,,d,,,\n")},  # solution 空
        content_type="multipart/form-data",
    )
    assert r.status_code == 400

    listed = client.get("/api/alarms").get_json()
    assert not any(a["code"] == "E9" for a in listed)


def test_commit_low_completeness_with_reason_succeeds_and_audits(client):
    r = client.post(
        "/api/admin/bulk-import/local/commit",
        data={
            "file": _csv_file(f"{CSV_HEADER}\nE9,CNC-A100,,d,,,\n"),
            "accept_incomplete": "原廠手冊未提供處置說明，僅有本廠自撰內容",
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.get_json()["succeeded"] == 1

    listed = client.get("/api/alarms").get_json()
    assert any(a["code"] == "E9" for a in listed)

    audit = client.get("/api/audit").get_json()
    entries = [e for e in audit["items"] if e["operation"] == "bulk_import_incomplete"]
    assert len(entries) == 1
    assert "原廠手冊未提供處置說明" in entries[0]["new_data"]["reason"]


def test_commit_high_completeness_ignores_blank_accept_incomplete(client):
    """完整度足夠時，accept_incomplete 欄位空白也不該被擋——那個欄位
    只在 requires_accept_incomplete 為真時才有意義。"""
    r = client.post(
        "/api/admin/bulk-import/local/commit",
        data={"file": _csv_file(f"{CSV_HEADER}\nE9,CNC-A100,,d,,有solution,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200


# ── variant 判定：逐機種各自進行，不是對整份來源判定一次 ──────────────

def test_multi_model_source_variant_decisions_are_per_model(client):
    """一份來源含多個機種時，variant 判定必須逐機種各自進行。

    全域判定（對整份來源算一次）在混合來源時會誤導：某個多變體機種的
    重複 code 會讓整份被判成「啟用」，但實際的一致性檢查是逐機種算的，
    兩者對不上會讓使用者誤以為每個機種都會啟用。
    """
    # MODEL-A：重複 code → 該機種判定啟用 variant
    # CNC-A100（conftest 既有機種）：唯一 code → 判定不啟用
    client.post("/api/devices/local", json={"model": "MODEL-A", "category": ""})
    csv_content = (
        f"{CSV_HEADER}\n"
        "V1,MODEL-A,情境A,d,,有solution,\n"
        "V1,MODEL-A,情境B,d,,有solution,\n"
        "E9,CNC-A100,,d,,有solution,\n"
    )
    r = client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": _csv_file(csv_content)},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    decisions = {d["device_model"]: d["use_variant"] for d in r.get_json()["variant_decisions"]}
    assert decisions["MODEL-A"] is True
    assert decisions["CNC-A100"] is False


def test_commit_requires_admin(anon_client):
    r = anon_client.post(
        "/api/admin/bulk-import/local/commit",
        data={"file": _csv_file(f"{CSV_HEADER}\nE1,CNC-A100,,d,,,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code in (302, 401, 403)


def test_commit_writes_new_and_existing_rows(client):
    r = client.post(
        "/api/admin/bulk-import/local/commit",
        data={
            "file": _csv_file(f"{CSV_HEADER}\nE001,CNC-A100,,更新後描述,,降低轉速,\nE888,CNC-A100,,新代碼,,更換零件,\n"),
            "import_mode": "upsert",
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["succeeded"] == 2
    assert body["partial"] is False

    listed = client.get("/api/alarms").get_json()
    e001 = next(a for a in listed if a["code"] == "E001")
    assert e001["description"] == "更新後描述"
    assert any(a["code"] == "E888" for a in listed)


def test_commit_rejects_when_preview_would_have_errors(client):
    """commit 不信任前端傳回的 preview 結果，自己重新跑一次完全相同的
    驗證 pipeline——即使沒有呼叫 preview，commit 遇到 errors 也要擋。"""
    r = client.post(
        "/api/admin/bulk-import/local/commit",
        data={"file": _csv_file(f"{CSV_HEADER}\nE1,GHOST-MODEL,,d,,,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400

    listed = client.get("/api/alarms").get_json()
    assert not any(a["code"] == "E1" for a in listed)


def test_commit_invalid_import_mode_rejected(client):
    r = client.post(
        "/api/admin/bulk-import/local/commit",
        data={
            "file": _csv_file(f"{CSV_HEADER}\nE9,CNC-A100,,d,,,\n"),
            "import_mode": "replace",
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_commit_preserves_absent_optional_field(client):
    """批次匯入更新既有列時，來源沒有 keywords 欄，既有 keywords 不該
    被清空——端到端驗證（HTTP → alarm_ingest → JsonStore），比
    test_alarm_ingest.py 的單元測試多驗證了 multipart 解析與端點組裝
    這幾層有沒有漏傳 _present。"""
    client.put("/api/alarms/local/CNC-A100/E001", json={
        "description": "主軸過載", "keywords": ["主軸", "重要"],
    })
    before = client.get("/api/alarms").get_json()
    e001_before = next(a for a in before if a["code"] == "E001")
    assert e001_before["keywords"] == ["主軸", "重要"]

    r = client.post(
        "/api/admin/bulk-import/local/commit",
        data={"file": _csv_file(f"{CSV_HEADER}\nE001,CNC-A100,,新描述,,更新方案,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200

    after = client.get("/api/alarms").get_json()
    e001_after = next(a for a in after if a["code"] == "E001")
    assert e001_after["description"] == "新描述"
    assert e001_after["solution"] == "更新方案"
    assert e001_after["keywords"] == ["主軸", "重要"]  # 保留，不是被清空


def test_commit_variant_inconsistency_blocks_write(client):
    """既有資料已有 variant 的機種，來源判定不啟用 variant（code 全
    唯一）時應被擋下——這是靜默資料損毀的防線，見 3.4 節。"""
    client.post("/api/alarms/local", json={
        "code": "V1", "device_model": "CNC-A100", "variant": "情境A",
        "description": "d",
    })
    client.post("/api/alarms/local", json={
        "code": "V1", "device_model": "CNC-A100", "variant": "情境B",
        "description": "d",
    })

    r = client.post(
        "/api/admin/bulk-import/local/commit",
        data={"file": _csv_file(f"{CSV_HEADER}\nV9,CNC-A100,,d,,,\n")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_preview_and_commit_agree_on_row_count(client):
    """preview 與 commit 跑完全相同的驗證 pipeline——同一份檔案兩者
    看到的筆數與機種判斷要一致。"""
    payload = f"{CSV_HEADER}\nE777,CNC-A100,,d,,降低轉速,\n"
    preview = client.post(
        "/api/admin/bulk-import/local/preview",
        data={"file": _csv_file(payload)},
        content_type="multipart/form-data",
    ).get_json()

    commit = client.post(
        "/api/admin/bulk-import/local/commit",
        data={"file": _csv_file(payload)},
        content_type="multipart/form-data",
    ).get_json()

    assert preview["row_count"] == 1
    assert commit["succeeded"] == 1
