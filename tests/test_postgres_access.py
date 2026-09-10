"""Optional integration test against a disposable, non-superuser PostgreSQL DB.

Set CMP_TEST_POSTGRES_URL to a localhost database named cmp_test*. The role needs
CREATE on that database. Each test creates and drops only its own random schema.
"""
import os
import uuid

import pytest


@pytest.mark.skipif(not os.getenv('CMP_TEST_POSTGRES_URL'), reason='Needs isolated PostgreSQL test database')
def test_postgres_migration_force_rls_and_maintenance(monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from fastapi.testclient import TestClient
    import app
    args = conninfo_to_dict(os.environ['CMP_TEST_POSTGRES_URL'])
    assert args.get('host') in ('127.0.0.1', 'localhost') and args['dbname'].startswith('cmp_test')
    schema = 'access_test_' + uuid.uuid4().hex
    with psycopg.connect(**args, autocommit=True) as connection:
        assert connection.execute('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone() == (False,)
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    args['options'] = '-c search_path=' + schema
    monkeypatch.setattr(app, 'DATABASE_URL', make_conninfo(**args))
    monkeypatch.setattr(app, 'PRODUCTION', False)
    monkeypatch.setenv('ADMIN_INITIAL_PASSWORD', 'synthetic-owner-password!')
    try:
        # Create the old schema first, then apply the real production migration.
        with psycopg.connect(**args) as c:
            c.execute('CREATE TABLE users (id TEXT PRIMARY KEY,username TEXT UNIQUE,name TEXT,role TEXT,password TEXT,active INTEGER,must_change INTEGER)')
            c.execute('CREATE TABLE entities (id TEXT PRIMARY KEY,kind TEXT,data TEXT,version INTEGER,created_at TEXT,updated_at TEXT,created_by TEXT,updated_by TEXT)')
            c.execute('CREATE TABLE audit (id TEXT PRIMARY KEY,at TEXT,actor TEXT,action TEXT,kind TEXT,entity_id TEXT,before_data TEXT,after_data TEXT)')
            c.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?)'.replace('?', '%s'), ('kevin-id','kevin','Kevin','admin',app.password_hash('synthetic-owner-password!'),1,0))
            c.execute("INSERT INTO entities VALUES ('legacy','projects','{\"title\":\"Legacy project\"}',3,'old','old','kevin-id','kevin-id')")
            c.execute("INSERT INTO audit VALUES ('old-audit','old','kevin-id','修改密码','user','kevin-id','null','null')")
        with TestClient(app.app, headers={'X-Cmp-Request': '1'}) as client:
            assert client.post('/api/login', json={'username':'kevin','password':'synthetic-owner-password!'}).status_code == 200
            assert client.get('/api/state').json()['records'][0]['version'] == 3
            assert client.get('/api/activity').json() == []
            assert client.get('/api/account/activity').json()[0]['id'] == 'old-audit'
            client.headers['X-Cmp-Workspace'] = 'personal'
            private = client.post('/api/records/projects', json={'title':'Private PG record'}).json()
            assert 'id' in private
            with psycopg.connect(**args) as c:
                # A legacy connection without scope must not see any personal data.
                assert c.execute('SELECT id FROM entities').fetchall() == []
                assert c.execute('SELECT id FROM audit').fetchall() == []
                assert c.execute('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass', ('entities',)).fetchone() == (True, True)
                c.execute("SELECT set_config('cmp.workspace_id',%s,true),set_config('cmp.access_version','2',true)", (private['workspace_id'],))
                assert c.execute('SELECT id FROM entities').fetchall() == [(private['id'],)]
                assert len(c.execute('SELECT id FROM audit').fetchall()) == 1
            app.initialize()  # Restart with FORCE RLS already enabled.
            assert client.get('/api/state').json()['records'][0]['id'] == private['id']
            member = client.post('/api/users', json={'name':'Chloe','username':'chloe','password':'synthetic-member-pass!','team_role':'member'}).json()
            with app.db() as c:
                c.scope(app.personal_space(member['id']))
                member_record = app.insert_record(c, 'projects', app.validate('projects', {'title':'Member secret'}, c), member['id'])
            grant = client.post('/api/maintenance', json={'user_id':member['id'],'reason':'PG isolated validation','password':'synthetic-owner-password!'}).json()
            assert 'id' in grant
            client.headers['X-Cmp-Workspace'] = grant['workspace_id']
            assert client.get('/api/state').status_code == 403
            client.headers['X-Cmp-Maintenance'] = grant['id']
            assert client.get('/api/state').json()['records'][0]['id'] == member_record
            assert client.post('/api/records/tasks', json={'title':'forbidden'}).status_code == 403
            assert client.post('/api/maintenance/' + grant['id'] + '/revoke').status_code == 200
            assert client.get('/api/state').status_code == 403
            client.headers['X-Cmp-Workspace']='company';client.headers.pop('X-Cmp-Maintenance')
            assert client.patch('/api/resource-access/projects/legacy',json={'members':[{'user_id':member['id'],'role':'viewer','version':0}]}).status_code==200
            ledger=client.post('/api/records/finance_profiles',json={'title':'PG ledger','member_access':[{'user_id':member['id'],'role':'editor'}]}).json()
            money=client.post('/api/records/transactions',json={'title':'PG money','amount':'12','profile_id':ledger['id']}).json()
            task=client.post('/api/records/tasks',json={'title':'PG ledger task','ledger_task':True,'profile_id':ledger['id']}).json()
            assert client.post('/api/login',json={'username':'chloe','password':'synthetic-member-pass!'}).status_code==200
            assert client.post('/api/password',json={'current_password':'synthetic-member-pass!','password':'synthetic-changed-pass!'}).status_code==200
            assert client.post('/api/login',json={'username':'chloe','password':'synthetic-changed-pass!'}).status_code==200
            scoped=client.get('/api/state');assert scoped.status_code==200,scoped.text
            assert {r['id'] for r in scoped.json()['records']}=={'legacy',ledger['id'],money['id'],task['id']}
            assert client.patch('/api/records/projects/legacy',json={'version':3,'title':'forged'}).status_code==403
            assert client.patch('/api/records/transactions/'+money['id'],json={'version':1,'amount':'15'}).status_code==200
            assert len(client.get('/api/activity?entity_id='+money['id']).json())==2
    finally:
        args.pop('options')
        with psycopg.connect(**args, autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
