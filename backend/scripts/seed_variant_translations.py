"""一次性腳本：把 AI 批次翻譯好的 variant 原文→中文對照寫入 Supabase
的 variant_translations 表（migration 008_add_variant_translations.sql）。

⚠️ 前置條件：migration 008 必須已經在正式環境執行過（表已存在），這個
腳本只負責資料寫入，不建表、不執行 DDL——DDL 執行時機須經使用者本人
確認（2026-09-01 顧問裁決），本腳本假設這個前提已經成立，執行前請自行
確認 variant_translations 表存在，不存在會直接收到 404/表不存在的錯誤
並中止，不會靜默略過。

預設 dry-run（只印出將寫入的筆數與前幾筆內容，不打任何 HTTP 請求）。
真正寫入需要明確帶 --execute，避免手滑直接打 production。

用法：
    python backend/scripts/seed_variant_translations.py <翻譯檔路徑>
    python backend/scripts/seed_variant_translations.py <翻譯檔路徑> --execute

翻譯檔格式（沿用 data/variant_translations.json 既有格式）：
    {"<英文原文>": {"zh": "<中文翻譯>", "status": "ai_translated_pending_review"}, ...}
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def _load_translations(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _to_rows(translations: dict) -> list:
    return [
        {
            "original_text": original_text,
            "translated_text": entry["zh"],
            "review_status": entry.get("status", "ai_translated_pending_review"),
        }
        for original_text, entry in translations.items()
    ]


def _post_rows(base: str, key: str, rows: list) -> None:
    """單次 POST 全部寫入。103 筆遠低於 Supabase 單次請求上限，不需要
    分批；on_conflict=original_text 搭配 Prefer: resolution=merge-duplicates
    讓腳本可重複執行不會因為唯一約束衝突而失敗（例如翻譯檔更新後重跑）。"""
    url = f"{base}/rest/v1/variant_translations?on_conflict=original_text"
    data = json.dumps(rows).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        r.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("translations_path", type=Path, help="翻譯 JSON 檔路徑")
    parser.add_argument("--execute", action="store_true",
                         help="真正寫入 Supabase；不帶此參數只做 dry-run")
    args = parser.parse_args()

    translations = _load_translations(args.translations_path)
    rows = _to_rows(translations)

    print(f"讀到 {len(rows)} 筆翻譯，來源：{args.translations_path}")
    for row in rows[:3]:
        print(f"  範例：{row['original_text'][:50]!r} -> {row['translated_text'][:30]!r} ({row['review_status']})")

    if not args.execute:
        print("\n[dry-run] 未帶 --execute，不會寫入 Supabase。確認上面內容無誤後加上 --execute 重新執行。")
        return 0

    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")
    if not base or not key:
        print("錯誤：未設定 SUPABASE_URL / SUPABASE_KEY 環境變數，中止。", file=sys.stderr)
        return 1

    print(f"\n即將寫入 {len(rows)} 筆到 {base} 的 variant_translations 表...")
    try:
        _post_rows(base, key, rows)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"寫入失敗：HTTP {e.code} — {body}", file=sys.stderr)
        print("常見原因：variant_translations 表尚未建立（migration 008 未執行）。", file=sys.stderr)
        return 1

    print("寫入完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
