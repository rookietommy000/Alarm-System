-- ============================================================
-- 005: alarm_suggestions 加部分唯一索引，防止同一筆警報重複提交
-- 對應 PLAN_local_solution.md 4.2 節、外部審查第四輪
--
-- 只約束 status='pending'，已審核的歷史記錄不受影響（同一筆警報
-- 可以有多筆已 accepted/rejected 的歷史）。應用層的 check-then-insert
-- 仍保留（給友善的 409 訊息），這個約束是保險——兩人同時提交或
-- 同一人連點兩下的競態，只有資料庫層能真正防住。
-- ============================================================

begin;

create unique index uniq_pending_suggestion
on alarm_suggestions (department, device_model, code)
where status = 'pending';

commit;
