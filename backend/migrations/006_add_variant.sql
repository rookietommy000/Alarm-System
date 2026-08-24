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
-- ⚠️ 執行前必做：先跑 006a_check_constraint_names.sql，把查到的
-- alarm_suggestions 外鍵約束名稱取代掉下面的 __ALARM_SUGGESTIONS_FK_NAME__
-- （兩處）。不要用猜的名稱。
--
-- 若忘記替換直接執行：DROP CONSTRAINT __ALARM_SUGGESTIONS_FK_NAME__ 會
-- 因為約束不存在而直接報錯中止（PostgreSQL 的識別字不能是佔位符字面
-- 值，這裡沒有另外寫 do $$ 檢查——約束名稱本身的「找不到就報錯」就是
-- 現成的中止機制，寫額外檢查反而是重複且容易寫錯的邏輯）。整段包在
-- 一個 transaction 內，這一行失敗會讓前面已執行的 ALTER TABLE 也一併
-- 回滾，不會留下半套變更。
--
-- 執行前置確認（2026-08-24 已查證，執行前建議再查一次，因為誰知道
-- 中間又累積了什麼）：
--   * alarm_suggestions 是空表（content-range: */0）——這是本腳本能
--     安全 drop/recreate 它的外鍵約束的前提
--   * alarms_pkey 目前是 PRIMARY KEY (department, device_model, code)
--     （003_switch_constraints.sql 建立，見該檔案開頭的約束名稱記錄）
-- ============================================================

begin;

-- variant 欄位：nullable 但用空字串當「無變體」的預設值，不用 NULL——
-- 主鍵欄位不能是 NULL（PostgreSQL 允許但語意上「兩個 NULL 不相等」
-- 在唯一約束比對上會出問題，複合主鍵含 NULL 欄位是已知的坑）。既有
-- 1759 筆義大利系機種代碼唯一，variant 一律為空字串，不影響顯示——
-- to_output() 已經是這樣輸出的（parse_alarms.py 第 425 行）。
alter table alarms add column variant text not null default '';

-- 主鍵切換：加入 variant。既有 1759 筆 variant 全是空字串，彼此仍然
-- 唯一，切換不會撞現有資料。
alter table alarms drop constraint alarms_pkey;
alter table alarms add constraint alarms_pkey primary key (department, device_model, code, variant);

-- alarm_suggestions 的外鍵指向 alarms 複合主鍵，主鍵改了外鍵要跟著換。
-- 該表目前是空表（審核路徑已停用，見 PLAN_local_solution.md），可以
-- 安全 drop 外鍵重建，不會有孤兒資料需要處理。suggestion 本身沒有
-- variant 概念（停用狀態，未來若重啟需重新評估），這裡只是讓外鍵能
-- 對上新主鍵形狀，不代表這張表本身要支援 variant。
alter table alarm_suggestions drop constraint __ALARM_SUGGESTIONS_FK_NAME__;
alter table alarm_suggestions add column variant text not null default '';
alter table alarm_suggestions
  add constraint __ALARM_SUGGESTIONS_FK_NAME__
  foreign key (department, device_model, code, variant)
  references alarms (department, device_model, code, variant) on delete cascade;

commit;

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
