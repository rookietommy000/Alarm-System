# variant 匯入工具（原廠格式 CLI）

`parse_alarms.py` 讀取原廠提供的警報清單（Excel/Word/PDF），偵測欄位、切分
code/variant，輸出成 `alarm_ingest` 共用模組吃得下的標準 JSON。用於陌生
原廠格式；批次匯入 UI（`backend/alarm_ingest`）只認固定範本，不做智慧偵測。

## fixtures/

原廠文件，含保密內容，不進版控。從對應機種的原廠文件取得後放這裡，
檔名建議保留原廠命名（去除不斷行空格等特殊字元）。

## output/

`parse_alarms.py` 的解析輸出（標準 JSON），內容含原廠警報描述，不進版控。

## 跑回歸測試

需要 `fixtures/` 下的原廠檔案才能跑，缺檔案時 pytest 會 skip（用
`pytest -rs` 可看到 skip 原因）。

```bash
python tools/variant/parse_alarms.py \
    -i tools/variant/fixtures/FILL203_batch_report_alarm_list_260529_ENG_revise.xlsx \
    -m FILL203 --variant-mode always --action-to local_solution --dry-run
# 預期：216 列 → 114 筆、28 個相異代碼
```
