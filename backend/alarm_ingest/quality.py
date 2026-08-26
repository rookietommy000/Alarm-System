"""與 Variant/parse_alarms.py 共用的純邏輯（clean/split_code/dedup/
decide_variant_mode）。這幾支函式跟欄位智慧偵測（_detect_columns/
read_tabular）無關、不耦合任何來源格式細節，是真正的通用規則（見
批次匯入 UI 規劃第 5 節專家分析：沒有欄位偵測那樣的來源格式判斷，
純粹是「拿到已切出的 code/variant/rows 之後怎麼處理」）。

Variant/parse_alarms.py 不進 git 版控，這裡是複製（非 import）——
跨越版控邊界的 import 很脆弱：alarm_ingest 改了 CLI 端不會有任何
提示、部署時 backend/ 不能依賴 Variant/ 存在。用
test_alarm_ingest.py 的 NORMALIZE_CASES/DECIDE_VARIANT_CASES 契約
測試釘住兩邊必須同步，而不是用 import 綁死。
"""
import re
from collections import defaultdict

NA_VALUES = {"#N/A", "N/A", "NA", "-", "—", "", "None", "nan"}


def clean(v) -> str:
    """壓掉換行與連續空白，#N/A 類值視為空。"""
    if v is None:
        return ""
    s = " ".join(str(v).split())
    return "" if s in NA_VALUES else s


def decide_variant_mode(rows: list, mode: str) -> tuple:
    """回傳 (是否啟用 variant, 判定理由)。mode 為 auto 時依代碼是否唯一判斷。

    純函式，不做 I/O——CLI 與後台批次匯入 API 共用，各自決定要不要把
    理由印出來（CLI 印 stderr；後台把理由放進 preview 回應給管理員看，
    見批次匯入 UI 規劃：後端固定傳 auto，管理員看不懂 variant-mode 是
    什麼，但要在預覽裡看得到系統做了什麼判定——無感但可見）。

    auto 有誤判風險：來源若只是全部警報的子集，可能剛好每個代碼只
    出現一次而被判成不啟用，然後匯入時跟既有的多變體資料撞在一起
    （FILL203 的 Problem 分頁就寫明「部分 alarm 未列於清單」）。CLI 因此
    把 --variant-mode 設為必填，auto 要明確指定才啟用；後台則搭配
    check_variant_consistency() 做強制擋錯（見 validate.py），不能只靠
    這裡的判定就直接寫入。
    """
    if mode == "always":
        return True, "手動指定啟用 variant"
    if mode == "never":
        return False, "手動指定不啟用 variant"
    codes = [r["code"] for r in rows]
    unique = len(codes) == len(set(codes))
    if unique:
        reason = f"本來源 {len(set(codes))} 個代碼全部唯一，variant 設為空字串"
    else:
        reason = f"偵測到代碼重複（{len(codes)} 列 / {len(set(codes))} 個代碼），啟用 variant"
    return not unique, reason


def split_code(text: str, code_prefix_re) -> tuple:
    """把「31033 Operation active weigh-in filling 1」拆成 (code, variant)。

    code_prefix_re 由呼叫端傳入（Variant/parse_alarms.py 的
    CODE_PREFIX_RE）——這支函式本身不含任何來源格式判斷，正則表達式
    才是跟「代碼長什麼樣」耦合的部分，留在呼叫端維護。
    """
    m = code_prefix_re.match(text or "")
    if not m:
        return None, clean(text)
    return m.group(1), clean(m.group(2))


def dedup(rows: list) -> tuple:
    """依 (code, variant) 去重，同鍵取內容最完整者。回傳 (結果, 衝突組數)。"""
    best: dict = {}
    seen: dict = defaultdict(set)
    for r in rows:
        k = (r["code"], r["variant"])
        seen[k].add((r.get("cause", ""), r.get("action", "")))
        score = len(r.get("cause", "")) + len(r.get("action", ""))
        if k not in best or score > len(best[k].get("cause", "")) + len(best[k].get("action", "")):
            best[k] = r
    conflicts = sum(1 for v in seen.values() if len(v) > 1)
    return sorted(best.values(), key=lambda x: (x["code"], x["variant"])), conflicts


def apply_semantic_fix(desc: str, suggested_zh: str) -> str:
    """把 description 裡「第一個中文字元開始到結尾」的整段換成建議修正
    文字，只保留最前面的英文標題（跟結尾的右括號，若原文有的話）。

    語意掃描離線工具（tools/variant/suggest_semantic_fixes.py）與後台
    語意審核端點（app.py 的 update_semantic_review）原本各自維護一份
    完全相同的邏輯，兩處分岔的代價高——任何一邊修 bug 沒同步到另一邊，
    現場審核跟離線建議會產生不一致結果，所以收斂成這一份共用實作。

    第一版寫法（已淘汰）用「連續中文字元」的正則抓中文區間，在中文
    標題內夾雜英數字時會斷開匹配（例如 "區域 1 跑步" 的 "1" 打斷了
    連續中文），只換到最後一小段連續中文，導致新舊文字疊加出現重複
    片段（實測案例：TFM001 0033 "Area 1 Running 區域 1 跑步" 曾被錯誤
    處理成 "區域 1 運行 1 跑步"）。中文標題本身合理含有數字/字母
    （NEST、B103.8.1 這類元件編號或英文縮寫），不能用「是否為中文
    字元」判斷標題邊界，只能認定「第一個中文字元出現後，到（可能的）
    結尾括號之前，全部都是中文標題」。

    第二版寫法（本版之前）只往前找到第一個中文字元，沒有考慮「緊鄰
    在中文字元前的數字」也可能屬於中文標題——這批資料常見「N 號」
    「N 站」這種格式（"Outfeed 3號剔除口阻塞"），數字後面緊接中文字
    沒有空格分隔。原本的切法會把這個數字留在英文標題那半，但 AI 給的
    suggested_zh 是重新生成的完整句子、本來就含這個數字（"3號剔除口
    回堵"），兩邊拼接後數字被疊加兩次（外部審查發現的實測案例：
    PILM003 0218 變成 "...Outfeed 33號剔除口回堵..."，7 筆受影響）。
    修法：找到第一個中文字元後，往回吃掉緊鄰的連續數字，讓那段數字
    併入要被整段替換掉的區間，不留在英文標題裡。

    另外 ACP002 機種有「英文(中文)」這種括號包住中文、無空格分隔的
    格式（例如 "BUILD-BACK AT CASEPACKER OUTFEED(在包裝機出料口進行
    重組)"），若不特別處理右括號會被吞掉，寫回去的 description 括號
    不成對。"""
    m = re.search(r'[一-鿿]', desc)
    if not m:
        return f"{desc.strip()} {suggested_zh}".strip()
    cut = m.start()
    while cut > 0 and desc[cut - 1].isdigit():
        cut -= 1
    en_title = desc[:cut]
    trailing = ")" if desc.rstrip().endswith(")") else ""
    return f"{en_title}{suggested_zh}{trailing}"
