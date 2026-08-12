#!/usr/bin/env python3
"""產生第一個部門（mf4d）的密碼雜湊，供 002_migrate_add_departments.sql 使用。

用法：
    cd backend && ../.venv/bin/python3 gen_department_hashes.py

會讀取現有 .env 的 LOGIN_PASSWORD / ADMIN_PASSWORD（沿用既有使用者密碼，
遷移後現有使用者密碼繼續有效，見 PLAN 1.7 節步驟2），輸出對應的
werkzeug 密碼雜湊，貼進 002_migrate_add_departments.sql 的佔位字串。

不會把明文密碼或雜湊寫進任何檔案，僅印在終端機。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

login_pw = os.environ.get("LOGIN_PASSWORD")
admin_pw = os.environ.get("ADMIN_PASSWORD")

if not login_pw or not admin_pw:
    print("錯誤：.env 缺少 LOGIN_PASSWORD 或 ADMIN_PASSWORD", file=sys.stderr)
    sys.exit(1)

print("=== 貼進 002_migrate_add_departments.sql 的佔位字串 ===")
print(f"__PW_HASH__       = {generate_password_hash(login_pw, method='pbkdf2:sha256')}")
print(f"__ADMIN_PW_HASH__ = {generate_password_hash(admin_pw, method='pbkdf2:sha256')}")
