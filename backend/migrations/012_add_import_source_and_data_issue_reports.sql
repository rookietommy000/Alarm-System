-- 僅準備 DDL，由操作員手動執行；不自動套用。
-- 資料內容異常不同於 feedback 的處置效果，故建立獨立回報表。
-- reporter 使用部門/角色：現行系統為共用帳號，無法識別個人。
begin;

alter table alarms
  add column import_source text,
  add column imported_at timestamptz;

create table data_issue_reports (
  id bigint generated always as identity primary key,
  department text not null,
  device_model text not null,
  code text not null,
  variant text not null,
  content text not null check (length(trim(content)) between 1 and 2000),
  reporter text not null,
  created_at timestamptz not null default now()
);
-- 保留回報歷史，不隨警報或部門刪除而 cascade。
create index idx_data_issue_reports_department_created_at
  on data_issue_reports (department, created_at desc);
alter table data_issue_reports enable row level security;

commit;
notify pgrst, 'reload schema';
