-- 003_switch_constraints.sql
-- 多部門隔離工程 階段 3：主鍵/唯一約束切換（對應 PLAN 1.2、1.3 節）
--
-- 前置條件：
--   - 002_migrate_add_departments.sql 已執行成功
--   - devices_null_dept = 0 且 alarms_null_dept = 0（見 002 的驗收輸出）
--   - 已完成 post_backfill pg_dump（PLAN 1.6 節步驟7）
--
-- 執行方式：psql "$SUPABASE_DB_URL" -f 003_switch_constraints.sql
-- 整支包在單一交易內（PLAN 1.6 節步驟5要求）。
--
-- 約束真實名稱已於第十五輪查證（見 rollback_stage3.sql 開頭註記）：
--   devices_pkey       PRIMARY KEY (id)                    -- 不動
--   devices_model_key  UNIQUE (model)                      -- 本腳本會 drop
--   alarms_pkey        PRIMARY KEY (device_model, code)     -- 本腳本會 drop

begin;

-- 收緊 NOT NULL（002 驗收通過後才能執行這一步）
alter table devices alter column department set not null;
alter table alarms  alter column department set not null;

-- devices：drop 舊的全域唯一約束，換成 (department, model) 複合唯一約束
-- 主鍵 id 完全不受影響，見 PLAN 1.2 節「混合型」結論
alter table devices drop constraint devices_model_key;
alter table devices add constraint devices_dept_model_key unique (department, model);

-- alarms：主鍵從 (device_model, code) 改成 (department, device_model, code)
alter table alarms drop constraint alarms_pkey;
alter table alarms add constraint alarms_pkey primary key (department, device_model, code);

commit;

-- 執行後請重跑 00_preflight_check.sql 驗收（PLAN 1.6 節步驟6），
-- 確認新約束已生效、devices/alarms 皆無 NULL department。
