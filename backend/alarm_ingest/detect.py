"""原廠格式（路線 A）的欄位偵測。純函式，分三層：讀檔成 grid、偵測欄位、
依人工確認過的欄位對應轉成 rows——三層之間刻意不耦合，因為「偵測」與
「轉換」中間隔著一個必要的人工確認關卡（見批次匯入 UI 規劃第 3 節）：
inspect 端點只能做到偵測，不能在人還沒確認欄位對應之前就先把 code 切好，
否則「人工確認」這一步會變成看既成事實而非真正做決定。

跟 Variant/parse_alarms.py（現 tools/variant/parse_alarms.py）共用同一份
_detect_columns() 邏輯，而非各自維護一份複本——這份判斷比 normalize_variant
複雜得多（表頭比對＋fallback＋門檻＋兩項誤報防護），分岔的代價是同一份
原廠檔案在 CLI 與後台跑出不同的欄位對應，且契約測試很難完整覆蓋各種
grid 形狀去守住一致性，比 normalize_variant 的分岔風險高很多。CLI 的
read_tabular() 改為呼叫這裡的 read_grid()/detect_columns()/grid_to_rows()
組合，行為不變（CLI 自動採用 detect_columns() 的建議，等同人工確認
這一步在 CLI 上是自動通過——這符合 CLI 「你在跑、你知道自己在做什麼」
的定位）。
"""
from __future__ import annotations

import csv
import pathlib
import re
import sys
from collections import Counter

from .quality import clean, split_code as _split_code

# 代碼在文字開頭：「31033 Operation active...」或「0024 - Forming Panel...」
CODE_PREFIX_RE = re.compile(r"^\s*(\d{3,6})\s*[-–—:：]?\s*(.*)$", re.S)


def split_code(text: str) -> tuple:
    """把「31033 Operation active weigh-in filling 1」拆成 (code, variant)。
    quality.split_code() 的特化版本，固定套用本模組的 CODE_PREFIX_RE
    （跟正則表達式耦合的部分留在這裡，quality.py 保持通用）。"""
    return _split_code(text, CODE_PREFIX_RE)


def _cell_to_str(v) -> str:
    """openpyxl 回傳的儲存格型別取決於儲存格格式，不保證是字串——
    code 這類欄位使用者可能存成數字（例如 24 而非 "24"），直接 str()
    對整數值沒問題，但對「整數值卻是 float 型別」的儲存格（公式結果、
    複製貼上運算、pandas 匯出等常見來源）會產生 "24.0" 這種帶小數尾碼
    的錯誤字串——這不是崩潰，是安靜產生錯的代碼，比 AttributeError
    更難發現。float 若無小數部分，先轉 int 再轉字串去掉尾碼；有小數
    部分（例如儲存格真的存了 31033.5）才保留原樣，讓後續驗證去處理
    這種本來就不像有效代碼格式的輸入。

    inspect 端點取樣本內容時也需要同樣的保護（避免 openpyxl 數字型別
    造成 JSON 序列化問題），跟 load_excel() 共用這支而非各自實作。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def read_grid(path: pathlib.Path, sheet: str = None) -> list:
    """讀 .xlsx/.xlsm/.csv 成 [(分頁名, grid), ...]。純讀檔，不做欄位判斷、
    不做 code 切分。CSV 沒有分頁概念，統一包成一個 ("csv", grid) 維持
    介面一致。

    sheet 指定時只回傳該分頁（找不到則報錯）——多分頁檔案預設會全部
    讀入，曾經因此把不相關的分頁一起解析進來而不自知（ACM002警報.xlsx
    有兩個分頁都含警報格式的資料，但資料庫裡只匯入過其中一個），所以
    需要能只鎖定單一分頁重現特定來源。"""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            grids = [("csv", [list(r) for r in csv.reader(f)])]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        grids = [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)])
                 for ws in wb.worksheets]

    if sheet is None:
        return grids
    matched = [g for g in grids if g[0] == sheet]
    if not matched:
        available = ", ".join(repr(n) for n, _ in grids)
        raise ValueError(f"找不到分頁 {sheet!r}，此檔案的分頁：{available}")
    return matched


def detect_columns(grid: list) -> tuple:
    """回傳 (描述欄, 原因欄, 處置欄, 資料起始列)。認不出來回 None。

    先用表頭關鍵字找，找不到再退回「掃前 20 列，哪一欄最常以代碼開頭」。
    後者是為了沒有表頭或表頭被合併儲存格吃掉的情況。

    兩個實測踩到的坑：
      * FILL203 的 alarm list 分頁，Cause 欄完全沒有表頭（儲存格是 None），
        只靠關鍵字比對會整欄漏掉 → 表頭找不到 cause 時，退回用 desc+1。
      * Problem 分頁是問答清單不是警報資料，但內文提到代碼，會被 fallback
        誤判 → 要求至少 MIN_CODE_ROWS 列以代碼開頭才採用該分頁。
    """
    HEAD = {"desc": ("description", "message", "alarm", "代碼", "訊息", "描述"),
            "cause": ("cause", "reason", "原因"),
            "action": ("action", "solution", "remedy", "comment", "處置", "對策", "備註")}
    MIN_CODE_ROWS = 5

    def code_rows(desc_i: int, start: int) -> int:
        return sum(1 for r in grid[start:]
                   if desc_i < len(r) and r[desc_i] and CODE_PREFIX_RE.match(str(r[desc_i])))

    for i, row in enumerate(grid[:5]):
        cells = [str(c).strip().lower() if c else "" for c in row]
        hit = {}
        for key, words in HEAD.items():
            for j, c in enumerate(cells):
                if c and any(w in c for w in words):
                    hit.setdefault(key, j)
        if "desc" not in hit:
            continue
        desc_i, start = hit["desc"], i + 1
        if code_rows(desc_i, start) < MIN_CODE_ROWS:
            continue
        cause_i, action_i = hit.get("cause"), hit.get("action")
        if cause_i is None:
            cand = desc_i + 1
            if cand != action_i and any(cand < len(r) and clean(r[cand]) for r in grid[start:]):
                cause_i = cand
        return desc_i, cause_i, action_i, start

    counts: Counter = Counter()
    for row in grid[:20]:
        for j, c in enumerate(row):
            if c and CODE_PREFIX_RE.match(str(c)):
                counts[j] += 1
    if not counts:
        return None
    desc_i = counts.most_common(1)[0][0]
    if code_rows(desc_i, 0) < MIN_CODE_ROWS:
        return None  # 零星提到代碼的分頁（例如問答清單），不是警報資料
    return desc_i, desc_i + 1, desc_i + 2, 0


def grid_to_rows(grid: list, mapping: tuple, source: str) -> list:
    """依 detect_columns() 的結果（或人工確認/修改過的對應）把 grid
    轉成 rows。code 切分（split_code()）在這裡才發生——這一步之前
    的所有資料都還是原始值，未被系統擅自解讀。"""
    desc_i, cause_i, action_i, start = mapping
    rows = []
    for r in grid[start:]:
        if desc_i >= len(r):
            continue
        code, variant = split_code(str(r[desc_i] or ""))
        if code is None:
            continue
        rows.append({
            "code": code,
            "variant": variant,
            "cause": clean(r[cause_i]) if cause_i is not None and cause_i < len(r) else "",
            "action": clean(r[action_i]) if action_i is not None and action_i < len(r) else "",
            "_source": source,
        })
    return rows


def read_tabular(path: pathlib.Path, sheet: str = None) -> list:
    """讀 .xlsx/.xlsm/.csv，自動掃描分頁（或 sheet 指定的單一分頁）並
    套用 detect_columns() 的建議。CLI 用這支——等同「人工確認欄位對應」
    這一步在 CLI 上是自動通過（CLI 使用者本來就知道自己在做什麼）。
    後台 inspect 端點不走這支，而是分開呼叫 read_grid()/detect_columns()，
    把建議交給人確認後才呼叫 grid_to_rows()。"""
    rows: list = []
    for name, grid in read_grid(path, sheet=sheet):
        cols = detect_columns(grid)
        if cols is None:
            print(f"  [略過] 分頁 {name!r}：找不到含警報代碼的欄位", file=sys.stderr)
            continue
        n_before = len(rows)
        rows.extend(grid_to_rows(grid, cols, source=f"{path.name}#{name}"))
        print(f"  分頁 {name!r}：{len(rows) - n_before} 列", file=sys.stderr)
    return rows


def list_sheets(path: pathlib.Path) -> list:
    """回傳 [(分頁名, 是否偵測到警報欄位), ...]，供 --sheet-list 與
    inspect 端點列出分頁供人選擇，不解析內容。"""
    return [(name, detect_columns(grid) is not None) for name, grid in read_grid(path)]
