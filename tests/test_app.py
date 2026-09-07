import importlib
import os
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('SQLITE_PATH', str(tmp_path / 'test.sqlite'))
    monkeypatch.setenv('ADMIN_INITIAL_PASSWORD', 'initial-password-123!')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('K_SERVICE', raising=False)
    import app
    importlib.reload(app)
    with TestClient(app.app, headers={'X-Cmp-Request':'1'}) as c:
        yield c

def login(c, username='kevin', password='initial-password-123!'):
    return c.post('/api/login', json={'username':username,'password':password})

def ready(c):
    assert login(c).status_code == 200
    assert c.post('/api/password',json={'current_password':'initial-password-123!','password':'changed-password-123!'}).status_code==200
    assert login(c,password='changed-password-123!').status_code==200

def project(c):
    response=c.post('/api/records/projects',json={'title':'测试项目','progress':20})
    assert response.status_code==200
    return response.json()

def test_authentication_and_password_revocation(client):
    assert client.get('/api/state').status_code==401
    assert login(client).json()['must_change']==1
    assert client.get('/api/state').status_code==403
    assert client.post('/api/password',json={'current_password':'bad','password':'new-password-123!'}).status_code==400
    assert client.post('/api/password',json={'current_password':'initial-password-123!','password':'new-password-123!'}).status_code==200
    assert client.get('/api/me').status_code==401
    assert login(client,password='new-password-123!').status_code==200
    assert client.get('/api/state').status_code==200

def test_audit_immutable_and_concurrent_update(client):
    ready(client);p=project(client)
    response=client.patch('/api/records/projects/'+p['id'],json={**p,'progress':50,'updated_by':'forged'})
    assert response.status_code==200
    assert response.json()['updated_by']==client.get('/api/me').json()['id']
    assert client.patch('/api/records/projects/'+p['id'],json={**p,'progress':80}).status_code==409
    log=client.get('/api/activity?entity_id='+p['id']).json()
    assert len(log)==2 and log[0]['before']['progress']==20 and log[0]['after']['progress']==50
    assert client.delete('/api/records/projects/'+p['id']).status_code==405
    assert client.get('/api/state').json()['records'][0]['progress']==50

def test_six_users_and_member_finance(client):
    ready(client)
    ids=[]
    for n in range(5):
        r=client.post('/api/users',json={'name':f'Member {n}','username':f'user{n}','password':'member-password-123!'})
        assert r.status_code==200;ids.append(r.json()['id'])
    assert client.post('/api/users',json={'name':'seventh','username':'user6','password':'member-password-123!'}).status_code==400
    assert login(client,'user0','member-password-123!').status_code==200
    assert client.post('/api/password',json={'current_password':'member-password-123!','password':'changed-member-123!'}).status_code==200
    login(client,'user0','changed-member-123!')
    assert client.post('/api/users',json={}).status_code==403
    r=client.post('/api/records/transactions',json={'title':'会费','date':'2026-09-06','amount':'100.10','currency':'USD','direction':'收入'})
    assert r.status_code==200 and r.json()['usd_amount']=='100.10'
    assert 'password' not in str(client.get('/api/users').json())
    login(client,password='changed-password-123!')
    assert client.patch('/api/users/'+ids[0],json={'active':False}).status_code==200
    assert login(client,'user0','changed-member-123!').status_code==401

@pytest.mark.parametrize('value',['NaN','Infinity','-1','0.001','1e100','abc'])
def test_bad_money_rejected(client,value):
    ready(client)
    assert client.post('/api/records/transactions',json={'title':'x','date':'2026-09-06','amount':value}).status_code==400

def test_rmb_unknown_conversion_and_validation(client):
    ready(client)
    r=client.post('/api/records/transactions',json={'title':'RMB','date':'2026-09-06','amount':'25.25','currency':'CNY'})
    assert r.status_code==200 and r.json()['usd_amount'] is None
    assert client.post('/api/records/notes',json={'title':'a','body':'b','date':'2026-09-06','project_id':'missing'}).status_code==400
    assert client.post('/api/records/transactions',json={'title':'a','date':'2026-99-10','amount':'1'}).status_code==400

def test_csrf_and_security_headers(client):
    assert client.post('/api/login',json={},headers={'Origin':'https://evil.test'}).status_code==403
    assert client.post('/api/login',json={},headers={'X-Cmp-Request':'0'}).status_code==403
    r=client.get('/api/me')
    assert r.headers['cache-control']=='no-store'
    assert 'frame-ancestors' in r.headers['content-security-policy']

def test_login_rate_limit_persists(client):
    for _ in range(10):assert login(client,password='wrong').status_code==401
    assert login(client).status_code==429

def test_notes_tasks_files_and_upload_not_configured(client):
    ready(client);p=project(client)
    for kind,body in [('notes',{'title':'客户沟通','body':'确认实际信息','date':'2026-09-06'}),('tasks',{'title':'跟进'}),('files',{'title':'合同','url':'https://drive.google.com/file/d/example/view'})]:
        r=client.post('/api/records/'+kind,json={**body,'project_id':p['id']})
        assert r.status_code==200
    assert client.post('/api/records/files',json={'title':'bad','url':'javascript:alert(1)','project_id':p['id']}).status_code==400
    assert client.post('/api/upload',data={'project_id':p['id']},files={'file':('x.pdf',b'%PDF-test','application/pdf')}).status_code==503

def test_database_reopen(client):
    ready(client);p=project(client)
    import app
    app.initialize()
    with app.db() as c:
        assert c.execute('SELECT data FROM entities WHERE id=?',(p['id'],)).fetchone() is not None
        assert c.execute('SELECT count(*) AS n FROM users').fetchone()['n']==1

def test_finance_permission_enforced_on_all_endpoints(client,monkeypatch):
    ready(client)
    client.post('/api/records/transactions',json={'title':'private','date':'2026-09-06','amount':'1'})
    profile=client.post('/api/records/finance_profiles',json={'title':'private ledger'}).json()
    client.post('/api/users',json={'name':'M','username':'member','password':'member-password-123!'})
    login(client,'member','member-password-123!')
    client.post('/api/password',json={'current_password':'member-password-123!','password':'changed-member-123!'})
    login(client,'member','changed-member-123!')
    import app
    monkeypatch.setattr(app,'FINANCE_MEMBERS',False)
    assert client.get('/api/state').json()['records']==[]
    assert client.post('/api/records/transactions',json={}).status_code==403
    assert client.post('/api/records/finance_profiles',json={}).status_code==403
    assert client.patch('/api/records/finance_profiles/'+profile['id'],json={'version':1,'title':'forged'}).status_code==403
    assert all(x['kind'] not in ('transactions','finance_profiles') for x in client.get('/api/activity').json())

def test_upload_multipart_and_audit(client,monkeypatch):
    ready(client);p=project(client)
    for key in ('GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','GOOGLE_REFRESH_TOKEN'):monkeypatch.setenv(key,'test-only')
    import app
    class Reply:
        def __init__(self,data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    calls=[]
    def post(url,**kwargs):
        calls.append((url,kwargs))
        if 'oauth2' in url:return Reply({'access_token':'test-token'})
        assert kwargs['headers']['Content-Type'].startswith('multipart/related; boundary=')
        assert p['id'].encode() in kwargs['data']
        assert app.DRIVE_FOLDER.encode() in kwargs['data']
        assert b'%PDF-test' in kwargs['data']
        return Reply({'id':'remote-test','webViewLink':'https://drive.google.com/file/d/remote-test/view'})
    monkeypatch.setattr(app.requests,'post',post)
    r=client.post('/api/upload',data={'project_id':p['id']},files={'file':('sample.pdf',b'%PDF-test','application/pdf')})
    assert r.status_code==200 and r.json()['kind']=='files'
    log=client.get('/api/activity?entity_id='+r.json()['id']).json()
    assert len(log)==1 and log[0]['actor_name']=='Kevin'
    assert len(calls)==2
