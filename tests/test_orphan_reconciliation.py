"""JsonStore 僅驗單租戶掃描；不能驗證跨部門 B 類真實情境或資料庫隔離。
PostgREST 測試使用 stub 檢查請求，不代表真實 Supabase 黑箱驗收。
"""
import copy
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs
from unittest.mock import Mock

import pytest


@pytest.fixture
def super_client(anon_client, monkeypatch):
    import storage
    monkeypatch.setattr(storage, '_urlopen', lambda *a, **kw: pytest.fail('禁止真實網路'))
    with anon_client.session_transaction() as session:
        session['superadmin'] = True
    return anon_client


@pytest.mark.parametrize('role', ['anonymous', 'user', 'admin'])
def test_permissions(anon_client, role):
    with anon_client.session_transaction() as session:
        if role != 'anonymous':
            session['auth'] = True
        if role == 'admin':
            session['admin'] = True
    assert anon_client.get('/api/admin/orphans').status_code == 403
    assert anon_client.post('/api/admin/orphans/purge', json={'confirm_token': []}).status_code == 403


@pytest.mark.parametrize('value', ['', '0', '-1', '1.5', 'abc', '３', '999999999999'])
def test_invalid_days(super_client, value):
    for method, path in [('get', '/api/admin/orphans'), ('post', '/api/admin/orphans/purge')]:
        assert getattr(super_client, method)(path, query_string={'pending_days': value}).status_code == 400


def seed():
    app = sys.modules['app']
    rows = [dict(device_model='CNC-A100', code='normal', variant=''),
            dict(device_model='', code='bad', variant='v1'),
            dict(device_model='missing', code='orphan', variant=''),
            dict(device_model='missing', code='', variant='')]
    app.alarms_store.save(rows, 'test')
    app.devices_store.save([dict(model='CNC-A100'), dict(model='')], 'test')
    return app


def test_orphan_scan_then_cleanup(super_client):
    app = seed()
    base = '/api/admin/orphans'
    response = super_client.get(base).get_json()
    assert response['counts'] == dict(A=3, B=0, C=0)
    assert 'B_skipped' in response
    response = super_client.get(base + '?department=test').get_json()
    assert response['counts'] == dict(A=3, B=1, C=0)
    assert {'device_model': '', 'code': 'bad', 'variant': 'v1'} in [r['pk'] for r in response['orphans']]
    result = super_client.post(base + '/purge?department=test', json={'confirm_token': response['orphans']})
    assert result.status_code == 200
    assert result.get_json() == dict(ok=True, deleted=4, rejected=0)
    assert [r['code'] for r in app.alarms_store.load('test')] == ['normal']
    assert [r['model'] for r in app.devices_store.load('test')] == ['CNC-A100']


def test_changed_pk_same_count_rejected(super_client):
    app = seed()
    url = '/api/admin/orphans?department=test'
    snapshot = super_client.get(url).get_json()['orphans']
    rows = app.alarms_store.load('test')
    rows[1]['code'] = 'changed'
    app.alarms_store.save(rows, 'test')
    result = super_client.post('/api/admin/orphans/purge?department=test', json={'confirm_token': snapshot})
    assert result.status_code == 409
    assert result.get_json()['mismatched']['confirmed'] != result.get_json()['mismatched']['actual']
    assert len(app.alarms_store.load('test')) == 4


@pytest.mark.parametrize('body', [None, [], {}, {'confirm_token': 'hash'}, {'confirm_token': [1]}])
def test_bad_token(super_client, body):
    assert super_client.post('/api/admin/orphans/purge', json=body).status_code == 400


def test_pending_rejected_retained(super_client, monkeypatch):
    app = sys.modules['app']
    now = datetime.now(timezone.utc)
    rows = [dict(id=1, department='test', submitted_at=(now-timedelta(days=31)).isoformat(), status='pending'),
            dict(id=2, department='test', submitted_at=now.isoformat(), status='pending')]
    monkeypatch.setattr(app.department_store, 'get_by_id', lambda dept: {'active': True})
    monkeypatch.setattr(app.pending_alarm_import_store, 'list_pending', lambda dept: [r for r in rows if r['status']=='pending'])
    def claim(row_id, **kw):
        assert kw['from_status'] == 'pending' and kw['to_status'] == 'rejected'
        row = next(r for r in rows if r['id'] == row_id)
        row['status'] = kw['to_status']
        return row
    monkeypatch.setattr(app.pending_alarm_import_store, 'claim', claim)
    snapshot = super_client.get('/api/admin/orphans').get_json()
    assert snapshot['counts']['C'] == 1
    assert super_client.get('/api/admin/orphans?pending_days=40').get_json()['counts']['C'] == 0
    result = super_client.post('/api/admin/orphans/purge', json={'confirm_token': snapshot['orphans']})
    assert result.get_json()['rejected'] == 1
    assert len(rows) == 2 and rows[0]['status'] == 'rejected'
    monkeypatch.setattr(app.department_store, 'get_by_id', lambda dept: None)
    assert super_client.get('/api/admin/orphans').get_json()['counts']['C'] == 1
    monkeypatch.setattr(app.department_store, 'get_by_id', lambda dept: {'active': False})
    assert super_client.get('/api/admin/orphans').get_json()['counts']['C'] == 1


def test_postgrest_exact_pk_and_truncation(super_client, monkeypatch):
    import storage
    alarms = storage.SupabaseStore('alarms', pk_fields=['department', 'device_model', 'code', 'variant'])
    devices = storage.SupabaseStore('devices', pk_fields=['department', 'model'])
    row = dict(department='test', device_model='', code='a&b,."', variant='v1')
    calls = []
    def req(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return [row]
    monkeypatch.setattr(alarms, '_req', req)
    monkeypatch.setattr(devices, '_req', lambda *a, **kw: [])
    empty = SimpleNamespace(list_pending=lambda dept: [])
    scanner = storage.OrphanScanner(alarms, devices, None, empty, empty)
    result = scanner.scan(None, 30)
    assert 'or=(device_model.eq.,code.eq.)' in calls[0][1]
    assert 'limit=1000' in calls[0][1]
    assert scanner.purge(None, 30, result['orphans'])['deleted'] == 1
    method, path, kwargs = calls[-1]
    assert method == 'DELETE'
    qs = parse_qs(path.split('?')[1], keep_blank_values=True)
    assert set(qs) == {'department', 'device_model', 'code', 'variant'}
    assert qs['device_model'] == ['eq.'] and qs['variant'] == ['eq.v1']
    assert kwargs['extra_headers']['Prefer'] == 'return=representation'
    monkeypatch.setattr(alarms, '_req', lambda *a, **kw: [row]*1000)
    with pytest.raises(RuntimeError, match='上限'):
        scanner.scan(None, 30)


def test_cascade_conflict_before_mutation(super_client, monkeypatch):
    app = seed()
    suggestion = dict(id=1, department='test', device_model='', code='bad', variant='v1',
                      submitted_at='2020-01-01T00:00:00Z')
    monkeypatch.setattr(app.alarm_suggestion_store, 'list_pending', lambda dept: [suggestion])
    monkeypatch.setattr(app.alarm_suggestion_store, 'get_by_id', lambda row_id: suggestion)
    claim = Mock(return_value={**suggestion, 'status': 'rejected'})
    monkeypatch.setattr(app.alarm_suggestion_store, 'claim', claim)
    monkeypatch.setattr(app.department_store, 'get_by_id', lambda dept: {'active': True})
    snapshot = super_client.get('/api/admin/orphans?department=test').get_json()['orphans']
    result = super_client.post('/api/admin/orphans/purge?department=test', json={'confirm_token': snapshot})
    assert result.status_code == 409
    assert 'CASCADE' in result.get_json()['mismatched']['actual']
    assert result.get_json()['processed'] == dict(deleted=0, rejected=0)
    claim.assert_not_called()
    assert len(app.alarms_store.load('test')) == 4
