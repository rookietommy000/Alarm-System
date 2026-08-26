"""AI 切分：把同一儲存格內混雜的原因/處置文字拆成 cause/solution 兩欄
（批次匯入 UI 規劃第 6 階段，split 端點用）。

獨立的呼叫路徑，不沿用 backend/ai/ai_pipeline.py 既有的分析管線——那支
處理的是「拍照辨識警報畫面」（不同輸入、不同 prompt、不同觸發時機），
審核 local_solution 品質那條路線先前也是同樣的判斷（見批次匯入 UI 規劃
第一輪可行性檢查記錄）。兩者只共用同一把 GEMINI_API_KEY 這個底層資源。

跟拍照辨識（可能辨識出原文沒有的代碼）不同，這裡的輸出必然是輸入的
子集——cause+solution 合併後的字元集合必須是原文字元集合的子集，這件
事可以程式化驗證（verify_no_generation()），不必只靠 prompt 約束。
不通過就強制降級成整段當 solution，不冒然採信 AI 的判斷。
"""
import json
import os
import re

# prompt 版本號，改動 prompt 時遞增，讓歷史記錄可回溯比較（同
# ai_analyzer.py 的慣例）
PROMPT_VERSION = "v1"

# 單次呼叫上限，超過需分批（批次匯入 UI 規劃第 3.7 節）——分批之間不
# 共享上下文，某一批出問題不影響其他批。
MAX_BATCH_SIZE = 100

_STRIP_RE = re.compile(r"[\s，。；、,.;]")

_SPLIT_PROMPT = """你是工廠設備警報資料的文字切分工具。輸入是一段中文或中英混合的說明文字，
裡面同時包含「發生原因」和「處理方式」。你的工作是把它切成兩段。

## 唯一的規則

**只做切分，不做改寫。**

你的輸出必須是原文的重組——把原文的字，分配到 cause 和 solution 兩個欄位。
不得新增、刪除、潤飾、翻譯、補充、推測任何內容。

這份資料會進入製藥設備的警報查詢系統，現場人員會照著 solution 的內容
實際操作。多寫一個不存在的元件編號，就會讓人去檢查錯的元件。

## 輸出格式

只輸出 JSON 陣列，不要有任何前後說明文字，不要包 markdown 程式碼區塊。
陣列長度必須與輸入完全相同，順序一一對應。

[
  {
    "index": 0,
    "cause": "充填針跳起碰到感應器",
    "solution": "操作人員檢查充填針完整度，確認無損傷後安裝回支架",
    "confident": true
  }
]

## 怎麼判斷切點

**cause（原因）**：描述「發生了什麼」「為什麼會觸發」的部分。
  常見形式：某個狀態、某個偵測結果、某個異常現象
  常見標記詞：因、由於、造成、導致、偵測到、發現

**solution（處理方式）**：描述「該做什麼」的部分。
  常見形式：具體動作、檢查步驟、復歸方式
  常見標記詞：檢查、確認、更換、復位、重啟、清潔、調整、待、等

**沒有明確切點時，整段放進 solution，cause 填 null，confident 設 false。**

這不是失敗，是正確的處理方式。切錯比不切嚴重得多——不切的話，
現場人員看到的是完整原文，資訊沒有遺失；切錯的話，原因的一部分
會被當成處置指示。

## 什麼時候設 confident: false

- 找不到明確的原因／處置分界
- 整段都是描述現象，沒有任何動作指示
- 整段都是動作指示，沒有描述原因
- 文字太短或語意不完整，無法判斷
- 你不確定切點是否正確

**寧可 false，不要猜。** 標成 false 的項目會由人工確認，不會被丟棄。

## 絕對不能做的事

1. **不得新增任何原文沒有的字元。**
   cause 與 solution 串接後的所有字元，必須都出現在原文中。

2. **不得補上原文沒有的元件編號、數值、站別、代碼。**
   即使你根據上下文「推論」出應該是 B42.0，原文沒寫就不能寫。

3. **不得翻譯。** 中文保持中文，英文保持英文，混排就照原樣切。

4. **不得潤飾或改寫語句。** 包含原文的錯字、贅字、不通順的地方，全部照抄。

5. **不得補完不完整的句子。** 原文斷在哪裡就切到哪裡。

6. **不得改變順序。** 若原文是「先講處置、後講原因」，照原順序切，
   不要為了符合欄位語意而調換。

7. **不得合併或拆分輸入項目。** 輸入幾筆就輸出幾筆。

## 標點與空白的處理

- 切點處的標點（，。；）可以省略，這是唯一允許的字元刪除
- 前後空白可以修剪
- 除此之外不得增減任何字元

## 範例

輸入：
  "充填針跳起碰到感應器，操作人員檢查充填針完整度，確認無損傷後安裝回支架"

輸出：
  cause: "充填針跳起碰到感應器"
  solution: "操作人員檢查充填針完整度，確認無損傷後安裝回支架"
  confident: true

---

輸入：
  "本訊息 12004 Overload filling needle(s)，因充填針跳起碰到感應器，
   操作人員當下會介入並檢查充填針完整度"

輸出：
  cause: "因充填針跳起碰到感應器"
  solution: "操作人員當下會介入並檢查充填針完整度"
  confident: true

  註：開頭的「本訊息 12004 Overload filling needle(s)」是代碼重述，
      不屬於 cause 或 solution，可以捨棄——這是允許的刪除（重複資訊），
      但仍不得新增任何字。

---

輸入：
  "等待溫度至設定溫度±5℃內，即可啟動"

輸出：
  cause: null
  solution: "等待溫度至設定溫度±5℃內，即可啟動"
  confident: false

  註：整段都是動作指示，沒有描述原因，不強行切分。

---

輸入：
  "馬達異音"

輸出：
  cause: null
  solution: "馬達異音"
  confident: false

  註：太短，無法判斷是現象描述還是處置提示。
"""


def verify_no_generation(original: str, cause, solution) -> bool:
    """切分結果必須是原文的重組，不得含原文沒有的字元。

    AI 幻覺的特徵是產生看起來合理但原文沒有的內容（最危險的是推論出
    一個元件編號——現場會照著去檢查錯的元件）。切分場景相對於自由
    抽取有一個優勢：輸出必然是輸入的子集，這件事可以程式化驗證。
    不要浪費這個優勢，prompt 是請求，這裡才是保證。

    只比對字元集合而非子字串，因為允許在切點處省略標點、允許捨棄
    代碼重述那類重複資訊——順序與連續性不做要求。
    """
    merged = (cause or "") + (solution or "")
    src = set(_STRIP_RE.sub("", original))
    out = set(_STRIP_RE.sub("", merged))
    return out.issubset(src)


def _downgrade(original: str) -> dict:
    return {"cause": None, "solution": original, "confident": False, "downgraded": True}


def _parse_response(raw: str, texts: list) -> list:
    """把 AI 回應解析回結果列表，用 index 對齊輸入（AI 可能少回或多回，
    靠陣列位置對應會錯位）。數量對不上、JSON 解析失敗、或個別項目未
    通過 verify_no_generation() 一律強制降級，不冒然採信。"""
    text = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("回應不是陣列")
    except (json.JSONDecodeError, ValueError):
        return [_downgrade(t) for t in texts]

    by_index = {}
    for item in data:
        idx = item.get("index")
        if isinstance(idx, int):
            by_index[idx] = item

    results = []
    for i, original in enumerate(texts):
        item = by_index.get(i)
        if item is None:
            results.append(_downgrade(original))
            continue
        cause, solution = item.get("cause"), item.get("solution")
        if not verify_no_generation(original, cause, solution):
            results.append(_downgrade(original))
            continue
        results.append({
            "cause": cause,
            "solution": solution,
            "confident": bool(item.get("confident")),
            "downgraded": False,
        })
    return results


def split_texts(texts: list) -> list:
    """批次呼叫 AI 切分。texts 超過 MAX_BATCH_SIZE 時呼叫端須自行分批
    （見批次匯入 UI 規劃第 3.7 節）——這裡不隱式分批，避免呼叫端誤以
    為一次呼叫涵蓋了全部輸入。

    回傳長度必為 len(texts)，逐項對應：
        {"cause": str|None, "solution": str, "confident": bool, "downgraded": bool}
    """
    if len(texts) > MAX_BATCH_SIZE:
        raise ValueError(f"單次最多 {MAX_BATCH_SIZE} 筆，收到 {len(texts)} 筆，請分批呼叫")
    if not texts:
        return []

    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

    payload = json.dumps(
        [{"index": i, "text": t} for i, t in enumerate(texts)],
        ensure_ascii=False,
    )
    response = client.models.generate_content(
        model=model,
        contents=[_SPLIT_PROMPT, f"\n輸入：\n{payload}"],
    )
    return _parse_response(response.text.strip(), texts)
