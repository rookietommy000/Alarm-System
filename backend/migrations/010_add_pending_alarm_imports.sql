-- ============================================================
-- 010: 新增 pending_alarm_imports，供異常匯入資料先進待審清單、
-- 人工核准後才寫入 alarms 正式表
--
-- 背景：新部門上線批次匯入時，格式異常或無法自動判斷的資料，原本的
-- 討論一度傾向直接在 alarms 表加 status 欄位過濾，後來翻出既有先例
-- AlarmSuggestionStore（見 004_add_local_solution.sql 的 alarm_
-- suggestions 表）——本專案已經有「待審→核准後才生效」這個確切模式
-- 的完整實作與端到端驗證，改回獨立表方案（顧問/使用者 2026-09-02
-- 確認裁決）。
--
-- ⚠️ 跟 alarm_suggestions 的關鍵差異，schema 設計時已避開：
-- alarm_suggestions 用 foreign key (department, device_model, code)
-- references alarms，這個外鍵要求對應的 alarm 必須已經存在於 alarms
-- 表——因為 suggestion 的情境是「對既有警報提建議」。這次完全相反：
-- 待審資料指向的 (department, device_model, code, variant) 在 alarms
-- 表裡根本還不存在，這正是「異常/新資料要先審核」的本質。若照抄這個
-- 外鍵約束會直接擋住所有正常的待審資料寫入，因此本表**不對 alarms
-- 設任何外鍵**。
--
-- variant 欄位定案 not null default ''（醫生查證比對 alarm_suggestions
-- 先例：variant text not null default ''，理由一致——這批資料的兩個
-- 已知來源，人工批次匯入原廠 Excel 跟 AI 拍照辨識，都證實原始資料
-- 可能完全沒有 variant 概念，不強制要求提供）。
--
-- ⚠️ 已知風險（醫生指出，審核 UI 設計時務必納入）：空字串 variant 不是
-- 「不會碰撞」的保證。若同一個 (department, device_model, code) 在
-- 其他 variant 下已有真實資料（例如 mf4c 的 FILL203 有 17 組多 variant
-- 案例），核准時 upsert_one() 走複合主鍵，空字串本身是合法值，若 alarms
-- 表剛好也有 variant='' 的既有那一筆，會直接覆蓋掉它，不會報錯、不會
-- 跟其他 variant 衝突——審核者若沒意識到這點，可能誤以為核准後只是
-- 「新增」，其實是「覆蓋了某個特定 variant 版本」。審核 UI（摘要預覽）
-- 要明確標示 variant 為空的待審項目有此覆蓋風險。
--
-- 核准動作走既有 alarms_store.upsert_one()（不是 patch_one()）——這批
-- 資料在 alarms 表裡本來就不存在，是新增全新一列，跟 AlarmSuggestionStore
-- 的 accept（改既有列）情境不同。match 主鍵須含完整四欄
-- (department, device_model, code, variant)，upsert_one() 的
-- on_conflict 明確指定 "department,device_model,code,variant"（同
-- commit.py 的 CONFLICT_TARGET 既有寫法）。OPTIONAL_FIELDS
-- （severity/keywords/sol_steps）保護邏輯沿用 commit.py 的 _to_payload()
-- 既有實作，不重新發明一份。
--
-- 手動貼到 Supabase Dashboard SQL Editor 執行（本專案既有慣例，見
-- 006_add_variant.sql 開頭的說明：PostgREST 的 REST API 不支援 DDL）。
--
-- ⚠️ 本檔案只準備好，不自動執行 DDL——要等使用者本人確認執行時機。
-- ============================================================

begin;

create table pending_alarm_imports (
  id             bigint generated always as identity primary key,
  department     text not null,
  device_model   text not null,
  code           text not null,
  variant        text not null default '',

  -- 待寫入 alarms 的內容欄位，跟 alarms 表本身的欄位對齊，核准時
  -- 整批（扣除 OPTIONAL_FIELDS 缺席的部分）直接餵給 upsert_one()。
  description    text not null,
  severity       text,
  cause          text,
  solution       text,
  keywords       jsonb,
  sol_steps      jsonb,

  status         text not null default 'pending',  -- pending/approved/rejected，沿用既有 status 欄位風格
  source         text not null,   -- 'bulk_import' / 'ai_recognition' 等，供審核 UI 顯示資料來源
  flagged_reason text not null,   -- 為什麼被標記異常需要人工審核（格式不符/信心度低/機種不在白名單等）

  -- 原始輸入的字串快照（QA 2026-09-02 提出，非加不可）：只存最終
  -- normalize 後的值，審核者看不出轉換過程有沒有問題——CLAUDE.md 已知
  -- 陷阱：openpyxl 讀出的儲存格值可能是 int/float 而非 str，涉及 code
  -- 欄位一律先過 alarm_ingest/detect.py 的 _cell_to_str()，即使轉換
  -- 後字串看起來完全合法（例如 "0123"），也看不出原始儲存格是不是被
  -- 存成數字格式導致前導零被吃掉。最終值合法不代表轉換過程沒問題，
  -- 這正是待審機制存在的意義。批次匯入來源存 Excel 該列原始儲存格
  -- 內容；AI 辨識來源存模型回傳的原始 code 文字（正規化前）。單一
  -- text 欄位（不用 jsonb）：這是給人追溯讀的顯示用快照，不是要被
  -- 程式再次解析的結構化資料，兩種來源的「原始」形狀本來就不一樣，
  -- 統一存成人類可讀的文字反而比勉強套同一份 jsonb schema更直接。
  raw_source_text text,

  -- AI 辨識信心度數值（0-100），批次匯入來源為 null。跟 flagged_reason
  -- 的文字說明互補——「信心度過低」不如直接給數字，59% 跟 1% 需要
  -- 投入的核對心力完全不同。
  confidence     numeric,
  submitted_by   text,            -- 批次匯入時的操作者；AI 辨識來源可為 null
  submitted_at   timestamptz not null default now(),
  reviewed_by    text,
  reviewed_at    timestamptz,
  review_note    text,

  -- 刻意不對 alarms 設外鍵，理由見檔案開頭說明。department 本身仍
  -- 參照 departments，維持既有的部門存在性保證。
  foreign key (department) references departments(id)
);

create index idx_pending_alarm_imports_pending
  on pending_alarm_imports (department, status, submitted_at desc);

commit;

notify pgrst, 'reload schema';

-- ── 驗收查詢（執行後手動核對）──────────────────────────────
-- select table_name from information_schema.tables
-- where table_name = 'pending_alarm_imports';
-- 預期：存在
--
-- select count(*) from pending_alarm_imports;
-- 預期：0（新表，尚無資料）
--
-- select conname, pg_get_constraintdef(oid) from pg_constraint
-- where conrelid = 'pending_alarm_imports'::regclass;
-- 預期：只有 department 的外鍵，沒有指向 alarms 的外鍵
