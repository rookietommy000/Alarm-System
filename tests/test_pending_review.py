"""聚合端點的組裝/授權測試；mock Store，不宣稱驗證 Supabase 隔離。"""
from copy import deepcopy

import pytest


@pytest.fixture
def sources(client, monkeypatch):
    import storage

    calls = []
    suggestions = [dict(id=1, department='local', code='S', status='pending')]
    imports = [dict(id=1, department='local', code='I', status='pending')]
    findings = [dict(code='done', status='accepted'), dict(code='R', status='pending'),
                dict(code='skip', status='rejected')]
    for kind, store, rows in [('suggestion', storage.alarm_suggestion_store, suggestions),
                              ('pending_import', storage.pending_alarm_import_store, imports)]:
        def pending(*, department, kind=kind, rows=rows):
            calls.append((kind, department))
            return rows
        monkeypatch.setattr(store, 'list_pending', pending)
    monkeypatch.setattr(storage.semantic_review_store, 'load_all', lambda: findings)
    return calls, suggestions, imports, findings


def test_merge_sources_preserves_semantic_index_and_original_rows(client, sources):
    before = deepcopy(sources[1:])
    response = client.get('/api/admin/pending-review')
    assert response.status_code == 200
    items = response.get_json()['items']
    assert [r['source_type'] for r in items] == ['suggestion', 'pending_import', 'semantic_review']
    assert [r['code'] for r in items] == ['S', 'I', 'R']
    assert items[2]['review_index'] == 1
    assert 'department' not in items[2]  # 既有全庫清單，不能偽裝成部門專屬
    assert sources[1:] == before
    assert sources[0] == [('suggestion', 'local'), ('pending_import', 'local')]


@pytest.mark.parametrize('query,expected', [('', None), ('?dept=__all__', None), ('?dept=line2', 'line2')])
def test_superadmin_passes_requested_scope_to_stores(client, sources, query, expected):
    with client.session_transaction() as session:
        session['superadmin'] = True
        session['department'] = None
    assert client.get('/api/admin/pending-review' + query).status_code == 200
    assert sources[0] == [('suggestion', expected), ('pending_import', expected)]


def test_department_admin_uses_session_not_query(client, sources):
    assert client.get('/api/admin/pending-review?dept=another').status_code == 200
    assert sources[0] == [('suggestion', 'local'), ('pending_import', 'local')]


@pytest.mark.parametrize('authenticated', [False, True])
def test_non_admin_cannot_read_list_or_page(anon_client, authenticated):
    with anon_client.session_transaction() as session:
        session['auth'] = authenticated
        session['department'] = 'local'
    assert anon_client.get('/api/admin/pending-review').status_code == 403
    assert anon_client.get('/admin/pending-review').status_code == 302


def test_missing_department_fails_closed(client, sources):
    with client.session_transaction() as session:
        session.pop('department')
    assert client.get('/api/admin/pending-review').status_code == 401
    assert sources[0] == []


def test_empty_list(client, sources):
    for rows in sources[1:]:
        rows.clear()
    assert client.get('/api/admin/pending-review').get_json() == {'items': []}


@pytest.mark.parametrize('store_name,method', [('alarm_suggestion_store', 'list_pending'),
                                             ('pending_alarm_import_store', 'list_pending'),
                                             ('semantic_review_store', 'load_all')])
def test_source_failure_is_not_reported_as_empty(client, sources, monkeypatch, store_name, method):
    import storage

    def fail(*args, **kwargs):
        raise RuntimeError('source unavailable')
    monkeypatch.setattr(getattr(storage, store_name), method, fail)
    with pytest.raises(RuntimeError, match='source unavailable'):
        client.get('/api/admin/pending-review')


def test_page_served_without_cache_and_direct_html_blocked(client):
    response = client.get('/admin/pending-review')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert '待審核總覽' in response.get_data(as_text=True)
    assert client.get('/pending-review.html').status_code == 404
