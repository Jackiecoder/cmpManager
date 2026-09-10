import json
import pytest
from test_app import client, ready, login


def expense(client, **fields):
    return client.post('/api/records/transactions', json={
        'title':'Synthetic expense','date':'2026-09-07','amount':'100.00',
        'payment_status':'已付','posting_status':'平帐',**fields})


def test_profiles_reimbursement_persistence_and_audit(client):
    ready(client)
    profile=client.post('/api/records/finance_profiles',json={'title':'Test ledger'}).json()
    t=expense(client,profile_id=profile['id'],reimbursement_status='待报销',claim_amount='80',reimbursed_amount='0').json()
    assert t['reimbursement_status']=='待报销'
    patch={'version':t['version'],'reimbursement_status':'部分报销','reimbursed_amount':'30.15','reimbursement_date':'2026-09-07','updated_by':'forged'}
    r=client.patch('/api/records/transactions/'+t['id'],json=patch)
    assert r.status_code==200
    assert r.json()['profile_id']==profile['id'] and r.json()['amount']=='100.00'
    assert r.json()['updated_by']==client.get('/api/me').json()['id']
    assert client.patch('/api/records/transactions/'+t['id'],json=patch).status_code==409
    r=client.patch('/api/records/transactions/'+t['id'],json={'version':2,'reimbursement_status':'已报销','reimbursed_amount':'80'})
    assert r.status_code==200
    records=client.get('/api/state').json()['records']
    assert len([r for r in records if r['kind']=='transactions'])==1
    assert next(r for r in records if r['id']==t['id'])['reimbursed_amount']=='80.00'
    audit=client.get('/api/activity?entity_id='+t['id']).json()
    assert len(audit)==3
    assert audit[0]['before']['reimbursed_amount']=='30.15'
    assert audit[0]['after']['reimbursed_amount']=='80.00'
    assert client.patch('/api/records/finance_profiles/'+profile['id'],json={'version':1,'title':'Renamed ledger'}).status_code==200
    assert client.patch('/api/records/transactions/'+t['id'],json={'version':3,'profile_id':''}).status_code==200


@pytest.mark.parametrize('fields',[
    {'profile_id':'missing'},
    {'reimbursement_status':'bogus'},
    {'reimbursement_status':'已报销','claim_amount':'80','reimbursed_amount':'50'},
    {'reimbursement_status':'部分报销','claim_amount':'80','reimbursed_amount':'80'},
    {'reimbursement_status':'部分报销','claim_amount':'80','reimbursed_amount':'0'},
    {'reimbursement_status':'待报销','claim_amount':'101'},
    {'reimbursement_status':'待报销','claim_amount':'0'},
    {'reimbursement_status':'待报销','claim_amount':'80','reimbursed_amount':'1'},
    {'reimbursement_status':'待报销','claim_amount':'80','reimbursement_date':'2026-09-07'},
    {'reimbursement_status':'不需报销','reimbursed_amount':'5'},
    {'reimbursement_status':'待确认','claim_amount':'80'},
    {'reimbursement_status':'部分报销','claim_amount':'80','reimbursed_amount':'90'},
    {'direction':'收入','reimbursement_status':'待报销','claim_amount':'80'},
    {'reimbursement_status':'已报销','claim_amount':'80','reimbursed_amount':'80','reimbursement_date':'not-a-date'},
    {'reimbursement_status':'已报销','claim_amount':'NaN','reimbursed_amount':'80'},
])
def test_invalid_reimbursements_are_atomic(client,fields):
    ready(client)
    before=client.get('/api/activity').json()
    assert expense(client,**fields).status_code==400
    assert client.get('/api/state').json()['records']==[]
    assert client.get('/api/activity').json()==before


def test_legacy_payment_never_implies_reimbursement(client):
    ready(client)
    t=expense(client).json()
    # Model a row saved before the new fields existed.
    import app
    with app.db() as c:
        row=c.execute('SELECT data FROM entities WHERE id=?',(t['id'],)).fetchone()
        data=json.loads(row['data'])
        for key in ('profile_id','reimbursement_status','claim_amount','reimbursed_amount','reimbursement_date','reimbursement_note'):
            data.pop(key,None)
        c.execute('UPDATE entities SET data=? WHERE id=?',(json.dumps(data),t['id']))
    r=client.patch('/api/records/transactions/'+t['id'],json={'version':1,'note':'Added note'})
    assert r.status_code==200
    assert r.json()['reimbursement_status']=='待确认' and r.json()['profile_id']==''
    assert r.json()['reimbursed_amount'] is None
    assert expense(client,direction='收入').json()['reimbursement_status']=='不需报销'


def test_member_can_manage_profile_and_reimbursement(client):
    ready(client)
    member=client.post('/api/users',json={'name':'Member','username':'member','password':'member-password-123!'}).json()
    profile=client.post('/api/records/finance_profiles',json={'title':'Member ledger','member_access':[{'user_id':member['id'],'role':'editor'}]})
    login(client,'member','member-password-123!')
    client.post('/api/password',json={'current_password':'member-password-123!','password':'changed-member-123!'})
    login(client,'member','changed-member-123!')
    assert client.post('/api/records/finance_profiles',json={'title':'forbidden'}).status_code==403
    assert profile.status_code==200
    t=expense(client,profile_id=profile.json()['id'],currency='CNY',reimbursement_status='已报销',claim_amount='80',reimbursed_amount='80')
    assert t.status_code==200
    assert t.json()['usd_amount'] is None
