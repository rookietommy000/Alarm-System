"""
守住「frontend/ 下每一支 HTML 都真的被某條路由服務」，不是驗證頁面內容對不對。

背景（外部審查）：frontend/admin.html 曾經是 dashboard.html 的前身，在
db4cb7e「深色主題、回饋儀表板、查詢計數、歷史紀錄手風琴展開」之後被
dashboard.html 取代，但檔案本身沒有刪除、也沒有任何路由再指向它。後來
在上面新增了一整個「後台待審清單」功能（現場處置做法階段 5），做完、
commit、部署，使用者卻永遠看不到——因為 /admin 路由回傳的是
dashboard.html，admin.html 是死檔案。

同一次審查也發現 frontend/portal.html：它在 29ffea4「Move portal to /,
alarm system to /app」之後曾經是真正的首頁，直到 cf13647（多部門隔離
工程 stage -1c 起點）把 / 路由改成 redirect("/app") 為止，之後就一直是
孤兒，甚至後來還被人在不知情的狀況下改過一次（PWA meta 標籤）。

兩次孤兒檔案的共同模式：檔案存在、內容看起來完整、甚至還會被繼續修改，
但沒有任何使用者路徑到得了它——跟本專案反覆踩過的「看起來有在運作，
實際上沒有」是同一類問題（見 test_no_fake_isolation_claims.py 的假通過
測試、_count() 解析失敗回 0、seed 寫死雜湊）。這個測試把「哪些前端檔案
真的被服務」變成自動化檢查，而不是靠人記得。
"""

import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parent.parent
APP_PY = BASE / "backend" / "app.py"
FRONTEND = BASE / "frontend"

# 檔名 → 保留原因。空字典代表：frontend/ 下不允許任何未被路由服務的 HTML。
# 新增孤兒檔案時，要嘛刪掉，要嘛在這裡登記原因（例如「刻意保留供未來
# xxx 使用」），不能讓它安靜地留在目錄裡。
KNOWN_UNUSED: dict[str, str] = {}


def _served_html_filenames() -> set[str]:
    """從 backend/app.py 找出所有 send_from_directory(FRONTEND, "*.html")
    呼叫，回傳實際被服務的檔名集合。用字串掃描而非 import app 模組，避免
    這個測試依賴 Supabase/環境變數設定就能跑（同 test_route_auth_registry.py
    的既有作法）。"""
    src = APP_PY.read_text(encoding="utf-8")
    return set(re.findall(r'send_from_directory\(FRONTEND,\s*"([^"]+\.html)"\)', src))


def test_no_orphan_frontend_html():
    """frontend/ 下每一支 HTML 都必須被某條路由服務，或明確登記為不使用。"""
    served = _served_html_filenames()
    actual = {p.name for p in FRONTEND.glob("*.html")}

    orphans = actual - served - set(KNOWN_UNUSED)
    assert not orphans, (
        f"以下 HTML 沒有被任何路由服務：{sorted(orphans)}\n"
        f"若為刻意保留，請加進 KNOWN_UNUSED 並註明原因；否則請刪除。"
    )


def test_known_unused_entries_are_still_orphans():
    """KNOWN_UNUSED 裡登記的檔案，防止「已經被接回路由但忘記從清單移除」
    這種相反方向的靜默錯誤——清單本身也要保持誠實。"""
    served = _served_html_filenames()
    actual = {p.name for p in FRONTEND.glob("*.html")}

    for name in KNOWN_UNUSED:
        assert name in actual, f"KNOWN_UNUSED 登記的 {name} 已不存在，請移除這筆登記"
        assert name not in served, (
            f"{name} 已經被路由服務了（見 app.py），但仍列在 KNOWN_UNUSED——"
            f"請移除這筆登記，避免下次真的變孤兒時被誤判為「已知」而放過"
        )
