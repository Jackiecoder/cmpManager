import hashlib
import uuid
import pytest
from test_app import client, ready, project
from test_access import account, as_member, as_admin, grant


@pytest.fixture
def drive(monkeypatch):
    import app
    for key in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN'):
        monkeypatch.setenv(key, 'synthetic-test-only')
    state = {'uploads': [], 'files': [], 'gets': 0}
    class Reply:
        def __init__(self, data): self.data = data
        def json(self): return self.data
        def raise_for_status(self): pass
    def post(url, **kwargs):
        if 'oauth2' in url: return Reply({'access_token': 'synthetic-access'})
        state['uploads'].append(kwargs)
        return Reply({'id': 'synthetic-remote-file'})
    def get(url, **kwargs):
        state['gets'] += 1
        return Reply({'files': state['files']})
    monkeypatch.setattr(app.requests, 'post', post)
    monkeypatch.setattr(app.requests, 'get', get)
    return state


def upload(c, p, key=None, content=b'%PDF-synthetic', **data):
    return c.post('/api/upload', data={'project_id': p['id'], 'upload_key': key or str(uuid.uuid4()), **data}, files={'file': ('测试资料.pdf', content, 'application/pdf')})


def test_creation_retries_keep_one_project_and_membership_audit(client, drive):
    ready(client); member = account(client)
    body = {'title': 'Attachment project', 'creation_key': str(uuid.uuid4()), 'with_attachments': True, 'member_access': [{'user_id': member['id'], 'role': 'editor'}]}
    first = client.post('/api/records/projects', json=body)
    assert first.status_code == 200, first.text
    again = client.post('/api/records/projects', json=body)
    assert again.json()['id'] == first.json()['id']
    state = client.get('/api/state').json()['records']
    assert len([r for r in state if r['kind'] == 'projects']) == 1
    audit = client.get('/api/activity').json()
    assert len([r for r in audit if r['kind'] == 'projects']) == 1
    assert len([r for r in audit if r['kind'] == 'resource_access']) == 1
    as_member(client)
    assert upload(client, first.json()).status_code == 200


def test_unconfigured_drive_does_not_create_attachment_project(client, monkeypatch):
    ready(client)
    for key in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    r = client.post('/api/records/projects', json={'title': 'Not saved', 'with_attachments': True, 'creation_key': str(uuid.uuid4())})
    assert r.status_code == 503
    assert client.get('/api/state').json()['records'] == []
    assert client.post('/api/records/projects', json={'title':'No attachment'}).status_code == 200


def test_retry_returns_saved_file_with_one_drive_upload_and_audit(client, drive):
    ready(client); p = project(client); key = str(uuid.uuid4())
    first = upload(client, p, key)
    assert first.status_code == 200, first.text
    again = upload(client, p, key)
    assert again.status_code == 200 and first.json()['id'] == again.json()['id']
    assert len(drive['uploads']) == 1
    log = client.get('/api/activity?entity_id=' + first.json()['id']).json()
    assert len(log) == 1 and log[0]['actor_name'] == 'Kevin'
    assert upload(client, p, key, content=b'different').status_code == 409
    changed = client.patch('/api/records/files/' + first.json()['id'], json={'version': 1, 'title':'Renamed', 'upload_sha256':'forged'})
    assert changed.status_code == 200
    assert changed.json()['upload_sha256'] == hashlib.sha256(b'%PDF-synthetic').hexdigest()
    assert upload(client, p, key).json()['title'] == 'Renamed'


def test_retry_recovers_drive_file_after_lost_db_commit(client, drive, monkeypatch):
    import app
    ready(client); p = project(client); key = str(uuid.uuid4())
    real_audit = app.audit
    def failed_audit(c, actor, action, kind, *args, **kwargs):
        if kind == 'files': raise RuntimeError('synthetic DB failure')
        return real_audit(c, actor, action, kind, *args, **kwargs)
    monkeypatch.setattr(app, 'audit', failed_audit)
    with pytest.raises(RuntimeError, match='synthetic DB failure'): upload(client, p, key)
    assert not any(r['kind'] == 'files' for r in client.get('/api/state').json()['records'])
    drive['files'] = [{'id':'synthetic-remote-file', 'parents':[app.DRIVE_FOLDER], 'appProperties':{'cmpSha256':hashlib.sha256(b'%PDF-synthetic').hexdigest()}}]
    monkeypatch.setattr(app, 'audit', real_audit)
    retry = upload(client, p, key)
    assert retry.status_code == 200, retry.text
    assert len(drive['uploads']) == 1
    assert len(client.get('/api/activity?entity_id=' + retry.json()['id']).json()) == 1


def test_denied_project_and_other_spaces_never_contact_drive(client, drive):
    ready(client); member = account(client); p = project(client)
    grant(client, member, 'projects', p['id'], 'viewer')
    as_member(client)
    assert upload(client, p).status_code == 403
    assert client.post('/api/records/projects', json={'title':'Forbidden', 'creation_key':str(uuid.uuid4())}).status_code == 403
    as_admin(client)
    client.headers['X-Cmp-Workspace'] = 'personal'
    own = project(client)
    assert upload(client, own).status_code == 403
    assert drive['uploads'] == [] and drive['gets'] == 0


def test_invalid_reference_and_oversized_file_never_contact_drive(client, drive):
    ready(client); p = project(client)
    assert upload(client, p, subsection_id='missing').status_code == 400
    assert upload(client, p, key='invalid').status_code == 400
    assert upload(client, p, content=b'x' * (20 * 1024 * 1024 + 1)).status_code == 413
    assert drive['uploads'] == [] and drive['gets'] == 0


def test_expired_session_after_network_does_not_create_attachment(client, drive, monkeypatch):
    import app
    ready(client); p = project(client)
    original = app.current_user
    def expired(request, allow_change=False, connection=None):
        if connection is not None and drive['uploads']: app.fail('请先登录', 401)
        return original(request, allow_change, connection)
    monkeypatch.setattr(app, 'current_user', expired)
    assert upload(client, p).status_code == 401
    monkeypatch.setattr(app, 'current_user', original)
    assert not any(r['kind'] == 'files' for r in client.get('/api/state').json()['records'])


def test_drive_failure_returns_no_attachment_and_no_secret(client, drive, monkeypatch):
    import app
    ready(client); p = project(client)
    def failure(*args, **kwargs): raise app.requests.ConnectionError('secret-content-must-not-leak')
    monkeypatch.setattr(app.requests, 'post', failure)
    r = upload(client, p)
    assert r.status_code == 502 and 'secret-content' not in r.text
    assert not any(r['kind'] == 'files' for r in client.get('/api/state').json()['records'])
