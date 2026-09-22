"""守住「所有 `_use_supabase()` 定義都具備測試隔離豁免分支」這個結構性
不變量——不依賴人記得每次新增/重寫這個函式時都要加隔離判斷。

背景：`_use_supabase()` 這個「該不該打正式 Supabase」的判斷邏輯，在
專案裡已經被獨立重寫至少三次（`storage.py`/`ai_memory.py`/
`ai_logger.py`），其中一次（`ai_memory.py`/`ai_logger.py` 最初版本）
漏掉隔離豁免分支，造成正式環境 `ai_scans`/`ai_corrections` 表被測試
污染超過一個月才被發現（見 `docs/verification/
ai_memory_pollution_cleanup_2026-09-22.md`）。根因不是「忘記加判斷」，
是「同一個安全概念散落成多份互不同步的實作，沒有機制能自動比對彼此
是否一致」。

刻意不禁止重複定義本身——三份定義判斷不同的隔離環境變數
（`ALARM_DATA_DIR`/`AI_MEM_DIR`/`AI_LOG_DIR`）是既有合理的模組化設計
（alarms 業務資料跟 AI 記憶/日誌是不同性質的本機資料，各自命名），
不是要收斂成同一份程式碼的架構缺陷。這裡守的是「不管未來哪個模組
新增/重寫這個函式，都必須具備隔離豁免分支+正式環境連線檢查」這個
固定形狀，見 `scripts/check_use_supabase_shape.py` 的完整說明。
"""
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
import sys
sys.path.insert(0, str(SCRIPTS.parent))

from scripts.check_use_supabase_shape import find_shape_violations, find_use_supabase_definitions


def test_use_supabase_definitions_exist():
    """至少要能找到目前已知的三處定義——如果掃描不到任何定義，代表
    掃描邏輯本身壞了（例如 backend/ 路徑算錯），不是「這個系統沒有
    _use_supabase()」，避免這個測試在腳本本身故障時假通過。"""
    defs = find_use_supabase_definitions()
    assert len(defs) >= 3, (
        f"預期至少找到 3 處 _use_supabase() 定義（storage.py/ai_memory.py/"
        f"ai_logger.py），實際只找到 {len(defs)} 處，掃描邏輯可能有問題"
    )


def test_all_use_supabase_definitions_have_local_dir_guard_shape():
    """純靜態 AST 檢查（不連線、不碰任何資料庫）：全專案任何一處
    _use_supabase() 定義的原始碼，函式體開頭是否為 `if
    os.environ.get(...): return False` 這個固定形狀。這裡驗證的是
    「程式碼有沒有寫這一段判斷分支」，不是「隔離機制在真實環境裡
    有沒有生效」——後者測不到、也不是這條測試宣稱的範圍。

    全專案任何一處 _use_supabase() 定義，都必須具備這個分支（函式體
    開頭 if 判斷某個環境變數為真就 return False）與正式環境連線檢查
    （最後一句判斷 SUPABASE_URL/SUPABASE_KEY 是否齊全）。

    這條測試失敗代表：有新的一份 _use_supabase() 定義（不管是全新
    模組還是重寫既有模組）缺少隔離豁免分支——這正是 AI 記憶污染事件
    的根因重演。修法：比照 storage.py/ai_memory.py/ai_logger.py 既有
    三份定義的固定形狀，補上 `if os.environ.get("<這個模組的隔離變數>"):
    return False` 這一步，不要假設「這次應該不會忘」。"""
    violations = find_shape_violations()
    assert not violations, (
        "發現 _use_supabase() 定義形狀不一致，可能缺少測試隔離豁免分支：\n"
        + "\n".join(f"  {rel}:{func.lineno} — {reason}" for rel, func, reason in violations)
    )
