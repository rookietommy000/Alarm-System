"""匯入前的驗證：機種存在性、部門內重複、資料完整度、variant 一致性。
純邏輯層，不寫入資料庫，只讀——CLI 與後台批次匯入 API 共用，讓兩邊看到
的「將發生什麼」保證一致（PLAN 3.2 節：preview 與 commit 跑完全相同的
驗證 pipeline）。
"""
from storage import alarms_store, devices_store

COMPLETENESS_WARN_THRESHOLD = 0.5  # solution 覆蓋率低於此比例需要明確承認


def validate_devices_exist(rows: list, department: str) -> tuple:
    """整批比對機種存在性，缺任何一個就回報，不在這裡分支處理
    （是否自動建立缺少的機種由呼叫端依旗標決定）。

    回傳 (missing_models, model_alarm_counts)。
    """
    existing = {d["model"] for d in devices_store.load(department=department)}
    model_counts = {}
    for r in rows:
        model_counts[r["device_model"]] = model_counts.get(r["device_model"], 0) + 1

    missing = {m: n for m, n in model_counts.items() if m not in existing}
    return missing, model_counts


def dedupe_check(rows: list) -> list:
    """(device_model, code, variant) 在同一部門內必須唯一，來源檔本身
    若有重複會在 upsert 階段被後者覆蓋前者、且不會有任何錯誤訊息——
    這裡先攔下來讓操作者知道。"""
    seen = {}
    dupes = []
    for i, r in enumerate(rows):
        key = (r["device_model"], r["code"], r.get("variant", ""))
        if key in seen:
            dupes.append(key)
        seen[key] = i
    return dupes


def completeness_report(rows: list) -> dict:
    """回報 cause/solution/local_solution 的完整度，供匯入前判斷要不要
    明確承認資料不完整。solution 覆蓋率低不一定是問題（本廠自撰處置走
    local_solution 是預期路徑），但需要有人明確確認過，理由寫進
    alarm_history——這是唯一能擋住「半年後看到一批空欄位，沒人記得是
    當初就沒有、抽取失敗、還是漏匯」這種情況的機制。"""
    total = len(rows)
    if total == 0:
        return {}
    return {
        field: sum(1 for r in rows if (r.get(field) or "").strip())
        for field in ("cause", "solution", "local_solution")
    }


def check_variant_consistency(rows: list, department: str, device_model: str,
                               use_variant: bool = None) -> list:
    """新來源對某機種判定的 variant 啟用狀態，若與該機種既有資料的
    variant 狀態不一致，回傳 errors（不是 warnings）——這裡刻意不用
    「知道風險後仍可繼續」的警告語意，因為後果是靜默的：主鍵含 variant
    與否不同，會讓 upsert 走 INSERT 而非 UPDATE，同一個 code 產生兩筆，
    人工累積在舊那筆的 local_solution 留在原地、現場查詢卻拿到新的
    空白那筆，過程不會有任何錯誤訊息（見批次匯入 UI 規劃第 5 節專家
    分析）。

    機種在該部門尚無既有資料時視為一致（沒有既有狀態可比對，第一批
    匯入本來就是在定義這個機種的 variant 狀態）。

    use_variant：呼叫端若已經用 decide_variant_mode() 對這批 rows 算過
    一次判定，可以直接傳進來，避免這裡用同一個公式（code 是否唯一）
    重算一次——兩處各自算容易在改動時只改一邊而不同步。留 None 時退回
    這裡自己算（維持既有呼叫端不用改的相容性）。

    回傳格式與 dedupe_check 等驗證函式的呼叫端慣例一致：非空 list
    代表有問題，呼叫端自行組成 error 物件。
    """
    existing = [
        a for a in alarms_store.load(department=department)
        if a.get("device_model") == device_model
    ]
    if not existing:
        return []

    existing_has_variant = any((a.get("variant") or "").strip() for a in existing)

    if use_variant is None:
        codes = [r["code"] for r in rows]
        use_variant = len(codes) != len(set(codes))
    new_has_variant = use_variant

    if existing_has_variant == new_has_variant:
        return []

    return [{
        "device_model": device_model,
        "existing_has_variant": existing_has_variant,
        "new_has_variant": new_has_variant,
        "reason": (
            f"機種 {device_model} 既有資料"
            f"{'已啟用' if existing_has_variant else '未啟用'} variant，"
            f"但本次來源判定為"
            f"{'啟用' if new_has_variant else '未啟用'}，"
            f"混用會導致主鍵不一致、同代碼產生重複列且不會有錯誤訊息"
        ),
    }]
