-- 002_migrate_add_departments.sql
-- 多部門隔離工程 階段 2：遷移腳本（對應 PLAN 1.7 節）
--
-- 前置條件：001_add_department_columns.sql 已執行成功。
--
-- 執行前必須先做重複值預檢（PLAN 1.7 節步驟1），確認回傳 0 筆：
--   select device_model, code, count(*) from alarms group by device_model, code having count(*) > 1;
-- （00_preflight_check.sql 已於第十一輪確認過 0 筆，執行前建議重跑一次確保現況未變）
--
-- 執行方式：psql "$SUPABASE_DB_URL" -f 002_migrate_add_departments.sql
-- 整支包在單一交易內（PLAN 1.6 節步驟5要求）。
--
-- 密碼雜湊已用 backend/gen_department_hashes.py 產生並貼入下方
-- （沿用現有 .env 的 LOGIN_PASSWORD / ADMIN_PASSWORD，遷移後既有使用者密碼繼續有效）。
-- 注意：本機/Render 皆為 Python 3.9，hashlib 無 scrypt 支援，werkzeug 預設演算法會噴
-- AttributeError；gen_department_hashes.py 已改用 method="pbkdf2:sha256"，見 PLAN 第十八輪。

begin;

-- 步驟2：建立第一個部門（PLAN 第十四輪定案：id=mf4d, name=製造四部包裝組）
insert into departments (id, name, pw_hash, admin_pw_hash, hidden, purgeable)
values (
  'mf4d',
  '製造四部包裝組',
  'pbkdf2:sha256:1000000$GZjxTrfefVpJuCOA$d8bae49e6fe73305839e7d1229a03bf07070834da9cd8f4b90485243db4a0a55',
  'pbkdf2:sha256:1000000$jz3recr8czHIKNzY$63e128ca60fbf632e646d98e43656c837393e8a7fc562ee52888120f52b4e23f',
  false,
  false
)
on conflict (id) do nothing;

-- 步驟3：回填 devices.department（全部 14 台機種）
update devices set department = 'mf4d' where department is null;

-- 步驟4：回填 alarms.department（全部 1759 筆警報）
update alarms set department = 'mf4d' where department is null;

-- 步驟5：feedback/alarm_views 歷史資料 best-effort 透過 device_model → devices.department 反查回填
-- 查不到的（device_model 對不到現存 devices）留 NULL，PLAN 1.5 節明確決定不強制指派、不刪除
update feedback f
set department = d.department
from devices d
where f.device_model = d.model
  and f.department is null;

update alarm_views v
set department = d.department
from devices d
where v.device_model = d.model
  and v.department is null;

-- 步驟6：印出處理筆數與驗證結果（NULL 計數）
select 'departments' as tbl, count(*) as rows from departments
union all
select 'devices_null_dept', count(*) from devices where department is null
union all
select 'alarms_null_dept', count(*) from alarms where department is null
union all
select 'feedback_null_dept', count(*) from feedback where department is null
union all
select 'alarm_views_null_dept', count(*) from alarm_views where department is null;

commit;

-- 驗收標準（PLAN 1.7 節）：
--   devices_null_dept = 0   -> 才能執行 003 收緊 devices.department 為 NOT NULL
--   alarms_null_dept  = 0   -> 才能執行 003 收緊 alarms.department 為 NOT NULL
--   feedback_null_dept / alarm_views_null_dept 可以 > 0（PLAN 1.5 節明確允許保留 NULL）
--
-- 驗收通過後，依 PLAN 1.6 節步驟7，執行一次 post_backfill pg_dump 作為新的還原點：
--   pg_dump "$SUPABASE_DB_URL" -Fc --no-owner --no-privileges \
--     -f post_backfill_$(date +%Y%m%d_%H%M).dump
--   pg_restore -l post_backfill_*.dump | head
