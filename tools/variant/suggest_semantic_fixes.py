#!/usr/bin/env python3
"""對 scan_semantic_quality.py 找出的疑慮補上建議修正文字（規劃第 1c 項
第二階段）——一次性工具，不進 Flask 路由。

跟第一階段（scan_semantic_quality.py）的差異：那支只標記「哪裡可能錯」，
不給修正文字（審核跟修正是兩個不同把關動作，先前決定分開執行，避免
「順手就改了」跳過人工核對這一步）。這支只處理已經被標記過的疑慮，
針對每一筆的 issue 理由，請 AI 給出建議的正確譯法——仍然只是「建議」，
不直接寫回 alarms.json，最終要不要採用由人工逐筆核對決定（同
EXTRACTION_SPEC.md「一條不能跨的線」：AI 產出的內容必須逐筆人工確認
才能進資料庫，這條線同樣適用於「修正建議」）。

用法：
    python suggest_semantic_fixes.py -i scan_report.json -o fixes.json
    python suggest_semantic_fixes.py -i scan_report.json -o fixes.json --limit 20  # 試跑

輸出：JSON，每筆疑慮附加 suggested_description（建議的完整 description
文字，英文標題不變、只改中文部分）。不寫入資料庫，不修改任何檔案。
"""
import argparse
import json
import os
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ALARMS_PATH = ROOT / "data" / "backup" / "alarms.json"

# apply_semantic_fix() 現在是 backend/alarm_ingest/quality.py 的共用實作
# （後台語意審核端點 app.py 的 update_semantic_review 也用同一份）——分岔
# 的代價是離線建議工具跟現場審核跑出不同結果，比各自維護一份風險高，
# 所以這裡改用 import 而非複製（同 parse_alarms.py 的 read_tabular 一樣的
# 判斷準則：CLI 依賴 backend/，而非反過來）。
sys.path.insert(0, str(ROOT / "backend"))
from alarm_ingest.quality import apply_semantic_fix as _apply_fix  # noqa: E402

BATCH_SIZE = 20

_PROMPT = """你是製藥設備警報資料庫的翻譯修正工具。輸入是一批已經被人工/AI
標記為「中文翻譯疑似有誤」的警報記錄，每筆包含原始的 description（英文
標題+中文標題）跟先前審核時給出的 issue（哪裡錯、為什麼錯）。

你的任務：針對每一筆，給出建議修正後的中文標題。

## 規則

1. **只改中文部分，英文標題原封不動照抄。**
2. **修正要基於 issue 指出的問題**，不要重新發明其他修正方向。
3. **保持原本的簡潔風格**（警報標題通常很短，不要寫成一整句說明）。
4. **不確定「原文到底在講什麼設備/元件」時，寧可保守修正**（只修正
   issue 明確指出的錯誤詞，不要順便重寫整句）——這批資料最終仍要
   人工核對，你的修正只是建議稿，不是最終答案。
5. **輸出的 suggested_zh 只放中文部分**（不要重複英文標題）。

## 輸出格式

只輸出 JSON 陣列，不要有任何前後說明文字，不要包 markdown 程式碼區塊。

[
  {
    "index": 0,
    "suggested_zh": "箱子聚合錯誤"
  }
]

## 範例

輸入：
  description: "CASE AGGREGATION ERROR 案例聚合錯誤"
  issue: "英文的「CASE」在此處指「紙箱」，中文被誤譯為「案例」"

輸出：
  suggested_zh: "箱子聚合錯誤"
"""


def _load_client():
    from google import genai
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _parse_response(raw: str) -> list:
    """回傳空 list 只代表「AI 確認沒有建議」，格式解析失敗必須用例外
    區分，否則呼叫端會把解析失敗誤記為「這批已處理、沒有建議」，跟真正
    的批次失敗一樣讓筆數靜默漏掉（同 scan_semantic_quality.py 的
    _parse_response 修法）。"""
    text = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"AI 回傳的 JSON 不是陣列（實際類型：{type(data).__name__}）")
    return data


def suggest_batch(client, model: str, batch: list) -> dict:
    payload = json.dumps(
        [{"index": i, "description": f["description"], "issue": f["issue"]}
         for i, f in enumerate(batch)],
        ensure_ascii=False,
    )
    response = client.models.generate_content(
        model=model,
        contents=[_PROMPT, f"\n輸入：\n{payload}"],
    )
    results = _parse_response(response.text.strip())
    by_index = {r["index"]: r.get("suggested_zh", "") for r in results if isinstance(r.get("index"), int)}
    return by_index


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-i", "--input", required=True, help="scan_semantic_quality.py 產出的報告")
    ap.add_argument("-o", "--output", required=True, help="附加修正建議後的輸出路徑")
    ap.add_argument("--limit", type=int, default=None, help="只處理前 N 筆（試跑用）")
    args = ap.parse_args()

    report = json.loads(pathlib.Path(args.input).read_text(encoding="utf-8"))
    findings = report["findings"]
    if args.limit:
        findings = findings[: args.limit]

    client = _load_client()
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

    failed_batches = []
    total_batches = (len(findings) + BATCH_SIZE - 1) // BATCH_SIZE
    for b in range(total_batches):
        batch = findings[b * BATCH_SIZE : (b + 1) * BATCH_SIZE]
        print(f"[{b+1}/{total_batches}] 產生修正建議 {len(batch)} 筆...", file=sys.stderr)
        try:
            by_index = suggest_batch(client, model, batch)
            batch_failed = False
        except Exception as e:
            print(f"  批次失敗，跳過：{e}", file=sys.stderr)
            failed_batches.append({
                "batch_index": b,
                "codes": [f["code"] for f in batch],
                "error": f"{type(e).__name__}: {e}",
            })
            by_index = {}
            batch_failed = True
        for i, f in enumerate(batch):
            if batch_failed:
                # 批次整批失敗跟「AI 判斷不需要修正」必須能區分，不能
                # 兩者都寫成同一種空值——否則這筆看起來像已經處理過、
                # 沒有建議，實際上根本沒被送去 AI 判斷過。
                f["suggested_zh"] = None
                f["suggested_description"] = None
                f["suggestion_failed"] = True
                continue
            suggested_zh = by_index.get(i, "")
            f["suggested_zh"] = suggested_zh
            f["suggested_description"] = _apply_fix(f["description"], suggested_zh) if suggested_zh else None
        time.sleep(1)

    out = {**report, "findings": findings, "failed_batches": failed_batches}
    pathlib.Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_with_fix = sum(1 for f in findings if f.get("suggested_description"))
    n_failed = sum(1 for f in findings if f.get("suggestion_failed"))
    print(f"完成，{n_with_fix}/{len(findings)} 筆有建議修正，已寫入 {args.output}", file=sys.stderr)
    if failed_batches:
        print(
            f"⚠ {len(failed_batches)} 個批次失敗，{n_failed} 筆未實際產生建議"
            f"（見輸出檔 failed_batches 欄位，或各筆的 suggestion_failed 標記）",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
