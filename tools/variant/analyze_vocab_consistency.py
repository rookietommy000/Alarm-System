#!/usr/bin/env python3
"""詞彙一致性分析（規劃第 1c 項後續優化，階段 0）——一次性分析工具。

跟先前版本（inline 寫在對話中、未進 repo 的版本）的差異：那版用「issue
理由文字」分組，理由裡順帶提到某個詞就會被誤歸類（例如 issue 提到
"driver" 是因為在解釋別的問題，不代表這筆的英文標題真的含 driver）。
外部審查指出這個問題，改成用「description 的英文標題本身」分組，只有
標題真的包含該詞才算進同一組。

同時修正「不一致」的判斷邏輯：先前是比較整句 suggested_zh 是否相同
（誤報，例如 tipper 組 8 筆全是「翻轉機構」開頭但後綴各異，被誤判為
不一致）。改成只抽取該詞彙本身對應的中文譯法片段來比較，不看整句。

用法：
    python analyze_vocab_consistency.py -i ../../data/semantic_scan_fixes.json
"""
import argparse
import json
import pathlib
import re
from collections import defaultdict

_EN_TITLE_RE = re.compile(r'^([A-Za-z0-9%.\-/() ]+)')

# 候選詞彙群組：key 是對外顯示用的詞彙決定名稱，value 是要在英文標題
# 裡搜尋比對的所有形式（含單複數變體）。單複數不該是兩個獨立的譯法
# 決定——"case" 跟 "cases" 該用同一個詞彙決定（外部審查指出：分開列
# 會讓現場人員誤以為這是兩件事，實際上只是文法變化）。
CANDIDATE_TERM_GROUPS = {
    "build-back": ["build-back"],
    "tape": ["tape"],
    "case": ["case", "cases"],
    "driver": ["driver"],
    "tipper": ["tipper"],
    "jam": ["jam"],
    "magazine": ["magazine"],
    "flap": ["flap"],
    "running idle": ["running idle"],
    "supervision system": ["supervision system"],
    "tamper evident": ["tamper evident"],
    "t.e.": ["t.e."],
    "label reel": ["label reel"],
    "bucket": ["bucket"],
}
# "remove" 刻意不列入：它是通用動詞，翻法取決於受詞（"remove label"
# 移除標籤 vs "remove product" 移除產品 vs "clear labels" 清除標籤），
# 不是固定的專有名詞/術語決定。分析後發現這組把完全不同語境的動作
# 混進同一組，不是真正的詞彙一致性問題（外部審查發現）。


def extract_en_title(description: str) -> str:
    """從 description 拆出開頭的英文標題部分（到第一個中文字元或行尾）。
    大小寫正規化為小寫供比對用。"""
    m = _EN_TITLE_RE.match(description)
    return m.group(1).strip().lower() if m else ""


def term_zh_fragment(term: str, suggested_zh: str) -> str:
    """粗略抽出 suggested_zh 裡『對應到這個英文詞』的中文片段，用於判斷
    譯法是否一致——不是精確的詞對齊（那需要真正的翻譯對齊模型），這裡
    用已知詞彙的常見中文對應詞表做字串命中，命中哪個就回傳哪個，供
    人工最終確認，不是自動判定的最終答案。"""
    KNOWN_TRANSLATIONS = {
        "build-back": ["積料", "堵料", "回堵", "積壓", "堵塞"],
        "tape": ["膠帶", "標籤帶", "標籤料捲", "標籤捲"],
        "case": ["紙箱", "箱子", "箱體"],
        "driver": ["驅動器", "驅動程式", "馬達驅動"],
        "tipper": ["翻轉機構", "自卸車", "傾卸機"],
        "jam": ["卡阻", "卡住", "堵塞", "卡紙"],
        "magazine": ["料架", "料倉"],
        "flap": ["撥桿", "擋板"],
        "running idle": ["空轉", "空載運行", "空運轉模式", "空轉功能"],
        "supervision system": ["監控系統"],
        "tamper evident": ["防篡改", "防拆封", "防拆"],
        "t.e.": ["防篡改", "防拆封", "防拆", "T.E."],
        "label reel": ["回捲器", "標籤捲", "標籤料捲"],
        "bucket": ["料斗", "鏟鬥", "桶"],
    }
    candidates = KNOWN_TRANSLATIONS.get(term, [])
    for c in candidates:
        if c in suggested_zh:
            return c
    return "(未辨識)"


def analyze(findings: list) -> list:
    groups = defaultdict(list)
    for i, f in enumerate(findings):
        en_title = extract_en_title(f["description"])
        for term, variants in CANDIDATE_TERM_GROUPS.items():
            if any(v in en_title for v in variants):
                groups[term].append(i)

    result = []
    for term, idxs in sorted(groups.items(), key=lambda x: -len(x[1])):
        items = []
        fragments = set()
        for i in idxs:
            f = findings[i]
            frag = term_zh_fragment(term, f["suggested_zh"])
            fragments.add(frag)
            items.append({
                "idx": i, "device_model": f["device_model"], "code": f["code"],
                "suggested_zh": f["suggested_zh"], "zh_fragment": frag,
            })
        inconsistent = len(fragments - {"(未辨識)"}) > 1
        result.append({
            "term": term, "count": len(idxs),
            "fragments": sorted(fragments),
            "inconsistent": inconsistent,
            "items": items,
        })
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-i", "--input", required=True)
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args()

    report = json.loads(pathlib.Path(args.input).read_text(encoding="utf-8"))
    findings = report["findings"]
    groups = analyze(findings)

    for g in groups:
        flag = " ⚠ 譯法不一致" if g["inconsistent"] else " ✓ 一致"
        print(f"\n{g['term']!r} — {g['count']} 筆{flag}")
        print(f"  出現的譯法: {g['fragments']}")
        for it in g["items"]:
            print(f"    [{it['idx']}] {it['device_model']} {it['code']}: {it['suggested_zh']} → {it['zh_fragment']}")

    if args.output:
        pathlib.Path(args.output).write_text(
            json.dumps({"groups": groups}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已寫入 {args.output}")


if __name__ == "__main__":
    main()
