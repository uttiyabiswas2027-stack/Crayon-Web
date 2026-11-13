"""Synthetic local-store tests. No credentials, live DB or provider calls."""
import concurrent.futures
import json
import os
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as appmod
import gmail_auth
import memory
import tasks


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('SECRET_KEY', 'synthetic-task-secret')
    monkeypatch.setenv('LOCAL_DB', str(tmp_path / 'tasks.db'))
    monkeypatch.setattr(gmail_auth, '_DB', '')
    monkeypatch.setattr(gmail_auth, '_ready', False)
    monkeypatch.setattr(appmod, 'limited', lambda ip: None)
    monkeypatch.setattr(appmod, 'model', lambda *a: pytest.fail('task commands must not call a model'))


def client():
    return TestClient(appmod.app, base_url='https://testserver')


def post(c, text, rid='synthetic-request-0001', header=True):
    return c.post('/api/chat', json={'message': text, 'request_id': rid},
                  headers={'X-Requested-With': 'crayon'} if header else {})


def output(r):
    assert r.status_code == 200, r.text
    events = [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith('data: {')]
    assert all(x.get('task_result') is True for x in events)
    return ''.join(x['t'] for x in events)


def add(c, text='Buy notebooks', rid='synthetic-request-0001'):
    text = output(post(c, '/todo add ' + text, rid))
    return re.search(r'Task saved: ([a-f0-9]{16})', text)[1]


def test_full_lifecycle_and_new_chat_survival():
    c = client(); tid = add(c)
    assert 'Buy notebooks' in output(post(c, '/todo list'))
    assert 'is done' in output(post(c, '/todo done ' + tid))
    assert '[done]' in output(post(c, '/todo list'))
    assert 'is open' in output(post(c, '/todo reopen ' + tid))
    c.post('/api/reset', json={'session_id': 'new-chat'})
    assert tid in output(post(c, '/todo list'))
    assert 'deleted' in output(post(c, '/todo delete ' + tid))
    assert 'No saved tasks' in output(post(c, '/todo list'))


def test_retry_is_idempotent_and_conflicting_text_rejected():
    c = client(); tid = add(c)
    assert add(c) == tid
    assert post(c, '/todo add Other task').status_code == 400
    assert output(post(c, '/todo list')).count('Buy notebooks') == 1


def test_visitor_isolation_and_forged_cookie():
    a, b = client(), client(); tid = add(a, 'Private task')
    assert 'Private task' not in output(post(b, '/todo list'))
    assert 'not found' in output(post(b, '/todo done ' + tid))
    assert 'not found' in output(post(b, '/todo delete ' + tid))
    assert '[open]' in output(post(a, '/todo list'))
    b.cookies.set('crayon_vid', 'v_' + 'a'*32)
    assert tid not in output(post(b, '/todo list'))


def test_encrypted_at_rest_and_readable_after_new_client():
    c = client(); tid = add(c, 'Synthetic sensitive title')
    raw = open(os.environ['LOCAL_DB'], 'rb').read()
    assert b'Synthetic sensitive title' not in raw
    fresh = client(); fresh.cookies.update(c.cookies)
    assert tid in output(post(fresh, '/todo list'))


def test_forget_erases_tasks_and_db_failure_not_success(monkeypatch):
    c = client(); add(c)
    vid = gmail_auth._unsign(c.cookies.get('crayon_vid'))
    memory.save_turn(vid, 'synthetic chat', 'synthetic reply')
    memory.save_chat_turn(vid, 'synthetic-chat', 'synthetic chat', 'synthetic reply')
    r = c.post('/api/forget', headers={'X-Requested-With': 'crayon'})
    assert r.status_code == 200
    assert memory.load_context(vid) == []
    assert memory.list_chats(vid) == []
    assert memory.load_chat(vid, 'synthetic-chat') == []
    assert 'No saved tasks' in output(post(c, '/todo list'))
    monkeypatch.setattr(tasks, 'erase', lambda v: (_ for _ in ()).throw(RuntimeError()))
    assert c.post('/api/forget', headers={'X-Requested-With': 'crayon'}).status_code == 503


def test_secret_off_and_csrf_denied(monkeypatch):
    c = client()
    assert post(c, '/todo add test', header=False).status_code == 403
    monkeypatch.delenv('SECRET_KEY')
    assert post(c, '/todo list').status_code == 503
    assert c.post('/api/forget', headers={'X-Requested-With': 'crayon'}).status_code == 503


@pytest.mark.parametrize('cmd', ['/todo unknown', '/todo add', '/todo list extra', '/todo done other', '/todo delete ../../', '/todo add ' + 'x'*501])
def test_validation(cmd):
    assert post(client(), cmd).status_code == 400


def test_bad_idempotency_key():
    assert post(client(), '/todo add test', 'short').status_code == 400


def test_database_failure_is_not_empty_or_saved(monkeypatch):
    monkeypatch.setattr(tasks, '_schema', lambda: (_ for _ in ()).throw(RuntimeError()))
    for cmd in ['/todo list', '/todo add A']:
        r = post(client(), cmd)
        assert r.status_code == 503
        assert 'could not be verified' in r.json()['error']


def test_expiry_and_cap(monkeypatch):
    c = client()
    monkeypatch.setattr(tasks, 'MAX_TASKS', 2)
    add(c, 'A'); add(c, 'B', 'synthetic-request-0002')
    assert post(c, '/todo add C', 'synthetic-request-0003').status_code == 400
    with sqlite3.connect(os.environ['LOCAL_DB']) as db:
        db.execute('UPDATE cw_tasks SET created = 0')
    assert 'No saved tasks' in output(post(c, '/todo list'))
    add(c, 'C', 'synthetic-request-0003')


def test_concurrent_retries_single_row():
    vid = 'v_' + 'a'*32
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda _: tasks.execute(vid, '/todo add one', 'synthetic-request-0001'), range(12)))
    assert gmail_auth.db('SELECT count(*) FROM cw_tasks', (), True)[0][0] == 1


def test_task_text_is_plain_data_not_tool_output():
    c = client()
    title = '<img src=x onerror=alert(1)> ```python-run print(1)```'
    add(c, title)
    result = output(post(c, '/todo list'))
    assert title in result
    js = open(os.path.join(os.path.dirname(appmod.__file__), 'app.js')).read()
    assert "if (taskResult) { bub.textContent = acc;" in js
    assert "stick(); return null;" in js


def test_storage_caveat_and_no_model_context():
    c = client(); add(c)
    result = output(post(c, '/todo list'))
    assert 'temporary local storage' in result and 'no reminders' in result
    assert not appmod.sessions or all('Buy notebooks' not in str(v) for v in appmod.sessions.values())


def test_merge_preserves_both_checklists_and_erase_scope():
    old, new = 'v_' + 'a'*32, 'v_' + 'b'*32
    tasks.execute(old, '/todo add guest task', 'synthetic-request-0001')
    tasks.execute(new, '/todo add account task', 'synthetic-request-0001')
    tasks.merge(old, new)
    result = tasks.execute(new, '/todo list', None)
    assert 'guest task' in result and 'account task' in result
    assert 'No saved tasks' in tasks.execute(old, '/todo list', None)
    tasks.erase(old)
    assert 'guest task' in tasks.execute(new, '/todo list', None)


def test_corrupt_ciphertext_is_failure_not_partial_list():
    c = client(); add(c)
    gmail_auth.db('UPDATE cw_tasks SET enc = %s', ('not-fernet',))
    r = post(c, '/todo list')
    assert r.status_code == 503 and 'could not be decrypted' in r.json()['error']


def test_concurrent_adds_cannot_exceed_cap(monkeypatch):
    monkeypatch.setattr(tasks, 'MAX_TASKS', 3)
    def attempt(n):
        try:
            tasks.execute('v_' + 'a'*32, '/todo add task', 'synthetic-request-%04d' % n)
        except ValueError:
            pass
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(attempt, range(12)))
    assert gmail_auth.db('SELECT count(*) FROM cw_tasks', (), True)[0][0] == 3
