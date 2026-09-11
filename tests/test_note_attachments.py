"""Note/file associations share project scope, durable objects and audit commits."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_app import client, ready, login
from test_access import account, as_admin, as_member, grant, record
from test_resource_access import created
from test_project_deletion import deletion_client
from storage_fakes import CloudStore


@pytest.fixture
def linked(deletion_client, monkeypatch):
    import app
    c = deletion_client
    ready(c)
    cloud = CloudStore()
    monkeypatch.setattr(app, 'STORAGE_BUCKET', 'synthetic-attachments')
    monkeypatch.setattr(app.attachment_storage.storage, 'Client', lambda: cloud)
    p = created(c, 'projects')
    n = created(c, 'notes', project_id=p['id'], body='Synthetic note', date='2026-09-10')
    return c, cloud, p, n


def upload(c, p, n, key=None, **overrides):
    return c.post('/api/upload', data={'project_id':p['id'], 'note_id':n['id'], 'subsection_id':n.get('subsection_id',''), 'upload_key':key or str(uuid.uuid4()), **overrides}, files={'file':('note.txt', b'Synthetic note attachment', 'text/plain')})


def get(c, item):
    return next(r for r in c.get('/api/state').json()['records'] if r['id'] == item['id'])


def test_upload_links_note_and_keeps_one_project_file_on_retry(linked):
    c, cloud, p, n = linked
    key = str(uuid.uuid4())
    f = upload(c,p,n,key).json()
    assert f['kind']=='files' and f['project_id']==p['id']
    assert get(c,n)['attachment_ids']==[f['id']] and get(c,n)['version']==2
    assert upload(c,p,n,key).json()['id']==f['id']
    assert len(cloud.objects)==1 and get(c,n)['version']==2
    log=c.get('/api/activity?entity_id='+n['id']).json()
    assert len(log)==2 and log[0]['action']=='关联附件'
    assert log[0]['before']['attachment_ids']==[] and log[0]['after']['attachment_ids']==[f['id']]
    assert log[0]['actor_name']=='Kevin'
    assert c.get(f['url']).content==b'Synthetic note attachment'
    # One file can appear on multiple notes without another upload.
    other=created(c,'notes',project_id=p['id'],body='Other note',date='2026-09-10',attachment_ids=[f['id'],f['id']])
    assert other['attachment_ids']==[f['id']]
    assert len(cloud.objects)==1
    assert c.patch('/api/records/notes/'+n['id'],json={'version':2,'attachment_ids':[]}).status_code==200
    assert upload(c,p,n,key).status_code==409  # A retry must not undo a deliberate unlink.
    assert get(c,other)['attachment_ids']==[f['id']] and c.get(f['url']).status_code==200


def test_link_validation_moves_and_legacy_files(linked):
    c, cloud, p, n=linked
    other=created(c,'projects',title='Another project')
    f=created(c,'files',project_id=p['id'],url='https://drive.google.com/file/d/synthetic/view')
    foreign=created(c,'files',project_id=other['id'],url='https://drive.google.com/file/d/other/view')
    for value in ('bad',[None],[foreign['id']],['missing'],[f['id']]*51):
        assert c.patch('/api/records/notes/'+n['id'],json={'version':1,'attachment_ids':value}).status_code==400
    assert c.patch('/api/records/notes/'+n['id'],json={'version':1,'attachment_ids':[f['id']]}).status_code==200
    assert c.patch('/api/records/files/'+f['id'],json={'version':1,'project_id':other['id']}).status_code==400
    assert c.patch('/api/records/notes/'+n['id'],json={'version':2,'project_id':other['id']}).status_code==400
    assert c.patch('/api/records/notes/'+n['id'],json={'version':2,'attachment_ids':[]}).status_code==200
    assert c.patch('/api/records/files/'+f['id'],json={'version':1,'project_id':other['id']}).status_code==200
    assert cloud.objects=={}


def test_upload_and_note_audit_roll_back_together(linked,monkeypatch):
    import app
    c,cloud,p,n=linked
    original=app.audit
    def broken(c,actor,action,kind,*args,**kwargs):
        if kind=='notes': raise RuntimeError('Synthetic note audit error')
        return original(c,actor,action,kind,*args,**kwargs)
    monkeypatch.setattr(app,'audit',broken)
    key=str(uuid.uuid4())
    with pytest.raises(RuntimeError):upload(c,p,n,key)
    assert get(c,n)['attachment_ids']==[] and get(c,n)['version']==1
    assert not any(r['kind']=='files' for r in c.get('/api/state').json()['records'])
    assert not any(a['kind']=='files' for a in c.get('/api/activity').json())
    monkeypatch.setattr(app,'audit',original)
    assert upload(c,p,n,key).status_code==200
    assert len(cloud.uploads)==1 and len(get(c,n)['attachment_ids'])==1


def test_note_create_retry_keeps_single_note_and_todo(linked):
    c,cloud,p,n=linked
    body={'title':'New note','body':'New content','date':'2026-09-10','project_id':p['id'],'creation_key':str(uuid.uuid4()),'task_action':'create','with_attachments':True}
    first=c.post('/api/records/notes',json=body)
    assert first.status_code==200
    again=c.post('/api/records/notes',json=body)
    assert again.json()['id']==first.json()['id']
    assert len([r for r in c.get('/api/state').json()['records'] if r['kind']=='tasks'])==1
    assert len(c.get('/api/activity?entity_id='+first.json()['id']).json())==1


def test_references_enforce_project_company_and_personal_scope(linked):
    c,cloud,p,n=linked
    member=account(c)
    other=created(c,'projects',title='Secret project')
    secret=created(c,'notes',project_id=other['id'],body='Private contents',date='2026-09-10')
    grant(c,member,'projects',p['id'],'viewer')
    as_member(c)
    assert upload(c,p,n).status_code==403
    assert c.patch('/api/records/notes/'+n['id'],json={'version':1,'attachment_ids':[]}).status_code==403
    as_admin(c);grant(c,member,'projects',p['id'],'editor',1)
    login(c,'chloe','changed-member-123!')
    assert upload(c,p,secret).status_code==404
    assert upload(c,other,n).status_code==403
    assert cloud.objects=={}
    f=upload(c,p,n).json()
    as_admin(c);grant(c,member,'projects',p['id'],'viewer',2)
    login(c,'chloe','changed-member-123!')
    assert get(c,n)['attachment_ids']==[f['id']] and c.get(f['url']).status_code==200
    c.headers['X-Cmp-Workspace']='personal'
    private=created(c,'projects')
    assert upload(c,private,n).status_code==404
    as_admin(c)
    company=c.post('/api/teams',json={'title':'Different company'}).json()
    c.headers['X-Cmp-Workspace']=company['id']
    new=created(c,'projects')
    assert upload(c,new,n).status_code==404


def test_wrong_section_fails_before_storage(linked):
    c,cloud,p,n=linked
    section=created(c,'subsections',project_id=p['id'])
    assert upload(c,p,n,subsection_id=section['id']).status_code==409
    assert cloud.objects=={}


def test_concurrent_uploads_keep_all_links_and_revalidate_after_io(linked):
    import app
    if not app.DATABASE_URL:pytest.skip('PostgreSQL permits concurrent network transactions')
    c,cloud,p,n=linked
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:upload(c,p,n),range(2)))
    assert all(r.status_code==200 for r in results),[r.text for r in results]
    assert set(get(c,n)['attachment_ids'])=={r.json()['id'] for r in results}
    section=created(c,'subsections',project_id=p['id'])
    def move_section():
        assert c.patch('/api/records/notes/'+n['id'],json={'version':3,'subsection_id':section['id']}).status_code==200
    cloud.on_upload=move_section
    assert upload(c,p,n).status_code==409
    assert len(get(c,n)['attachment_ids'])==2
