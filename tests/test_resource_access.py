"""Business authorization follows company + project/ledger, including history."""
import json
import pytest
from test_app import client, ready, login
from test_access import account, as_member, as_admin, grant, record


def created(c, kind, **data):
    response=record(c,kind,**data)
    assert response.status_code==200,response.text
    return response.json()


def ids(c): return {r['id'] for r in c.get('/api/state').json()['records']}


def test_project_grants_include_children_but_not_other_projects_or_finance(client):
    ready(client);member=account(client)
    a=created(client,'projects',title='Allowed project')
    b=created(client,'projects',title='Hidden project')
    note=created(client,'notes',project_id=a['id'],body='visible note',date='2026-09-07')
    secret=created(client,'notes',project_id=b['id'],body='secret note text',date='2026-09-07')
    task=created(client,'tasks',project_id=a['id'])
    attachment=created(client,'files',project_id=a['id'],url='https://drive.google.com/file/d/synthetic/view')
    transaction=created(client,'transactions',project_id=a['id'],amount='50')
    as_member(client)
    assert ids(client)==set()
    assert client.get('/api/activity').json()==[]
    assert record(client,'projects',member_access=[]).status_code==403
    as_admin(client);grant(client,member,'projects',a['id'])
    login(client,'chloe','changed-member-123!')
    assert ids(client)=={a['id'],note['id'],task['id'],attachment['id']}
    state=client.get('/api/state').json()
    assert not state['config']['finance_access'] and state['config']['drive_folder_url'] is None
    for route in ('/api/state','/api/activity','/api/activity?entity_id='+secret['id']):
        assert 'secret note text' not in client.get(route).text
    assert client.patch('/api/records/projects/'+b['id'],json={'version':1,'title':'forged'}).status_code==404
    assert record(client,'transactions',project_id=a['id'],amount='1').status_code==403
    response=record(client,'notes',project_id=a['id'],body='follow-up',date='2026-09-07',task_action='create')
    assert response.status_code==200,response.text
    assert response.json()['linked_task_id'] in ids(client)
    assert client.get('/api/resource-access/projects/'+a['id']).status_code==403


def test_ledger_permission_gives_complete_ledger_without_project_contents(client):
    ready(client);member=account(client)
    project=created(client,'projects',title='Hidden project details')
    another=created(client,'projects',title='Another private project')
    section=created(client,'subsections',title='Hidden section',project_id=project['id'])
    ledger=created(client,'finance_profiles',title='Authorized ledger',member_access=[{'user_id':member['id'],'role':'editor'}])
    other=created(client,'finance_profiles',title='Other private ledger')
    x=created(client,'transactions',title='Authorized expense',amount='100',profile_id=ledger['id'],project_id=project['id'],subsection_id=section['id'])
    y=created(client,'transactions',title='Authorized income',amount='175',direction='收入',profile_id=ledger['id'],project_id=another['id'])
    z=created(client,'transactions',title='Hidden money entry',amount='999',profile_id=other['id'])
    as_member(client)
    assert ids(client)=={ledger['id'],x['id'],y['id']}
    assert 'Hidden project details' not in client.get('/api/state').text
    assert 'Hidden money entry' not in client.get('/api/activity').text
    # Preserve an existing project/section relation while editing the authorized ledger.
    changed=client.patch('/api/records/transactions/'+x['id'],json={'version':1,'amount':'110'})
    assert changed.status_code==200,changed.text
    assert changed.json()['project_id']==project['id'] and changed.json()['subsection_id']==section['id']
    assert client.patch('/api/records/transactions/'+x['id'],json={'version':2,'project_id':another['id'],'subsection_id':''}).status_code==400
    assert client.patch('/api/records/transactions/'+x['id'],json={'version':2,'profile_id':other['id']}).status_code==400
    assert record(client,'transactions',amount='1',profile_id=ledger['id'],project_id=project['id']).status_code==400
    assert record(client,'transactions',amount='1',profile_id=ledger['id']).status_code==200
    assert client.get('/api/activity?entity_id='+z['id']).json()==[]


def test_default_ledger_tasks_follow_ledger_permissions_and_assignee_access(client):
    ready(client);member=account(client);other=account(client,'morgan')
    hidden=created(client,'tasks',title='Unassigned company task')
    grant(client,member,'finance_profiles','')
    as_member(client)
    t=record(client,'tasks',title='Reimburse invoice',ledger_task=True,assignee_id=member['id'])
    assert t.status_code==200,t.text
    assert t.json()['ledger_task'] and t.json()['profile_id']==''
    assert hidden['id'] not in ids(client)
    assert record(client,'tasks',ledger_task=True,assignee_id=other['id']).status_code==400
    assert record(client,'tasks').status_code==403
    assert record(client,'tasks',ledger_task='false').status_code==400
    assert record(client,'transactions',amount='10').status_code==200
    as_admin(client);grant(client,member,'finance_profiles','','viewer',1)
    login(client,'chloe','changed-member-123!')
    assert t.json()['id'] in ids(client)
    assert client.patch('/api/records/tasks/'+t.json()['id'],json={'version':1,'status':'已完成'}).status_code==403


def test_resource_revocation_takes_effect_in_existing_session_and_rejoin_does_not_restore(client):
    ready(client);member=account(client)
    p=created(client,'projects',member_access=[{'user_id':member['id'],'role':'editor'}])
    as_member(client);member_cookie=client.cookies.get('cmp_session')
    assert p['id'] in ids(client)
    as_admin(client)
    grant(client,member,'projects',p['id'],'removed',1)
    client.cookies.set('cmp_session',member_cookie,domain='testserver.local',path='/')
    assert p['id'] not in ids(client)
    assert client.patch('/api/records/projects/'+p['id'],json={'version':1,'title':'forged'}).status_code==404
    assert client.get('/api/activity?entity_id='+p['id']).json()==[]
    as_admin(client);grant(client,member,'projects',p['id'],'editor',2)
    assert client.patch('/api/teams/company/members/'+member['id'],json={'version':1,'role':'removed'}).status_code==200
    assert client.post('/api/teams/company/members',json={'username':'chloe','role':'member'}).status_code==200
    login(client,'chloe','changed-member-123!')
    assert ids(client)==set()


def test_moves_require_both_permissions_and_history_does_not_leak_previous_scope(client):
    ready(client);member=account(client)
    a=created(client,'projects',title='Project A')
    b=created(client,'projects',title='Project B')
    task=created(client,'tasks',title='Old private title',project_id=a['id'])
    grant(client,member,'projects',b['id'])
    client.patch('/api/records/tasks/'+task['id'],json={'version':1,'project_id':b['id'],'title':'New shared title'})
    as_member(client)
    assert task['id'] in ids(client)
    assert 'Old private title' not in client.get('/api/activity').text
    assert client.get('/api/activity?entity_id='+task['id']).json()==[]
    assert client.patch('/api/records/tasks/'+task['id'],json={'version':2,'project_id':a['id']}).status_code==400
    assert client.patch('/api/records/tasks/'+task['id'],json={'version':2,'title':'Authorized edit'}).status_code==200
    assert len(client.get('/api/activity?entity_id='+task['id']).json())==1


def test_grants_are_versioned_and_atomic_with_project_creation_and_audit(client,monkeypatch):
    ready(client);member=account(client)
    before=ids(client)
    response=record(client,'projects',member_access=[{'user_id':member['id'],'role':'editor'},{'user_id':'missing','role':'viewer'}])
    assert response.status_code==400 and ids(client)==before
    p=created(client,'projects')
    grant(client,member,'projects',p['id'])
    route='/api/resource-access/projects/'+p['id']
    assert client.patch(route,json={'members':[{'user_id':member['id'],'role':'viewer'}]}).status_code==400
    assert client.patch(route,json={'members':[{'user_id':member['id'],'role':'viewer','version':0}]}).status_code==409
    import app
    def fail_audit(*args,**kwargs): raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(app,'audit',fail_audit)
    with pytest.raises(RuntimeError): client.patch(route,json={'members':[{'user_id':member['id'],'role':'viewer','version':1}]})
    members=client.get(route).json()['members']
    assert next(m for m in members if m['id']==member['id'])['access']=='editor'


def test_project_inventory_finance_side_effect_needs_separate_ledger_write(client):
    ready(client);member=account(client)
    p=created(client,'projects',member_access=[{'user_id':member['id'],'role':'editor'}])
    q=created(client,'projects',title='Other project')
    product=created(client,'products',title='Shared catalog item',sku='RESOURCE-1',unit='kg')
    hidden=created(client,'stock_movements',title='Hidden delivery',product_id=product['id'],project_id=q['id'],date='2026-09-07',quantity='3')
    ledger=created(client,'finance_profiles')
    body={'product_id':product['id'],'project_id':p['id'],'date':'2026-09-07','quantity':'2','finance_action':'create','finance_profile_id':ledger['id'],'finance_amount':'20'}
    as_member(client)
    assert product['id'] in ids(client) and hidden['id'] not in ids(client)
    before=ids(client)
    assert record(client,'stock_movements',**body).status_code==400
    assert ids(client)==before
    as_admin(client);grant(client,member,'finance_profiles',ledger['id'],'viewer')
    login(client,'chloe','changed-member-123!')
    assert record(client,'stock_movements',**body).status_code==403
    as_admin(client);grant(client,member,'finance_profiles',ledger['id'],'editor',1)
    login(client,'chloe','changed-member-123!')
    movement=record(client,'stock_movements',**body)
    assert movement.status_code==200,movement.text
    assert movement.json()['linked_transaction_id'] in ids(client)
    assert 'Hidden delivery' not in client.get('/api/activity').text


def test_resource_grants_are_scoped_to_company_even_for_admin(client):
    ready(client);member=account(client)
    first=created(client,'projects')
    second=client.post('/api/teams',json={'title':'Company 2'}).json()
    client.headers['X-Cmp-Workspace']=second['id']
    assert client.patch('/api/resource-access/projects/'+first['id'],json={'members':[]}).status_code==404
    second_project=created(client,'projects')
    assert record(client,'projects',member_access=[{'user_id':member['id'],'role':'editor'}]).status_code==400
    client.headers['X-Cmp-Workspace']='company'
    grant(client,member,'projects',first['id'])
    as_member(client)
    assert second_project['id'] not in ids(client)
    client.headers['X-Cmp-Workspace']=second['id']
    assert client.get('/api/state').status_code==403


def test_login_default_selects_joined_company_and_keeps_personal_space_available(client):
    ready(client);member=account(client,role='none')
    team=client.post('/api/teams',json={'title':'Joined company'}).json()
    assert client.post('/api/teams/'+team['id']+'/members',json={'username':'chloe','role':'member'}).status_code==200
    client.headers['X-Cmp-Workspace']=team['id']
    p=created(client,'projects',member_access=[{'user_id':member['id'],'role':'viewer'}])
    client.headers.pop('X-Cmp-Workspace')
    as_member(client)
    state=client.get('/api/state').json()
    assert state['workspace']['id']==team['id'] and {r['id'] for r in state['records']}=={p['id']}
    assert {w['kind'] for w in state['workspaces']}=={'personal','team'}
    client.headers['X-Cmp-Workspace']='personal'
    assert created(client,'projects',title='My own project')['workspace_id'].startswith('personal:')
