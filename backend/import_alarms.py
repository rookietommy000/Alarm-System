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

CSV/Excel 欄位（對應 alarms 表，表頭必須完整——alarm_ingest.load_csv/
load_excel 會比對固定範本，缺表頭直接報「缺少欄位」而不是悄悄漏值，
見 alarm_ingest/parse.py 的 REQUIRED_HEADERS）：
    code, device_model, variant, severity, description, cause, solution,
    local_solution, keywords, sol_steps
    - code, device_model, variant, description, cause, solution,
      local_solution 為必要表頭（值可留空，但欄位本身必須存在）
    - severity 必須是「嚴重」/「警告」/「資訊」之一，或留空
    - keywords 用分號分隔（避免與 CSV 逗號分隔衝突），例如 "主軸;過載"
    - sol_steps 是 JSON 字串（可留空）
    - Excel（.xlsx/.xlsm）只讀第一個工作表、第一列為表頭，不做分頁
      掃描或欄位智慧偵測
"""
import argparse
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from storage import alarms_store, audit_logger, devices_store  # noqa: E402
from alarm_ingest import (  # noqa: E402
    load_file,
    validate_devices_exist,
    dedupe_check,
    completeness_report,
    commit_rows,
    COMPLETENESS_WARN_THRESHOLD,
)

# 與 app.py 的 DEPT_ID_RE 定義一致（PLAN 2.2.4/4 節），此工具不依賴 Flask app
# context，避免 import app 觸發不必要的 create_app() 初始化，故在此重複定義。
DEPT_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")

# 解析、驗證、寫入的共用邏輯抽進 alarm_ingest/（後台批次匯入 API 共用同一份，
# 見 PLAN 批次匯入 UI 第 3.2 節：preview 與 commit 跑完全相同的驗證 pipeline）。
# 這支檔案只保留 CLI 特有的呈現層：argparse 介面與 print 輸出。


def _create_missing_devices(missing_models: list, department: str) -> None:
    for model in missing_models:
        new_id = f"{department}-{model}"
        devices_store.upsert_one(
            {"id": new_id, "model": model, "category": "", "line": ""},
            department=department,
            on_conflict="department,model",
        )


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
        rows, row_errors = load_file(file_path)
    except ValueError as e:
        print(f"錯誤：檔案解析失敗 — {e}", file=sys.stderr)
        sys.exit(1)

    # CLI 是技術端手動操作的離線工具，不進後台 API 專屬的
    # pending_alarm_imports 待審機制——格式異常的列一律視為整批失敗，
    # 維持原本嚴格把關的行為，不自動略過或部分寫入。
    if row_errors:
        print(f"錯誤：來源有 {len(row_errors)} 筆格式異常，整批中止，未寫入任何一筆：", file=sys.stderr)
        for e in row_errors:
            print(f"  - 第 {e['row']} 筆：{e['reason']}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("錯誤：來源沒有任何資料列", file=sys.stderr)
        sys.exit(1)

    dupes = dedupe_check(rows)
    if dupes:
        print(f"錯誤：來源內有重複的 (device_model, code, variant) 組合，會導致後者覆蓋前者：", file=sys.stderr)
        for d in dupes:
            print(f"  - {d[0]} / {d[1]} / {d[2] or '(無變體)'}", file=sys.stderr)
        sys.exit(1)

    missing, csv_models = validate_devices_exist(rows, args.department)
    if missing and not args.create_missing_devices:
        print(f"錯誤：以下機種在部門 {args.department!r} 的機種表中不存在，整批中止，未寫入任何一筆：",
              file=sys.stderr)
        for model, count in sorted(missing.items()):
            print(f"  - {model}：{count} 筆警報受影響", file=sys.stderr)
        print("若確定要自動建立這些機種，加上 --create-missing-devices（請先確認機種名稱無錯字）",
              file=sys.stderr)
        sys.exit(1)

    to_create = sorted(missing.keys()) if (missing and args.create_missing_devices) else []

    completeness = completeness_report(rows)
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
        result = {"partial": False, "succeeded": len(rows), "failed": 0}
    else:
        # commit_rows() 遇錯即停，不繼續跑同樣會失敗的後續筆（見
        # alarm_ingest/commit.py：系統性錯誤繼續跑只會產生一堆重複的
        # 錯誤訊息，且拖長佔用時間）。CLI 與後台批次匯入 API 共用同一份
        # 邏輯，這裡只是把回傳結果印成人看得懂的文字。
        print(f"\n逐筆 upsert（append/upsert 模式，不刪除既有資料）...")
        result = commit_rows(rows, department=args.department, import_mode=args.mode)

    if result["partial"]:
        f = result["first_failure"]
        print(f"\n⚠ 部分寫入：成功 {result['succeeded']} / {len(rows)} 筆，"
              f"於第 {f['row']} 筆中止（code={f['code']!r}, variant={f['variant']!r}）", file=sys.stderr)
        print(f"原因：{f['reason']}", file=sys.stderr)
        print(f"\n{result['recovery']}", file=sys.stderr)

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

    if result["partial"]:
        sys.exit(1)

    print(f"\n完成：寫入 {result['succeeded']} 筆警報" + (f"，刪除 {to_delete_count} 筆" if args.mode == "replace" else ""))


if __name__ == "__main__":
    main()
