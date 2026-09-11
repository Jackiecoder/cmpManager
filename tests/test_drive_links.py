"""Drive link creation and optional note attachment are one scoped transaction."""
import uuid
import pytest
from test_app import client, ready, login
from test_project_deletion import deletion_client, delete
from test_resource_access import created
from test_access import account, as_admin, as_member, grant

@pytest.fixture
def data(deletion_client):
    c=deletion_client;ready(c)
    p=created(c,'projects')
    n=created(c,'notes',project_id=p['id'],body='Synthetic note',date='2026-09-11')
    return c,p,n

def link(p,n=None,**extra):
    return {'title':'Synthetic Drive folder','url':'https://drive.google.com/drive/folders/synthetic-folder?resourcekey=synthetic-key','project_id':p['id'],'note_id':n['id'] if n else '', 'creation_key':str(uuid.uuid4()),**extra}

def state(c):return {r['id']:r for r in c.get('/api/state').json()['records']}

def test_link_retry_audits_and_intentional_unlink(data):
    c,p,n=data;body=link(p,n)
    first=c.post('/api/records/files',json=body);assert first.status_code==200
    f=first.json();assert state(c)[n['id']]['attachment_ids']==[f['id']]
    assert c.post('/api/records/files',json=body).json()['id']==f['id']
    assert state(c)[n['id']]['version']==2
    audit=c.get('/api/activity?entity_id='+n['id']).json()[0]
    assert audit['actor_name']=='Kevin' and audit['action']=='关联附件'
    assert audit['before']['attachment_ids']==[] and audit['after']['attachment_ids']==[f['id']]
    assert c.patch('/api/records/notes/'+n['id'],json={'version':2,'attachment_ids':[]}).status_code==200
    assert c.post('/api/records/files',json=body).status_code==409
    assert state(c)[n['id']]['attachment_ids']==[]
    assert delete(c,p).status_code==200
    assert c.post('/api/records/files',json=body).status_code in (403,409)

def test_link_cannot_cross_scopes_or_escalate_viewer(data):
    c,p,n=data;other=created(c,'projects')
    private=created(c,'notes',project_id=other['id'],body='Private note',date='2026-09-11')
    member=account(c);grant(c,member,'projects',p['id'],'viewer');as_member(c)
    assert c.post('/api/records/files',json=link(p,n)).status_code==403
    as_admin(c);grant(c,member,'projects',p['id'],'editor',1);assert login(c,'chloe','changed-member-123!').status_code==200
    assert c.post('/api/records/files',json=link(p,private)).status_code==404
    assert c.post('/api/records/files',json=link(p,n)).status_code==200
    assert c.post('/api/records/files',json=link(other)).status_code==403
    as_admin(c)
    assert c.post('/api/records/files',json=link(p,private)).status_code==409
    c.headers['X-Cmp-Workspace']='personal:'+member['id']
    assert c.post('/api/records/files',json=link(p,n)).status_code==403

def test_link_audit_failure_rolls_back_file_and_note(data,monkeypatch):
    import app
    c,p,n=data;original=app.audit
    def broken(c,actor,action,kind,*args,**kwargs):
        if kind=='notes':raise RuntimeError('Synthetic audit failure')
        return original(c,actor,action,kind,*args,**kwargs)
    monkeypatch.setattr(app,'audit',broken)
    with pytest.raises(RuntimeError):c.post('/api/records/files',json=link(p,n))
    assert not [r for r in state(c).values() if r['kind']=='files']
    assert state(c)[n['id']]['version']==1
    assert not [a for a in c.get('/api/activity').json() if a['kind']=='files']

def test_link_rejects_unsafe_urls_and_stale_section(data):
    c,p,n=data
    for url in ['javascript:alert(1)','https://drive.google.com.evil.test/file/d/id/view','https://user:pass@drive.google.com/file/d/id/view','https://drive.google.com:bad/file/d/id/view','https://drive.google.com\\@evil.test/file/d/id/view']:
        assert c.post('/api/records/files',json=link(p,url=url)).status_code==400
    section=created(c,'subsections',project_id=p['id'])
    assert c.patch('/api/records/notes/'+n['id'],json={'version':1,'subsection_id':section['id']}).status_code==200
    assert c.post('/api/records/files',json=link(p,n)).status_code==409
    assert not [r for r in state(c).values() if r['kind']=='files']
