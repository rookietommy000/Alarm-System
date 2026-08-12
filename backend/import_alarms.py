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

from storage import alarms_store, devices_store  # noqa: E402

# 與 app.py 的 DEPT_ID_RE 定義一致（PLAN 2.2.4/4 節），此工具不依賴 Flask app
# context，避免 import app 觸發不必要的 create_app() 初始化，故在此重複定義。
DEPT_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
SEVERITIES = {"嚴重", "警告", "資訊"}
ALARM_FIELDS = ["code", "device_model", "severity", "description", "cause", "solution", "keywords", "sol_steps"]


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
        "severity": severity,
        "description": (row.get("description") or "").strip(),
        "cause": (row.get("cause") or "").strip(),
        "solution": (row.get("solution") or "").strip(),
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
    """PLAN 1.3 節：(device_model, code) 在同一部門內必須唯一，CSV 本身若有重複
    會在 upsert 階段被後者覆蓋前者、且不會有任何錯誤訊息——這裡先攔下來讓操作者知道。"""
    seen = {}
    dupes = []
    for i, r in enumerate(rows):
        key = (r["device_model"], r["code"])
        if key in seen:
            dupes.append(key)
        seen[key] = i
    return dupes


def main():
    parser = argparse.ArgumentParser(description="批次匯入部門警報代碼（PLAN 第 6 節）")
    parser.add_argument("--department", required=True, help="目標部門 id（小寫 ASCII slug，例如 mf4d）")
    parser.add_argument("--file", required=True, help="CSV 檔路徑")
    parser.add_argument("--mode", choices=["append", "upsert", "replace"], default="append",
                        help="append/upsert：只新增/更新 CSV 裡出現的代碼，不刪除既有資料（預設）。"
                             "replace：整批取代，CSV 沒有的代碼會被刪除，需搭配 --yes-i-mean-replace")
    parser.add_argument("--yes-i-mean-replace", action="store_true",
                        help="replace 模式的安全閥，防止誤觸破壞性操作")
    parser.add_argument("--create-missing-devices", action="store_true",
                        help="opt-in：CSV 裡出現但部門機種表沒有的 device_model，自動建立新機種。"
                             "預設關閉，因為預設自動建立會讓 CSV 錯字（PLIM003）直接變成一台新機種")
    parser.add_argument("--dry-run", action="store_true", help="只印出將發生的動作，不實際寫入")
    args = parser.parse_args()

    if not DEPT_ID_RE.match(args.department):
        print(f"錯誤：--department 必須符合 ^[a-z0-9_]{{1,32}}$，收到：{args.department!r}", file=sys.stderr)
        sys.exit(1)

    if args.mode == "replace" and not args.yes_i_mean_replace:
        print("錯誤：--mode replace 會刪除該部門所有不在 CSV 裡的警報，"
              "必須加上 --yes-i-mean-replace 才會執行", file=sys.stderr)
        sys.exit(1)

    csv_path = Path(args.file)
    if not csv_path.exists():
        print(f"錯誤：找不到檔案 {csv_path}", file=sys.stderr)
        sys.exit(1)

    try:
        rows = _load_csv(csv_path)
    except ValueError as e:
        print(f"錯誤：CSV 解析失敗 — {e}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("錯誤：CSV 沒有任何資料列", file=sys.stderr)
        sys.exit(1)

    dupes = _dedupe_check(rows)
    if dupes:
        print(f"錯誤：CSV 內有重複的 (device_model, code) 組合，會導致後者覆蓋前者：", file=sys.stderr)
        for d in dupes:
            print(f"  - {d[0]} / {d[1]}", file=sys.stderr)
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

    print(f"部門：{args.department}")
    print(f"模式：{args.mode}")
    print(f"CSV 筆數：{len(rows)}，涉及機種數：{len(csv_models)}")
    if to_create:
        print(f"將自動建立 {len(to_create)} 個新機種：{', '.join(to_create)}")
    print(f"將寫入 {len(rows)} 筆警報")

    to_delete_count = 0
    if args.mode == "replace":
        existing_alarms = alarms_store.load(department=args.department)
        new_keys = {(r["device_model"], r["code"]) for r in rows}
        to_delete_count = sum(
            1 for a in existing_alarms
            if (a.get("device_model"), a.get("code")) not in new_keys
        )
        print(f"將刪除 {to_delete_count} 筆既有警報（不在本次 CSV 內）")

    if args.dry_run:
        print("\n[dry-run] 未實際寫入，以上為預覽結果")
        sys.exit(0)

    if to_create:
        print(f"\n建立 {len(to_create)} 個新機種...")
        _create_missing_devices(to_create, args.department)

    if args.mode == "replace":
        print(f"\n整批取代寫入（含刪除）...")
        alarms_store.save(rows, department=args.department, on_conflict="department,device_model,code")
    else:
        print(f"\n逐筆 upsert（append/upsert 模式，不刪除既有資料）...")
        for r in rows:
            alarms_store.upsert_one(r, department=args.department, on_conflict="department,device_model,code")

    print(f"\n完成：寫入 {len(rows)} 筆警報" + (f"，刪除 {to_delete_count} 筆" if args.mode == "replace" else ""))


if __name__ == "__main__":
    main()
