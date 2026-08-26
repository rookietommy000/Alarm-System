"""load_valid_models() 的 fail-closed + TTL 快取行為（外部審查修法：
讀取失敗不再回空 list，避免白名單消失讓所有辨識結果被判定為不在
白名單而全數拒絕，見 backend/ai/ai_rules.py 的 ValidModelsUnavailable）。
"""
import json

import pytest

from ai import ai_rules
from ai.ai_rules import ValidModelsUnavailable, load_valid_models


@pytest.fixture(autouse=True)
def _reset_cache():
    """每個測試前後清掉模組級快取，避免測試之間互相污染。"""
    ai_rules._valid_models_cache["models"] = None
    ai_rules._valid_models_cache["expires_at"] = 0.0
    yield
    ai_rules._valid_models_cache["models"] = None
    ai_rules._valid_models_cache["expires_at"] = 0.0


def _write_devices(data_dir, devices):
    (data_dir / "devices.json").write_text(json.dumps(devices, ensure_ascii=False), encoding="utf-8")


def test_loads_active_models_only(tmp_path):
    _write_devices(tmp_path, [
        {"model": "CNC-A100", "active": True},
        {"model": "CNC-B200", "active": False},
        {"model": "CNC-C300"},  # 無 active 欄位視為有效
    ])
    result = load_valid_models(str(tmp_path))
    assert result == ["CNC-A100", "CNC-C300"]


def test_missing_file_raises_when_cache_empty(tmp_path):
    """沒有裝過快取、檔案也不存在時，必須 fail-closed，不能回空 list。"""
    with pytest.raises(ValidModelsUnavailable):
        load_valid_models(str(tmp_path / "does-not-exist"))


def test_malformed_json_raises_when_cache_empty(tmp_path):
    (tmp_path / "devices.json").write_text("not valid json{{{", encoding="utf-8")
    with pytest.raises(ValidModelsUnavailable):
        load_valid_models(str(tmp_path))


def test_cache_hits_avoid_filesystem_read(tmp_path, monkeypatch):
    _write_devices(tmp_path, [{"model": "CNC-A100", "active": True}])
    first = load_valid_models(str(tmp_path))
    assert first == ["CNC-A100"]

    # 快取命中時，即使檔案被刪除也不該觸發任何讀取或報錯
    (tmp_path / "devices.json").unlink()
    second = load_valid_models(str(tmp_path))
    assert second == ["CNC-A100"]


def test_stale_cache_used_as_fallback_on_transient_failure(tmp_path):
    """已有成功讀過的快取時，之後讀取失敗要降級回傳舊值，不是直接掛掉——
    一次瞬斷不該讓正在使用中的辨識功能整個失效。"""
    _write_devices(tmp_path, [{"model": "CNC-A100", "active": True}])
    first = load_valid_models(str(tmp_path))
    assert first == ["CNC-A100"]

    # 快取還沒過期，即使檔案壞掉也該直接命中快取，不會走到讀取失敗分支
    ai_rules._valid_models_cache["expires_at"] = 0.0  # 強制視為過期，測試 fallback 分支
    (tmp_path / "devices.json").write_text("not valid json{{{", encoding="utf-8")
    fallback = load_valid_models(str(tmp_path))
    assert fallback == ["CNC-A100"]


def test_recovers_after_file_fixed(tmp_path):
    """快取過期後檔案已修復，應該讀到新內容，不是永遠卡在舊快取或例外。"""
    _write_devices(tmp_path, [{"model": "CNC-A100", "active": True}])
    load_valid_models(str(tmp_path))

    ai_rules._valid_models_cache["expires_at"] = 0.0
    _write_devices(tmp_path, [{"model": "CNC-Z900", "active": True}])
    result = load_valid_models(str(tmp_path))
    assert result == ["CNC-Z900"]
