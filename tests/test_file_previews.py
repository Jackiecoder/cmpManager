import json
import pytest
from test_app import client, ready, project, login
from test_access import account, as_member, as_admin, grant
from test_project_uploads import cloud, upload


def test_viewer_reads_but_unassigned_member_never_reaches_storage(client, cloud):
    ready(client); member=account(client); p=project(client)
    file=upload(client,p).json()
    as_member(client)
    assert client.get(file['url']).status_code==404
    assert cloud.reads==0
    as_admin(client);grant(client,member,'projects',p['id'],'viewer')
    login(client,'chloe','changed-member-123!')
    preview=client.get(file['url'])
    assert preview.status_code==200 and preview.content==b'%PDF-synthetic'
    assert preview.headers['content-type']=='application/pdf'
    assert preview.headers['content-disposition'].startswith('inline;')
    assert 'no-store' in preview.headers['cache-control']
    assert preview.headers['x-content-type-options']=='nosniff'
    assert "default-src 'none'" in preview.headers['content-security-policy']
    download=client.get(file['url']+'?download=true')
    assert download.content==preview.content
    assert download.headers['content-disposition'].startswith('attachment;')
    assert upload(client,p).status_code==403
    assert client.patch('/api/records/files/'+file['id'],json={'version':1,'title':'Forbidden'}).status_code==403
    client.cookies.clear()
    assert client.get(file['url']).status_code==401
    assert cloud.reads==2


def test_preview_rechecks_permission_after_network(client, cloud):
    import app
    ready(client);member=account(client);p=project(client);file=upload(client,p).json()
    grant(client,member,'projects',p['id'],'viewer');as_member(client)
    def revoke():
        with app.db() as c:
            c.execute("UPDATE resource_access SET role='removed' WHERE user_id=? AND resource_id=?",(member['id'],p['id']))
    cloud.on_read=revoke
    result=client.get(file['url'])
    assert result.status_code==404 and '%PDF' not in result.text


def test_personal_files_private_and_explicit_maintenance_audited(client, cloud):
    ready(client);member=account(client);as_member(client)
    client.headers['X-Cmp-Workspace']='personal'
    p=project(client);file=upload(client,p).json()
    assert client.get(file['url']).status_code==200
    as_admin(client)
    assert client.get(file['url']).status_code==404
    client.headers['X-Cmp-Workspace']=p['workspace_id']
    assert client.get(file['url']).status_code==403
    reads=cloud.reads
    maintenance=client.post('/api/maintenance',json={'user_id':member['id'],'password':'changed-password-123!','reason':'Synthetic preview maintenance'}).json()
    client.headers['X-Cmp-Maintenance']=maintenance['id']
    assert client.get(file['url']).status_code==200
    assert upload(client,p).status_code==403
    logs=client.get('/api/activity').json()
    assert any(a['action']=='维护读取' and a['after']['path']==file['url'] for a in logs)
    assert cloud.reads==reads+1


def test_cross_company_url_and_object_boundaries(client, cloud):
    import app
    ready(client);p=project(client);file=upload(client,p).json()
    team=client.post('/api/teams',json={'title':'Independent company'}).json()
    client.headers['X-Cmp-Workspace']=team['id']
    other=upload(client,project(client)).json()
    assert other['storage_object']!=file['storage_object']
    assert client.get(file['url']).status_code==404
    assert client.get(other['url']).status_code==200
    client.headers['X-Cmp-Workspace']='company'
    with app.db() as c:
        tampered={**file,'storage_object':other['storage_object']}
        c.execute('UPDATE entities SET data=? WHERE id=?',(json.dumps(tampered),file['id']))
    reads=cloud.reads
    assert client.get(file['url']).status_code==409
    assert cloud.reads==reads


def test_file_move_requires_both_projects_and_keeps_preview(client, cloud):
    ready(client);member=account(client);p=project(client);q=project(client);file=upload(client,p).json()
    grant(client,member,'projects',p['id']);as_member(client)
    assert client.patch('/api/records/files/'+file['id'],json={'version':1,'project_id':q['id']}).status_code in (400,403)
    as_admin(client);grant(client,member,'projects',q['id']);login(client,'chloe','changed-member-123!')
    moved=client.patch('/api/records/files/'+file['id'],json={'version':1,'project_id':q['id'],'title':'Moved document'})
    assert moved.status_code==200,moved.text
    assert client.get(file['url']).content==b'%PDF-synthetic'
    as_admin(client);grant(client,member,'projects',q['id'],'removed',1);login(client,'chloe','changed-member-123!')
    assert client.get(file['url']).status_code==404
    assert client.get('/api/activity?entity_id='+file['id']).json()==[]


def test_cannot_forge_stored_object_or_replace_uploaded_file_link(client, cloud):
    ready(client);p=project(client);file=upload(client,p).json()
    modified=client.patch('/api/records/files/'+file['id'],json={'version':1,'title':'New title',
        'storage_bucket':'other','storage_object':'other','storage_generation':'999','mime_type':'text/html','filename':'bad.html','size':1})
    assert modified.status_code==200
    for key in ('storage_bucket','storage_object','storage_generation','mime_type','filename','size'):
        assert modified.json()[key]==file[key]
    assert client.patch('/api/records/files/'+file['id'],json={'version':2,'url':'https://drive.google.com/file/d/other/view'}).status_code==400
    created=client.post('/api/records/files',json={'title':'Forged','project_id':p['id'],'url':file['url'],**{k:v for k,v in file.items() if k.startswith('storage_')}})
    assert created.status_code==400
    linked=client.post('/api/records/files',json={'title':'External','project_id':p['id'],'url':'https://drive.google.com/file/d/synthetic/view','storage_provider':'gcs'})
    assert linked.status_code==200 and 'storage_provider' not in linked.json()
    assert client.get('/api/files/'+linked.json()['id']+'/content').status_code==400


@pytest.mark.parametrize('filename,content,mime',[
    ('notes.txt','你好\n<script>alert(1)</script>'.encode(),'text/plain'),
    ('fake.png',b'<html><script>alert(1)</script></html>','application/octet-stream'),
    ('vector.svg',b'<svg onload="alert(1)"></svg>','application/octet-stream'),
    ('photo.png',b'\x89PNG\r\n\x1a\nsynthetic','image/png'),
    ('report.pdf',b'%PDF-1.7 synthetic','application/pdf'),
])
def test_preview_types_are_detected_not_trusted(client,cloud,filename,content,mime):
    ready(client);p=project(client)
    file=client.post('/api/upload',data={'project_id':p['id']},files={'file':(filename,content,'text/html')}).json()
    result=client.get(file['url'])
    assert result.status_code==200 and result.content==content
    assert result.headers['content-type'].startswith(mime)
    if mime=='application/octet-stream':assert result.headers['content-disposition'].startswith('attachment;')


def test_corrupt_or_missing_blob_cannot_be_previewed(client,cloud):
    ready(client);file=upload(client,project(client)).json();key=cloud.uploads[0]
    cloud.objects[key]['content']=b'corrupt'
    assert client.get(file['url']).status_code==502
    del cloud.objects[key]
    assert client.get(file['url']).status_code==404


def test_empty_upload_rejected_before_storage(client,cloud):
    ready(client);p=project(client)
    assert upload(client,p,content=b'').status_code==400
    assert cloud.uploads==[]
