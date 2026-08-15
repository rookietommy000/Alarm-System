-- ============================================================
-- 004: 現場處置做法（local_solution）階段 1 資料庫改動
-- 對應 PLAN_local_solution.md 第三節
--
-- 零行為變更：全部新增 nullable 欄位/新表，不動任何既有欄位、
-- 不影響既有查詢。與多部門隔離工程的遷移腳本一樣，手動貼到
-- Supabase Dashboard SQL Editor 執行（本專案刻意不裝 psql/直連
-- 字串，見 PLAN_department_isolation.md 第三十九輪）。
-- ============================================================

begin;

-- alarms 加四個欄位，全部 nullable。local_solution/local_reason 是
-- 現場做法本身；local_updated_by 由伺服器端組成 department/role
-- （不信任前端傳值，見 PLAN_local_solution.md 4.4 節）；
-- local_updated_at 是最後更新時間，供前端顯示「上次更新於」。
alter table alarms add column local_solution   text;
alter table alarms add column local_reason     text;
alter table alarms add column local_updated_by text;
alter table alarms add column local_updated_at timestamptz;

-- 一般使用者提交的建議，不直接寫入 alarms，等管理員審核後才寫入
-- local_solution（PLAN_local_solution.md 2.2 節權限界線）。
-- 複合外鍵指向 alarms 現有的複合主鍵 (department, device_model, code)，
-- 防止建議指向不存在的警報；on delete cascade 是刻意的——警報都
-- 刪了，針對它的建議沒有保留意義。
create table alarm_suggestions (
  id            bigserial primary key,
  department    text not null references departments(id),
  device_model  text not null,
  code          text not null,
  suggestion    text not null,
  reason        text,
  submitted_by  text not null,
  submitted_at  timestamptz not null default now(),
  status        text not null default 'pending',
  reviewed_by   text,
  reviewed_at   timestamptz,
  review_note   text,
  ai_grade      text,
  ai_notes      text,
  foreign key (department, device_model, code)
    references alarms (department, device_model, code) on delete cascade
);

create index idx_suggestions_pending
  on alarm_suggestions (department, status, submitted_at desc);

commit;

-- ── 驗收查詢（執行後手動核對）──────────────────────────────
-- select column_name, data_type, is_nullable
-- from information_schema.columns
-- where table_name = 'alarms' and column_name like 'local_%'
-- order by column_name;
--
-- select count(*) from alarm_suggestions;  -- 應為 0（新表，空的）
