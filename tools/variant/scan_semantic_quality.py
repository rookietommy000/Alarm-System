#!/usr/bin/env python3
"""全庫語意品質掃描（規劃第 1c 項）——一次性工具，不進 Flask 路由。

跟 alarm_ingest/split.py 的差異：split 做「切分」，輸出必然是輸入的
字元子集，可以程式化驗證（verify_no_generation()）。這裡做「審核」，
判斷一段已經通順的中文是不是誤譯——沒有子集可驗證，只能列出 AI 的
懷疑清單，最終由人工逐筆確認，AI 的角色到此為止。

背景：TFM001 量測時發現既有資料庫本身有音譯亂碼案例（MANCANZA TENSIONE
DI RETE → 曼坎薩緊張迪雷特、ARRESTO MANUALE → 逮捕手冊），這類錯誤
「通順但錯誤」，regex 掃全庫誤報率太高（ACM001 曾誤報 135 處），
改用 AI 語意判斷。

用法：
    python scan_semantic_quality.py                # 掃全部 1759 筆
    python scan_semantic_quality.py --limit 50      # 先跑一小批試跑
    python scan_semantic_quality.py -o report.json  # 指定報告輸出路徑

輸出：JSON 報告，每筆疑慮包含 code/device_model/欄位/AI 的判斷理由。
不寫入資料庫，不修改任何檔案。
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

BATCH_SIZE = 30

_PROMPT = """你是製藥設備警報資料庫的語意審核工具。輸入是一批警報記錄，每筆
有 code、description（英文標題+中文標題）、cause、solution 欄位。

這份資料庫可能混有音譯或機器翻譯造成的錯誤——文字讀起來通順、文法正確，
但語意跟英文原文完全不符。這類錯誤人工肉眼掃過去不會發現，因為它「看起來
沒問題」，只有對照英文原文才看得出來。

已知案例（來自這個資料庫的既有錯誤，用來校準你的判斷標準）：
- "MANCANZA TENSIONE DI RETE" 被翻成「曼坎薩緊張迪雷特」（義大利文被
  當成人名/地名音譯，實際意思是「電源電壓中斷」）
- "ARRESTO MANUALE" 被翻成「逮捕手冊」（Arresto 是「停止」被誤譯成
  「逮捕」，Manuale 是「手動」被誤譯成「手冊」，實際意思是「手動停止」）
- "REPHASE MACHINE" 被翻成「複相機」（Rephase 是「重新相位/重啟」被
  誤譯成「複相」，Machine 被誤譯成「相機」，實際意思接近「機器重新啟動」）

## 你的任務

對每一筆，檢查中文部分（description 的中文標題、cause、solution 如果
有中文）是否與英文原文語意相符。只標記你有把握的可疑項目，不要為了
湊數而亂猜——寧可漏掉，不要誤報淹沒真正的問題。

## 輸出格式

只輸出 JSON 陣列，不要有任何前後說明文字，不要包 markdown 程式碼區塊。
沒有可疑項目就輸出空陣列 []。

[
  {
    "index": 0,
    "issue": "中文「曼坎薩緊張迪雷特」是英文的音譯亂碼，不是翻譯，實際意思應為電源電壓中斷",
    "confidence": "high"
  }
]

confidence 只能是 "high"（幾乎確定是錯的）或 "medium"（懷疑但不確定，
需要人工用英文原文重新判斷）。

## 絕對不要做的事

- 不要輸出修正後的文字，你的工作只是標記，不是修正
- 不要對本來就是空白或本來就沒有中文的欄位提出意見
- 不要因為用詞不夠精確或不夠通順就標記——只標記語意錯誤（意思不對），
  不是文筆問題
"""


def _load_client():
    from google import genai
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _parse_response(raw: str) -> list:
    """回傳空 list 代表「AI 確認沒有疑慮」，跟「AI 回傳格式解析失敗」
    必須能區分——後者若也靜默回空 list，會讓這個批次被誤記為「已掃描、
    無發現」，跟批次整批失敗一樣掩蓋掉未實際完成掃描的筆數，所以格式
    錯誤這裡改成拋出例外，讓呼叫端 scan_batch()/main() 既有的批次失敗
    處理（記錄進 report 的 failed_batches）一併涵蓋這個情況。"""
    text = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"AI 回傳的 JSON 不是陣列（實際類型：{type(data).__name__}）")
    return data


def scan_batch(client, model: str, batch: list) -> list:
    payload = json.dumps(
        [
            {
                "index": i,
                "code": r["code"],
                "device_model": r["device_model"],
                "description": r.get("description") or "",
                "cause": r.get("cause") or "",
                "solution": r.get("solution") or "",
            }
            for i, r in enumerate(batch)
        ],
        ensure_ascii=False,
    )
    response = client.models.generate_content(
        model=model,
        contents=[_PROMPT, f"\n輸入：\n{payload}"],
    )
    findings = _parse_response(response.text.strip())

    results = []
    for f in findings:
        idx = f.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(batch)):
            continue
        row = batch[idx]
        results.append({
            "code": row["code"],
            "device_model": row["device_model"],
            "description": row.get("description") or "",
            "issue": f.get("issue", ""),
            "confidence": f.get("confidence", "medium"),
        })
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="只掃前 N 筆（試跑用）")
    ap.add_argument("-o", "--output", default=None, help="報告輸出路徑（預設印到 stdout）")
    args = ap.parse_args()

    rows = json.loads(ALARMS_PATH.read_text(encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]

    client = _load_client()
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

    all_findings = []
    failed_batches = []
    total_batches = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE
    for b in range(total_batches):
        batch = rows[b * BATCH_SIZE : (b + 1) * BATCH_SIZE]
        print(f"[{b+1}/{total_batches}] 掃描 {len(batch)} 筆...", file=sys.stderr)
        try:
            findings = scan_batch(client, model, batch)
        except Exception as e:
            print(f"  批次失敗，跳過：{e}", file=sys.stderr)
            failed_batches.append({
                "batch_index": b,
                "codes": [r["code"] for r in batch],
                "error": f"{type(e).__name__}: {e}",
            })
            continue
        all_findings.extend(findings)
        if findings:
            print(f"  發現 {len(findings)} 筆疑慮", file=sys.stderr)
        time.sleep(1)

    scanned_count = len(rows) - sum(len(fb["codes"]) for fb in failed_batches)
    report = {
        "total_scanned": len(rows),
        "total_findings": len(all_findings),
        "failed_batches": failed_batches,
        "findings": all_findings,
    }
    out = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        pathlib.Path(args.output).write_text(out, encoding="utf-8")
        print(f"報告已寫入 {args.output}（共 {len(all_findings)} 筆疑慮）", file=sys.stderr)
    else:
        print(out)

    if failed_batches:
        missed = sum(len(fb["codes"]) for fb in failed_batches)
        print(
            f"⚠ {len(failed_batches)} 個批次失敗，{missed} 筆未實際掃描"
            f"（僅 {scanned_count}/{len(rows)} 筆完成掃描，見報告 failed_batches 欄位）",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
