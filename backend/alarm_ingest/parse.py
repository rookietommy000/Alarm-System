"""解析來源檔（CSV/JSON/Excel）成標準 alarm dict 列表。純解析，不碰
資料庫、不驗證資料庫狀態（機種是否存在等驗證在 validate.py）——CLI
（import_alarms.py）與後台批次匯入 API 共用同一份解析邏輯，避免兩邊
各自實作、行為漂移。

Excel 走固定範本（必要表頭比對），這裡的 load_excel()/load_csv() 不做
智慧欄位偵測——這條路徑（標準格式匯入）鎖定的情境是部門管理員補充/
更新自己部門的資料，偵測失敗時他沒有能力處理（不會打開檔案看結構、
不能改程式碼），唯一能做的是找技術端——而偵測成功但抓錯欄位是比失敗
更糟的靜默失敗（FILL203 的 Cause 欄一度被抓到只有 33% 覆蓋率，是比對
數字才發現的，管理員不會發現）。固定範本讓格式錯誤變成「缺少欄位：
cause」這種看得懂、改得動的明確訊息，見批次匯入 UI 規劃第 5 節專家
分析。

智慧欄位偵測（原廠格式匯入，路線 A）在 detect.py，走 inspect 端點：
上傳檔案 → 系統建議欄位對應 → 人工確認/修改 → 才轉成 rows，跟這裡的
固定範本是兩條並行路徑，適用不同情境（見批次匯入 UI 規劃）。
"""
import csv
import json
from pathlib import Path

from .detect import _cell_to_str  # noqa: F401 (re-export，見下方說明)

# 與 app.py 的 ALARM_FIELDS 定義一致（多加 variant）。
ALARM_FIELDS = ["code", "device_model", "severity", "description", "cause", "solution",
                "local_solution", "keywords", "sol_steps", "variant"]

# 固定範本的必要表頭（批次匯入 UI 規劃第 5 節專家分析）。code/
# device_model 同時也是 row_to_alarm() 的必填檢查（缺值時報「code 為
# 必填」），但這裡額外要求「表頭本身存在」，是為了在整欄因表頭打錯字
# /漏列而消失時明確報錯——DictReader 對缺欄位預設讀出 None，被當成
# 空字串處理不會報錯，例如 cause 欄表頭打成 "cuase" 會讓整欄悄悄消失、
# 只能靠完整度統計異常低才被發現。severity/keywords/sol_steps 不列
# 為必要：本來就允許留空，缺表頭與有表頭留空是同一種情況，不需要
# 額外攔。
REQUIRED_HEADERS = {"code", "device_model", "variant", "description", "cause", "solution", "local_solution"}

# 批次匯入是 upsert 語意，不是整列取代——這三個欄位在來源缺席時不該
# 被送進資料庫覆蓋既有值。剛好是 ALARM_FIELDS 扣掉 REQUIRED_HEADERS
# 的補集：REQUIRED_HEADERS 之內的欄位一定有表頭，使用者留空代表刻意
# 清空，是正當意圖；只有「表頭根本不存在」的欄位才需要保護，這條界線
# 不用另外定義規則，直接對應既有的必要表頭清單。
#
# 實測驗證過這個修法的前提（PostgREST upsert 對缺席欄位的行為）：
# 對 zztest 部門送一筆含 keywords 的資料，再送一次不含 keywords 的
# payload（Prefer: resolution=merge-duplicates），查回來 keywords 保留
# 原值——INSERT ... ON CONFLICT DO UPDATE SET col = EXCLUDED.col 只更新
# payload 裡列出的欄位，不送就不動。這是這個修法成立的前提，不是假設。
OPTIONAL_FIELDS = {"severity", "keywords", "sol_steps"}

SEVERITIES = {"嚴重", "警告", "資訊"}


def normalize_variant(s: str) -> str:
    """variant 進主鍵，任何字元差異都是不同的警報。與 app.py 的
    normalize_variant()、Variant/parse_alarms.py 的同名函式是同一份邏輯
    （app.py 複製一份而非 import 這裡——那是 Flask 服務，這裡是批次匯入
    共用模組，兩者部署邊界不同；parse_alarms.py 是離線 CLI 不進 backend
    部署，同樣是複製）。三邊修改要同步。

    做不影響顯示的正規化，避免前端/來源檔複製貼上帶入的破折號/空白變體
    讓 variant 打不到既有列。刻意不做大小寫轉換——原廠標題大小寫穩定，
    轉了反而讓顯示變醜。"""
    s = " ".join((s or "").split())
    s = s.replace("–", "-").replace("—", "-")   # en/em dash → hyphen
    s = s.replace("（", "(").replace("）", ")")   # 全形括號
    s = s.replace("／", "/")
    return s.strip()


def row_to_alarm(row: dict) -> dict:
    keywords_raw = (row.get("keywords") or "").strip()
    keywords = [s.strip() for s in keywords_raw.split(";") if s.strip()] if keywords_raw else []

    sol_steps_raw = (row.get("sol_steps") or "").strip()
    sol_steps = {}
    if sol_steps_raw:
        try:
            sol_steps = json.loads(sol_steps_raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"sol_steps 不是合法 JSON：{e}")

    code = (row.get("code") or "").strip()
    device_model = (row.get("device_model") or "").strip()
    severity = (row.get("severity") or "").strip()

    if not code:
        raise ValueError("code 為必填")
    if not device_model:
        raise ValueError("device_model 為必填")
    if severity and severity not in SEVERITIES:
        raise ValueError(f"severity 必須為 {sorted(SEVERITIES)} 之一，收到：{severity!r}")

    out = {
        "code": code,
        "device_model": device_model,
        "variant": normalize_variant(row.get("variant") or ""),
        "severity": severity,
        "description": (row.get("description") or "").strip(),
        "cause": (row.get("cause") or "").strip(),
        "solution": (row.get("solution") or "").strip(),
        "local_solution": (row.get("local_solution") or "").strip(),
        "keywords": keywords,
        "sol_steps": sol_steps,
    }
    # 記錄來源實際提供了哪些欄位，供 commit_rows() 決定寫入 payload 要不要
    # 包含 OPTIONAL_FIELDS——寫入前會被剝掉（見 commit.py），不影響 out
    # 本身作為完整 dict 給 dedupe_check()/completeness_report() 等下游使用。
    # list 而非 set：set 不可 JSON 序列化，若 _to_payload() 的過濾
    # 疏漏（或未來有呼叫端忘記剝除 "_present" 就直接送進 JsonStore.
    # upsert_one() 的 json.dump()），set 會讓錯誤變成不明顯的
    # TypeError；list 至少能被序列化，讓問題在別處（PostgREST 400 或
    # 資料裡多一個不該有的欄位）更快被發現，而不是死在寫檔這一步。
    out["_present"] = sorted(k for k in row if k in ALARM_FIELDS)
    return out


def _canonicalize_header(h) -> str:
    """表頭比對容錯：strip + lower。使用者從 Excel/原廠文件複製表頭
    很容易帶到前後空白或大小寫不一致（例如 "Cause "、"CODE"），這些
    差異不影響「這一欄是什麼」的判斷，不該因此被判定成缺欄位。"""
    return (str(h) if h is not None else "").strip().lower()


def _map_headers(headers) -> dict:
    """回傳 {正規化後的欄名: 原始索引}。多個原始表頭正規化後撞名時
    （例如同時有 "Code" 和 "code "），後者覆蓋前者——這種情況本身就是
    來源檔案有問題，不特別偵測，缺欄位或值錯誤會在後續驗證自然浮現。
    """
    mapping = {}
    for i, h in enumerate(headers):
        canon = _canonicalize_header(h)
        if canon:
            mapping[canon] = i
    return mapping


def _check_required_headers(headers) -> dict:
    """headers 本身缺表頭時明確報錯，不讓整欄悄悄消失（見模組開頭說明）。
    只用在人工填寫的來源（CSV/Excel）——JSON 是 parse_alarms.py 產出的
    機器格式，已經過完整度攔截等把關，不需要重複這道檢查。

    回傳 _map_headers() 的結果，供呼叫端用正規化後的欄名重建每一列，
    不然容錯比對只在「有沒有缺欄位」這一步生效，後面 row_to_alarm()
    用 row.get("code") 抓值時還是會因為原始表頭是 "Code" 而抓不到。
    """
    mapping = _map_headers(headers)
    missing = REQUIRED_HEADERS - set(mapping)
    if missing:
        detected = ", ".join(h if h else "(空白欄)" for h in headers) or "(無表頭)"
        raise ValueError(
            f"缺少欄位：{', '.join(sorted(missing))}（請對照範本檢查表頭拼字）\n"
            f"偵測到的表頭：{detected}"
        )
    return mapping


def load_csv(path: Path) -> tuple:
    """回傳 (rows, row_errors)。row_errors 是單一列格式異常的清單
    （例如 code 缺值），不再讓整份來源因為其中一列有問題就整批失敗——
    這些列改成收集起來交給呼叫端決定怎麼處理（批次匯入走
    pending_alarm_imports 待審機制，見 app.py bulk_import_commit()）。
    表頭本身缺欄位仍是整批 raise（_check_required_headers()），那不是
    「這一列有問題」而是「整份來源沒辦法解析」，性質不同。"""
    rows = []
    row_errors = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        raw_headers = reader.fieldnames or []
        mapping = _check_required_headers(raw_headers)
        for i, raw_row in enumerate(reader, start=2):  # 第 1 行是表頭
            raw = {canon: raw_row.get(raw_headers[idx]) for canon, idx in mapping.items()}
            try:
                rows.append(row_to_alarm(raw))
            except ValueError as e:
                row_errors.append({"row": i, "raw": raw, "reason": str(e)})
    return rows, row_errors


def load_excel(path: Path) -> tuple:
    """讀固定範本 .xlsx/.xlsm，只認第一個工作表、第一列為表頭（不做
    分頁掃描或表頭關鍵字比對——那是 Variant/parse_alarms.py 的智慧
    偵測職責，見模組開頭說明）。

    回傳 (rows, row_errors)，同 load_csv() 的說明：單一列格式異常收集
    起來交給呼叫端處理，不整批失敗；表頭/空檔案仍是整批 raise。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        raise ValueError("檔案是空的，找不到表頭列")
    headers = [str(h).strip() if h is not None else "" for h in header_row]
    mapping = _check_required_headers(headers)

    rows = []
    row_errors = []
    for i, raw_row in enumerate(rows_iter, start=2):  # 第 1 列是表頭
        if all(v is None or str(v).strip() == "" for v in raw_row):
            continue  # 跳過完全空白的列（Excel 常見的尾端空列）
        raw = {canon: _cell_to_str(raw_row[idx] if idx < len(raw_row) else None)
               for canon, idx in mapping.items()}
        try:
            rows.append(row_to_alarm(raw))
        except ValueError as e:
            row_errors.append({"row": i, "raw": raw, "reason": str(e)})
    return rows, row_errors


# _cell_to_str() 本體在 detect.py（inspect 端點取樣本內容也需要同樣的
# 型別轉換保護），上方 import 已 re-export，這裡不重複定義。


def load_json(path: Path) -> tuple:
    """讀 parse_alarms.py 產出的標準 JSON（見 Variant/parse_alarms.py
    to_output()）。跟 CSV 走同一個 row_to_alarm() 正規化，兩種來源
    最終格式一致，其餘邏輯（去重檢查、機種驗證、完整度攔截）不需要
    分別處理來源型態。

    回傳 (rows, row_errors)，同 load_csv() 的說明。"""
    with path.open(encoding="utf-8") as f:
        raw_rows = json.load(f)
    if not isinstance(raw_rows, list):
        raise ValueError("JSON 檔內容必須是陣列（parse_alarms.py 的標準輸出格式）")
    rows = []
    row_errors = []
    for i, raw in enumerate(raw_rows, start=1):
        try:
            rows.append(row_to_alarm(raw))
        except ValueError as e:
            row_errors.append({"row": i, "raw": raw, "reason": str(e)})
    return rows, row_errors


def load_file(path: Path) -> tuple:
    """回傳 (rows, row_errors)，同三個 load_*() 的介面。"""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_csv(path)
    if suffix == ".json":
        return load_json(path)
    if suffix in (".xlsx", ".xlsm"):
        return load_excel(path)
    raise ValueError(f"不支援的檔案格式：{suffix}（支援 .csv/.json/.xlsx/.xlsm）")
