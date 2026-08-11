#!/usr/bin/env python3
"""
translate.py — 為缺少中文的警報 description / cause 批次加入繁體中文翻譯
使用 Google 翻譯（免費，不需 API key）
輸出格式: "MAIN POWER CUT 主電源切斷"（英文 空格 中文）

用法: python3 backend/translate.py
"""

import json
import re
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")  # 壓制 LibreSSL warning

from deep_translator import GoogleTranslator

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

CJK = re.compile(r'[一-鿿㐀-䶿]')
SKIP_FILES = {"alarms.json", "devices.json"}

translator = GoogleTranslator(source="en", target="zh-TW")


def needs_translation(text: str) -> bool:
    return bool(text and text.strip() and not CJK.search(text))


def safe_translate(text: str) -> str:
    """翻譯單一文字，失敗回傳空字串"""
    text = text.strip()
    if not text:
        return ""
    # Google 翻譯單次上限約 5000 字，超過則截斷
    if len(text) > 4500:
        text = text[:4500]
    for attempt in range(3):
        try:
            result = translator.translate(text)
            return result or ""
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                print(f"    翻譯失敗: {e}")
                return ""
    return ""


def process_file(json_path: Path) -> int:
    alarms = json.loads(json_path.read_text(encoding="utf-8"))
    model_name = json_path.stem

    needs = [(i, a) for i, a in enumerate(alarms)
             if any(needs_translation(a.get(f, ""))
                    for f in ("description", "cause", "solution"))]

    if not needs:
        print(f"  {model_name}: 無需翻譯，略過")
        return 0

    print(f"  {model_name}: 翻譯 {len(needs)} 筆…", flush=True)
    updated = 0

    for count, (i, alarm) in enumerate(needs, 1):
        if count % 20 == 0:
            print(f"    進度 {count}/{len(needs)}…", flush=True)

        for field in ("description", "cause", "solution"):
            if needs_translation(alarm.get(field, "")):
                cn = safe_translate(alarm[field])
                if cn:
                    alarm[field] = f"{alarm[field].strip()} {cn}"
                    updated += 1

        # 避免請求過快被 block
        time.sleep(0.3)

    json_path.write_text(json.dumps(alarms, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    ✓ 完成，更新 {updated} 個欄位")
    return updated


def main():
    total = 0
    for path in sorted(DATA.glob("*.json")):
        if path.name in SKIP_FILES:
            continue
        print(f"處理 {path.name}…")
        total += process_file(path)

    print(f"\n翻譯完成，共更新 {total} 個欄位")


if __name__ == "__main__":
    main()
