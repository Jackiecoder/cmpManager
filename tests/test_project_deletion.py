"""Deletion keeps accounting/stock facts and enforces scope on stale requests."""
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from test_app import client, ready, login
from test_access import account, as_member, as_admin, grant, record
from test_resource_access import created, ids
from storage_fakes import CloudStore


@pytest.fixture(params=['sqlite', 'postgres'])
def deletion_client(request, monkeypatch):
    if request.param == 'sqlite':
        yield request.getfixturevalue('client')
        return
    if not os.getenv('CMP_TEST_POSTGRES_URL'):
        pytest.skip('Needs isolated PostgreSQL test database')
    import app
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    args = conninfo_to_dict(os.environ['CMP_TEST_POSTGRES_URL'])
    assert args.get('host') in ('127.0.0.1', 'localhost') and args['dbname'].startswith('cmp_test')
    schema = 'deletion_test_' + uuid.uuid4().hex
    with psycopg.connect(**args, autocommit=True) as c:
        assert c.execute('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone() == (False,)
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    scoped = {**args, 'options': '-c search_path=' + schema}
    monkeypatch.setattr(app, 'DATABASE_URL', make_conninfo(**scoped))
    monkeypatch.setattr(app, 'PRODUCTION', False)
    monkeypatch.setenv('ADMIN_INITIAL_PASSWORD', 'initial-password-123!')
    try:
        with TestClient(app.app, headers={'X-Cmp-Request': '1'}) as c:
            yield c
    finally:
        with psycopg.connect(**args, autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def delete(c, project, **override):
    return c.request('DELETE', '/api/records/projects/' + project['id'], json={
        'version': project['version'], 'confirm_title': project['title'], **override})


def test_delete_preserves_ledger_stock_objects_and_audit(deletion_client, monkeypatch):
    c = deletion_client
    ready(c)
    import app
    cloud = CloudStore()
    monkeypatch.setattr(app, 'STORAGE_BUCKET', 'synthetic-attachments')
    monkeypatch.setattr(app.attachment_storage.storage, 'Client', lambda: cloud)
    p = created(c, 'projects', title='Synthetic deletion')
    keep = created(c, 'projects', title='Unaffected project')
    section = created(c, 'subsections', project_id=p['id'])
    note = created(c, 'notes', project_id=p['id'], subsection_id=section['id'], body='Retained history', date='2026-09-10', task_action='create')
    file = c.post('/api/upload', data={'project_id': p['id']}, files={'file': ('retained.txt', b'retained', 'text/plain')}).json()
    ledger = created(c, 'finance_profiles')
    expense = created(c, 'transactions', project_id=p['id'], subsection_id=section['id'], profile_id=ledger['id'], amount='25', date='2026-09-10')
    product = created(c, 'products', sku='DELETE-TEST', unit='kg')
    movement = created(c, 'stock_movements', product_id=product['id'], project_id=p['id'], quantity='5', date='2026-09-10')
    assert delete(c, p).status_code == 200
    state = {r['id']: r for r in c.get('/api/state').json()['records']}
    assert not {p['id'], section['id'], note['id'], note['linked_task_id'], file['id']} & state.keys()
    assert {keep['id'], ledger['id'], expense['id'], product['id'], movement['id']} <= state.keys()
    assert state[expense['id']]['amount'] == '25.00'
    assert state[movement['id']]['quantity'] == '5.000'
    assert state[movement['id']]['_access'] == 'viewer'
    assert state[movement['id']]['_project_title'] == p['title']
    assert len(cloud.objects) == 1 and cloud.reads == 0
    assert c.get(file['url']).status_code == 404
    assert c.get(file['url'] + '?download=true').status_code == 404
    assert cloud.reads == 0
    assert c.patch('/api/records/notes/' + note['id'], json={'version': 1, 'body': 'forged'}).status_code == 404
    assert c.patch('/api/records/tasks/' + note['linked_task_id'], json={'version': 1, 'status': '已完成'}).status_code == 404
    assert c.patch('/api/records/stock_movements/' + movement['id'], json={'version': 1, 'quantity': '4'}).status_code == 409
    assert c.patch('/api/records/projects/' + p['id'], json={'version': 2, 'deleted_at': ''}).status_code == 404
    assert record(c, 'notes', project_id=p['id'], body='no', date='2026-09-10').status_code == 409
    assert record(c, 'transactions', project_id=p['id'], amount='1').status_code == 400
    assert c.patch('/api/records/transactions/' + expense['id'], json={'version': 1, 'note': 'Reimbursement follow-up'}).status_code == 200
    # Inventory from a deleted project still supports the global stock balance.
    assert record(c, 'stock_movements', product_id=product['id'], project_id=keep['id'], quantity='5', movement_type='销售出库', date='2026-09-10').status_code == 200
    assert record(c, 'stock_movements', product_id=product['id'], project_id=keep['id'], quantity='1', movement_type='销售出库', date='2026-09-10').status_code == 400
    audit = c.get('/api/activity?entity_id=' + p['id']).json()
    assert len(audit) == 2 and audit[0]['action'] == '删除'
    assert audit[0]['after']['deleted_by'] == c.get('/api/me').json()['id']
    assert audit[0]['before']['title'] == p['title'] and audit[0]['after']['deleted_at']
    assert len(c.get('/api/activity?entity_id=' + note['id']).json()) == 1
    assert delete(c, p).status_code == 409
    with app.db() as db:
        assert db.records('id=?', (file['id'],), internal=True).fetchone()


def test_delete_requires_full_scope_and_preserves_ledger_privacy(deletion_client):
    c = deletion_client
    ready(c)
    member = account(c)
    p = created(c, 'projects', title='Secret deleted project')
    ledger = created(c, 'finance_profiles', member_access=[{'user_id': member['id'], 'role': 'editor'}])
    expense = created(c, 'transactions', project_id=p['id'], profile_id=ledger['id'], amount='42')
    as_member(c)
    assert delete(c, p).status_code == 403
    as_admin(c)
    grant(c, member, 'projects', p['id'], 'viewer')
    login(c, 'chloe', 'changed-member-123!')
    assert delete(c, p).status_code == 403
    as_admin(c)
    grant(c, member, 'projects', p['id'], 'editor', 1)
    login(c, 'chloe', 'changed-member-123!')
    assert delete(c, p).status_code == 403
    as_admin(c)
    assert delete(c, p).status_code == 200
    login(c, 'chloe', 'changed-member-123!')
    assert p['id'] not in ids(c)
    assert c.get('/api/activity?entity_id=' + p['id']).json()[0]['action'] == '删除'
    # Revoke through company membership to ensure retained history is scoped too.
    as_admin(c)
    assert c.patch('/api/teams/company/members/' + member['id'], json={'version': 1, 'role': 'removed'}).status_code == 200
    assert c.post('/api/teams/company/members', json={'username': 'chloe', 'role': 'member'}).status_code == 200
    grant(c, member, 'finance_profiles', ledger['id'], 'editor', 2)
    login(c, 'chloe', 'changed-member-123!')
    assert 'Secret deleted project' not in c.get('/api/state').text
    assert c.get('/api/activity?entity_id=' + p['id']).json() == []
    assert c.patch('/api/records/transactions/' + expense['id'], json={'version': 1, 'note': 'Keep opaque association'}).status_code == 200


def test_delete_confirm_version_and_audit_rollback(deletion_client, monkeypatch):
    c = deletion_client
    ready(c)
    import app
    key = str(uuid.uuid4())
    p = created(c, 'projects', creation_key=key)
    assert delete(c, p, confirm_title='wrong').status_code == 400
    assert delete(c, p, version=0).status_code == 409
    original = app.audit
    def fail_audit(*args, **kwargs): raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(app, 'audit', fail_audit)
    with pytest.raises(RuntimeError): delete(c, p)
    assert p['id'] in ids(c)
    assert len(c.get('/api/activity?entity_id=' + p['id']).json()) == 1
    monkeypatch.setattr(app, 'audit', original)
    assert delete(c, p).status_code == 200
    assert record(c, 'projects', creation_key=key).status_code == 409
    assert c.request('DELETE', '/api/records/projects/' + p['id'], json={}, headers={'X-Cmp-Request': '0'}).status_code == 403
    c.post('/api/logout')
    assert delete(c, p).status_code == 401


def test_delete_company_personal_and_maintenance_boundaries(deletion_client):
    c = deletion_client
    ready(c)
    p = created(c, 'projects')
    company = c.post('/api/teams', json={'title': 'Synthetic company 2'}).json()
    c.headers['X-Cmp-Workspace'] = company['id']
    assert delete(c, p).status_code == 404
    c.headers['X-Cmp-Workspace'] = 'company'
    member = account(c)
    as_member(c)
    c.headers['X-Cmp-Workspace'] = 'personal'
    private = created(c, 'projects', title='Private deletion')
    assert delete(c, private).status_code == 200
    another = created(c, 'projects')
    as_admin(c)
    c.headers['X-Cmp-Workspace'] = another['workspace_id']
    assert delete(c, another).status_code == 403
    grant_response = c.post('/api/maintenance', json={'user_id': member['id'], 'password': 'changed-password-123!', 'reason': 'Synthetic readonly test'})
    assert grant_response.status_code == 200
    c.headers['X-Cmp-Maintenance'] = grant_response.json()['id']
    assert delete(c, another).status_code == 403


def test_delete_during_preview_does_not_return_file(deletion_client, monkeypatch):
    c = deletion_client
    ready(c)
    import app
    cloud = CloudStore()
    monkeypatch.setattr(app, 'STORAGE_BUCKET', 'synthetic-attachments')
    monkeypatch.setattr(app.attachment_storage.storage, 'Client', lambda: cloud)
    p = created(c, 'projects')
    file = c.post('/api/upload', data={'project_id': p['id']}, files={'file': ('retained.txt', b'retained', 'text/plain')}).json()
    cloud.on_read = lambda: delete(c, p)
    assert c.get(file['url']).status_code == 404
    assert p['id'] not in ids(c)


def test_delete_during_upload_blocks_late_attachment(deletion_client, monkeypatch):
    c = deletion_client
    import app
    if not app.DATABASE_URL:
        pytest.skip('Concurrent network transactions use PostgreSQL; SQLite serializes writes')
    ready(c)
    cloud = CloudStore()
    monkeypatch.setattr(app, 'STORAGE_BUCKET', 'synthetic-attachments')
    monkeypatch.setattr(app.attachment_storage.storage, 'Client', lambda: cloud)
    p = created(c, 'projects')
    def delete_while_uploading():
        assert delete(c, p).status_code == 200
    cloud.on_upload = delete_while_uploading
    result = c.post('/api/upload', data={'project_id': p['id']}, files={'file': ('late.txt', b'synthetic', 'text/plain')})
    assert result.status_code == 409
    with app.db() as db:
        assert db.records("kind='files'", internal=True).fetchall() == []
    assert len(cloud.objects) == 1  # No destructive storage cleanup on deletion.
    assert c.get('/api/activity?entity_id=' + p['id']).json()[0]['action'] == '删除'
