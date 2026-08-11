#!/usr/bin/env python3
"""
cleanup_subcodes.py — 清除誤解析的子項目警報
將父警報（高 code）後面跟著的小 code（0001-0010）合回父警報的 cause/solution，
再從清單中刪除這些子項目。

影響設備: TFM002, TFM001, ACP002, PILM004
"""

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

# 小 code 上限：<=10 視為子項目（子說明或子解法）
SUBCODE_MAX = 10


def is_subcode(code_str: str) -> bool:
    try:
        return int(code_str) <= SUBCODE_MAX
    except ValueError:
        return False


def merge_and_clean(alarms: list) -> tuple[list, int]:
    """
    掃描 alarms，把每個高 code 父警報後面緊跟的小 code 子項目
    合入父警報的 cause（子說明）與 solution（處理步驟），然後刪除子項目。
    回傳 (新清單, 刪除筆數)
    """
    result = []
    i = 0
    removed = 0

    while i < len(alarms):
        alarm = alarms[i]
        current_code = alarm["code"]

        # 如果這筆本身是小 code 且前面沒有父項（開頭就是小 code），保留不動
        if is_subcode(current_code) and not result:
            result.append(alarm)
            i += 1
            continue

        # 往後收集緊跟著的子項目
        sub_items = []
        j = i + 1
        while j < len(alarms) and is_subcode(alarms[j]["code"]):
            sub_items.append(alarms[j])
            j += 1

        if sub_items:
            # 分類：desc 有實質內容 → 子說明（加入 cause）；只有 Check/Verify 等 → 加入 solution
            sub_causes = []
            sub_solutions = []
            for s in sub_items:
                desc = s["description"].strip()
                cause = s.get("cause", "").strip()
                sol = s.get("solution", "").strip()

                # 合並子項目的 description 進父警報 cause
                if desc:
                    sub_causes.append(f"{int(s['code'])} - {desc}")
                if cause:
                    sub_causes.append(cause)
                if sol:
                    sub_solutions.append(sol)

            if sub_causes:
                existing = alarm.get("cause", "").strip()
                addition = "\n".join(sub_causes)
                alarm["cause"] = (existing + "\n" + addition).strip() if existing else addition

            if sub_solutions:
                existing = alarm.get("solution", "").strip()
                addition = "\n".join(sub_solutions)
                alarm["solution"] = (existing + "\n" + addition).strip() if existing else addition

            removed += len(sub_items)
            result.append(alarm)
            i = j
        else:
            result.append(alarm)
            i += 1

    return result, removed


def process(fname: str):
    path = DATA / fname
    if not path.exists():
        print(f"  找不到 {fname}")
        return

    alarms = json.loads(path.read_text(encoding="utf-8"))
    before = len(alarms)
    cleaned, removed = merge_and_clean(alarms)

    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {fname}: {before} 筆 → {len(cleaned)} 筆，刪除 {removed} 筆子項目")


def main():
    targets = ["TFM002.json", "TFM001.json", "ACP002.json", "PILM004.json"]
    for t in targets:
        process(t)

    # 驗證：確認無重複 code
    print()
    for t in targets:
        path = DATA / t
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        from collections import Counter
        counts = Counter(a["code"] for a in data)
        dups = {c: n for c, n in counts.items() if n > 1}
        if dups:
            print(f"  ⚠ {t} 仍有重複 code: {dups}")
        else:
            print(f"  ✓ {t} ({len(data)}筆) 無重複 code")


if __name__ == "__main__":
    main()
