#!/usr/bin/env python3
"""對 scan_semantic_quality.py 找出的疑慮補上建議修正文字（規劃第 1c 項
第二階段）——一次性工具，不進 Flask 路由。

跟第一階段（scan_semantic_quality.py）的差異：那支只標記「哪裡可能錯」，
不給修正文字（審核跟修正是兩個不同把關動作，先前決定分開執行，避免
「順手就改了」跳過人工核對這一步）。這支只處理已經被標記過的疑慮，
針對每一筆的 issue 理由，請 AI 給出建議的正確譯法——仍然只是「建議」，
寫入 semantic_review_findings 待人工審核，不修改 alarms。

用法：
    python suggest_semantic_fixes.py -i scan_report.json              # 預設 dry-run
    python suggest_semantic_fixes.py -i scan_report.json --limit 20
    ALLOW_LOCAL_PRODUCTION_WRITE=1 python suggest_semantic_fixes.py -i scan_report.json --write

預設只印出將寫入的 JSON；--write 才呼叫 save_all()。
請安排在沒有其他審核寫入的時段執行：既有介面無法保證讀取與 upsert 的原子性。
"""
import argparse
from contextlib import redirect_stderr
import io
import json
import os
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
from scan_semantic_quality import load_storage, load_alarms

# apply_semantic_fix() 現在是 backend/alarm_ingest/quality.py 的共用實作
# （後台語意審核端點 app.py 的 update_semantic_review 也用同一份）——分岔
# 的代價是離線建議工具跟現場審核跑出不同結果，比各自維護一份風險高，
# 所以這裡改用 import 而非複製（同 parse_alarms.py 的 read_tabular 一樣的
# 判斷準則：CLI 依賴 backend/，而非反過來）。
sys.path.insert(0, str(ROOT / "backend"))

def _apply_fix(description, suggested_zh):
    # 延遲 import：alarm_ingest 間接載入 storage，須等 .env 設定就緒。
    from alarm_ingest.quality import apply_semantic_fix
    return apply_semantic_fix(description, suggested_zh)

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


def prepare_findings(findings, alarms, store):
    current = {(a["device_model"], a["code"]): a for a in alarms}
    diagnostics = io.StringIO()
    with redirect_stderr(diagnostics):
        existing = store.load_all()
    if diagnostics.getvalue():
        print(diagnostics.getvalue(), end="", file=sys.stderr)
        raise RuntimeError("load_all() 有異常訊息，無法確認既有審核狀態，停止寫入")
    print(f"既有 findings：{len(existing)} 筆", file=sys.stderr)
    # 只有明確 pending 才能更新；已審核或未知／缺漏狀態均保留，
    # 避免 upsert 重置審核歷史。不可把整份 existing 再送回 save_all。
    protected = {(f["device_model"], f["code"]) for f in existing if f.get("status") != "pending"}
    rows = []
    seen = set()
    for f in findings:
        key = (f["device_model"], f["code"])
        if key in seen:
            raise ValueError(f"報告有重複唯一鍵：{key}")
        seen.add(key)
        if key not in current or f["description"] != (current[key].get("description") or ""):
            raise ValueError(f"{key} 不在 mf4d 或 description 已變更，請重新掃描")
        if key in protected or f.get("suggestion_failed"):
            continue
        rows.append({
            **{k: f[k] for k in ("device_model", "code", "description", "issue", "confidence",
                                 "suggested_zh", "suggested_description")},
            "status": "pending",
        })
    print(f"將寫入 {len(rows)} 筆；跳過 {len(findings) - len(rows)} 筆已審核／失敗項目", file=sys.stderr)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-i", "--input", required=True, help="scan_semantic_quality.py 產出的報告")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="預設：只印出將寫入的內容")
    mode.add_argument("--write", action="store_true", help="寫入 Supabase 待審核清單")
    ap.add_argument("--limit", type=int, default=None, help="只處理前 N 筆（試跑用）")
    args = ap.parse_args()

    # 在載入 .env 前檢查，授權必須由執行者明確傳入。
    if args.write and os.environ.get("ALLOW_LOCAL_PRODUCTION_WRITE") != "1":
        raise RuntimeError("--write 必須明確設定 ALLOW_LOCAL_PRODUCTION_WRITE=1")
    storage = load_storage()
    load_alarms(storage)
    store = storage.SemanticReviewStore()
    report = json.loads(pathlib.Path(args.input).read_text(encoding="utf-8"))
    if report.get("department", "mf4d") != "mf4d":
        raise ValueError("只允許 mf4d 的掃描報告")
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

    # 產生建議後重新查詢，避免使用掃描前的 variant 與審核狀態。
    rows = prepare_findings(findings, load_alarms(storage), store)
    if args.write:
        if rows:
            store.save_all(rows)
        print(f"已送出 {len(rows)} 筆 pending findings", file=sys.stderr)
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        print("DRY-RUN 完成：未呼叫 save_all()", file=sys.stderr)
    n_failed = sum(1 for f in findings if f.get("suggestion_failed"))
    if failed_batches:
        print(
            f"⚠ {len(failed_batches)} 個批次失敗，{n_failed} 筆未實際產生建議"
            f"（失敗項目未寫入，詳見上述批次錯誤）",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
