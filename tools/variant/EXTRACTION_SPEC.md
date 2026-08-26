# 原廠警報文件 → 標準中繼格式：抽取規格

版本：2026-08。銜接 `import_alarms.py`（「標準格式 → 資料庫」已完成），本文件補上前半段。

---

## 0. 定位

```
原廠文件（PDF / Word / Excel）
   ↓  ① 每個來源一支拋棄式腳本         ← 本文件（來源層）
標準中繼格式
   ↓  ② import_alarms.py（已完成）
資料庫
```

**不寫通用解析器。** 每家原廠的表格結構都不同，通用化的成本遠高於為每個來源重寫一支五十行的腳本。

**共用層**（中繼格式、品質檢查、人工確認流程、正確率量測）寫死，所有來源適用。
**來源層**每新增一個來源就加一節。

---

## 1. 拿到新文件的三個判斷

**Q1：選得到文字嗎？**

```python
from pypdf import PdfReader
r = PdfReader(path)
for i, p in enumerate(r.pages):
    print(i+1, len(p.extract_text() or ""), p.get("/Rotate", 0))
# 全部 0 字元 = 掃描件
```

- 選得到 → 文字型，走 `pdfplumber` / `python-docx`，成本低
- 選不到 → 掃描件，走光柵化 + 視覺辨識，成本高好幾倍

**Q2：有沒有「只存在於視覺」的資訊？**

顏色、粗體、圖示、螢光標記。看有沒有圖例（例如首頁的「紅-停機警報 橘-警示警報…」）。

> 本專案**不使用 `severity` / `keywords`**（既有資料兩欄皆 100% 空白），所以顏色編碼的嚴重度**不抽取**。若日後決定啟用，這一層要重做。

**Q3：現場批註在哪裡？**

決定 `local_solution` 抽不抽得出來。可能是文件內的中文段落、手寫在旁邊、或另一份 Excel。

---

## 2. 中繼格式欄位

| 欄位 | 說明 | 必填 |
|---|---|---|
| `code` | 四位數代碼，保留前導零 | ✅ |
| `device_model` | 由 `-m` 參數帶入，不從文件抽 | ✅ |
| `variant` | 同代碼下的變體識別（見 `--variant-mode`） | |
| `description` | `英文標題 中文標題`，**無代碼前綴** | ✅ |
| `cause` | `The message indicates...` 那段 | |
| `solution` | 原廠處置（`- Check ...` bullet） | |
| `local_solution` | **本廠做法**（中文檢查步驟，含元件編號） | |
| `severity` / `keywords` | 留空（未使用） | |
| `_source_page` | 來源頁碼，校對用，匯入前移除 | ✅ |
| `_flags` | 品質標記，匯入前移除 | |

底線開頭的兩欄是**校對用鷹架**。

---

## 3. 光柵化（掃描件專用）

**必須光柵化整頁，不要用 `page.images` 抽內嵌影像。** 頁面 `/Rotate` 可能有 0/180/90 三種，`render()` 會套用旋轉，`page.images` 不會——直接抽會得到顛倒的影像，辨識結果全是垃圾。

```python
import pypdfium2 as pdfium

pdf = pdfium.PdfDocument(path)
for i, page in enumerate(pdf, start=1):
    img = page.render(scale=3.0).to_pil()      # 原始約 200 DPI，放大取樣補強
    if img.width > img.height:                  # 橫向頁轉正
        img = img.rotate(-90, expand=True)
    img.save(outdir / f"page_{i:02d}.png")
```

**一次送一頁**給模型，不要整份送。頁與頁之間沒有跨頁依賴，一頁一次的錯誤比較好定位，且 `source_page` 天然正確。

---

## 4. 視覺辨識 Prompt

````
你是工廠設備原廠警報手冊的資料抽取工具。輸入是手冊掃描頁的影像，
輸出是結構化 JSON。這份資料會進入製藥設備的警報查詢系統，現場人員
會照著上面的處置方式操作，所以**準確性優先於完整性**。

## 輸出格式

只輸出 JSON 陣列，不要有任何前後說明文字，不要包 markdown 程式碼區塊。

[
  {
    "code": "0024",
    "title_en": "Forming Panel Heating Units Automatic",
    "title_zh": "成型加熱站點位異常（保險開關跳脫）",
    "cause": "The message indicates that a circuit breaker protecting the heating units has tripped.",
    "solution_vendor": "- Check that the temperature of the units is not too high and that the sensors are not faulty.",
    "solution_local": "檢查加熱板溫度是否過高；檢查Sensor B42.0/B42.3是否鬆脫；檢查後將F43.1/F43.3復位",
    "source_page": 1,
    "has_figure": false,
    "uncertain": []
  }
]

## 每個欄位的判斷規則

**code**：標題最前面的數字，保留前導零（`0024` 不是 `24`）。

**title_en**：`code -` 之後的英文標題。

**title_zh**：英文標題**同一行後方**的中文。沒有就填 `null`，不要自己翻譯。

**cause**：以 `The message indicates` / `Message indicates` / `The message signals`
開頭的英文段落，通常一到兩句。

**solution_vendor**：以 `-` 開頭的英文 bullet 行，全部保留，多行用 `\n` 連接，
保留開頭的 `- `。這是原廠的處置建議。

**solution_local**：**中文的檢查步驟**。判斷依據：
  - 通常出現在英文 bullet 之後
  - 內容含元件編號，例如 `B42.0`、`F43.1`、`K20.6`、`M53.0`、`QF46.1`
  - 或含具體數值、站別、動作（`等待溫度至設定溫度±5℃內`）
  - **例外**：中文有時直接接在英文 bullet 的同一行後方，例如
    `- Check that the feed step set is correct.  確認每個成型柱(Step)大小相同`
    這種情況，中文部分要抽出來放進 solution_local，英文留在 solution_vendor
  - 多條用 `；` 或 `\n` 連接，保留原文的分隔方式
  - 沒有就填 `null`

**source_page**：這一筆出現在第幾頁（從 1 開始）。

**has_figure**：該筆是否附有圖表、示意圖、電路圖。有的話設 true，
並在 uncertain 加上 `"figure_not_extracted"`。

## uncertain 陣列：不確定就標記，不要猜

- `"low_confidence_code"` — 代碼數字看不清楚
- `"component_id_unclear"` — 元件編號的字元無法確定（`B42.0` vs `B420` vs `842.0`）
- `"text_cut_off"` — 文字被裁切、被裝訂線遮住、或延續到下一頁
- `"figure_not_extracted"` — 有圖無法轉成文字
- `"annotated"` — 有螢光標記、底色、手寫或後製加註（通常是本廠自己
  加的，應歸入 solution_local 而非 solution_vendor——不是雜訊，是
  跟原廠內容不同來源的線索，供人工判斷這段是不是本廠的操作經驗）
- `"layout_unusual"` — 版面與其他筆明顯不同

## 絕對不能做的事

1. **不要翻譯任何內容。** 英文保持英文，中文保持中文。缺中文標題就填 null。
2. **不要改寫或潤飾。** 逐字照抄，包含原文的錯字與不一致的用詞。
3. **不要填入 `%1` 這類佔位符的實際內容。** 原樣保留 `%1`。
4. **不要推測看不清楚的字元。** 標進 uncertain，該欄位填你最接近的判讀。
5. **不要合併或拆分警報項目。** 文件上一個代碼就是一筆。
6. **不要補上文件沒有的內容。** 沒有就是 null。

## 特別注意元件編號

`B42.0`、`F143.5`、`M183.1`、`QF160.1`、`K120.6` 這類編號是現場人員實際
要去檢查的位置，**錯一個字元就會讓人檢查錯的元件**。

常見誤判：
  - 小數點消失：`B42.0` → `B420`
  - `0` 與 `O`、`1` 與 `l`、`8` 與 `B`
  - 斜線分隔的多個編號被誤合：`F43.1/F43.3`

看不清楚時務必標 `component_id_unclear`，不要猜。
````

---

## 5. 品質檢查

```python
import re

COMPONENT_RE = re.compile(r'\b[A-Z]{1,3}\d+\.\d+\b')      # B42.0 / QF160.1
SUSPECT_RE   = re.compile(r'\b[A-Z]{1,3}\d{3,}\b')        # B420 ← 小數點掉了
PHONETIC     = set('薩坎納曼蒂庫西迪雷特阿比埃')            # 音譯亂碼常用字

# 簡體字混入繁體語境的標記字（挑常見於警報處置文字中的簡體用字）。
# 只標記，不自動轉換——一簡對多繁的字（发→發/髮）自動轉會出錯，而
# 處置說明錯一個字可能改變操作意思（TFM001 0139 實測案例：AI 輸出
# 「冷却板」，原圖是「冷卻板」，簡體字混入）。
SIMPLIFIED_ONLY = set('却开关闭电压设备温阀门检查连接线圈显示屏')

def check(rows):
    issues = []
    codes = [r["code"] for r in rows]

    # 只有當同一字母前綴在文件別處確實出現過帶小數點的形式，才把無小數點的
    # 版本視為疑似 OCR 掉點。否則像閥號 V46004 會被大量誤報（實測 36 項
    # 警告中 27 項是這種誤判）。
    all_text = " ".join(f'{r.get("cause","")} {r.get("solution_local","")}' for r in rows)
    decimal_prefixes = {m[:re.search(r"\d", m).start()] for m in COMPONENT_RE.findall(all_text)}

    for r in rows:
        c = r["code"]
        blob = f'{r.get("solution_local") or ""} {r.get("solution_vendor") or ""}'

        if not re.fullmatch(r'\d{4}', c):
            issues.append((c, "ERROR", f"代碼格式不符四位數：{c}"))

        for m in SUSPECT_RE.findall(blob):
            if m[:re.search(r"\d", m).start()] in decimal_prefixes:
                issues.append((c, "WARN", f"元件編號疑似缺小數點：{m}"))

        if "%1" in (r.get("title_en") or "") and "%1" not in (r.get("cause") or "") + blob:
            issues.append((c, "WARN", "標題含 %1 但內文未出現，可能被誤填"))

        zh = r.get("title_zh") or ""
        if zh and sum(ch in PHONETIC for ch in zh) >= 3:
            issues.append((c, "WARN", f"中文標題疑似音譯亂碼：{zh}"))

        zh_all = f'{zh} {r.get("solution_local") or ""}'
        simplified_hits = sorted(set(zh_all) & SIMPLIFIED_ONLY)
        if simplified_hits:
            issues.append((c, "WARN", f"簡繁混用（不自動轉換，人工確認）：{''.join(simplified_hits)}"))

        for u in r.get("uncertain", []):
            issues.append((c, "REVIEW", f"模型標記：{u}"))

        if not r.get("solution_vendor") and not r.get("solution_local"):
            issues.append((c, "INFO", "無任何處置方式"))

    # 跳號門檻用相對值。寫死絕對值在不同原廠體系間必然失準：義大利系四位數
    # 連續編號，Bosch/Syntegon 五位數依子系統分區段。
    nums = sorted(int(c) for c in set(codes) if c.isdigit())
    gaps = [b - a for a, b in zip(nums, nums[1:])]
    if len(gaps) >= 5:
        median = sorted(gaps)[len(gaps) // 2]
        for a, b in zip(nums, nums[1:]):
            if b - a > max(100, median * 20):
                issues.append((f"{a}→{b}", "WARN", f"代碼跳號 {b-a}（中位數 {median}）"))

    dup = {c for c in codes if codes.count(c) > 1}
    for c in dup:
        issues.append((c, "ERROR", "代碼重複"))

    return issues
```

> **音譯亂碼那條不要拿掉。** 既有資料已證實這類錯誤會發生且人工看不出來——`MANCANZA TENSIONE DI RETE → 曼坎薩緊張迪雷特`（音譯）、`ARRESTO MANUALE → 逮捕手冊`、`REPHASE MACHINE → 複相機`。這些不是空白，是通順但錯誤的內容。

---

## 6. 執行與校對流程

1. **先跑一頁**（約 12 筆），人工核對五分鐘。格式有問題現在調 prompt
2. 確認無誤 → 跑完全部
3. 跑品質檢查，修掉技術性錯誤
4. **`_flags` 非空的優先人工看**——模型自己說「我不確定」的部分，命中率遠高於隨機抽樣
5. 交**接收部門的資深人員**抽樣確認（每台機種 10 筆）
6. 確認完才正式匯入
7. 上線後前三個月留意「回饋：沒幫助」的比例——資料錯誤的落後指標

### 一條不能跨的線

> **AI 抽出來的內容，必須逐筆經過人工確認才能進資料庫。**

理由不是技術上的，是後果上的。這是製藥設備的警報處置指示——AI 把「檢查冷卻水流量」幻覺成「重新啟動驅動器」，現場人員會照做。

而且 AI 的錯誤特性是**它不會空著，它會編一個看起來合理的**。空白很好發現，一句通順但錯誤的處置沒人會懷疑。

### 誰來確認：不是你

**接收部門的資深人員比你更懂他們的設備。** 你負責抽取與技術檢查。

---

## 7. 正確率量測

需要「原始文件」與「已校對資料」兩者對應。

1. 光柵化 → 逐頁送 prompt
2. 抽出的 `description` 跟已校對資料比對
3. 得出：正確率、錯誤集中在哪類欄位、人工校對耗時

**那個數字決定「要不要把 AI 解析做成後台功能開放給其他部門」。在有數字之前不該開放。**

> ⚠️ **2026-08 實測狀態：尚未完成。**
> 原本預期 `警報001.pdf` 是 ACM002 的手冊，可用既有 179 筆當對照組。實測比對後確認**兩者不是同一台機器**：第 1–2 頁 24 筆中僅 8 個代碼與 ACM002 重疊，且那 8 筆內容**零筆一致**；同一組警報在兩邊代碼被重新編號（`Manual Stop` PDF=0035／DB=0003，`No Compressed Air` PDF=0044／DB=0002）；PDF 的獨有內容（`Forming Panel Heating`、`Ethercat`、`成型加熱站點位異常`）在 `ACM002.json` 與 `DTS003-BL-A420.json` 皆零命中。
>
> 同一家原廠、同一套警報詞彙，但不同機型。**目前沒有可用的 ground truth。**
>
> 要量測需要：找到該 PDF 對應機種的已校對資料，或人工校對 25 筆當基準（後者不吃虧——那 25 筆校完就能用）。

---

## 8. 來源層登記

### 8.1 `警報001.pdf`（掃描件，8 頁）

| 項目 | 實測 |
|---|---|
| 文字層 | **無**（8 頁全部 0 字元） |
| 解析度 | 約 200 DPI（中文 OCR 建議下限 300） |
| 頁面旋轉 | 第 1–2 頁 `0°`、第 3–7 頁 `180°`、第 8 頁 `90°` |

**特有的坑**：

- 旋轉不一致 → **必須光柵化整頁**，用 `page.images` 會有五頁顛倒
- 第 8 頁橫向 → 光柵化後要再轉正
- 200 DPI 對含小數點的元件編號辨識率偏低，`scale=3` 補強
- 第 4 頁 `0174` 含裁切迴圈示意圖 → `has_figure`
- `0054` 有綠色螢光標記的手動加註（`E1`）
- `0059`／`0060` 標題含 `%1` 佔位符
- `0054` 的中文**交錯在英文 bullet 同一行**，不是集中在段落最後

**結構對應**：

```
0024 - Forming Panel Heating Units Automatic   成型加熱站點位異常（保險開關跳脫）
The message indicates that a circuit breaker protecting the heating units has tripped.
- Check that the temperature of the units is not too high and that the sensors are not faulty.
檢查加熱板溫度是否過高；檢查Sensor B42.0/B42.3是否鬆脫；檢查後將F43.1/F43.3復位
```

| 文件內容 | 欄位 |
|---|---|
| `0024` | `code` |
| 英文標題 + 中文標題 | `description` |
| `The message indicates that...` | `cause` |
| `- Check that...` | `solution` |
| `檢查加熱板溫度是否過高；檢查Sensor B42.0...` | **`local_solution`** |

**重要**：最後一層的中文提到 `B42.0`、`F43.1`、`K20.6`、`M53.0`、`QF46.1`——**這些是本廠的實際元件編號，原廠通用手冊不會有**。現場知識已經寫在文件上，不需要人工重新輸入。

⚠️ **這份 PDF 對應的 `device_model` 未確認**（見第 7 節）。匯入前務必先確認是哪一台機器。

### 8.2 FILL203 批次報告（`.xlsx`）

格式：Excel，四個分頁（`alarm count` / `alarm list` / `Problem` / `工作表1`），只有前兩個是警報資料。

```bash
python parse_alarms.py -i <xlsx> -m FILL203 \
    --variant-mode always --action-to local_solution
```

**特有的坑**：

- `alarm list` 的 Cause 欄**無表頭**（只靠關鍵字比對會整欄漏掉，`cause` 一度只有 33%）
- `Problem` 分頁是問答清單，內文提到代碼會觸發 fallback 誤判
- `batch` 欄是合併儲存格（忽略）
- 5 筆 `#N/A`
- 閥號無小數點，元件編號檢查會誤報

**結構對應**：

| Excel | 欄位 |
|---|---|
| `Description` 前的五位數字 | `code` |
| `Description` 後的文字 | `variant` ⚠️ **28 個代碼撐 114 筆** |
| `Cause` | `cause` |
| `Action`（中文「本訊息…」） | `local_solution`（**本廠自撰，非原廠**） |
| `Count` | 不匯入 |

---

## 9. 待確認

- ⬜ `警報001.pdf` 對應哪一台機種？內容是否已進資料庫？
- ⬜ 正確率量測（見第 7 節，需先解決 ground truth）
- ⬜ 其他部門的文件是掃描件還是電子檔
- ⬜ 原廠文件有無保密限制，影響能否用外部 AI 服務處理
