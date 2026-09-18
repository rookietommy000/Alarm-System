-- ============================================================
-- 011: 新增 department_audit_log，記錄部門管理端點（改名/密碼重設/
-- 啟用停用）的稽核軌跡——這三個端點目前完全沒有 audit log（外部審查
-- 2026-09-02 路由重導向調查時發現的獨立缺口，非本次新增問題，使用者
-- 裁決另開一輪處理，見 docs/worklog/2026-09.md）。
--
-- 為什麼是獨立表，不塞進既有 alarm_history：alarm_history 的 schema
-- 是以 alarms 業務資料為中心（entry 固定帶 code 欄位），部門管理操作
-- 沒有這個概念，硬塞會產生語意不吻合（比照 pending_alarm_imports 不
-- 硬套 alarm_suggestions schema 的既有先例，見 010 的說明）。
--
-- ⚠️ reset_password 動作只記「發生過」這個粗粒度事實跟時間，不記是
-- password 還是 admin_password 哪個欄位被改，絕對不記明文或雜湊值
-- （雜湊值本身也不能記，等於多開一個攻擊面）——這是老師設計方案裡
-- 明確要求的紅線，記錄粒度寧可粗也不能碰觸密碼相關的任何實際內容。
--
-- 操作者：這三個端點只有 superadmin 能碰（superadmin_required），
-- 記固定標記 'superadmin' 即可，不需要更細的身份欄位——本專案目前
-- 沒有個人帳號機制，superadmin 是共用密碼，記更細的欄位也無法追溯
-- 到實際操作的是哪個人。
--
-- 這輪只做寫入，不做讀取端點/UI（使用者裁決，先把記錄累積起來，之後
-- 有需要再做讀取）。
--
-- 手動貼到 Supabase Dashboard SQL Editor 執行（本專案既有慣例，見
-- 006_add_variant.sql 開頭的說明：PostgREST 的 REST API 不支援 DDL）。
--
-- ⚠️ 本檔案只準備好，不自動執行 DDL——要等使用者本人確認執行時機。
-- ============================================================

begin;

create table department_audit_log (
  id             bigint generated always as identity primary key,
  department     text not null,
  action         text not null,   -- 'rename' / 'reset_password' / 'active'
  actor          text not null default 'superadmin',

  -- rename：記 before/after 部門名稱。reset_password：兩欄皆 null
  -- （不記密碼相關的任何實際內容，見檔案開頭說明）。active：記
  -- before/after 的布林值（用 text 存 'true'/'false'，避免這張表為了
  -- 單一動作類型多開一個 boolean 欄位——這張表本來就是稀疏記錄，
  -- 不同 action 用到的欄位不同，是合理的設計，不是偷懶）。
  before_value   text,
  after_value    text,

  created_at     timestamptz not null default now(),

  -- 刻意不對 departments 設外鍵——部門被 purge 後，這張表的歷史記錄
  -- 應該保留（稽核軌跡的價值就在於留存，不該因為部門被刪除就跟著
  -- cascade 刪掉），比照 alarm_history 對已刪除警報的保留原則。
  check (action in ('rename', 'reset_password', 'active'))
);

create index idx_department_audit_log_department
  on department_audit_log (department, created_at desc);

commit;

notify pgrst, 'reload schema';

-- ── 驗收查詢（執行後手動核對）──────────────────────────────
-- select table_name from information_schema.tables
-- where table_name = 'department_audit_log';
-- 預期：存在
--
-- select count(*) from department_audit_log;
-- 預期：0（新表，尚無資料）
--
-- select conname, pg_get_constraintdef(oid) from pg_constraint
-- where conrelid = 'department_audit_log'::regclass;
-- 預期：只有 action 的 check 約束，沒有指向 departments 的外鍵
