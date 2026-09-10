"""Import a reviewed Google Sheets cell snapshot into one finance profile.

Administrative CLI, not a public upload endpoint. Supply DATABASE_URL through
the environment (or SQLITE_PATH for local tests). Preview is the default;
--apply commits the entire batch and its audit records together. Keep snapshots
and reports outside Git, e.g. under .local/. The source Sheet is never changed.
"""
import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

HEADERS = ['日期', '项目', '事件', '金额', '人民币支付', '转化美元', '支出/收入',
           '支付状态', '付款形式', '入账状态', '入账金额', '负责人', '最终金额',
           '收入', '支出', '总数额', '备注']


def cell_value(cell):
    value = cell.get('effectiveValue', {})
    if 'errorValue' in value: raise ValueError('Source contains a formula error')
    if not value: return ''
    for key in ('numberValue', 'stringValue'):
        if key in value: return value[key]
    raise ValueError('Unsupported cell value')


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite(): raise ValueError('Non-finite source amount')
    return result


def cents(value):
    return None if value in ('', None) else str(decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def sheet_date(value):
    if value == '': return ''
    if isinstance(value, (int, float)):
        if int(value) != value: raise ValueError('Date has an unexpected time component')
        return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
    return date.fromisoformat(value).isoformat()


def parse_sheet(snapshot, sheet_id):
    sheet = next(s for s in snapshot['sheets'] if s['properties']['sheetId'] == sheet_id)
    blocks = sheet['data']
    if len(blocks) != 1 or blocks[0].get('startRow', 0) or blocks[0].get('startColumn', 0):
        raise ValueError('Snapshot must start at A1 and contain one complete ledger range')
    rows = blocks[0]['rowData']
    if [cell_value(c) for c in rows[0]['values'][:17]] != HEADERS:
        raise ValueError('Ledger headers do not match the reviewed mapping')
    result = []
    for row_number, row in enumerate(rows[1:], 2):
        cells = row.get('values', [])
        values = [cell_value(c) for c in cells]
        if all(v == '' for v in values): continue
        if any(v != '' for v in values[17:]): raise ValueError(f'Unexpected extra fields at row {row_number}')
        values = (values + [''] * 17)[:17]
        d, title, event, usd, rmb, converted, direction, payment, method, posting, booked, responsible, final, income, expense, balance, note = values
        if not title or (usd == '') == (rmb == ''):
            raise ValueError(f'Row {row_number} needs a title and exactly one source currency')
        currency, amount = ('USD', usd) if usd != '' else ('CNY', rmb)
        if decimal(amount) != decimal(cents(amount)):
            raise ValueError(f'Original currency amount has sub-cent precision at row {row_number}')
        if direction not in ('收入', '支出', '其他') or converted == '':
            raise ValueError(f'Unknown direction or missing conversion at row {row_number}')
        if currency == 'USD' and decimal(converted) != decimal(amount):
            raise ValueError(f'Unexpected USD conversion at row {row_number}')
        # Check the source's derived income/expense cells. Running totals and
        # summary tabs are retained as evidence, never imported as extra rows.
        expected_income = decimal(converted) if direction == '收入' else Decimal(0)
        expected_expense = -decimal(converted) if direction == '支出' else Decimal(0)
        for actual, expected in [(income, expected_income), (expense, expected_expense), (final, expected_income + expected_expense)]:
            if abs(decimal(actual or 0) - expected) > Decimal('.000001'):
                raise ValueError(f'Source formula disagrees with ledger fields at row {row_number}')
        raw = (cells + [{}] * 17)[:17]
        digest = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        result.append({
            'title': str(title), 'date': sheet_date(d), 'event': str(event),
            'currency': currency, 'amount': cents(amount), 'usd_amount': cents(converted),
            'direction': direction, 'payment_status': payment or '待确认',
            'payment_method': str(method), 'posting_status': posting,
            'booked_amount': cents(booked), 'responsible': str(responsible), 'note': str(note),
            'reimbursement_status': '待确认' if direction == '支出' else '不需报销',
            'source_import': {'spreadsheet_id': snapshot['spreadsheetId'], 'sheet_id': sheet_id,
                              'sheet_title': sheet['properties']['title'], 'row': row_number,
                              'sha256': digest, 'raw_cells': raw},
        })
    if not result: raise ValueError('No ledger rows found')
    return result


def source_key(data):
    source = data.get('source_import')
    return (source['spreadsheet_id'], source['sheet_id'], source['row']) if source else None


def signature(data):
    return tuple(data.get(k, '') for k in ('title', 'date', 'event', 'currency', 'amount', 'direction'))


def summarize(rows):
    totals = {}
    converted = {key: Decimal(0) for key in ('收入', '支出', '其他')}
    exact = converted.copy()
    for row in rows:
        group = totals.setdefault(row['currency'], {key: Decimal(0) for key in ('收入', '支出', '其他')})
        group[row['direction']] += decimal(row['amount'])
        converted[row['direction']] += decimal(row['usd_amount'])
        exact[row['direction']] += decimal(cell_value(row['source_import']['raw_cells'][5]))
    return {'count': len(rows), 'directions': dict(Counter(r['direction'] for r in rows)),
            'missing_dates': sum(not r['date'] for r in rows),
            'unknown_payments': sum(r['payment_status'] == '待确认' for r in rows),
            'original_currency_totals': {c: {k: str(v) for k, v in t.items()} for c, t in totals.items()},
            'usd_rounded_per_row': {k: str(v) for k, v in converted.items()},
            'usd_source_precision': {k: str(v) for k, v in exact.items()}}


def import_ledger(c, rows, profile_title, actor_username, apply=False):
    app.lock_records(c)
    user = c.execute('SELECT id, role, active FROM users WHERE username=?', (actor_username,)).fetchone()
    if not user or not user['active'] or user['role'] != 'admin':
        raise ValueError('Import requires an existing active administrator')
    title = app.text_field({'title': profile_title}, 'title', 120, True)
    profiles = [app.entity(r) for r in c.records("kind='finance_profiles'").fetchall()]
    matches = [p for p in profiles if p['title'] == title]
    if len(matches) > 1: raise ValueError('Ambiguous target profile name')
    profile_id = matches[0]['id'] if matches else ''
    existing = [app.entity(r) for r in c.records("kind='transactions'").fetchall()]
    sources = {}
    native = {signature(r) for r in existing if r.get('profile_id', '') == profile_id and not source_key(r)} if profile_id else set()
    for record in existing:
        key = source_key(record)
        if key:
            if key in sources: raise ValueError('Duplicate source references already exist')
            sources[key] = record
    prepared, skipped, seen = [], [], set()
    for row in rows:
        key = source_key(row)
        if not key or key in seen: raise ValueError('Duplicate or missing source row')
        seen.add(key)
        data = app.validate('transactions', {**row, 'profile_id': profile_id}, c)
        if key in sources:
            record = sources[key]
            if record.get('profile_id') != profile_id or record['source_import']['sha256'] != row['source_import']['sha256']:
                raise ValueError(f'Source changed or record moved for row {key[2]}; review before importing')
            skipped.append(record['id'])
            continue
        if signature(data) in native: raise ValueError(f'Possible previously entered duplicate at row {key[2]}')
        prepared.append({**data, 'source_import': {**row['source_import'], 'imported_at': app.now()}})
    report = {**summarize(rows), 'profile': title, 'profile_id': profile_id,
              'new': len(prepared), 'skipped': len(skipped), 'applied': apply, 'inserted_ids': []}
    if apply and prepared:
        if not profile_id:
            profile = app.validate('finance_profiles', {'title': title}, c)
            profile_id = app.insert_record(c, 'finance_profiles', profile, user['id'])
            report['profile_id'] = profile_id
        for data in prepared:
            data['profile_id'] = profile_id
            report['inserted_ids'].append(app.insert_record(c, 'transactions', data, user['id'], action='导入'))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--sheet-id', required=True, type=int)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--actor-username', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    rows = parse_sheet(json.loads(args.snapshot.read_text()), args.sheet_id)
    with app.db() as c:
        report = import_ledger(c, rows, args.profile, args.actor_username, args.apply)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(output + '\n')
        args.report.chmod(0o600)
    print(output)


if __name__ == '__main__': main()
