-- ============================================================
-- 013: 下架 AI 語意審核功能，semantic_review_findings 改名封存
-- 手動貼到 Supabase Dashboard SQL Editor 執行，不自動執行。
-- 只改名，不 DROP、不刪資料；預期保留 303 筆，脫離正式部門資料生命週期。
-- 執行前先單獨查詢實際索引名稱，核對下方三個 rename；若不同，先調整再執行。
-- select indexname from pg_indexes
-- where schemaname = 'public' and tablename = 'semantic_review_findings';
-- 以下名稱來自 migration 009/011，尚未連線正式資料庫確認。
-- ============================================================
begin;

alter table semantic_review_findings
  rename to semantic_review_findings_archived_20260929;

alter index semantic_review_findings_status_idx
  rename to semantic_review_findings_archived_20260929_status_idx;
alter index semantic_review_findings_device_model_idx
  rename to semantic_review_findings_archived_20260929_device_model_idx;
alter index semantic_review_findings_department_idx
  rename to semantic_review_findings_archived_20260929_department_idx;

commit;
notify pgrst, 'reload schema';

-- 驗收查詢（執行後手動核對）
-- select count(*) from semantic_review_findings_archived_20260929; -- 預期 303
-- select to_regclass('semantic_review_findings'); -- 預期 NULL
