-- ============================================================
-- 007: 新增 import_snapshots，供批次匯入「整批復原」使用
--
-- 背景：commit_rows()（backend/alarm_ingest/commit.py）是逐筆 upsert，
-- 對已存在的列是 merge（覆蓋舊值），不是新增。要能真正復原到匯入前
-- 的狀態，必須在每筆 upsert 之前先把「寫入前的值」記下來——不存在
-- 就記 NULL，undo 時對應刪除該筆；存在就記舊值，undo 時對應寫回。
-- 只記「這次新增了哪些 code」不夠：若某筆原本就存在、upsert 覆蓋了
-- 舊值，undo 只刪除的話舊資料就回不來了。
--
-- 一列一筆（不是把整批塞進一個 JSONB 陣列）：跟 alarm_history 的扁平化
-- 風格一致，也讓 undo 可以逐筆處理、部分失敗時知道還剩哪些筆沒處理完
-- （commit_rows() 本身也是遇錯即停的邏輯，undo 沿用同樣的容錯哲學）。
--
-- 手動貼到 Supabase Dashboard SQL Editor 執行（本專案既有慣例，見
-- 006_add_variant.sql 開頭的說明：PostgREST 的 REST API 不支援 DDL，
-- 自動化只能開一個可執行任意 DDL 的 RPC 後門，跟部門隔離全靠應用層
-- 守的原則衝突，不採用）。
-- ============================================================

begin;

-- 主表：一次 commit 操作一筆。department 與 device_models 只是給列表
-- UI 顯示用的摘要，不是查詢條件的唯一來源——真正決定復原範圍的是
-- import_snapshot_rows 裡的逐筆記錄。
create table import_snapshots (
  id            bigint generated always as identity primary key,
  department    text not null,
  device_models text not null,   -- 逗號分隔，UI 顯示用（同 commit.py 的 audit_logger.log 慣例）
  total_rows    integer not null,
  import_mode   text not null,
  created_at    timestamptz not null default now(),
  undone_at     timestamptz,     -- null 代表尚未復原；有值代表已復原，不可重複復原
  undone_result jsonb            -- 復原結果摘要（成功/失敗筆數），undo 執行後回填
);

-- 明細表：commit 前逐筆記下的「寫入前的值」。before_data 為 null 代表
-- 這筆在 commit 前不存在（undo 時要 delete），否則是完整的舊列內容
-- （undo 時要 upsert 回這個值）。
create table import_snapshot_rows (
  id            bigint generated always as identity primary key,
  snapshot_id   bigint not null references import_snapshots(id) on delete cascade,
  device_model  text not null,
  code          text not null,
  variant       text not null default '',
  before_data   jsonb   -- null = 復原時應刪除；非 null = 復原時應寫回此值
);

create index import_snapshot_rows_snapshot_id_idx on import_snapshot_rows(snapshot_id);
create index import_snapshots_department_idx on import_snapshots(department);

commit;

notify pgrst, 'reload schema';

-- ── 驗收查詢（執行後手動核對）──────────────────────────────
-- select table_name from information_schema.tables
-- where table_name in ('import_snapshots', 'import_snapshot_rows');
-- 預期：兩張表都存在
--
-- select count(*) from import_snapshots;
-- select count(*) from import_snapshot_rows;
-- 預期：兩者皆 0（新表，尚無資料）
