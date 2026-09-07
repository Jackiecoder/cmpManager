import pytest
from test_app import client, ready, project


def create(c, kind, **body):
    response=c.post('/api/records/'+kind,json=body)
    assert response.status_code==200,response.text
    return response.json()


def section(c,p,title='店家联系'):
    return create(c,'subsections',project_id=p['id'],title=title)


def note(c,p,s,**extra):
    return create(c,'notes',project_id=p['id'],subsection_id=s['id'],title='确认样品',body='已沟通样品规格',date='2026-09-07',**extra)


def test_subsections_rename_and_old_records(client):
    ready(client);p=project(client);s=section(client,p)
    old=create(client,'notes',project_id=p['id'],title='旧笔记',body='原始记录',date='2026-09-07')
    assert old['subsection_id']==''
    r=client.patch('/api/records/notes/'+old['id'],json={'version':1,'subsection_id':s['id']})
    assert r.status_code==200 and r.json()['body']=='原始记录'
    r=client.patch('/api/records/subsections/'+s['id'],json={'version':1,'title':'店家 A 联系'})
    assert r.status_code==200
    log=client.get('/api/activity?entity_id='+s['id']).json()
    assert log[0]['before']['title']=='店家联系' and log[0]['after']['title']=='店家 A 联系'


@pytest.mark.parametrize('kind,fields',[
    ('notes',{'title':'N','body':'B','date':'2026-09-07'}),
    ('tasks',{'title':'T'}),('transactions',{'title':'X','date':'2026-09-07','amount':'5'}),
    ('files',{'title':'F','url':'https://drive.google.com/file/d/test/view'})])
def test_sections_cannot_cross_projects(client,kind,fields):
    ready(client);a=project(client);b=project(client);s=section(client,a)
    r=client.post('/api/records/'+kind,json={**fields,'project_id':b['id'],'subsection_id':s['id']})
    assert r.status_code==400
    valid=create(client,kind,**fields,project_id=a['id'],subsection_id=s['id'])
    assert valid['subsection_id']==s['id']
    assert client.patch('/api/records/subsections/'+s['id'],json={'version':1,'project_id':b['id']}).status_code==400


def test_default_task_and_bidirectional_reference(client):
    ready(client);p=project(client);s=section(client,p)
    n=note(client,p,s,task_action='create')
    records=client.get('/api/state').json()['records']
    task=next(r for r in records if r['id']==n['linked_task_id'])
    assert task['title']=='跟进：确认样品' and task['status']=='待办'
    assert task['subsection_id']==s['id'] and task['project_id']==p['id']
    assert task['assignee_id']==client.get('/api/me').json()['id'] and task['due_date']==''
    log=client.get('/api/activity?entity_id='+n['id']).json()
    assert log[0]['after']['linked_task_id']==task['id']
    assert client.patch('/api/records/notes/'+n['id'],json={'version':1,'body':'再次沟通'}).json()['linked_task_id']==task['id']


def test_link_existing_unlink_and_change_project_guards(client):
    ready(client);p=project(client);s=section(client,p);other=project(client)
    task=create(client,'tasks',title='现有任务',project_id=p['id'])
    n=note(client,p,s,task_action='link',linked_task_id=task['id'])
    assert n['linked_task_id']==task['id']
    assert client.patch('/api/records/tasks/'+task['id'],json={'version':1,'project_id':other['id']}).status_code==400
    assert client.patch('/api/records/notes/'+n['id'],json={'version':1,'project_id':other['id'],'subsection_id':''}).status_code==400
    unlinked=client.patch('/api/records/notes/'+n['id'],json={'version':1,'task_action':'none'})
    assert unlinked.status_code==200 and unlinked.json()['linked_task_id']==''
    assert client.patch('/api/records/tasks/'+task['id'],json={'version':1,'project_id':other['id']}).status_code==200


def test_invalid_link_and_stale_note_do_not_create_tasks(client):
    ready(client);p=project(client);s=section(client,p);other=project(client)
    foreign=create(client,'tasks',title='别的项目',project_id=other['id'])
    base={'title':'N','body':'B','date':'2026-09-07','project_id':p['id'],'subsection_id':s['id']}
    for extra in [{'task_action':'link'},{'task_action':'link','linked_task_id':foreign['id']},{'task_action':'invalid'},{'task_action':'create','task_due_date':'invalid'}]:
        assert client.post('/api/records/notes',json={**base,**extra}).status_code==400
    n=note(client,p,s)
    client.patch('/api/records/notes/'+n['id'],json={'version':1,'body':'更新'})
    assert client.patch('/api/records/notes/'+n['id'],json={'version':1,'task_action':'create'}).status_code==409
    records=client.get('/api/state').json()['records']
    assert len([r for r in records if r['kind']=='tasks'])==1


def test_note_task_atomic_rollback(client,monkeypatch):
    ready(client);p=project(client);s=section(client,p)
    import app
    original=app.audit
    def fail_note(c,actor,action,kind,*args,**kwargs):
        if kind=='notes':raise RuntimeError('simulated note write failure')
        return original(c,actor,action,kind,*args,**kwargs)
    monkeypatch.setattr(app,'audit',fail_note)
    with pytest.raises(RuntimeError):note(client,p,s,task_action='create')
    records=client.get('/api/state').json()['records']
    assert not any(r['kind'] in ('tasks','notes') for r in records)
    assert not any(r['kind']=='tasks' for r in client.get('/api/activity').json())


def test_custom_default_task_and_edit_link(client):
    ready(client);p=project(client);s=section(client,p)
    n=note(client,p,s)
    r=client.patch('/api/records/notes/'+n['id'],json={'version':1,'task_action':'create','task_title':'索取报价','task_due_date':'2026-10-01','task_assignee_id':''})
    assert r.status_code==200
    task=next(t for t in client.get('/api/state').json()['records'] if t['id']==r.json()['linked_task_id'])
    assert task['title']=='索取报价' and task['due_date']=='2026-10-01' and task['assignee_id']==''
