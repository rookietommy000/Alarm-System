"""端點授權及 payload 單元測試；不宣稱驗證真實 Supabase 租戶隔離。"""
import io
import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest


BODY = {"device_model": "CNC-A100", "code": "E001", "variant": "", "content": "原因欄位有誤"}
URL = "/api/data-issue-reports/local"


def test_submit_records_server_identity(client):
    r = client.post(URL, json={**BODY, "reporter": "forged", "created_at": "forged"})
    assert r.status_code == 201
    records = json.loads((Path(os.environ['ALARM_DATA_DIR']) / 'data_issue_reports.json').read_text())
    assert len(records) == 1
    assert records[0].items() >= BODY.items()
    assert records[0]['department'] == 'local'
    assert records[0]['reporter'] == 'local/admin'
    assert datetime.fromisoformat(records[0]['created_at']).tzinfo is not None


@pytest.mark.parametrize('field', BODY)
def test_required_fields(client, field):
    body = {k: v for k, v in BODY.items() if k != field}
    assert client.post(URL, json=body).status_code == 400


@pytest.mark.parametrize('body', [[], None, {**BODY, 'content': ' '}, {**BODY, 'code': 3},
                                  {**BODY, 'variant': None}, {**BODY, 'content': 'a' * 2001}])
def test_invalid_body(client, body):
    assert client.post(URL, json=body).status_code == 400


def test_login_required(anon_client):
    assert anon_client.post(URL, json=BODY).status_code == 401


def test_path_department_and_full_pk_forwarded(client, monkeypatch):
    import app
    lookup = Mock(return_value=BODY)
    append = Mock()
    monkeypatch.setattr(app.alarms_store, 'get_one', lookup)
    monkeypatch.setattr(app.data_issue_report_store, 'append', append)
    assert client.post(URL + '?department=other&dept=other', json=BODY).status_code == 201
    lookup.assert_called_once_with(department='local', match={k: BODY[k] for k in ('device_model', 'code', 'variant')})
    assert append.call_args.kwargs == {'department': 'local'}
    assert client.post('/api/data-issue-reports/other', json=BODY).status_code == 404
    assert client.post(URL, json={**BODY, 'department': 'other'}).status_code == 400
    assert append.call_count == 1


def test_missing_alarm_or_variant_rejected(client):
    assert client.post(URL, json={**BODY, 'code': 'missing'}).status_code == 404
    assert client.post(URL, json={**BODY, 'variant': 'different'}).status_code == 404


def test_write_failure_not_success(client, monkeypatch, caplog):
    import app
    monkeypatch.setattr(app.data_issue_report_store, 'append', Mock(side_effect=RuntimeError('unavailable')))
    assert client.post(URL, json=BODY).status_code == 503
    assert '資料異常回報儲存失敗' in caplog.text


def test_store_uses_guarded_http(client, monkeypatch):
    import storage
    monkeypatch.setattr(storage, '_use_supabase', lambda: True)
    opener = Mock()
    opener.return_value.__enter__ = Mock()
    opener.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(storage, '_urlopen', opener)
    storage.DataIssueReportStore().append(BODY, department='local')
    req = opener.call_args.args[0]
    assert req.full_url.endswith('/rest/v1/data_issue_reports')
    assert req.get_method() == 'POST'
    assert json.loads(req.data)['department'] == 'local'
    opener.side_effect = RuntimeError('offline')
    with pytest.raises(RuntimeError, match='offline'):
        storage.DataIssueReportStore().append(BODY, department='local')


def test_route_b_preserves_absent_source(client):
    import app
    match = {k: BODY[k] for k in ('device_model', 'code', 'variant')}
    source = {'import_source': 'original.xlsx', 'imported_at': '2026-09-24T01:00:00+00:00'}
    app.alarms_store.patch_one(match=match, patch=source, department='local')
    csv = 'code,device_model,variant,description,cause,solution,local_solution\nE001,CNC-A100,,updated,c,s,\n'
    r = client.post('/api/admin/bulk-import/local/commit', data={
        'file': (io.BytesIO(csv.encode()), 'existing.csv'), 'import_mode': 'upsert',
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['succeeded'] == 1
    row = app.alarms_store.get_one(department='local', match=match)
    assert row.items() >= source.items()
    assert row['description'] == 'updated'


def test_parser_source_presence():
    from alarm_ingest.parse import row_to_alarm, OPTIONAL_FIELDS, ALARM_FIELDS
    from alarm_ingest.commit import _to_payload
    source = {'import_source': 'original.xlsx', 'imported_at': '2026-09-24T01:00:00Z'}
    assert set(source) <= OPTIONAL_FIELDS
    assert set(source) <= set(ALARM_FIELDS)
    assert not set(source) & _to_payload(row_to_alarm(BODY)).keys()
    assert _to_payload(row_to_alarm({**BODY, **source})).items() >= source.items()
    assert _to_payload(row_to_alarm({**BODY, 'imported_at': ''}))['imported_at'] is None
