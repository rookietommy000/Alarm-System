-- ============================================================
-- 006a: 查詢 006_add_variant.sql 需要用到的真實約束名稱
--
-- 純查詢，不修改任何資料或結構，可以隨時安全重跑。
-- 在 Supabase Dashboard SQL Editor 執行，把 conname 那欄的值抄到
-- 006_add_variant.sql 裡標示 __ALARM_SUGGESTIONS_FK_NAME__ 的地方。
--
-- 為什麼不能猜：PostgreSQL 的預設命名慣例不保證每次都一樣（受限定
-- 長度、複合外鍵的 constraint 命名規則影響），003_switch_constraints.sql
-- 開頭也是靠實際查詢核對過名稱才動工，不是憑推測——這個專案已經在
-- devices 表欄位名稱這件事上吃過「憑推測寫 SQL 結果跟正式庫不符」的
-- 教訓，約束名稱同樣不該用猜的。
-- ============================================================

select conname, pg_get_constraintdef(oid)
from pg_constraint
where conrelid = 'alarm_suggestions'::regclass and contype = 'f';

-- 預期看到一列，是 004_add_local_solution.sql 建立的那個複合外鍵
-- （foreign key (department, device_model, code) references alarms ...）。
-- 把 conname 欄的值抄到 006_add_variant.sql。
