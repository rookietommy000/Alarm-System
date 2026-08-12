-- 001_add_department_columns.sql
-- 多部門隔離工程 階段 1：建表 + 加欄位（對應 PLAN 1.1、1.2、1.3、1.4、2.2.1）
-- 全部 nullable，零行為變更；departments/login_attempts 為新表。
--
-- 執行方式：psql "$SUPABASE_DB_URL" -f 001_add_department_columns.sql
-- 整支包在單一交易內（PLAN 1.6 節步驟5要求）。

begin;

-- === 1.1 節：departments 表 ===
create table if not exists departments (
  id              text primary key,
  name            text not null,
  pw_hash         text not null,
  admin_pw_hash   text not null,
  session_version integer not null default 1,
  active          boolean not null default true,
  hidden          boolean not null default false,
  purgeable       boolean not null default false,
  created_at      timestamptz not null default now()
);

-- === 2.2.1 節：login_attempts 表 ===
-- department 刻意不加 FK：要能記錄「嘗試登入不存在的部門」，這是有價值的稽核訊號。
create table if not exists login_attempts (
  id           bigserial primary key,
  ip           text not null,
  department   text,
  success      boolean not null,
  attempted_at timestamptz not null default now()
);

create index if not exists idx_login_attempts_lookup
  on login_attempts (ip, department, attempted_at desc);

-- === 1.2 節：devices 加 department（欄位實際叫 model，不是 device_model，見 PLAN 1.2 節） ===
alter table devices add column if not exists department text references departments(id);
create index if not exists idx_devices_department on devices(department);

-- === 1.3 節：alarms 加 department ===
alter table alarms add column if not exists department text references departments(id);
create index if not exists idx_alarms_department on alarms(department);

-- === 1.4 節：其餘表加 department（全部 nullable + index） ===
alter table ai_scans       add column if not exists department text;
alter table ai_corrections add column if not exists department text;
alter table ai_logs        add column if not exists department text;
alter table alarm_history  add column if not exists department text;
alter table feedback       add column if not exists department text;
alter table alarm_views    add column if not exists department text;

create index if not exists idx_ai_scans_department       on ai_scans(department);
create index if not exists idx_ai_corrections_department on ai_corrections(department);
create index if not exists idx_alarm_history_department  on alarm_history(department);
create index if not exists idx_feedback_department       on feedback(department);
create index if not exists idx_alarm_views_department    on alarm_views(department);

commit;

-- 執行後請跑一次 00_preflight_check.sql，並用以下 SQL 確認欄位/索引都已建立：
-- select table_name, column_name from information_schema.columns
--   where column_name = 'department' order by table_name;
