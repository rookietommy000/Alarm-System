"""_apply_fix() 的字串組裝邊界測試（全庫語意品質掃描優化，階段 0/1）。

純本地字串處理，不依賴 AI API、不依賴 Supabase——pytest 完全測得到，
釘住兩輪外部審查各自發現的疊字 bug，避免回歸。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "variant"))
from suggest_semantic_fixes import _apply_fix


def test_mixed_cjk_ascii_no_duplication():
    """中文標題夾雜英數字時（區域 1 跑步），用「連續中文字元」定位
    替換範圍會斷開匹配，產生新舊文字重疊（區域 1 運行 1 跑步）。
    第一輪外部審查發現，影響 303 筆中的 107 筆。"""
    result = _apply_fix("Area 1 Running 區域 1 跑步", "區域 1 運行")
    assert result == "Area 1 Running 區域 1 運行"
    assert "跑步" not in result


def test_number_immediately_before_chinese_not_duplicated():
    """緊鄰中文字元前的數字（"3號剔除口..."的 "3"）屬於中文標題，不是
    英文標題的一部分——若不往回吃掉這個數字，會跟 AI 重新生成的完整
    句子（本身已含這個數字）疊加兩次。第二輪外部審查發現的案例：
    PILM003 0218 曾被錯誤處理成 "...Outfeed 33號剔除口回堵..."，
    7 筆受影響。"""
    result = _apply_fix(
        "Final Rejects Build-Back At Outfeed 3號剔除口阻塞 Sensor B126.3偵測",
        "3號剔除口回堵 Sensor B126.3偵測",
    )
    assert result == "Final Rejects Build-Back At Outfeed 3號剔除口回堵 Sensor B126.3偵測"
    assert "33號" not in result


def test_parenthesized_chinese_title_preserves_closing_paren():
    """ACP002 機種「英文(中文)」格式，無空格分隔——右括號要保留，
    不能被吞掉。"""
    result = _apply_fix(
        "BUILD-BACK AT CASEPACKER OUTFEED(在包裝機出料口進行重組)",
        "裝箱機出料口積料",
    )
    assert result == "BUILD-BACK AT CASEPACKER OUTFEED(裝箱機出料口積料)"


def test_no_chinese_in_original_appends_with_space():
    """description 本身沒有中文（極端情況），退回附加模式。"""
    result = _apply_fix("PURE ENGLISH TITLE", "純中文建議")
    assert result == "PURE ENGLISH TITLE 純中文建議"


def test_english_prefix_kept_verbatim():
    """英文標題本身不受影響，只有中文區段被替換——確認替換不會誤動
    到英文標題裡的字元（例如英文標題結尾剛好也有數字但後面接空格，
    不該被誤判成「緊鄰中文」而被吃掉）。"""
    result = _apply_fix("TIPPER JAMMED 1 ALARM 自卸車卡住 1 個警報", "翻轉機構卡住 1 警報")
    assert result.startswith("TIPPER JAMMED 1 ALARM ")
    assert result == "TIPPER JAMMED 1 ALARM 翻轉機構卡住 1 警報"
