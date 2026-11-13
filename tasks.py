"""Explicit, visitor-scoped task commands. No scheduling or model-driven writes.

Uses the existing database connection and signed visitor cookie. Titles are
Fernet encrypted at rest, never included in model context. Local SQLite is
clearly labelled temporary: this is not an always-on notification service.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi.responses import JSONResponse, StreamingResponse

import gmail_auth
import memory

KEEP_DAYS = 90
MAX_TASKS = 200
HELP = ("Task commands: /todo add <task>, /todo list, /todo done <id>, "
        "/todo reopen <id>, /todo delete <id>. Tasks older than 90 days are removed when you next use task commands. "
        "This is a checklist, not a reminder: no notifications or background actions.")


def _cipher():
    key = hashlib.sha256(('tasks:' + os.environ['SECRET_KEY']).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _schema():
    gmail_auth.db('CREATE TABLE IF NOT EXISTS cw_tasks (vid TEXT NOT NULL, id TEXT NOT NULL, request_id TEXT NOT NULL, enc TEXT NOT NULL, state TEXT NOT NULL, created BIGINT NOT NULL, updated BIGINT NOT NULL, PRIMARY KEY (vid, id), UNIQUE (vid, request_id))')


def erase(vid):
    _schema()
    gmail_auth.db('DELETE FROM cw_tasks WHERE vid = %s', (vid,))


def _insert(vid, request_id, enc, now):
    # Count + insert need a transaction lock: an INSERT ... SELECT alone can
    # overshoot under Postgres READ COMMITTED with multiple server workers.
    pg = gmail_auth._DB.startswith('postgres')
    with gmail_auth._lock:
        c = gmail_auth._conn()
        try:
            if pg:
                lock_id = int.from_bytes(hashlib.sha256(('task:' + vid).encode()).digest()[:8], 'big', signed=True)
                c.execute('SELECT pg_advisory_xact_lock(%s)', (lock_id,))
            else:
                c.execute('BEGIN IMMEDIATE')
            sql = ('INSERT INTO cw_tasks (vid,id,request_id,enc,state,created,updated) '
                   'SELECT %s,%s,%s,%s,%s,%s,%s WHERE (SELECT count(*) FROM cw_tasks WHERE vid = %s) < %s '
                   'ON CONFLICT (vid, request_id) DO NOTHING')
            c.execute(sql if pg else sql.replace('%s', '?'),
                      (vid, secrets.token_hex(8), request_id, enc, 'open', now, now, vid, MAX_TASKS))
            c.commit()
        finally:
            c.close()


def merge(old_vid, new_vid):
    # Preserve guest tasks on Google sign-in. Re-key retry tokens so two devices
    # cannot collide. A merged checklist may exceed the new-task cap; show all.
    _schema()
    gmail_auth.db("UPDATE cw_tasks SET vid = %s, request_id = 'merged_' || id WHERE vid = %s", (new_vid, old_vid))


def execute(vid, text, request_id):
    """Only call with the current request's raw user message, never model output."""
    if text == '/todo' or text == '/todo help':
        return HELP
    match = re.fullmatch(r'/todo (add|list|done|reopen|delete)(?: (.*))?', text, re.S)
    if not match:
        raise ValueError(HELP)
    action, arg = match.groups()
    arg = (arg or '').strip()
    if action == 'add':
        if not arg or len(arg) > 500:
            raise ValueError('Use /todo add followed by 1-500 characters.')
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', request_id):
            raise ValueError('A valid request_id is required to save a task safely.')
    elif action == 'list':
        if arg:
            raise ValueError('Use /todo list without extra text.')
    elif not re.fullmatch(r'[0-9a-f]{16}', arg):
        raise ValueError('Use the exact task id from /todo list.')
    _schema()
    now = int(time.time())
    cutoff = now - KEEP_DAYS * 86400
    gmail_auth.db('DELETE FROM cw_tasks WHERE vid = %s AND created <= %s', (vid, cutoff))
    if action == 'add':
        enc = _cipher().encrypt(arg.encode()).decode()
        _insert(vid, request_id, enc, now)
        rows = gmail_auth.db('SELECT id, enc, state FROM cw_tasks WHERE vid = %s AND request_id = %s', (vid, request_id), True)
        if not rows:
            raise ValueError('Task limit reached (200). Delete a task before adding another.')
        tid, saved_enc, state = rows[0]
        if _cipher().decrypt(saved_enc.encode()).decode() != arg:
            raise ValueError('This request id was already used for different task text. Nothing changed.')
        return f'Task saved: {tid} ({state}). Use /todo list to see your checklist. No reminder was set.'
    if action == 'list':
        rows = gmail_auth.db('SELECT id, enc, state FROM cw_tasks WHERE vid = %s ORDER BY state DESC, created, id', (vid,), True) or []
        lines = []
        for tid, enc, state in rows:
            # Do not silently drop unreadable rows and claim a complete checklist.
            title = _cipher().decrypt(enc.encode()).decode()
            # JSON quoting makes newlines/markup literal in the plain-text result.
            lines.append(f'{tid} [{state}] {json.dumps(title, ensure_ascii=False)}')
        return ('Your tasks:\n' + '\n'.join(lines) if lines else 'No saved tasks.') + '\nUse /todo for commands. Checklist only: no reminders or background actions.'
    if action == 'delete':
        rows = gmail_auth.db('DELETE FROM cw_tasks WHERE vid = %s AND id = %s RETURNING id', (vid, arg), True)
        return f'Task deleted: {arg}.' if rows else 'Task not found. Nothing deleted.'
    state = 'done' if action == 'done' else 'open'
    rows = gmail_auth.db('UPDATE cw_tasks SET state = %s, updated = %s WHERE vid = %s AND id = %s RETURNING id', (state, now, vid, arg), True)
    return f'Task {arg} is {state}.' if rows else 'Task not found. Nothing changed.'


def response(req, text, request_id, vid, new_vid):
    if req.headers.get('x-requested-with') != 'crayon':
        return JSONResponse({'error': 'bad request'}, status_code=403)
    if not memory.on():
        return JSONResponse({'error': 'Task storage is unavailable. Nothing saved.'}, status_code=503)
    try:
        result = execute(vid, text, request_id)
    except InvalidToken:
        return JSONResponse({'error': 'Saved tasks could not be decrypted. Nothing has been listed as complete.'}, status_code=503)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    except Exception:
        # A commit may have succeeded before a read-back failed. Never claim that
        # nothing saved in this case; retry with the same request id or list.
        return JSONResponse({'error': 'Task storage could not be verified. Use /todo list to check before adding again.'}, status_code=503)
    mode = 'persistent database' if gmail_auth._DB.startswith('postgres') else 'temporary local storage (may be lost on restart)'
    result += '\nStorage: ' + mode + '. Keep this browser identity; clearing cookies can lose access to tasks.'
    async def events():
        yield 'data: ' + json.dumps({'t': result, 'task_result': True}) + '\n\n'
        yield 'data: [DONE]\n\n'
    resp = StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
    if new_vid:
        memory.set_vid_cookie(resp, vid)
    return resp
