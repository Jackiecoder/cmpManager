import pytest
from test_app import client, ready, project
from test_subsections import create


def test_timeline_default_toggle_persistence_and_audit(client):
    ready(client)
    p = project(client)
    note = create(client, 'notes', title='沟通', body='测试内容', date='2026-09-07', project_id=p['id'])
    assert note['show_on_timeline'] is True
    updated = client.patch('/api/records/notes/' + note['id'], json={'version': 1, 'show_on_timeline': False}).json()
    assert updated['show_on_timeline'] is False
    updated = client.patch('/api/records/notes/' + note['id'], json={'version': 2, 'body': '修改内容'}).json()
    assert updated['show_on_timeline'] is False
    stored = next(r for r in client.get('/api/state').json()['records'] if r['id'] == note['id'])
    assert stored['body'] == '修改内容' and stored['show_on_timeline'] is False
    history = client.get('/api/activity?entity_id=' + note['id']).json()
    assert history[1]['before']['show_on_timeline'] is True
    assert history[1]['after']['show_on_timeline'] is False
    assert client.patch('/api/records/notes/' + note['id'], json={'version': 3, 'show_on_timeline': True}).json()['show_on_timeline'] is True


def test_hidden_note_can_still_create_linked_todo(client):
    ready(client)
    p = project(client)
    note = create(client, 'notes', title='内部记录', body='需要跟进', date='2026-09-07', project_id=p['id'], show_on_timeline=False, task_action='create')
    assert note['show_on_timeline'] is False and note['linked_task_id']


@pytest.mark.parametrize('invalid', ['false', 'true', 0, 1, None])
def test_visibility_requires_boolean(client, invalid):
    ready(client)
    p = project(client)
    response = client.post('/api/records/notes', json={'title':'N','body':'B','date':'2026-09-07','project_id':p['id'],'show_on_timeline':invalid})
    assert response.status_code == 400
