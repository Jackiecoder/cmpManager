"""Exercise upload locks and live revocation with the production database engine."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest


@pytest.mark.skipif(not os.getenv('CMP_TEST_POSTGRES_URL'), reason='Needs isolated PostgreSQL test database')
def test_postgres_upload_retries_and_mid_upload_revocation(monkeypatch):
    import app
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from fastapi.testclient import TestClient
    args = conninfo_to_dict(os.environ['CMP_TEST_POSTGRES_URL'])
    assert args.get('host') in ('127.0.0.1','localhost') and args['dbname'].startswith('cmp_test')
    schema = 'upload_test_' + uuid.uuid4().hex
    with psycopg.connect(**args, autocommit=True) as c:
        assert c.execute('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone() == (False,)
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    args['options'] = '-c search_path=' + schema
    monkeypatch.setattr(app, 'DATABASE_URL', make_conninfo(**args))
    monkeypatch.setattr(app, 'PRODUCTION', False)
    monkeypatch.setenv('ADMIN_INITIAL_PASSWORD','synthetic-owner-password!')
    from storage_fakes import CloudStore
    cloud=CloudStore();revoke=None
    monkeypatch.setattr(app,'STORAGE_BUCKET','synthetic-attachments')
    monkeypatch.setattr(app.attachment_storage.storage,'Client',lambda:cloud)
    def after_upload():
        if revoke:
            with psycopg.connect(**args) as c:
                c.execute("UPDATE resource_access SET role='removed',version=version+1 WHERE user_id=%s AND resource_id=%s", revoke)
    cloud.on_upload=after_upload

    try:
        with TestClient(app.app,headers={'X-Cmp-Request':'1'}) as client:
            client.post('/api/login',json={'username':'kevin','password':'synthetic-owner-password!'})
            client.post('/api/password',json={'current_password':'synthetic-owner-password!','password':'synthetic-changed-password!'})
            client.post('/api/login',json={'username':'kevin','password':'synthetic-changed-password!'})
            body={'title':'Concurrent upload project','creation_key':str(uuid.uuid4()),'with_attachments':True}
            project=client.post('/api/records/projects',json=body).json()
            assert client.post('/api/records/projects',json=body).json()['id']==project['id']
            key=str(uuid.uuid4())
            def upload():
                return client.post('/api/upload',data={'project_id':project['id'],'upload_key':key},files={'file':('test.txt',b'synthetic','text/plain')})
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses=list(pool.map(lambda _: upload(),range(2)))
            assert all(r.status_code==200 for r in responses),[r.text for r in responses]
            assert responses[0].json()['id']==responses[1].json()['id'] and len(cloud.uploads)==1
            assert len(client.get('/api/activity?entity_id='+responses[0].json()['id']).json())==1
            member=client.post('/api/users',json={'name':'Chloe','username':'chloe','password':'synthetic-member-password!','team_role':'member'}).json()
            assert client.patch('/api/resource-access/projects/'+project['id'],json={'members':[{'user_id':member['id'],'role':'editor','version':0}]}).status_code==200
            client.post('/api/login',json={'username':'chloe','password':'synthetic-member-password!'})
            client.post('/api/password',json={'current_password':'synthetic-member-password!','password':'synthetic-member-changed!'})
            client.post('/api/login',json={'username':'chloe','password':'synthetic-member-changed!'})
            revoke=(member['id'],project['id']);key=str(uuid.uuid4())
            assert upload().status_code==403
            assert client.get('/api/state').json()['records']==[]
            with app.db() as c:
                c.scope('company')
                assert c.execute("SELECT count(*) AS n FROM entities WHERE kind='files'").fetchone()['n']==1
                assert c.execute("SELECT count(*) AS n FROM audit WHERE kind='files'").fetchone()['n']==1
    finally:
        args.pop('options')
        with psycopg.connect(**args,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
