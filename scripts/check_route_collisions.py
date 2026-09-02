"""掃描：把某條路由的一個動態段去掉後，會不會意外命中另一條路由。

背景：merge_slashes 關閉前，Werkzeug 對含連續斜線的路徑（動態段被
傳成空字串時常見的形狀）會先做斜線合併再嘗試匹配，讓「參數位移後」
的路徑意外命中完全不同的路由（reset-password 端點被合併重導向到
rename_department() 的真實案例，見 CLAUDE.md「已知陷阱」跟
tests/test_route_safety.py 開頭說明）。merge_slashes 關閉後，這種
「清空一段、字面上 collapse 成另一條真實路由」的組合理論上不會再被
Werkzeug 自動嘗試匹配——這支腳本的意義是靜態驗證這個理論成立，以及
在未來新增路由時持續守住：一旦某次新增路由不小心製造出新的 collapse
組合，這裡的掃描要能抓到，不依賴逐一發送請求撞。

執行方式：`python scripts/check_route_collisions.py`（獨立腳本，也被
tests/test_route_safety.py::test_no_route_collapse_collisions import
使用）。
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def find_collapse_collisions(app):
    """回傳 [(原始路由, method, collapse 後的路徑, 實際命中的 endpoint), ...]。

    對每條路由的每一個動態段（<string> 等 converter，不含字面量路徑
    段），假設該段被清空、整段從路徑消失（模擬 merge_slashes 合併後
    的路徑形狀），用 url_map 的 adapter 靜態比對這個 collapse 後的
    路徑是否意外命中另一條路由（endpoint 不同代表撞到別的端點）。
    命中同一條路由自己（endpoint 相同）不算碰撞——那只是路徑剛好還
    能匹配到自己，不構成端點混淆風險。
    """
    adapter = app.url_map.bind("localhost")
    rules = list(app.url_map.iter_rules())
    hits = []
    for rule in rules:
        parts = rule.rule.strip("/").split("/")
        for i, seg in enumerate(parts):
            if not (seg.startswith("<") and seg.endswith(">")):
                continue
            collapsed = "/" + "/".join(parts[:i] + parts[i + 1:])
            for method in (rule.methods or set()) - {"HEAD", "OPTIONS"}:
                try:
                    endpoint, _ = adapter.match(collapsed, method=method)
                except Exception:
                    continue
                if endpoint != rule.endpoint:
                    hits.append((rule.rule, method, collapsed, endpoint))
    return hits


if __name__ == "__main__":
    from app import app  # create_app() factory pattern 的模組級全域實例（app.py 結尾 app = create_app()）

    collisions = find_collapse_collisions(app)
    if collisions:
        print(f"發現 {len(collisions)} 組碰撞：")
        for original, method, collapsed, hit_endpoint in collisions:
            print(f"  {method} {original} → 去掉一段後變成 {collapsed} → 命中 {hit_endpoint}")
        raise SystemExit(1)
    print("沒有發現路由碰撞。")
