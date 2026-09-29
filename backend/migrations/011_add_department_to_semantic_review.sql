-- ============================================================
-- 011: 語意審核清單加入 department，修復跨部門資料外洩
-- 手動貼到 Supabase Dashboard SQL Editor 執行，不自動執行。
-- 已人工查證現有 303 筆均唯一對應 mf4d；SQL 仍獨立驗證假設。
-- 請先完成本 migration 再部署依賴 department 欄位的程式。
-- 任一防呆失敗會回滾整個交易，需人工判斷，不猜測補值。
-- ============================================================
begin;

alter table semantic_review_findings add column department text;

-- UPDATE FROM 多行匹配會任選一行，故先拒絕跨部門歧義。
do $$
declare ambiguous_count int;
begin
  select count(*) into ambiguous_count from (
    select f.device_model, f.code
    from semantic_review_findings f
    join alarms a on a.device_model = f.device_model and a.code = f.code
    group by f.device_model, f.code
    having count(distinct a.department) > 1
  ) t;
  if ambiguous_count > 0 then
    raise exception '% 組(device_model,code)對應到多個department，backfill無法無歧義判斷，需人工介入', ambiguous_count;
  end if;
end $$;

update semantic_review_findings f
set department = a.department
from alarms a
where a.device_model = f.device_model and a.code = f.code
  and f.department is null;

do $$
begin
  if exists (select 1 from semantic_review_findings where department is null) then
    raise exception 'backfill 後仍有 department 為 null，需人工介入';
  end if;
end $$;

alter table semantic_review_findings alter column department set not null;
alter table semantic_review_findings drop constraint semantic_review_findings_device_model_code_key;
alter table semantic_review_findings add constraint semantic_review_findings_department_device_model_code_key
  unique (department, device_model, code);
create index semantic_review_findings_department_idx on semantic_review_findings(department);

commit;
notify pgrst, 'reload schema';

-- 驗收查詢（執行後手動核對）
-- select count(*) from semantic_review_findings where department is null; -- 預期 0
-- select department, count(*) from semantic_review_findings group by department; -- 預期只有 mf4d，303 筆
