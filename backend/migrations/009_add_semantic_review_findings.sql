-- ============================================================
-- 009: 新增 semantic_review_findings，供全庫語意品質審核 UI 顯示
-- 303 筆 AI 語意疑慮清單（比照 008_add_variant_translations.sql 模式）
--
-- 背景：tools/variant/scan_semantic_quality.py + suggest_semantic_fixes.py
-- 離線工具產出的一次性語意疑慮清單，目前存在 data/semantic_scan_fixes.json
-- （整個 data/ 目錄被 .gitignore 排除，正式環境永遠讀不到這個檔案）。
-- 跟 008 的 variant_translations 是同一種「本機開發時用 JSON 檔方便，
-- production 需要 DB 才能真正上線」的落差，改存 DB 才能在正式環境
-- 被審核 UI 讀到、也才有多人協作校對（不是每次校對都要走 commit+部署）。
--
-- ⚠️ 這張表只負責「存放 findings 供顯示與審核」，不是套用機制本身。
-- 「採用並寫入」動作（把 suggested_zh/final_zh 寫進 alarms.description）
-- 目前仍暫緩，需要兩個前提都成立：
--   ① migration 007（import_snapshots，復原快照）在正式環境執行完成
--   ② 30 筆假陰性抽樣表由現場人員填完判斷
-- 見 project_semantic_review_findings_20260826 記憶檔的完整裁決記錄。
-- 這張表建好、資料搬進來，不代表上述前提被滿足——update_semantic_review()
-- 的 accept 分支已有獨立防呆（is_available() 檢查）擋住這件事，這裡的
-- DDL 執行時機不受那個防呆影響，可以先建表只是不能點採用。
--
-- 跟 variant_translations 的關鍵差異：variant_translations 是可共用的
-- 字典查找表（同一句英文可以對應多個 device_model/code），這張表則是
-- 「每一筆對應特定 (device_model, code) 的具體發現」，不能共用——
-- 已用 data/semantic_scan_fixes.json 實際資料查證：303 筆對應 303 組
-- 不重複的 (device_model, code)，可以直接拿這組當唯一鍵。
--
-- 這批資料本身沒有 variant 概念（審核清單是掃描工具產出，不知道
-- variant，只知道 device_model+code），也沒有 department 欄位——
-- 既有 API（list_semantic_review()）本來就不依 department 過濾，回傳
-- 整份清單给任何呼叫的部門看，這裡忠實反映既有行為，不在這次 migration
-- 順便改變過濾邏輯（那是獨立的功能決策，不在本次任務範圍）。
--
-- 手動貼到 Supabase Dashboard SQL Editor 執行（本專案既有慣例，見
-- 006_add_variant.sql 開頭的說明：PostgREST 的 REST API 不支援 DDL）。
--
-- ⚠️ 範圍限縮（2026-09-02 顧問/使用者裁決）：本檔案只準備好，不自動
-- 執行 DDL——要等使用者本人確認執行時機。
-- ============================================================

begin;

create table semantic_review_findings (
  id                     bigint generated always as identity primary key,
  device_model           text not null,
  code                   text not null,
  description            text not null,   -- 掃描當下的原文（含原廠英文+既有中文）
  issue                  text not null,   -- AI 對語意問題的說明（供審核者判斷）
  confidence             text not null,   -- AI 信心度："high"/"medium"/"low"，沿用既有 JSON 格式的字串值，不建 enum
  suggested_zh           text not null,   -- AI 建議的修正中文
  suggested_description  text not null,   -- AI 建議修正後的完整 description
  review_status          text not null default 'pending',  -- pending/accepted/rejected，沿用既有 JSON 格式的 status 欄位
  final_zh               text,             -- 審核者採用時的最終中文（可能覆寫 suggested_zh），未處理前為 null
  snapshot_id            bigint,           -- 採用時對應的復原快照 id，未採用或快照機制不可用時為 null。
                                            -- 刻意不設外鍵參照 import_snapshots(id)：那張表要 migration 007
                                            -- 執行後才存在，若這裡建外鍵約束，這張表的 DDL 就會被迫綁定
                                            -- 007 的執行順序，違背「303 筆 schema 先準備、套用時機分開」
                                            -- 的設計意圖（見上方防呆機制說明）。兩張表本來就允許獨立存在、
                                            -- 不保證同時就緒，一致性由應用層的 is_available() 檢查負責，
                                            -- 不依賴資料庫外鍵。
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),

  unique (device_model, code)
);

create index semantic_review_findings_status_idx on semantic_review_findings(review_status);
create index semantic_review_findings_device_model_idx on semantic_review_findings(device_model);

commit;

notify pgrst, 'reload schema';

-- ── 驗收查詢（執行後手動核對）──────────────────────────────
-- select table_name from information_schema.tables
-- where table_name = 'semantic_review_findings';
-- 預期：存在
--
-- select count(*) from semantic_review_findings;
-- 預期：0（新表，尚無資料，一次性搬移腳本尚未執行）
