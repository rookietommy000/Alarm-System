#!/bin/sh
# 前端 Vue 組件單元測試統一入口（Node.js 原生 test runner，沙箱執行
# frontend/*.html 裡的 <script> 內容，mock 掉 AlarmApi，不連網路）。
#
# 這些測試原本各自獨立、只在驗收文件裡手動記一行 `node --test <file>`，
# 沒有統一執行入口，容易變成沒人跑的孤兒（test_data_issue_admin_ui.cjs
# 就是這樣被遺漏在 commit 之外的案例）。新增前端單元測試請直接加進
# tests/ 目錄、副檔名 .cjs，這支腳本會自動找到並執行，不需要手動登記。
#
# 用法：sh tests/run_frontend_unit_tests.sh
set -eu
cd "$(dirname "$0")/.."
for f in tests/*.cjs; do
  echo "── $f ──"
  node --test "$f"
done
