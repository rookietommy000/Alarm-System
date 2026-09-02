-- ============================================================
-- 008: 新增 variant_translations，供拍照辨識多 variant 選擇 UI
-- 顯示中文翻譯（拍照辨識故障修復 Q3 延伸）
--
-- 背景：mf4c/FILL203 等機種的 variant 是完整英文語意描述（非短編號，
-- 已用 PostgREST 查證真實資料，見 project_variant_normalize_fix_20260901
-- 記憶檔），前端只顯示英文原文會增加現場人員選錯的風險。已用 Gemini
-- 批次翻譯 103 筆真實 variant 文字，翻譯結果需要一個可持續新增/校對的
-- 存放位置。
--
-- 原本考慮存成 data/variant_translations.json 直接 track 進 git（員工
-- 提案的較輕量替代方案），裁決不採用：這批資料會持續增加與校對，改存
-- DB 才不用每次校對都走 commit+部署流程。比照 devices_store 雙軌設計
-- （本機/測試維持讀本地 JSON，production 走這張表）。
--
-- 這張表跟 department 無關（variant 文字本身跟部門無關，同一句英文
-- 描述不會因部門不同而有不同翻譯），因此不比照 alarms 帶 department
-- 欄位、也不會出現在任何 department 隔離檢查的範圍內。
--
-- review_status 沿用 alarm_suggestions 的字串狀態欄位風格（不建 enum
-- type，理由同 003_switch_constraints.sql：改狀態值不需要額外的 DDL）。
-- 'ai_translated_pending_review' 是目前唯一會被寫入的初始值（一次性
-- 腳本 backend/scripts/seed_variant_translations.py 使用），校對完成後
-- 由人工介面（尚未建置）更新為 'reviewed'。
--
-- 手動貼到 Supabase Dashboard SQL Editor 執行（本專案既有慣例，見
-- 006_add_variant.sql 開頭的說明：PostgREST 的 REST API 不支援 DDL）。
--
-- ⚠️ 範圍限縮（2026-09-01 顧問裁決）：本檔案只準備好，不在今晚/這輪
-- 自動執行 DDL——要等使用者本人確認執行時機。
-- ============================================================

begin;

-- original_text 當唯一鍵（不是 device_model/code，因為同一句英文描述
-- 可能出现在多個 device_model/code 底下，翻譯只跟文字本身有關，一份
-- 翻譯可以共用；重複儲存同一句話的翻譯没有意義也難維護一致性）。
create table variant_translations (
  id             bigint generated always as identity primary key,
  original_text  text not null unique,
  translated_text text not null,
  review_status  text not null default 'ai_translated_pending_review',
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index variant_translations_original_text_idx on variant_translations(original_text);

commit;

notify pgrst, 'reload schema';

-- ── 驗收查詢（執行後手動核對）──────────────────────────────
-- select table_name from information_schema.tables
-- where table_name = 'variant_translations';
-- 預期：存在
--
-- select count(*) from variant_translations;
-- 預期：0（新表，尚無資料，一次性腳本尚未執行）
