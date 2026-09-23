"""共用測試 fixture。

client / anon_client 的隔離依賴 sys.modules.pop + 重新 import
（見 CLAUDE.md「測試通過『重載模組』切換資料目錄」）。這個機制原本
散在多支測試檔各自複製，任一份調整時容易漏改，漏改的症狀是間歇性
失敗、極難定位——收斂到單一來源。

client 建構在 anon_client 之上，不各自重複一次隔離設定：需要
未登入 client 的測試用 anon_client，需要已登入的用 client。
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


@pytest.fixture(autouse=True)
def _ai_isolation_env(tmp_path, monkeypatch):
    """AI_MEM_DIR/AI_LOG_DIR 的安全網：ai_memory.py/ai_logger.py 的
    _mem_dir()/_log_dir() 每次呼叫都即時讀取這兩個環境變數（不是模組
    載入時固化的常數），所以這裡單純 monkeypatch.setenv() 就足夠讓任何
    測試——即使完全沒有主動設定隔離目錄的測試——也不會不小心打到
    .env 載入的正式 Supabase。個別測試若需要用自己的 tmp_path 子目錄
    （例如既有的 mem/pipeline_mem fixture），可以再自行覆寫，不會跟
    這裡衝突。"""
    monkeypatch.setenv("AI_MEM_DIR", str(tmp_path / "ai_mem"))
    monkeypatch.setenv("AI_LOG_DIR", str(tmp_path / "ai_log"))


@pytest.fixture
def anon_client(tmp_path, monkeypatch):
    """未登入的測試 client（本機模式，.env 明文密碼 fallback）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "devices.json").write_text(
        json.dumps([{"id": "M-1", "model": "CNC-A100", "category": "車床"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "alarms.json").write_text(
        json.dumps(
            [
                {
                    "code": "E001",
                    "device_model": "CNC-A100",
                    "severity": "嚴重",
                    "description": "主軸過載",
                    "cause": "負荷過大",
                    "solution": "降低進給",
                    "keywords": ["主軸"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ALARM_DATA_DIR", str(data_dir))

    for mod in ("app", "storage"):
        sys.modules.pop(mod, None)
    from app import create_app

    monkeypatch.setenv("LOGIN_PASSWORD", "test-pw")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def client(anon_client):
    """已建立 admin session 的測試 client（涵蓋一般 + 管理員存取範圍）。"""
    anon_client.post("/admin/login", data={"password": "test-admin-pw"})
    return anon_client
