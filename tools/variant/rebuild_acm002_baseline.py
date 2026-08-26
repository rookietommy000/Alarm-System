#!/usr/bin/env python3
"""重建 ACM002 回歸基準。

這組基準不是「解析 ACM002警報.xlsx 應該得到 179 筆」——沒有人驗證過
原始 Excel 該解析出什麼。它要證明的是：parse_alarms.py 的輸出跟資料庫
既有的 179 筆完全一致，upsert 進去不會製造重複列。

做法：把 data/backup/ACM002.json（既有資料庫備份）反推成來源格式的
CSV，餵給 parse_alarms.py，再跟原始 JSON 比對。

用法：
    python tools/variant/rebuild_acm002_baseline.py
"""
import csv
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BACKUP = ROOT / "data" / "backup" / "ACM002.json"
SRC_CSV = pathlib.Path("/tmp/acm002_src.csv")
OUT_JSON = pathlib.Path("/tmp/acm002_out.json")


def main() -> None:
    existing = json.loads(BACKUP.read_text(encoding="utf-8"))
    print(f"既有資料：{len(existing)} 筆")

    with SRC_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Description", "Cause", "Action"])
        for r in existing:
            w.writerow([f"{r['code']} {r['description']}", r.get("cause", ""), r.get("solution", "")])

    subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).parent / "parse_alarms.py"),
         "-i", str(SRC_CSV), "-m", "ACM002", "--variant-mode", "never", "-o", str(OUT_JSON)],
        check=True,
    )

    produced = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    a = {r["code"]: r for r in existing}
    b = {r["code"]: r for r in produced}

    print("代碼集合相同:", set(a) == set(b))
    print("variant 全空:", all(r.get("variant", "") == "" for r in produced))
    diff = [c for c in a if a[c]["description"] != b.get(c, {}).get("description")]
    print("description 不一致:", len(diff))


if __name__ == "__main__":
    main()
