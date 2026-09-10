import json
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_app import client, ready, project, login


def product(c, sku='TEST-SKU'):
    r=c.post('/api/records/products',json={'title':'Test item','sku':sku,'unit':'kg','low_stock':'2.5'})
    assert r.status_code==200,r.text
    return r.json()


def movement(c, product_id, **fields):
    return c.post('/api/records/stock_movements',json={
        'title':'Test movement','product_id':product_id,'quantity':'10.125',
        'date':'2026-09-07','movement_type':'采购入库',**fields})


def test_stock_and_finance_created_atomically_with_project_and_audit(client):
    ready(client);p=project(client);item=product(client)
    profile=client.post('/api/records/finance_profiles',json={'title':'Test ledger'}).json()
    section=client.post('/api/records/subsections',json={'title':'Supplier','project_id':p['id']}).json()
    r=movement(client,item['id'],project_id=p['id'],subsection_id=section['id'],finance_action='create',finance_amount='200.15',finance_currency='CNY',finance_profile_id=profile['id'],updated_by='forged')
    assert r.status_code==200,r.text
    m=r.json();records=client.get('/api/state').json()['records']
    t=next(x for x in records if x['id']==m['linked_transaction_id'])
    assert t['direction']=='支出' and t['amount']=='200.15' and t['usd_amount'] is None
    assert t['project_id']==p['id'] and t['subsection_id']==section['id'] and t['profile_id']==profile['id']
    assert t['reimbursement_status']=='待确认'
    assert m['quantity']=='10.125' and m['updated_by']==t['updated_by']==client.get('/api/me').json()['id']
    for record in (m,t):
        log=client.get('/api/activity?entity_id='+record['id']).json()
        assert len(log)==1 and log[0]['after']['title']=='Test movement'
    # An invalid outbound must also roll back the financial row and its audit.
    before=client.get('/api/activity').json()
    r=movement(client,item['id'],movement_type='销售出库',quantity='11',finance_action='create',finance_amount='300',project_id=p['id'])
    assert r.status_code==400 and '库存不足' in r.text
    assert client.get('/api/activity').json()==before
    assert len([x for x in client.get('/api/state').json()['records'] if x['kind']=='transactions'])==1


def test_warehouse_balances_backdates_and_edit_protection(client):
    ready(client);item=product(client)
    warehouse=client.post('/api/records/warehouses',json={'title':'Second warehouse'}).json()
    incoming=movement(client,item['id'],quantity='10',date='2026-09-05').json()
    assert movement(client,item['id'],movement_type='领用出库',quantity='1',warehouse_id=warehouse['id']).status_code==400
    assert movement(client,item['id'],movement_type='销售出库',quantity='1',date='2026-09-04').status_code==400
    outgoing=movement(client,item['id'],movement_type='领用出库',quantity='7',date='2026-09-06').json()
    for patch in ({'quantity':'6'},{'warehouse_id':warehouse['id']},{'date':'2026-09-07'}):
        r=client.patch('/api/records/stock_movements/'+incoming['id'],json={'version':1,**patch})
        assert r.status_code==400 and '库存不足' in r.text
    r=client.patch('/api/records/stock_movements/'+outgoing['id'],json={'version':1,'quantity':'6.001'})
    assert r.status_code==200 and r.json()['quantity']=='6.001'
    assert client.patch('/api/records/stock_movements/'+outgoing['id'],json={'version':1,'quantity':'1'}).status_code==409
    history=client.get('/api/activity?entity_id='+outgoing['id']).json()
    assert history[0]['before']['quantity']=='7.000' and history[0]['after']['quantity']=='6.001'
    import app
    with app.db() as c:
        assert json.loads(c.execute('SELECT data FROM entities WHERE id=?',(incoming['id'],)).fetchone()['data'])['quantity']=='10.000'


def test_concurrent_outbound_cannot_oversell(client):
    ready(client);item=product(client);movement(client,item['id'],quantity='10')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:movement(client,item['id'],quantity='7',movement_type='销售出库').status_code,range(2)))
    assert sorted(results)==[200,400]


def test_links_reject_cross_project_wrong_direction_and_preserve_finance(client):
    ready(client);p=project(client);other=project(client);item=product(client)
    t=client.post('/api/records/transactions',json={'title':'Purchase','amount':'100','date':'2026-09-07','project_id':p['id']}).json()
    assert movement(client,item['id'],project_id=other['id'],finance_action='link',linked_transaction_id=t['id']).status_code==400
    assert movement(client,item['id'],project_id=p['id'],movement_type='销售出库',finance_action='link',linked_transaction_id=t['id']).status_code==400
    m=movement(client,item['id'],project_id=p['id'],finance_action='link',linked_transaction_id=t['id']).json()
    # Multiple products can share an invoice without recording it twice.
    item2=product(client,'SECOND')
    assert movement(client,item2['id'],project_id=p['id'],finance_action='link',linked_transaction_id=t['id']).status_code==200
    for patch in ({'project_id':other['id']},{'direction':'收入'}):
        assert client.patch('/api/records/transactions/'+t['id'],json={'version':1,**patch}).status_code==400
    assert client.patch('/api/records/stock_movements/'+m['id'],json={'version':1,'finance_action':'create','finance_amount':'200'}).status_code==400
    assert client.patch('/api/records/stock_movements/'+m['id'],json={'version':1,'quantity':'5'}).status_code==200
    records=client.get('/api/state').json()['records']
    assert next(x for x in records if x['id']==t['id'])['amount']=='100.00'
    assert client.patch('/api/records/stock_movements/'+m['id'],json={'version':2,'finance_action':'none'}).status_code==200
    assert len([x for x in client.get('/api/state').json()['records'] if x['kind']=='transactions'])==1


@pytest.mark.parametrize('fields',[
    {'quantity':'0'},{'quantity':'-1'},{'quantity':'0.0001'},{'quantity':'NaN'},{'quantity':'Infinity'},
    {'quantity':'1e100'},{'product_id':'missing'},{'warehouse_id':'missing'},{'project_id':'missing'},
    {'date':(date.today()+timedelta(days=2)).isoformat()},
    {'subsection_id':'missing'},{'movement_type':'unknown'},
    {'finance_action':'link'},{'finance_action':'link','linked_transaction_id':'missing'},
    {'finance_action':'create','finance_amount':'NaN'},
    {'movement_type':'期初入库','finance_action':'create','finance_amount':'10'},
])
def test_invalid_stock_creates_no_partial_records(client,fields):
    ready(client);item=product(client)
    before=client.get('/api/state').json()['records']
    assert movement(client,**{'product_id':item['id'],**fields}).status_code==400
    assert client.get('/api/state').json()['records']==before


def test_unique_sku_unit_protection_and_finance_permissions(client,monkeypatch):
    ready(client);item=product(client)
    assert client.post('/api/records/products',json={'title':'duplicate','sku':'test-sku','unit':'件'}).status_code==400
    movement(client,item['id'])
    assert client.patch('/api/records/products/'+item['id'],json={'version':1,'unit':'件'}).status_code==400
    client.post('/api/users',json={'name':'M','username':'member','password':'member-password-123!','team_role':'admin'})
    login(client,'member','member-password-123!')
    client.post('/api/password',json={'current_password':'member-password-123!','password':'changed-member-123!'})
    login(client,'member','changed-member-123!')
    assert movement(client,item['id'],finance_action='create',finance_amount='10').status_code==200
    import app
    monkeypatch.setattr(app,'FINANCE_MEMBERS',False)
    assert movement(client,item['id'],finance_action='create',finance_amount='10').status_code==403
    assert movement(client,item['id'],finance_action='none').status_code==200
    assert all(r['kind']!='transactions' for r in client.get('/api/state').json()['records'])
