"""正式環境 commit_rows() -> undo_snapshot() 整批復原機制實地驗證。

只在一個新建、hidden=True、purgeable=True 的一次性測試部門內操作，
驗證結束後用 purge_department() 徹底清除，不觸碰任何既有部門/資料。

需要 ALLOW_LOCAL_PRODUCTION_WRITE=1 才能對正式 Supabase 寫入（見
backend/storage.py 的 _guard_production_write()）。
"""
import os
import sys
import time
import uuid

os.environ["ALLOW_LOCAL_PRODUCTION_WRITE"] = "1"

sys.path.insert(0, "/Applications/My Project/testing/backend")
os.chdir("/Applications/My Project/testing/backend")

from dotenv import load_dotenv
load_dotenv("/Applications/My Project/testing/.env")

from werkzeug.security import generate_password_hash
from storage import department_store, devices_store, alarms_store, import_snapshot_store
from alarm_ingest.commit import commit_rows, undo_snapshot

DEPT_ID = f"zzverify{int(time.time())}"[:32]
DEVICE_MODEL = "ZZVERIFY_DEVICE"
CODE = "ZZVERIFY_CODE_0001"

print(f"=== 驗證開始，測試部門 id={DEPT_ID} ===\n")

results = {}

try:
    # 1. 建立一次性測試部門（hidden + purgeable）
    print("[1] 建立測試部門...")
    dept = department_store.create(
        DEPT_ID, "ZZ 一次性驗證部門（migration 007 驗證用，應被自動清除）",
        generate_password_hash("verify-only-pw", method="pbkdf2:sha256"),
        generate_password_hash("verify-only-admin-pw", method="pbkdf2:sha256"),
        hidden=True, purgeable=True,
    )
    print(f"    建立成功: {dept.get('id')}, hidden={dept.get('hidden')}, purgeable={dept.get('purgeable')}")
    results["step1_create"] = "OK"

    # 2. 確認 is_available()（migration 007 是否真的存在）
    print("\n[2] 探測 import_snapshots 表是否存在...")
    available = import_snapshot_store.is_available()
    print(f"    is_available() = {available}")
    results["step2_is_available"] = available
    if not available:
        raise RuntimeError("import_snapshots 表不存在，migration 007 尚未生效，中止驗證")

    # 3. 新增一台測試機種
    print("\n[3] 新增測試機種...")
    devices_store.upsert_one(
        {"id": f"{DEPT_ID}-{DEVICE_MODEL}", "model": DEVICE_MODEL,
         "category": "驗證用", "line": DEPT_ID},
        department=DEPT_ID, on_conflict="department,model",
    )
    print(f"    機種 {DEVICE_MODEL} 已建立")
    results["step3_device"] = "OK"

    # 4. commit_rows() 寫入一筆測試警報，確認快照建立
    print("\n[4] commit_rows() 寫入測試警報...")
    row = {
        "code": CODE, "device_model": DEVICE_MODEL, "variant": "",
        "severity": "warning", "description": "驗證用警報，不是真實資料",
        "cause": "migration 007 實地驗證", "solution": "此筆資料應在驗證結束後被 undo",
    }
    commit_result = commit_rows([row], department=DEPT_ID, import_mode="upsert")
    print(f"    commit_rows() 回傳: {commit_result}")
    results["step4_commit"] = commit_result
    snapshot_id = commit_result.get("snapshot_id")
    if not snapshot_id:
        raise RuntimeError(f"commit_rows() 沒有回傳 snapshot_id，無法驗證復原：{commit_result}")

    # 5. 確認警報真的寫進 alarms 表
    print("\n[5] 確認警報已寫入 alarms 表...")
    written = alarms_store.get_one(department=DEPT_ID, match={"device_model": DEVICE_MODEL, "code": CODE, "variant": ""})
    print(f"    讀回: {written}")
    results["step5_written_before_undo"] = written is not None

    # 6. undo_snapshot() 整批復原
    print(f"\n[6] undo_snapshot(snapshot_id={snapshot_id}) 執行復原...")
    undo_result = undo_snapshot(snapshot_id, department=DEPT_ID)
    print(f"    undo_snapshot() 回傳: {undo_result}")
    results["step6_undo"] = undo_result

    # 7. 確認警報真的被復原（這筆 commit 前不存在 -> undo 應刪除）
    print("\n[7] 確認警報已被復原（應該讀不到）...")
    after_undo = alarms_store.get_one(department=DEPT_ID, match={"device_model": DEVICE_MODEL, "code": CODE, "variant": ""})
    print(f"    讀回: {after_undo}")
    results["step7_gone_after_undo"] = after_undo is None

    # 8. 確認 already_undone 防重複復原生效
    print("\n[8] 重複呼叫 undo_snapshot() 應回傳 already_undone=True...")
    undo_again = undo_snapshot(snapshot_id, department=DEPT_ID)
    print(f"    第二次 undo_snapshot() 回傳: {undo_again}")
    results["step8_already_undone"] = undo_again.get("already_undone") is True

except Exception as e:
    print(f"\n!!! 驗證中發生例外: {type(e).__name__}: {e}")
    results["exception"] = f"{type(e).__name__}: {e}"

finally:
    # 9. 無論成敗，清除測試部門（purge）
    print(f"\n[9] 清除測試部門 {DEPT_ID}...")
    try:
        counts = department_store.count_impact(DEPT_ID)
        print(f"    purge 前筆數統計: {counts}")
        removed = department_store.purge(DEPT_ID, DEPT_ID, counts)
        print(f"    purge 完成，removed={removed}")
        results["step9_purge"] = removed
    except Exception as e:
        print(f"    purge 失敗（需要手動清除！）: {type(e).__name__}: {e}")
        results["step9_purge_FAILED"] = f"{type(e).__name__}: {e}"

print("\n=== 驗證結果彙總 ===")
for k, v in results.items():
    print(f"  {k}: {v}")
