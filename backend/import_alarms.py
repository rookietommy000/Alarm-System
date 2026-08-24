"""
批次匯入警報代碼（PLAN 第 6/6.1 節）。

新部門上線動輒 1000+ 筆警報，後台單筆 CRUD 不可能手動輸入，這支 CLI
工具負責批次寫入。核心風險（見 PLAN 6.1 節）：`alarms` 與 `devices` 之間
沒有外鍵，匯入不存在的 device_model 不會報錯，只會安靜產生前台永遠查不到
的孤兒警報——所以寫入前一定要先做機種存在性驗證，不能邊匯邊查。

用法：
    python backend/import_alarms.py --department mf4d --file alarms.csv
    python backend/import_alarms.py --department mf4d --file alarms.csv --dry-run
    python backend/import_alarms.py --department mf4d --file alarms.csv \
        --mode replace --yes-i-mean-replace

CSV 欄位（對應 alarms 表）：
    code, device_model, severity, description, cause, solution, keywords, sol_steps
    - severity 必須是「嚴重」/「警告」/「資訊」之一
    - keywords 用分號分隔（避免與 CSV 逗號分隔衝突），例如 "主軸;過載"
    - sol_steps 是 JSON 字串（可留空）
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from storage import alarms_store, audit_logger, devices_store  # noqa: E402

# 與 app.py 的 DEPT_ID_RE 定義一致（PLAN 2.2.4/4 節），此工具不依賴 Flask app
# context，避免 import app 觸發不必要的 create_app() 初始化，故在此重複定義。
DEPT_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
SEVERITIES = {"嚴重", "警告", "資訊"}
ALARM_FIELDS = ["code", "device_model", "severity", "description", "cause", "solution",
                "local_solution", "keywords", "sol_steps", "variant"]

# 與 app.py 的 normalize_variant()、Variant/parse_alarms.py 的同名函式是
# 同一份邏輯（三處複製而非共用 import——三支程式各自獨立部署/執行，
# app.py 是 Flask 服務、parse_alarms.py 是離線 CLI 不進 backend 部署、
# 這支也是離線 CLI），修改要三邊同步。


def normalize_variant(s: str) -> str:
    s = " ".join((s or "").split())
    s = s.replace("–", "-").replace("—", "-")
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("／", "/")
    return s.strip()


def _row_to_alarm(row: dict) -> dict:
    keywords_raw = (row.get("keywords") or "").strip()
    keywords = [s.strip() for s in keywords_raw.split(";") if s.strip()] if keywords_raw else []

    sol_steps_raw = (row.get("sol_steps") or "").strip()
    sol_steps = {}
    if sol_steps_raw:
        try:
            sol_steps = json.loads(sol_steps_raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"sol_steps 不是合法 JSON：{e}")

    code = (row.get("code") or "").strip()
    device_model = (row.get("device_model") or "").strip()
    severity = (row.get("severity") or "").strip()

    if not code:
        raise ValueError("code 為必填")
    if not device_model:
        raise ValueError("device_model 為必填")
    if severity and severity not in SEVERITIES:
        raise ValueError(f"severity 必須為 {sorted(SEVERITIES)} 之一，收到：{severity!r}")

    return {
        "code": code,
        "device_model": device_model,
        "variant": normalize_variant(row.get("variant") or ""),
        "severity": severity,
        "description": (row.get("description") or "").strip(),
        "cause": (row.get("cause") or "").strip(),
        "solution": (row.get("solution") or "").strip(),
        "local_solution": (row.get("local_solution") or "").strip(),
        "keywords": keywords,
        "sol_steps": sol_steps,
    }


def _load_csv(path: Path) -> list:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, raw in enumerate(reader, start=2):  # 第 1 行是表頭
            try:
                rows.append(_row_to_alarm(raw))
            except ValueError as e:
                raise ValueError(f"第 {i} 行錯誤：{e}")
    return rows


def _load_json(path: Path) -> list:
    """讀 parse_alarms.py 產出的標準 JSON（見 Variant/parse_alarms.py
    to_output()）。跟 CSV 走同一個 _row_to_alarm() 正規化，兩種來源
    最終格式一致，本檔案其餘邏輯（去重檢查、機種驗證、完整度攔截）
    不需要分別處理來源型態。"""
    with path.open(encoding="utf-8") as f:
        raw_rows = json.load(f)
    if not isinstance(raw_rows, list):
        raise ValueError("JSON 檔內容必須是陣列（parse_alarms.py 的標準輸出格式）")
    rows = []
    for i, raw in enumerate(raw_rows, start=1):
        try:
            rows.append(_row_to_alarm(raw))
        except ValueError as e:
            raise ValueError(f"第 {i} 筆錯誤：{e}")
    return rows


def _load_file(path: Path) -> list:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(path)
    if suffix == ".json":
        return _load_json(path)
    raise ValueError(f"不支援的檔案格式：{suffix}（支援 .csv/.json）")


def _validate_devices_exist(rows: list, department: str) -> tuple:
    """PLAN 6.1 節：整批比對機種存在性，缺任何一個就整批中止並列出清單。

    回傳 (missing_models, model_alarm_counts)。是否自動建立缺少的機種由
    呼叫端（main()）依 --create-missing-devices 旗標決定，這裡只負責回報
    缺什麼，不在這裡分支——避免「missing 是否為空」的語意隨旗標值改變。
    """
    existing = {d["model"] for d in devices_store.load(department=department)}
    csv_models = {}
    for r in rows:
        csv_models[r["device_model"]] = csv_models.get(r["device_model"], 0) + 1

    missing = {m: n for m, n in csv_models.items() if m not in existing}
    return missing, csv_models


def _create_missing_devices(missing_models: list, department: str) -> None:
    for model in missing_models:
        new_id = f"{department}-{model}"
        devices_store.upsert_one(
            {"id": new_id, "model": model, "category": "", "line": ""},
            department=department,
            on_conflict="department,model",
        )


def _dedupe_check(rows: list) -> list:
    """PLAN 1.3 節：(device_model, code, variant) 在同一部門內必須唯一，
    來源檔本身若有重複會在 upsert 階段被後者覆蓋前者、且不會有任何
    錯誤訊息——這裡先攔下來讓操作者知道。

    加 variant 進鍵值是 PLAN_variant 的改動：不加的話，同一 code 的
    多個變體會被誤判成「重複」而整批中止，即使它們其實是合法的不同筆
    （FILL203 就有 17 個 code 各自對應多筆 variant）。"""
    seen = {}
    dupes = []
    for i, r in enumerate(rows):
        key = (r["device_model"], r["code"], r.get("variant", ""))
        if key in seen:
            dupes.append(key)
        seen[key] = i
    return dupes


def _completeness_report(rows: list) -> dict:
    """回報 cause/solution/local_solution 的完整度百分比，供匯入前判斷
    要不要用 --accept-incomplete 明確承認資料不完整。solution 覆蓋率
    低不一定是問題（本廠自撰處置走 local_solution 是預期路徑），但
    需要有人明確確認過，理由寫進 alarm_history——這是唯一能擋住
    「半年後看到一批空欄位，沒人記得是當初就沒有、抽取失敗、還是
    漏匯」這種情況的機制。"""
    total = len(rows)
    if total == 0:
        return {}
    return {
        field: sum(1 for r in rows if (r.get(field) or "").strip())
        for field in ("cause", "solution", "local_solution")
    }


COMPLETENESS_WARN_THRESHOLD = 0.5  # solution 覆蓋率低於此比例需要 --accept-incomplete


def main():
    parser = argparse.ArgumentParser(description="批次匯入部門警報代碼（PLAN 第 6 節 / PLAN_variant）")
    parser.add_argument("--department", required=True, help="目標部門 id（小寫 ASCII slug，例如 mf4d）")
    parser.add_argument("--file", required=True, help="CSV 或 JSON 檔路徑（JSON 為 parse_alarms.py 的標準輸出）")
    parser.add_argument("--mode", choices=["append", "upsert", "replace"], default="append",
                        help="append/upsert：只新增/更新來源裡出現的代碼，不刪除既有資料（預設）。"
                             "replace：整批取代，來源沒有的代碼會被刪除，需搭配 --yes-i-mean-replace")
    parser.add_argument("--yes-i-mean-replace", action="store_true",
                        help="replace 模式的安全閥，防止誤觸破壞性操作")
    parser.add_argument("--create-missing-devices", action="store_true",
                        help="opt-in：來源裡出現但部門機種表沒有的 device_model，自動建立新機種。"
                             "預設關閉，因為預設自動建立會讓來源錯字（PLIM003）直接變成一台新機種")
    parser.add_argument("--accept-incomplete", metavar="理由",
                        help="solution 覆蓋率低於門檻時，明確承認並說明原因（會寫進 alarm_history）。"
                             "沒有這個參數且完整度不足時整批中止——這是唯一能擋住『半年後看到一批"
                             "空欄位，沒人記得是當初就沒有、抽取失敗、還是漏匯』的機制")
    parser.add_argument("--dry-run", action="store_true", help="只印出將發生的動作，不實際寫入")
    args = parser.parse_args()

    if not DEPT_ID_RE.match(args.department):
        print(f"錯誤：--department 必須符合 ^[a-z0-9_]{{1,32}}$，收到：{args.department!r}", file=sys.stderr)
        sys.exit(1)

    if args.mode == "replace" and not args.yes_i_mean_replace:
        print("錯誤：--mode replace 會刪除該部門所有不在來源裡的警報，"
              "必須加上 --yes-i-mean-replace 才會執行", file=sys.stderr)
        sys.exit(1)

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"錯誤：找不到檔案 {file_path}", file=sys.stderr)
        sys.exit(1)

    try:
        rows = _load_file(file_path)
    except ValueError as e:
        print(f"錯誤：檔案解析失敗 — {e}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("錯誤：來源沒有任何資料列", file=sys.stderr)
        sys.exit(1)

    dupes = _dedupe_check(rows)
    if dupes:
        print(f"錯誤：來源內有重複的 (device_model, code, variant) 組合，會導致後者覆蓋前者：", file=sys.stderr)
        for d in dupes:
            print(f"  - {d[0]} / {d[1]} / {d[2] or '(無變體)'}", file=sys.stderr)
        sys.exit(1)

    missing, csv_models = _validate_devices_exist(rows, args.department)
    if missing and not args.create_missing_devices:
        print(f"錯誤：以下機種在部門 {args.department!r} 的機種表中不存在，整批中止，未寫入任何一筆：",
              file=sys.stderr)
        for model, count in sorted(missing.items()):
            print(f"  - {model}：{count} 筆警報受影響", file=sys.stderr)
        print("若確定要自動建立這些機種，加上 --create-missing-devices（請先確認機種名稱無錯字）",
              file=sys.stderr)
        sys.exit(1)

    to_create = sorted(missing.keys()) if (missing and args.create_missing_devices) else []

    completeness = _completeness_report(rows)
    solution_pct = completeness.get("solution", 0) / len(rows) if rows else 0
    incomplete = solution_pct < COMPLETENESS_WARN_THRESHOLD
    if incomplete and not args.accept_incomplete:
        print(f"\n錯誤：solution 完整度 {solution_pct*100:.1f}%，低於門檻 "
              f"{COMPLETENESS_WARN_THRESHOLD*100:.0f}%，整批中止：", file=sys.stderr)
        for field, n in completeness.items():
            print(f"  {field:14} 有值 {n}/{len(rows)} ({n*100//max(len(rows),1)}%)", file=sys.stderr)
        print("\n若確定要在這個完整度下匯入（例如本廠自撰處置走 local_solution 是預期路徑，"
              "非原廠文件缺漏），加上 --accept-incomplete \"理由\"", file=sys.stderr)
        sys.exit(1)

    print(f"部門：{args.department}")
    print(f"模式：{args.mode}")
    print(f"來源筆數：{len(rows)}，涉及機種數：{len(csv_models)}")
    if to_create:
        print(f"將自動建立 {len(to_create)} 個新機種：{', '.join(to_create)}")
    print(f"完整度：" + "，".join(
        f"{f} {n}/{len(rows)} ({n*100//max(len(rows),1)}%)" for f, n in completeness.items()
    ))
    if incomplete:
        print(f"⚠ solution 完整度低於門檻，已用 --accept-incomplete 理由：{args.accept_incomplete!r}")
    print(f"將寫入 {len(rows)} 筆警報")

    to_delete_count = 0
    if args.mode == "replace":
        existing_alarms = alarms_store.load(department=args.department)
        new_keys = {(r["device_model"], r["code"], r.get("variant", "")) for r in rows}
        to_delete_count = sum(
            1 for a in existing_alarms
            if (a.get("device_model"), a.get("code"), a.get("variant", "")) not in new_keys
        )
        print(f"將刪除 {to_delete_count} 筆既有警報（不在本次來源內）")

    if args.dry_run:
        print("\n[dry-run] 未實際寫入，以上為預覽結果")
        sys.exit(0)

    if to_create:
        print(f"\n建立 {len(to_create)} 個新機種...")
        _create_missing_devices(to_create, args.department)

    if args.mode == "replace":
        print(f"\n整批取代寫入（含刪除）...")
        alarms_store.save(rows, department=args.department, on_conflict="department,device_model,code,variant")
    else:
        print(f"\n逐筆 upsert（append/upsert 模式，不刪除既有資料）...")
        for r in rows:
            alarms_store.upsert_one(r, department=args.department, on_conflict="department,device_model,code,variant")

    if incomplete and args.accept_incomplete:
        # 理由寫進 alarm_history，跟一般 CREATE/UPDATE 的稽核軌跡同一張表，
        # 用專屬 operation 區分——這是唯一能擋住「第二次」的機制：不寫的
        # 話，半年後看到這批資料 solution 全空，沒人知道是當初就沒有、
        # 抽取失敗、還是漏匯（見 parse_alarms.py 規格文件第六節）。
        audit_logger.log(
            "bulk_import_incomplete", department=args.department,
            new_data={"device_model": ",".join(sorted(csv_models)), "code": f"{len(rows)} 筆",
                      "reason": args.accept_incomplete, "solution_pct": round(solution_pct * 100, 1)},
        )

    print(f"\n完成：寫入 {len(rows)} 筆警報" + (f"，刪除 {to_delete_count} 筆" if args.mode == "replace" else ""))


if __name__ == "__main__":
    main()
