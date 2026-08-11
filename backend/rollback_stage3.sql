-- rollback_stage3.sql
-- 多部門隔離工程：階段 1~3 的回滾腳本
-- 對應 PLAN_department_isolation.md 1.6 節（第十五輪定案）
--
-- 用途：若階段 1（加欄位）、階段 2（遷移腳本）、階段 3（主鍵/約束切換）任一步出錯，
-- 用本腳本退回到「多部門隔離工程開始前」的資料庫結構狀態。
--
-- 約束真實名稱已於 2026-08-11 用 \d devices / \d alarms 查證（見 PLAN 1.6 節步驟 1）：
--   devices_pkey        PRIMARY KEY (id)          -- 不受本工程影響，不用還原
--   devices_model_key    UNIQUE (model)            -- 階段 3 會 drop 這個
--   alarms_pkey          PRIMARY KEY (device_model, code)  -- 階段 3 會 drop 這個
--
-- 執行方式：psql "$SUPABASE_DB_URL" -f rollback_stage3.sql
-- 整支包在單一交易內，任何一步失敗就整體不生效。

begin;

-- === 還原階段 3：主鍵/唯一約束切換 ===
-- 只有在階段 3 已經執行過（新約束已存在）時，以下才有東西可退。
-- 用 IF EXISTS 讓腳本在階段 3 尚未執行時也能安全跑（不報錯、無副作用）。

alter table if exists devices
  drop constraint if exists devices_dept_model_key;

alter table if exists devices
  add constraint devices_model_key unique (model);

alter table if exists alarms
  drop constraint if exists alarms_pkey;

alter table if exists alarms
  add constraint alarms_pkey primary key (device_model, code);

-- === 還原階段 1：新增的 department 欄位與索引 ===
-- DROP COLUMN 會連帶刪除該欄位上的索引，不需要另外 DROP INDEX。

alter table if exists devices        drop column if exists department;
alter table if exists alarms         drop column if exists department;
alter table if exists ai_scans       drop column if exists department;
alter table if exists ai_corrections drop column if exists department;
alter table if exists ai_logs        drop column if exists department;
alter table if exists alarm_history  drop column if exists department;
alter table if exists feedback       drop column if exists department;
alter table if exists alarm_views    drop column if exists department;

-- === 還原階段 2：新表 ===
-- login_attempts 沒有外鍵指向它，可直接砍；departments 若有其他表的 FK 指向它，
-- 上面的 DROP COLUMN 已經把這些 FK 一併移除，此處砍表不會撞依賴。

drop table if exists login_attempts;
drop table if exists departments;

commit;

-- 執行後請重跑 00_preflight_check.sql，確認狀態回到本工程開始前
-- （devices/alarms 約束型態、欄位清單與基準筆數應與 PLAN 1.2/1.3 節記載的原始現況一致）。
