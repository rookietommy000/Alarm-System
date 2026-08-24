-- ============================================================
-- 006: alarms 加 variant 欄位並納入主鍵（PLAN_variant 方案 C）
--
-- 背景：Bosch/Syntegon 體系（如 FILL203）的警報代碼是參數化訊息模板，
-- 一個 code 可對應十幾種實際情境，處置各不相同。既有主鍵
-- (department, device_model, code) 無法區分同一 code 底下的不同變體，
-- 若直接匯入這類來源，後面的 variant 會覆蓋前面的且不會有任何錯誤訊息
-- （parse_alarms.py 已在應用層擋過這個情境，但資料庫層本身也要能承載）。
--
-- 手動貼到 Supabase Dashboard SQL Editor 執行（本專案既有慣例，見
-- PLAN_department_isolation.md 第三十九輪：DDL 不自動化，PostgREST 的
-- REST API 不支援 DDL，要自動化只能走 RPC function，等於在資料庫裡
-- 開一個可執行任意 DDL 的後門，跟整套部門隔離靠應用層守的原則衝突）。
--
-- ✅ 已用 006a_check_constraint_names.sql 查證（2026-08-24）：
-- alarm_suggestions 指向 alarms 複合主鍵的外鍵真實名稱是
-- alarm_suggestions_department_device_model_code_fkey——下面兩處已經
-- 是這個查到的真實名稱，不是佔位符。查詢結果另外還有一條
-- alarm_suggestions_department_fkey，那是指向 departments 的外鍵，
-- 不是本腳本要動的那條，不要搞混。
--
-- 若這份文件是被複製去用在其他環境（例如未來新增類似的表結構），
-- 約束名稱不保證相同，執行前務必重新跑一次 006a 查證，不要照抄這裡
-- 寫死的名稱。
--
-- 執行前置確認（2026-08-24 已查證，執行前建議再查一次，因為誰知道
-- 中間又累積了什麼）：
--   * alarm_suggestions 是空表（content-range: */0）——這是本腳本能
--     安全 drop/recreate 它的外鍵約束的前提
--   * alarms_pkey 目前是 PRIMARY KEY (department, device_model, code)
--     （003_switch_constraints.sql 建立，見該檔案開頭的約束名稱記錄）
-- ============================================================

begin;

-- 第一次執行順序寫反了：alarm_suggestions 的外鍵約束依賴著
-- alarms_pkey 的索引，若先 drop 主鍵會撞 2BP01（cannot drop constraint
-- ... because other objects depend on it）。實際執行時撞到過這個錯誤，
-- 整個 transaction 自動 rollback（已確認 rollback 後資料庫狀態完好、
-- variant 欄位不存在、筆數不變）——這裡改成先 drop 依賴方（外鍵），
-- 才能 drop 被依賴的主鍵，最後依序重建。

-- 先 drop 外鍵約束（它依賴 alarms_pkey 的索引，必須在 drop 主鍵之前
-- 先移除，順序反了會撞 2BP01）。該表目前是空表（審核路徑已停用，見
-- PLAN_local_solution.md），可以安全 drop 重建，不會有孤兒資料需要
-- 處理。
alter table alarm_suggestions drop constraint alarm_suggestions_department_device_model_code_fkey;

-- variant 欄位：nullable 但用空字串當「無變體」的預設值，不用 NULL——
-- 主鍵欄位不能是 NULL（PostgreSQL 允許但語意上「兩個 NULL 不相等」
-- 在唯一約束比對上會出問題，複合主鍵含 NULL 欄位是已知的坑）。既有
-- 1759 筆義大利系機種代碼唯一，variant 一律為空字串，不影響顯示——
-- to_output() 已經是這樣輸出的（parse_alarms.py 第 425 行）。
alter table alarms add column variant text not null default '';
alter table alarm_suggestions add column variant text not null default '';

-- 主鍵切換：加入 variant。既有 1759 筆 variant 全是空字串，彼此仍然
-- 唯一，切換不會撞現有資料。外鍵已在上面 drop 掉，這裡可以安全換。
alter table alarms drop constraint alarms_pkey;
alter table alarms add constraint alarms_pkey primary key (department, device_model, code, variant);

-- 外鍵重建，對上新主鍵形狀。suggestion 本身沒有 variant 概念（停用
-- 狀態，未來若重啟需重新評估），這裡只是讓外鍵能對上新主鍵，不代表
-- 這張表本身要支援 variant。
alter table alarm_suggestions
  add constraint alarm_suggestions_department_device_model_code_fkey
  foreign key (department, device_model, code, variant)
  references alarms (department, device_model, code, variant) on delete cascade;

commit;

-- PostgREST 快取 schema，DDL 後不主動重新載入的話，即使欄位已經加了，
-- API 仍會回「欄位不存在」的 400，容易誤判成 DDL 沒生效。單獨一句、
-- 不在上面的 transaction 內執行也沒關係（這不是 DDL 本身，只是通知）。
notify pgrst, 'reload schema';

-- ── 驗收查詢（執行後手動核對）──────────────────────────────
-- select column_name, data_type, column_default, is_nullable
-- from information_schema.columns
-- where table_name = 'alarms' and column_name = 'variant';
-- 預期：text / '' / not null
--
-- select conname, pg_get_constraintdef(oid) from pg_constraint
-- where conrelid = 'alarms'::regclass and contype = 'p';
-- 預期：alarms_pkey PRIMARY KEY (department, device_model, code, variant)
--
-- select count(*) from alarms where variant <> '';
-- 預期：0（既有資料尚未匯入任何 variant，本腳本只加欄位不改資料）
--
-- select count(*) from alarms;
-- 預期：1759（跟切換前一致，本腳本不刪不改既有列）
