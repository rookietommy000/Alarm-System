"""VariantTranslationStore 的本機（JsonStore）路徑測試。

Supabase 路徑（_load_supabase）需要真實網路請求，不在 pytest 環境測試
範圍內（同 CLAUDE.md「測試的能力邊界」：pytest 只測 JsonStore 單租戶
路徑，跨環境行為要靠 sentinel_pack 對真實 Supabase 驗證）。這裡只驗證
本機 JSON 檔案讀取的 fail-open 行為與欄位映射是否正確。
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import storage as storage_mod


def test_load_all_missing_file_returns_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setenv("ALARM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: False)
    store = storage_mod.VariantTranslationStore()
    assert store.load_all() == {}


def test_load_all_reads_local_json_with_expected_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("ALARM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage_mod, "_use_supabase", lambda: False)
    path = tmp_path / "variant_translations.json"
    path.write_text(
        json.dumps({"Operation active fault handlling outfeed 1": {
            "zh": "運轉中故障處理 出料1", "status": "ai_translated_pending_review"}}),
        encoding="utf-8",
    )
    store = storage_mod.VariantTranslationStore()
    result = store.load_all()
    assert result == {
        "Operation active fault handlling outfeed 1": {
            "zh": "運轉中故障處理 出料1", "status": "ai_translated_pending_review"
        }
    }
