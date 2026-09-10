import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_app import client, ready, login


def account(c, username='chloe', role='member'):
    r = c.post('/api/users', json={'name': username.title(), 'username': username,
        'password': 'member-password-123!', 'team_role': role})
    assert r.status_code == 200, r.text
    return r.json()


def grant(c, member, kind, resource_id, role='editor', version=0):
    response=c.patch('/api/resource-access/'+kind+'/'+(resource_id or 'default'), json={'members':[{'user_id':member['id'],'role':role,'version':version}]})
    assert response.status_code==200, response.text


def as_member(c, username='chloe'):
    assert login(c, username, 'member-password-123!').status_code == 200
    assert c.post('/api/password', json={'current_password': 'member-password-123!', 'password': 'changed-member-123!'}).status_code == 200
    assert login(c, username, 'changed-member-123!').status_code == 200


def as_admin(c):
    assert login(c, password='changed-password-123!').status_code == 200


def record(c, kind='projects', **data):
    return c.post('/api/records/' + kind, json={'title': 'Synthetic record', **data})


def set_role(c, user_id, role, version=1):
    return c.patch('/api/teams/company/members/' + user_id, json={'role': role, 'version': version})


def test_personal_records_and_audits_are_isolated_even_from_admin(client):
    ready(client);member = account(client)
    shared = record(client).json()
    as_member(client)
    client.headers['X-Cmp-Workspace'] = 'personal'
    private = record(client, description='private-note-value').json()
    assert private['workspace_id'] == 'personal:' + member['id']
    assert [r['id'] for r in client.get('/api/state').json()['records']] == [private['id']]
    assert [u['id'] for u in client.get('/api/users').json()] == [member['id']]
    assert client.get('/api/state').json()['config']['drive_folder_url'] is None
    assert client.get('/api/activity?entity_id=' + shared['id']).json() == []
    assert client.patch('/api/records/projects/' + shared['id'], json={'version': 1, 'title': 'forged'}).status_code == 404
    as_admin(client)
    assert client.get('/api/state').json()['records'] == []  # Kevin's own personal space
    client.headers['X-Cmp-Workspace'] = private['workspace_id']
    assert client.get('/api/state').status_code == 403
    assert client.get('/api/activity?entity_id=' + private['id']).status_code == 403
    assert client.get('/api/users').status_code == 403
    client.headers['X-Cmp-Workspace'] = 'company'
    assert 'private-note-value' not in client.get('/api/state').text
    assert 'private-note-value' not in client.get('/api/activity').text
    assert 'private-note-value' not in client.get('/api/account/activity').text
    assert private['workspace_id'] not in [w['id'] for w in client.get('/api/state').json()['workspaces']]
    # Payload scope cannot move or create a record in a private space.
    forged = record(client, workspace_id=private['workspace_id'], created_by=member['id']).json()
    assert forged['workspace_id'] == 'company' and forged['created_by'] != member['id']


@pytest.mark.parametrize('kind', ['projects','notes','tasks','files','transactions','finance_profiles','products','warehouses','stock_movements'])
def test_viewer_cannot_write_any_business_kind(client, kind):
    ready(client);member = account(client)
    shared = record(client).json()
    grant(client,member,'projects',shared['id'],'viewer')
    grant(client,member,'finance_profiles','','viewer')
    as_member(client)
    state = client.get('/api/state').json()
    assert state['workspace']['access'] == 'member' and not state['config']['can_edit']
    assert shared['id'] in [r['id'] for r in state['records']]
    before = client.get('/api/activity').json()
    assert record(client, kind).status_code == 403
    assert client.patch('/api/records/' + kind + '/' + shared['id'], json={'version': 1}).status_code in (403,404)
    assert client.get('/api/activity').json() == before
    assert client.post('/api/upload', data={'project_id': shared['id']}, files={'file': ('test.pdf', b'%PDF-test', 'application/pdf')}).status_code == 403
    client.headers['X-Cmp-Workspace'] = 'personal'
    assert record(client).status_code == 200  # Team viewer can still edit own personal space.


def test_team_owner_controls_roles_membership_and_independent_teams(client):
    ready(client);member=account(client)
    team=client.post('/api/teams',json={'title':'Second team'}).json()
    assert client.post('/api/teams/'+team['id']+'/members',json={'username':'chloe','role':'member'}).status_code==200
    as_member(client)
    assert set_role(client,member['id'],'admin').status_code==403
    assert client.get('/api/admin/users').status_code==403
    as_admin(client)
    assert set_role(client,member['id'],'admin').status_code==200
    assert set_role(client,member['id'],'member').status_code==409
    as_chloe=login(client,'chloe','changed-member-123!');assert as_chloe.status_code==200
    assert record(client).status_code==200  # Company admin can create projects here.
    client.headers['X-Cmp-Workspace']=team['id']
    assert record(client).status_code==403  # Admin of company 1 isn't admin of company 2.
    assert client.get('/api/state').json()['records']==[]
    as_admin(client)
    assert set_role(client,member['id'],'removed',2).status_code==200
    assert login(client,'chloe','changed-member-123!').status_code==200
    client.headers['X-Cmp-Workspace']='company'
    assert client.get('/api/state').status_code==403
    client.headers['X-Cmp-Workspace']=team['id']
    assert client.get('/api/state').json()['workspace']['access']=='member'


def test_cross_space_references_are_rejected_and_stock_is_scoped(client):
    ready(client)
    p = record(client).json()
    finance = record(client, 'finance_profiles').json()
    product = record(client, 'products', sku='SAME', unit='kg').json()
    task = record(client, 'tasks', project_id=p['id']).json()
    client.headers['X-Cmp-Workspace'] = 'personal'
    personal = record(client).json()
    for kind, data in [
        ('notes', {'body': 'Private', 'date': '2026-09-07', 'project_id': p['id']}),
        ('notes', {'body': 'Private', 'date': '2026-09-07', 'project_id': personal['id'], 'linked_task_id': task['id']}),
        ('transactions', {'amount': '20', 'profile_id': finance['id']}),
        ('stock_movements', {'date': '2026-09-07', 'product_id': product['id'], 'quantity': '5'}),
    ]:
        assert record(client, kind, **data).status_code == 400
    # Same SKU is allowed in separate spaces; no stock balance is shared.
    private_product = record(client, 'products', sku='SAME', unit='kg').json()
    assert private_product['id'] != product['id']
    assert record(client, 'stock_movements', date='2026-09-07', product_id=private_product['id'], quantity='5', movement_type='领用出库').status_code == 400


def test_maintenance_requires_reauth_is_readonly_session_bound_and_audited(client):
    ready(client);member = account(client)
    as_member(client);client.headers['X-Cmp-Workspace'] = 'personal'
    private = record(client, description='Private content').json()
    assert client.post('/api/maintenance', json={'user_id': member['id'], 'reason': 'Check', 'password': 'changed-member-123!'}).status_code == 403
    as_admin(client)
    body = {'user_id': member['id'], 'reason': 'Restore verification', 'password': 'bad'}
    assert client.post('/api/maintenance', json=body).status_code == 403
    body['password'] = 'changed-password-123!'
    assert client.post('/api/maintenance', json={**body, 'reason': ''}).status_code == 400
    grant = client.post('/api/maintenance', json=body).json()
    assert 0 < grant['expires'] - time.time() <= 900
    client.headers['X-Cmp-Workspace'] = grant['workspace_id']
    assert client.get('/api/state').status_code == 403
    client.headers['X-Cmp-Maintenance'] = grant['id']
    state = client.get('/api/state').json()
    assert state['workspace']['maintenance'] and not state['config']['can_edit']
    assert state['records'][0]['id'] == private['id']
    assert record(client).status_code == 403
    assert client.patch('/api/records/projects/' + private['id'], json={'version': 1, 'description': 'changed'}).status_code == 403
    # A fresh login cannot reuse a grant bound to the earlier authenticated session.
    as_admin(client)
    assert client.get('/api/state').status_code == 403
    as_chloe = login(client, 'chloe', 'changed-member-123!');assert as_chloe.status_code == 200
    client.headers['X-Cmp-Workspace'] = 'personal';client.headers.pop('X-Cmp-Maintenance')
    events = client.get('/api/activity').json()
    assert {'开启维护访问', '维护读取'} <= {a['action'] for a in events}
    assert client.get('/api/maintenance').json()[0]['reason'] == 'Restore verification'
    assert client.post('/api/maintenance/' + grant['id'] + '/revoke').status_code == 200
    assert client.get('/api/maintenance').json()[0]['revoked'] == 1


def test_maintenance_expiry_reauth_rate_limit_and_no_password_disclosure(client):
    ready(client);member = account(client)
    body = {'user_id': member['id'], 'reason': 'Check stored data', 'password': 'changed-password-123!'}
    grant = client.post('/api/maintenance', json=body).json()
    import app
    with app.db() as c: c.execute('UPDATE maintenance_access SET expires=? WHERE id=?', (time.time() - 1, grant['id']))
    client.headers['X-Cmp-Workspace'] = grant['workspace_id'];client.headers['X-Cmp-Maintenance'] = grant['id']
    assert client.get('/api/state').status_code == 403
    for _ in range(5): assert client.post('/api/maintenance', json={**body, 'password': 'bad'}).status_code == 403
    assert client.post('/api/maintenance', json=body).status_code == 429
    accounts = client.get('/api/admin/users').json()
    assert all('password' not in u for u in accounts)
    assert 'member-password-123!' not in client.get('/api/account/activity').text
    assert 'changed-password-123!' not in client.get('/api/maintenance').text


def test_migration_is_idempotent_and_does_not_readd_removed_members(client):
    ready(client);member = account(client)
    shared = record(client).json()
    assert set_role(client, member['id'], 'removed').status_code == 200
    import app
    app.initialize();app.initialize()
    with app.db() as c:
        assert c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?', ('company', member['id'])).fetchone()['role'] == 'removed'
        assert c.execute('SELECT COUNT(*) AS n FROM workspaces WHERE owner_id=? AND kind=?', (member['id'], 'personal')).fetchone()['n'] == 1
    assert client.get('/api/state').json()['records'][0]['id'] == shared['id']


def test_legacy_schema_migration_preserves_data_versions_and_audits(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, 'DATABASE_URL', '')
    monkeypatch.setenv('SQLITE_PATH', str(tmp_path / 'legacy.sqlite'))
    connection = sqlite3.connect(tmp_path / 'legacy.sqlite')
    connection.executescript('''CREATE TABLE users (id TEXT PRIMARY KEY,username TEXT,name TEXT,role TEXT,password TEXT,active INTEGER,must_change INTEGER);
        CREATE TABLE entities (id TEXT PRIMARY KEY,kind TEXT,data TEXT,version INTEGER,created_at TEXT,updated_at TEXT,created_by TEXT,updated_by TEXT);
        CREATE TABLE audit (id TEXT PRIMARY KEY,at TEXT,actor TEXT,action TEXT,kind TEXT,entity_id TEXT,before_data TEXT,after_data TEXT);''')
    connection.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?)', ('owner', 'kevin', 'Kevin', 'admin', 'existing-hash', 1, 0))
    connection.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?)', ('member', 'chloe', 'Chloe', 'member', 'existing-member-hash', 1, 0))
    data = json.dumps({'title': 'Legacy business record'})
    connection.execute('INSERT INTO entities VALUES (?,?,?,?,?,?,?,?)', ('record', 'projects', data, 7, 'original-date', 'update-date', 'owner', 'member'))
    connection.execute('INSERT INTO audit VALUES (?,?,?,?,?,?,?,?)', ('audit', 'original-date', 'member', '修改', 'projects', 'record', 'null', data))
    connection.commit();connection.close()
    app.initialize();app.initialize()
    with app.db() as c:
        assert dict(c.records('id=?', ('record',)).fetchone()) == {'id': 'record', 'kind': 'projects', 'data': data, 'version': 7, 'created_at': 'original-date', 'updated_at': 'update-date', 'created_by': 'owner', 'updated_by': 'member', 'workspace_id': 'company'}
        assert c.execute('SELECT after_data FROM audit WHERE id=?', ('audit',)).fetchone()['after_data'] == data
        assert c.execute('SELECT password FROM users WHERE id=?', ('owner',)).fetchone()['password'] == 'existing-hash'
        assert c.execute('SELECT role FROM memberships WHERE user_id=?', ('member',)).fetchone()['role'] == 'member'


def test_membership_audit_failure_rolls_back_permission_change(client, monkeypatch):
    ready(client);member = account(client)
    import app
    def fail_audit(*args, **kwargs): raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(app, 'audit', fail_audit)
    with pytest.raises(RuntimeError): set_role(client, member['id'], 'admin')
    assert next(u for u in client.get('/api/teams/company/members').json() if u['id'] == member['id'])['team_role'] == 'member'
