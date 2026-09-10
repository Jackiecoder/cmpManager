import copy
import json

import pytest
from test_app import client, ready
from scripts.import_sheet_ledger import HEADERS, import_ledger, parse_sheet, summarize


def snapshot():
    # Synthetic data only. A missing date/payment and a non-income loan are
    # deliberate: none should be assigned invented accounting meaning.
    rows = [HEADERS,
            [46179, 'Dues', 'Event', 100, '', 100, '收入', '已付', '转账', '平帐', 0, 'Member', 100, 100, '', 100, ''],
            ['', 'Supplies', 'Event', '', 10, 1.475, '支出', '', '现金', '待支出', -1, 'Member', -1.475, '', -1.475, 98.525, 'Source note'],
            ['', 'Loan', '', 50, '', 50, '其他', '已付', '转账', '平帐', 0, '', 0, '', '', 98.525, 'Loan note']]
    def cell(value):
        if value == '': return {}
        return {'effectiveValue': {('numberValue' if isinstance(value, (float, int)) else 'stringValue'): value}}
    return {'spreadsheetId': 'synthetic-sheet', 'sheets': [
        {'properties': {'sheetId': 0, 'title': 'Ledger'},
         'data': [{'rowData': [{'values': [cell(v) for v in row]} for row in rows]}]}]}


def test_source_mapping_preserves_unknowns_and_precision():
    rows = parse_sheet(snapshot(), 0)
    assert rows[0]['date'] == '2026-06-06'
    assert rows[1]['date'] == '' and rows[1]['payment_status'] == '待确认'
    assert rows[1]['amount'] == '10.00' and rows[1]['usd_amount'] == '1.48'
    assert rows[1]['booked_amount'] == '-1.00'
    assert rows[1]['reimbursement_status'] == '待确认'
    assert rows[1]['source_import']['raw_cells'][5]['effectiveValue']['numberValue'] == 1.475
    assert rows[2]['direction'] == '其他' and rows[2]['reimbursement_status'] == '不需报销'
    totals = summarize(rows)
    assert totals['directions'] == {'收入': 1, '支出': 1, '其他': 1}
    assert totals['usd_rounded_per_row']['支出'] == '1.48'
    assert totals['usd_source_precision']['支出'] == '1.475'


@pytest.mark.parametrize('change', ['header', 'currency', 'formula', 'error', 'extra', 'subcent'])
def test_bad_snapshot_is_rejected(change):
    source = snapshot()
    cells = source['sheets'][0]['data'][0]['rowData']
    if change == 'header': cells[0]['values'][0] = {}
    if change == 'currency': cells[1]['values'][4] = {'effectiveValue': {'numberValue': 3}}
    if change == 'formula': cells[1]['values'][12] = {'effectiveValue': {'numberValue': 999}}
    if change == 'error': cells[1]['values'][5] = {'effectiveValue': {'errorValue': {'type': 'REF'}}}
    if change == 'extra': cells[1]['values'].append({'effectiveValue': {'stringValue': 'unmapped'}})
    if change == 'subcent': cells[2]['values'][4] = {'effectiveValue': {'numberValue': 10.111}}
    with pytest.raises(ValueError): parse_sheet(source, 0)


def test_import_atomic_idempotent_and_source_immutable(client):
    ready(client)
    import app
    rows = parse_sheet(snapshot(), 0)
    with app.db() as c:
        preview = import_ledger(c, rows, 'Test profile', 'kevin')
    assert preview['new'] == 3 and not preview['applied']
    assert client.get('/api/state').json()['records'] == []
    with app.db() as c:
        report = import_ledger(c, rows, 'Test profile', 'kevin', apply=True)
    saved = client.get('/api/state').json()['records']
    assert len(saved) == 4
    t = next(t for t in saved if t.get('title') == 'Supplies')
    assert t['profile_id'] == report['profile_id'] and t['date'] == ''
    audit = client.get('/api/activity?entity_id=' + t['id']).json()
    assert len(audit) == 1 and audit[0]['action'] == '导入' and audit[0]['before'] is None
    assert audit[0]['actor'] == client.get('/api/me').json()['id']
    assert audit[0]['after']['source_import'] == t['source_import']
    edited = client.patch('/api/records/transactions/' + t['id'], json={
        'version': 1, 'note': 'Actual update', 'source_import': {'row': 999}})
    assert edited.status_code == 200
    assert edited.json()['source_import'] == t['source_import']
    with app.db() as c:
        retry = import_ledger(c, rows, 'Test profile', 'kevin', apply=True)
    assert retry['new'] == 0 and retry['skipped'] == 3
    assert next(t for t in client.get('/api/state').json()['records'] if t['title'] == 'Supplies')['note'] == 'Actual update'
    assert len(client.get('/api/activity').json()) == 5  # profile + 3 imports + edit; password events are account-private
    # A source revision is a review conflict, never an automatic overwrite.
    changed = copy.deepcopy(rows)
    changed[0]['source_import']['sha256'] = 'changed'
    with pytest.raises(ValueError), app.db() as c:
        import_ledger(c, changed, 'Test profile', 'kevin', apply=True)


def test_audit_failure_rolls_back_whole_import(client, monkeypatch):
    ready(client)
    import app
    original = app.audit
    def audit(c, actor, action, kind, entity_id, before=None, after=None):
        if after.get('title') == 'Supplies': raise RuntimeError('Simulated audit failure')
        return original(c, actor, action, kind, entity_id, before, after)
    monkeypatch.setattr(app, 'audit', audit)
    before = client.get('/api/activity').json()
    with pytest.raises(RuntimeError), app.db() as c:
        import_ledger(c, parse_sheet(snapshot(), 0), 'Test profile', 'kevin', apply=True)
    assert client.get('/api/state').json()['records'] == []
    assert client.get('/api/activity').json() == before


def test_native_duplicate_and_unknown_actor_abort(client):
    ready(client)
    import app
    rows = parse_sheet(snapshot(), 0)
    profile = client.post('/api/records/finance_profiles', json={'title': 'Test profile'}).json()
    r = client.post('/api/records/transactions', json={**rows[0], 'profile_id': profile['id']})
    assert r.status_code == 200 and 'source_import' not in r.json()
    before = client.get('/api/state').json()['records']
    with pytest.raises(ValueError), app.db() as c:
        import_ledger(c, rows, 'Test profile', 'kevin', apply=True)
    with pytest.raises(ValueError), app.db() as c:
        import_ledger(c, rows, 'Other profile', 'missing', apply=True)
    assert client.get('/api/state').json()['records'] == before


def test_other_direction_cannot_claim_reimbursement(client):
    ready(client)
    r = client.post('/api/records/transactions', json={'title': 'Loan', 'direction': '其他',
        'amount': '50', 'date': '', 'reimbursement_status': '待报销', 'claim_amount': '50'})
    assert r.status_code == 400
