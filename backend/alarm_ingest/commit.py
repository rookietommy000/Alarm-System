"""實際寫入資料庫。PostgREST 沒有跨請求交易，commit 中途失敗會留下
部分寫入——這裡的策略是遇錯即停（不繼續跑，避免同一個系統性錯誤重複
N 次、拖長佔用時間），並回傳足夠資訊讓呼叫端判斷「現在該怎麼做」，
不只是回一個籠統的成功/失敗（見第 3.3 節部分寫入處理）。
"""
from storage import alarms_store, audit_logger, import_snapshot_store
from .parse import OPTIONAL_FIELDS

CONFLICT_TARGET = "department,device_model,code,variant"


def _to_payload(row: dict) -> dict:
    """批次匯入是 upsert 語意，不是整列取代——OPTIONAL_FIELDS
    （severity/keywords/sol_steps）在來源缺席時不送進 payload，讓既有
    值保留（PostgREST 的 merge-duplicates upsert 只更新 payload 裡列出
    的欄位，不送就不動，這個前提已對 zztest 部門實測驗證過）。

    row_to_alarm() 產出的 dict 帶一個 "_present" 鍵記錄來源實際提供
    的欄位；沒有這個鍵（例如非批次匯入路徑呼叫 upsert_one 時）代表
    呼叫端不使用這層保護，行為與過去一致（照送整包）。
    """
    present = row.get("_present")
    if present is None:
        return {k: v for k, v in row.items() if not k.startswith("_")}
    return {
        k: v for k, v in row.items()
        if not k.startswith("_") and (k not in OPTIONAL_FIELDS or k in present)
    }


def commit_rows(rows: list, department: str, import_mode: str) -> dict:
    """逐筆 upsert，遇到第一筆失敗就停止（不繼續往下跑同樣會失敗的
    後續筆）。import_mode 只影響回傳的 recovery 建議文字，不影響寫入
    行為本身——append/upsert 目前都是逐筆 upsert_one()（沒有 replace，
    這支函式不處理刪除語意，PLAN 已定案：批次匯入 UI 不提供 replace
    模式，部分寫入在 upsert 下可修復，在 replace 下是資料遺失）。

    回傳：
        {
          "committed": True,
          "partial": bool,       # 是否中途停止（succeeded < len(rows)）
          "succeeded": int,
          "failed": int,
          "first_failure": {"row": i, "code": ..., "variant": ..., "reason": ...} | None,
          "recovery": str,       # 依 import_mode 給的具體建議
          "snapshot_id": int | None,  # 整批復原用，JsonStore fallback 下為 None
        }
    """
    succeeded = 0
    first_failure = None
    rows_before = []  # 整批復原用：commit 前的舊值（見 storage.ImportSnapshotStore）

    for i, r in enumerate(rows, start=1):
        try:
            payload = _to_payload(r)
            match = {"device_model": r.get("device_model"), "code": r.get("code"),
                     "variant": r.get("variant", "")}
            before = alarms_store.get_one(department=department, match=match)
            rows_before.append({**match, "before_data": before})
            alarms_store.upsert_one(payload, department=department, on_conflict=CONFLICT_TARGET)
            succeeded += 1
        except Exception as e:
            first_failure = {
                "row": i,
                "code": r.get("code"),
                "variant": r.get("variant"),
                "reason": str(e)[:200],  # PostgREST 錯誤可能很長，且不外洩過多實作細節（見 B4）
            }
            break

    total = len(rows)
    failed = total - succeeded
    partial = first_failure is not None

    if not partial:
        recovery = "全部寫入成功。"
    elif import_mode == "upsert":
        recovery = (f"已寫入 {succeeded} 筆。目前為 upsert 模式，直接重新匯入同一份檔案即可補齊，"
                    f"已寫入的部分會被更新而非重複。")
    else:  # append，不冪等
        recovery = (f"已寫入 {succeeded} 筆。目前為 append 模式，重新匯入同一份檔案會讓已寫入的 "
                    f"{succeeded} 筆撞上重複主鍵而失敗——請先將來源檔中已成功的部分移除，"
                    f"只保留第 {first_failure['row']} 筆之後（含）尚未寫入的資料再重新匯入。")

    if partial:
        audit_logger.log(
            "bulk_import_partial", department=department,
            new_data={
                "device_model": ",".join(sorted({r.get("device_model", "") for r in rows})),
                "code": f"部分寫入：成功 {succeeded} / {total} 筆，於第 {first_failure['row']} 筆中止",
                "first_failure_code": first_failure["code"],
                "first_failure_variant": first_failure["variant"],
                "reason": first_failure["reason"],
            },
        )

    # 只有真的寫入過的筆數（succeeded）才需要記進快照——中途失敗那筆
    # upsert_one() 沒有真的執行，不該出現在復原範圍內，undo 時對它做
    # 任何事都是誤操作（那筆從未被改動過）。succeeded=0（第一筆就失敗）
    # 時不建立空快照，undo 一份空快照沒有意義，只會在列表 UI 添噪音。
    snapshot_id = None
    if succeeded:
        snapshot_id = import_snapshot_store.save_snapshot(
            department=department,
            device_models=[r.get("device_model", "") for r in rows[:succeeded]],
            rows_before=rows_before[:succeeded],
            total_rows=succeeded,
            import_mode=import_mode,
        )

    return {
        "committed": True,
        "partial": partial,
        "succeeded": succeeded,
        "failed": failed,
        "first_failure": first_failure,
        "recovery": recovery,
        "snapshot_id": snapshot_id,
    }


def undo_snapshot(snapshot_id: int, department: str) -> dict:
    """整批復原：逐筆把 before_data 寫回（None 代表這筆在 commit 前
    不存在，改為刪除）。department 必須跟快照建立時一致——由
    ImportSnapshotStore.get_snapshot() 的查詢條件把關，越權查詢視同
    快照不存在，不外洩其他部門是否有這筆快照。

    遇錯即停（同 commit_rows() 的容錯哲學），已復原的部分不回滾——
    undo 本身也是一次性寫入操作，中途失敗要讓呼叫端知道復原到哪裡、
    還剩什麼沒處理，而不是整批當作沒發生過。

    回傳：
        {
          "found": bool,
          "already_undone": bool,
          "succeeded": int,
          "failed": int,
          "first_failure": {"code": ..., "variant": ..., "reason": ...} | None,
        }
    """
    data = import_snapshot_store.get_snapshot(snapshot_id, department)
    if data is None:
        return {"found": False}
    if data["snapshot"].get("undone_at"):
        return {"found": True, "already_undone": True}

    succeeded = 0
    first_failure = None
    for row in data["rows"]:
        try:
            match = {"device_model": row["device_model"], "code": row["code"],
                     "variant": row.get("variant", "")}
            before = row.get("before_data")
            if before is None:
                alarms_store.delete_one(department=department, match=match)
            else:
                alarms_store.upsert_one(before, department=department, on_conflict=CONFLICT_TARGET)
            succeeded += 1
        except Exception as e:
            first_failure = {"code": row["code"], "variant": row.get("variant", ""), "reason": str(e)[:200]}
            break

    result = {
        "found": True, "already_undone": False,
        "succeeded": succeeded, "failed": len(data["rows"]) - succeeded,
        "first_failure": first_failure,
    }
    import_snapshot_store.mark_undone(snapshot_id, result)

    audit_logger.log(
        "bulk_import_undo", department=department,
        new_data={
            "device_model": data["snapshot"].get("device_models", ""),
            "code": f"整批復原：成功 {succeeded} / {len(data['rows'])} 筆",
        },
    )
    return result
