"""devices 表列與 API payload 之間的欄位轉換測試。"""
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import storage


def test_row_to_device_exposes_model_and_device_model_with_same_value():
    result = storage._row_to_device({
        "id": 1,
        "model": "ABC-100",
        "category": "x",
        "line": "L1",
        "department": "mf4d",
    })

    assert result["model"] == "ABC-100"
    assert result["device_model"] == "ABC-100"


def test_row_to_device_passes_through_other_device_fields():
    row = {
        "id": 7,
        "model": "ABC-100",
        "category": "assembly",
        "line": "L2",
        "department": "mf4d",
    }

    result = storage._row_to_device(row)

    assert result["id"] == row["id"]
    assert result["category"] == row["category"]
    assert result["line"] == row["line"]
    assert result["department"] == row["department"]


def test_row_to_device_missing_model_returns_none_for_both_model_keys():
    result = storage._row_to_device({})

    assert result["model"] is None
    assert result["device_model"] is None


def test_row_to_device_none_model_returns_none_for_both_model_keys():
    result = storage._row_to_device({"model": None})

    assert result["model"] is None
    assert result["device_model"] is None


def test_row_to_device_missing_optional_field_returns_none_without_error():
    result = storage._row_to_device({"id": 1, "model": "ABC-100"})

    assert result["department"] is None


def test_device_payload_to_row_uses_and_strips_model():
    result = storage._device_payload_to_row({"model": "  ABC-100  "})

    assert result["model"] == "ABC-100"


def test_device_payload_to_row_uses_and_strips_device_model_when_model_missing():
    result = storage._device_payload_to_row({"device_model": "  ABC-100  "})

    assert result["model"] == "ABC-100"


def test_device_payload_to_row_prefers_model_over_device_model():
    result = storage._device_payload_to_row({
        "model": "preferred",
        "device_model": "ignored",
    })

    assert result["model"] == "preferred"


def test_device_payload_to_row_missing_or_empty_model_values_return_empty_string():
    assert storage._device_payload_to_row({})["model"] == ""
    assert storage._device_payload_to_row({"model": "", "device_model": None})["model"] == ""


def test_device_payload_to_row_strips_category_and_line_or_defaults_to_empty_strings():
    assert storage._device_payload_to_row({
        "category": "  assembly  ",
        "line": " L2 ",
    }) == {"model": "", "category": "assembly", "line": "L2"}
    assert storage._device_payload_to_row({}) == {
        "model": "", "category": "", "line": "",
    }


def test_device_payload_to_row_returns_only_writable_device_keys():
    result = storage._device_payload_to_row({
        "id": 1,
        "department": "mf4d",
        "device_model": "ABC-100",
    })

    assert set(result) == {"model", "category", "line"}
