"""Company Manager: authenticated shared ledger and project journal."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager, asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
PRODUCTION = bool(os.getenv('K_SERVICE'))
DATABASE_URL = os.getenv('DATABASE_URL', '')
DRIVE_FOLDER = os.getenv('DRIVE_FOLDER_ID', '1N58QFJPdxpIjsdzZaUIn2tm39_yp4wD2')
SHEET_URL = 'https://docs.google.com/spreadsheets/d/1R9hbqZftVj6BWH82d2l-RVYMUzz6XTed6LEFcibKWWU/edit'
FINANCE_MEMBERS = os.getenv('FINANCE_MEMBERS', 'true').lower() == 'true'
COMPANY_SPACE = 'company'
SYSTEM_SPACE = 'system'


class DB:
    def __init__(self, conn):
        self.conn = conn
        self.workspace_id = COMPANY_SPACE
    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace('?', '%s') if DATABASE_URL else sql, params)

    def scope(self, workspace_id):
        self.workspace_id = workspace_id
        if DATABASE_URL: self.execute("SELECT set_config('cmp.workspace_id', ?, true)", (workspace_id,))

    def records(self, where='1=1', params=(), columns='*', order=''):
        return self.execute(f'SELECT {columns} FROM entities WHERE workspace_id=? AND ({where})' + (f' ORDER BY {order}' if order else ''), (self.workspace_id, *params))


@contextmanager
def db():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    else:
        if PRODUCTION: raise RuntimeError('Production requires persistent PostgreSQL DATABASE_URL')
        path = os.getenv('SQLITE_PATH', str(ROOT / '.local/app.sqlite'))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
    try:
        connection = DB(conn)
        connection.scope(COMPANY_SPACE)
        yield connection
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally: conn.close()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return salt + ':' + digest


def check_password(password, stored):
    return hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored)


def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())
def fail(message, status=400): raise HTTPException(status, message)
def clean_user(row): return {k: row[k] for k in ('id', 'username', 'name', 'role', 'active', 'must_change')}


def audit(c, actor, action, kind, entity_id, before=None, after=None):
    c.execute('INSERT INTO audit (id,at,actor,action,kind,entity_id,before_data,after_data,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)',
              (uid(), now(), actor, action, kind, entity_id, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), c.workspace_id))


def initialize():
    with db() as c:
        if DATABASE_URL: c.execute('SELECT pg_advisory_xact_lock(74391001)')
        for sql in [
            'CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, password TEXT NOT NULL, active INTEGER NOT NULL, must_change INTEGER NOT NULL)',
            'CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)',
            'CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_by TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL, before_data TEXT NOT NULL, after_data TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS login_limits (key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset_at DOUBLE PRECISION NOT NULL)',
            'CREATE INDEX IF NOT EXISTS entities_kind ON entities(kind)',
            'CREATE INDEX IF NOT EXISTS audit_time ON audit(at)'
        ]: c.execute(sql)
        if not c.execute('SELECT id FROM users LIMIT 1').fetchone():
            password = os.getenv('ADMIN_INITIAL_PASSWORD')
            if not password or len(password) < 12: raise RuntimeError('Set ADMIN_INITIAL_PASSWORD to at least 12 characters for first startup')
            c.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?)', (uid(), 'kevin', 'Kevin', 'admin', password_hash(password), 1, 1))
        migrate_workspaces(c)


def personal_space(user_id): return 'personal:' + user_id


def create_personal_space(c, user_id):
    c.execute('INSERT INTO workspaces VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
              (personal_space(user_id), 'personal', '个人空间', user_id, now()))


def migrate_workspaces(c):
    for table in ('entities', 'audit'):
        if DATABASE_URL:
            c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS workspace_id TEXT NOT NULL DEFAULT 'company'")
        elif 'workspace_id' not in [r['name'] for r in c.execute(f'PRAGMA table_info({table})').fetchall()]:
            c.execute(f"ALTER TABLE {table} ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'company'")
    for sql in [
        'CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, owner_id TEXT NOT NULL, created_at TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS memberships (workspace_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(workspace_id,user_id))',
        'CREATE TABLE IF NOT EXISTS maintenance_access (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, actor TEXT NOT NULL, session_hash TEXT NOT NULL, reason TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL, revoked INTEGER NOT NULL, created_at TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)',
        'CREATE INDEX IF NOT EXISTS entities_workspace ON entities(workspace_id,kind)',
        'CREATE INDEX IF NOT EXISTS audit_workspace ON audit(workspace_id,at)',
    ]: c.execute(sql)
    if not c.execute("SELECT name FROM app_migrations WHERE name='workspaces-v1'").fetchone():
        owner = c.execute("SELECT id FROM users WHERE role='admin' ORDER BY CASE WHEN username='kevin' THEN 0 ELSE 1 END, id LIMIT 1").fetchone()
        if not owner: raise RuntimeError('Workspace migration needs an existing administrator')
        c.execute('INSERT INTO workspaces VALUES (?,?,?,?,?)', (COMPANY_SPACE, 'team', 'C&K Commerce', owner['id'], now()))
        for user in c.execute('SELECT id FROM users').fetchall():
            create_personal_space(c, user['id'])
            c.execute('INSERT INTO memberships VALUES (?,?,?,?,?)', (COMPANY_SPACE, user['id'], 'owner' if user['id'] == owner['id'] else 'editor', 1, now()))
        # Account security events are not team business history.
        c.execute("UPDATE audit SET workspace_id='system' WHERE kind='user'")
        c.execute('INSERT INTO app_migrations VALUES (?,?)', ('workspaces-v1', now()))
    if DATABASE_URL:
        # FORCE also covers the table-owning runtime role. Older revisions that
        # do not set a scope can only see company rows during a rolling release.
        for table in ('entities', 'audit'):
            c.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            c.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
            if not c.execute('SELECT policyname FROM pg_policies WHERE schemaname=current_schema() AND tablename=? AND policyname=?', (table, 'workspace_isolation')).fetchone():
                c.execute(f"CREATE POLICY workspace_isolation ON {table} USING (workspace_id=COALESCE(NULLIF(current_setting('cmp.workspace_id',true),''),'company')) WITH CHECK (workspace_id=COALESCE(NULLIF(current_setting('cmp.workspace_id',true),''),'company'))")


def accessible_spaces(c, user):
    return [dict(r) for r in c.execute('''SELECT w.*, CASE WHEN w.kind='personal' THEN 'owner' ELSE m.role END AS access
        FROM workspaces w LEFT JOIN memberships m ON m.workspace_id=w.id AND m.user_id=?
        WHERE (w.kind='personal' AND w.owner_id=?) OR (w.kind='team' AND m.role IN ('owner','editor','viewer'))
        ORDER BY w.kind, w.created_at''', (user['id'], user['id'])).fetchall()]


def scope_request(c, request, user, write=False):
    active = c.execute('SELECT active FROM users WHERE id=?', (user['id'],)).fetchone()
    if not active or not active['active']: fail('请先登录', 401)
    requested = request.headers.get('x-cmp-workspace', '')
    if requested == 'personal': requested = personal_space(user['id'])
    if not requested:
        member = c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?', (COMPANY_SPACE, user['id'])).fetchone()
        requested = COMPANY_SPACE if member and member['role'] != 'removed' else personal_space(user['id'])
    workspace = c.execute('SELECT * FROM workspaces WHERE id=?', (requested,)).fetchone()
    if not workspace: fail('无权访问此空间', 403)
    workspace = dict(workspace)
    access = None
    if workspace['kind'] == 'personal' and workspace['owner_id'] == user['id']: access = 'owner'
    elif workspace['kind'] == 'team':
        member = c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?', (requested, user['id'])).fetchone()
        if member and member['role'] in ('owner', 'editor', 'viewer'): access = member['role']
    maintenance = None
    if not access and workspace['kind'] == 'personal' and user['role'] == 'admin':
        grant_id = request.headers.get('x-cmp-maintenance', '')
        grant = c.execute('SELECT * FROM maintenance_access WHERE id=? AND workspace_id=? AND actor=? AND session_hash=? AND revoked=0 AND expires>?',
            (grant_id, requested, user['id'], hashlib.sha256(request.cookies.get('cmp_session', '').encode()).hexdigest(), time.time())).fetchone()
        if grant: access, maintenance = 'viewer', dict(grant)
    if not access: fail('无权访问此空间', 403)
    if write and access == 'viewer': fail('当前空间为只读权限，不能修改记录', 403)
    c.scope(requested)
    if maintenance:
        audit(c, user['id'], '维护读取', 'maintenance', maintenance['id'], after={'reason': maintenance['reason'], 'path': request.url.path})
    return {**workspace, 'access': access, 'maintenance': bool(maintenance), 'maintenance_expires': maintenance['expires'] if maintenance else None}


def require_team_owner(c, workspace_id, user):
    workspace = c.execute("SELECT * FROM workspaces WHERE id=? AND kind='team' AND owner_id=?", (workspace_id, user['id'])).fetchone()
    if not workspace: fail('只有此团队的 owner 可以管理成员', 403)
    c.scope(workspace_id)
    return workspace


def scope_roster(c):
    workspace = c.execute('SELECT kind, owner_id FROM workspaces WHERE id=?', (c.workspace_id,)).fetchone()
    if workspace['kind'] == 'personal':
        return [clean_user(r) for r in c.execute('SELECT * FROM users WHERE id=?', (workspace['owner_id'],)).fetchall()]
    return [{**clean_user(r), 'team_role': r['team_role'], 'membership_version': r['membership_version']} for r in c.execute('''SELECT u.*, m.role AS team_role, m.version AS membership_version FROM users u JOIN memberships m ON m.user_id=u.id
        WHERE m.workspace_id=? AND m.role IN ('owner','editor','viewer') ORDER BY u.name''', (c.workspace_id,)).fetchall()]


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(title='Company Manager', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def security(request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': '请求来源不匹配，请重新打开应用'}, status_code=403)
        if request.url.path.startswith('/api/') and request.headers.get('x-cmp-request') != '1':
            return JSONResponse({'detail': '请求校验失败'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    if request.url.path.startswith('/api/'): response.headers['Cache-Control'] = 'no-store'
    if PRODUCTION: response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


def current_user(request, allow_change=False):
    token = hashlib.sha256(request.cookies.get('cmp_session', '').encode()).hexdigest()
    with db() as c:
        row = c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>? AND u.active=1', (token, time.time())).fetchone()
    if not row: fail('请先登录', 401)
    if row['must_change'] and not allow_change: fail('请先修改初始密码', 403)
    return clean_user(row)


def administrator(user):
    if user['role'] != 'admin': fail('只有管理员可以进行此操作', 403)


@app.get('/api/healthz')
def health():
    with db() as c: c.execute('SELECT 1').fetchone()
    return {'ok': True, 'revision': os.getenv('K_REVISION', 'local')}


@app.post('/api/login')
def login(body: dict, request: Request, response: Response):
    username = str(body.get('username', '')).strip().lower()[:80]
    password = str(body.get('password', ''))[:256]
    key = hashlib.sha256(username.encode()).hexdigest()
    blocked = False
    user = None
    with db() as c:
        c.execute('INSERT INTO login_limits VALUES (?,0,?) ON CONFLICT(key) DO NOTHING', (key, time.time() + 900))
        suffix = ' FOR UPDATE' if DATABASE_URL else ''
        limit = c.execute('SELECT * FROM login_limits WHERE key=?' + suffix, (key,)).fetchone()
        if limit['reset_at'] < time.time():
            c.execute('UPDATE login_limits SET attempts=0, reset_at=? WHERE key=?', (time.time() + 900, key))
        elif limit['attempts'] >= 10: blocked = True
        if not blocked:
            row = c.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
            stored = row['password'] if row else password_hash('dummy-password')
            valid = check_password(password, stored)
            if row and valid:
                user = clean_user(row)
                token = secrets.token_urlsafe(48)
                c.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
                c.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], time.time() + 43200))
                c.execute('DELETE FROM login_limits WHERE key=?', (key,))
            else: c.execute('UPDATE login_limits SET attempts=attempts+1 WHERE key=?', (key,))
    if blocked: fail('尝试次数过多，请 15 分钟后重试', 429)
    if not user: fail('账号或密码不正确', 401)
    response.set_cookie('cmp_session', token, httponly=True, secure=PRODUCTION, samesite='strict', max_age=43200)
    return user


@app.post('/api/logout')
def logout(request: Request, response: Response):
    token_hash = hashlib.sha256(request.cookies.get('cmp_session', '').encode()).hexdigest()
    with db() as c:
        lock_records(c)
        grants = c.execute('SELECT * FROM maintenance_access WHERE session_hash=? AND revoked=0', (token_hash,)).fetchall()
        for grant in grants:
            c.execute('UPDATE maintenance_access SET revoked=1 WHERE id=?', (grant['id'],))
            c.scope(grant['workspace_id'])
            audit(c, grant['actor'], '退出登录结束维护', 'maintenance', grant['id'], after={'reason': grant['reason']})
        c.execute('DELETE FROM sessions WHERE token=?', (token_hash,))
    response.delete_cookie('cmp_session')
    return {'ok': True}


@app.get('/api/me')
def me(request: Request): return current_user(request, True)


@app.post('/api/password')
def change_password(body: dict, request: Request):
    user = current_user(request, True)
    password = str(body.get('password', ''))
    if not 12 <= len(password) <= 128: fail('密码长度须为 12–128 个字符')
    with db() as c:
        lock_records(c)
        c.scope(SYSTEM_SPACE)
        row = c.execute('SELECT password FROM users WHERE id=?', (user['id'],)).fetchone()
        if not check_password(str(body.get('current_password', '')), row['password']): fail('当前密码不正确')
        c.execute('UPDATE users SET password=?, must_change=0 WHERE id=?', (password_hash(password), user['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
        audit(c, user['id'], '修改密码', 'user', user['id'])
    return {'ok': True}


@app.get('/api/users')
def users(request: Request):
    user = current_user(request)
    with db() as c:
        scope_request(c, request, user)
        return scope_roster(c)


@app.get('/api/admin/users')
def admin_users(request: Request):
    administrator(current_user(request))
    with db() as c: return [clean_user(x) for x in c.execute('SELECT * FROM users ORDER BY name').fetchall()]


@app.post('/api/users')
def create_user(body: dict, request: Request):
    user = current_user(request); administrator(user)
    name = str(body.get('name', '')).strip()[:80]
    username = str(body.get('username', '')).strip().lower()
    password = str(body.get('password', ''))
    if not name or not re.fullmatch(r'[a-z0-9_.-]{3,40}', username): fail('姓名必填，账号须为 3–40 位英文字母、数字或 . _ -')
    if not 12 <= len(password) <= 128: fail('初始密码须为 12–128 个字符')
    with db() as c:
        if DATABASE_URL: c.execute('SELECT pg_advisory_xact_lock(74391002)')
        lock_records(c)
        c.scope(SYSTEM_SPACE)
        if c.execute('SELECT count(*) AS n FROM users WHERE active=1').fetchone()['n'] >= 6: fail('最多启用 6 个账号（含管理员）')
        if c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone(): fail('该账号已存在')
        item = {'id': uid(), 'username': username, 'name': name, 'role': 'member', 'active': 1, 'must_change': 1}
        c.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?)', (item['id'], username, name, 'member', password_hash(password), 1, 1))
        create_personal_space(c, item['id'])
        audit(c, user['id'], '创建账号', 'user', item['id'], after=item)
        team_role = choice(body, 'team_role', ['none', 'viewer', 'editor'], 'editor')
        if team_role != 'none':
            team_id = body.get('team_id') or COMPANY_SPACE
            require_team_owner(c, team_id, user)
            c.execute('INSERT INTO memberships VALUES (?,?,?,?,?)', (team_id, item['id'], team_role, 1, now()))
            audit(c, user['id'], '加入团队', 'membership', item['id'], after={'name': name, 'role': team_role})
    return item


@app.patch('/api/users/{user_id}')
def update_user(user_id: str, body: dict, request: Request):
    actor = current_user(request); administrator(actor)
    with db() as c:
        if DATABASE_URL: c.execute('SELECT pg_advisory_xact_lock(74391002)')
        lock_records(c)
        c.scope(SYSTEM_SPACE)
        row = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not row: fail('账号不存在', 404)
        if row['role'] == 'admin': fail('管理员请使用修改密码功能')
        before = clean_user(row)
        if 'active' in body:
            active = 1 if body['active'] else 0
            if active and not row['active'] and c.execute('SELECT count(*) AS n FROM users WHERE active=1').fetchone()['n'] >= 6: fail('最多启用 6 个账号')
            c.execute('UPDATE users SET active=? WHERE id=?', (active, user_id))
        if 'password' in body:
            password = str(body['password'])
            if not 12 <= len(password) <= 128: fail('密码须为 12–128 个字符')
            c.execute('UPDATE users SET password=?, must_change=1 WHERE id=?', (password_hash(password), user_id))
        c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        c.execute('UPDATE maintenance_access SET revoked=1 WHERE workspace_id=? OR actor=?', (personal_space(user_id), user_id))
        after = clean_user(c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())
        audit(c, actor['id'], '重置密码' if 'password' in body else '更新账号', 'user', user_id, before, after)
    return after


@app.post('/api/teams')
def create_team(body: dict, request: Request):
    user = current_user(request); administrator(user)
    title = text_field(body, 'title', 120, True)
    with db() as c:
        lock_records(c)
        team_id = uid()
        c.execute('INSERT INTO workspaces VALUES (?,?,?,?,?)', (team_id, 'team', title, user['id'], now()))
        c.execute('INSERT INTO memberships VALUES (?,?,?,?,?)', (team_id, user['id'], 'owner', 1, now()))
        c.scope(team_id)
        audit(c, user['id'], '创建团队', 'workspace', team_id, after={'title': title})
    return {'id': team_id, 'title': title}


@app.get('/api/teams/{team_id}/members')
def team_members(team_id: str, request: Request):
    user = current_user(request)
    with db() as c:
        member = c.execute("SELECT role FROM memberships WHERE workspace_id=? AND user_id=? AND role IN ('owner','editor','viewer')", (team_id, user['id'])).fetchone()
        if not member: fail('无权访问此团队', 403)
        c.scope(team_id)
        return scope_roster(c)


@app.post('/api/teams/{team_id}/members')
def add_team_member(team_id: str, body: dict, request: Request):
    user = current_user(request)
    role = choice(body, 'role', ['viewer', 'editor'], 'viewer')
    username = text_field(body, 'username', 40, True).lower()
    with db() as c:
        lock_records(c); require_team_owner(c, team_id, user)
        member = c.execute('SELECT id,name FROM users WHERE username=? AND active=1', (username,)).fetchone()
        if not member: fail('请先由管理员创建并启用此账号')
        before = c.execute('SELECT role,version FROM memberships WHERE workspace_id=? AND user_id=?', (team_id, member['id'])).fetchone()
        if before and before['role'] != 'removed': fail('该成员已在团队中，请修改现有权限', 409)
        if before:
            c.execute('UPDATE memberships SET role=?,version=version+1,updated_at=? WHERE workspace_id=? AND user_id=?', (role, now(), team_id, member['id']))
        else: c.execute('INSERT INTO memberships VALUES (?,?,?,?,?)', (team_id, member['id'], role, 1, now()))
        after = {'name': member['name'], 'role': role}
        audit(c, user['id'], '加入团队', 'membership', member['id'], dict(before) if before else None, after)
    return after


@app.patch('/api/teams/{team_id}/members/{member_id}')
def update_team_member(team_id: str, member_id: str, body: dict, request: Request):
    user = current_user(request)
    role = choice(body, 'role', ['viewer', 'editor', 'removed'], 'viewer')
    with db() as c:
        lock_records(c); require_team_owner(c, team_id, user)
        before = c.execute('SELECT role,version FROM memberships WHERE workspace_id=? AND user_id=?', (team_id, member_id)).fetchone()
        if not before: fail('团队成员不存在', 404)
        if before['role'] == 'owner': fail('不能修改团队 owner 的权限')
        if before['version'] != body.get('version'): fail('权限已被更新，请刷新后再编辑', 409)
        c.execute('UPDATE memberships SET role=?,version=version+1,updated_at=? WHERE workspace_id=? AND user_id=?', (role, now(), team_id, member_id))
        name = c.execute('SELECT name FROM users WHERE id=?', (member_id,)).fetchone()['name']
        after = {'name': name, 'role': role, 'version': before['version'] + 1}
        audit(c, user['id'], '移出团队' if role == 'removed' else '修改团队权限', 'membership', member_id, {**dict(before), 'name': name}, after)
    return after


def reauthenticate_admin(user, password):
    administrator(user)
    key = hashlib.sha256(('maintenance:' + user['id']).encode()).hexdigest()
    valid, blocked = False, False
    with db() as c:
        c.execute('INSERT INTO login_limits VALUES (?,0,?) ON CONFLICT(key) DO NOTHING', (key, time.time() + 900))
        limit = c.execute('SELECT * FROM login_limits WHERE key=?' + (' FOR UPDATE' if DATABASE_URL else ''), (key,)).fetchone()
        if limit['reset_at'] < time.time():
            c.execute('UPDATE login_limits SET attempts=0,reset_at=? WHERE key=?', (time.time() + 900, key))
        elif limit['attempts'] >= 5: blocked = True
        if not blocked:
            row = c.execute("SELECT password FROM users WHERE id=? AND role='admin' AND active=1", (user['id'],)).fetchone()
            valid = bool(row and check_password(str(password)[:256], row['password']))
            if valid: c.execute('DELETE FROM login_limits WHERE key=?', (key,))
            else: c.execute('UPDATE login_limits SET attempts=attempts+1 WHERE key=?', (key,))
    if blocked: fail('验证次数过多，请 15 分钟后重试', 429)
    if not valid: fail('管理员密码不正确', 403)


@app.post('/api/maintenance')
def begin_maintenance(body: dict, request: Request):
    user = current_user(request)
    reauthenticate_admin(user, body.get('password', ''))
    reason = text_field(body, 'reason', 1000, True)
    target = text_field(body, 'user_id', 80, True)
    with db() as c:
        lock_records(c)
        if not c.execute('SELECT id FROM users WHERE id=? AND active=1', (target,)).fetchone(): fail('维护目标不存在或已停用', 404)
        if target == user['id']: fail('请直接打开自己的个人空间')
        grant_id, expires = uid(), time.time() + 900
        workspace_id = personal_space(target)
        c.execute('INSERT INTO maintenance_access VALUES (?,?,?,?,?,?,0,?)',
            (grant_id, workspace_id, user['id'], hashlib.sha256(request.cookies.get('cmp_session', '').encode()).hexdigest(), reason, expires, now()))
        c.scope(workspace_id)
        after = {'reason': reason, 'expires': expires, 'access': '只读', 'administrator': user['name']}
        audit(c, user['id'], '开启维护访问', 'maintenance', grant_id, after=after)
    return {'id': grant_id, 'workspace_id': workspace_id, **after}


@app.get('/api/maintenance')
def maintenance_history(request: Request):
    user = current_user(request)
    with db() as c:
        return [dict(r) for r in c.execute('''SELECT g.id,g.workspace_id,g.actor,g.reason,g.expires,g.revoked,g.created_at,u.name AS actor_name
            FROM maintenance_access g JOIN users u ON u.id=g.actor WHERE g.workspace_id=? OR g.actor=? ORDER BY g.created_at DESC LIMIT 100''',
            (personal_space(user['id']), user['id'])).fetchall()]


@app.post('/api/maintenance/{grant_id}/revoke')
def revoke_maintenance(grant_id: str, request: Request):
    user = current_user(request)
    with db() as c:
        lock_records(c)
        grant = c.execute('SELECT * FROM maintenance_access WHERE id=?', (grant_id,)).fetchone()
        if not grant or (grant['actor'] != user['id'] and grant['workspace_id'] != personal_space(user['id'])): fail('无权结束此维护访问', 403)
        if not grant['revoked']:
            c.execute('UPDATE maintenance_access SET revoked=1 WHERE id=?', (grant_id,))
            c.scope(grant['workspace_id'])
            audit(c, user['id'], '结束维护访问', 'maintenance', grant_id, after={'reason': grant['reason']})
    return {'ok': True}


@app.get('/api/account/activity')
def account_activity(request: Request):
    user = current_user(request)
    with db() as c:
        c.scope(SYSTEM_SPACE)
        where, params = ('1=1', ()) if user['role'] == 'admin' else ('a.entity_id=?', (user['id'],))
        rows = c.execute('SELECT a.*, u.name AS actor_name FROM audit a LEFT JOIN users u ON u.id=a.actor WHERE a.workspace_id=? AND ' + where + ' ORDER BY a.at DESC LIMIT 100', (SYSTEM_SPACE, *params)).fetchall()
        return [{**dict(r), 'before': json.loads(r['before_data']), 'after': json.loads(r['after_data'])} for r in rows]


def text_field(data, key, maximum=1000, required=False):
    value = str(data.get(key, '') or '').strip()
    if len(value) > maximum or (required and not value): fail(f'{key} 内容为空或过长')
    return value


def choice(data, key, choices, default):
    value = data.get(key, default)
    if value not in choices: fail(f'{key} 选项无效')
    return value


def money(data, key, required=False, signed=False):
    value = data.get(key)
    if value in (None, ''):
        if required: fail('请填写金额')
        return None
    try:
        value = Decimal(str(value))
        if not value.is_finite() or (not signed and value < 0) or abs(value) > Decimal('999999999999') or value != value.quantize(Decimal('.01')): raise ValueError()
        return str(value.quantize(Decimal('.01')))
    except (InvalidOperation, ValueError): fail('金额最多两位小数' if signed else '金额须为非负数，最多两位小数')


def date_field(data, key, required=False):
    value = text_field(data, key, 10, required)
    if value:
        try: date.fromisoformat(value)
        except ValueError: fail('日期格式应为 YYYY-MM-DD')
    return value


STOCK_TYPES = {
    '期初入库': 1, '采购入库': 1, '销售出库': -1, '领用出库': -1,
    '销售退货入库': 1, '采购退货出库': -1, '盘盈入库': 1, '盘亏出库': -1,
}
STOCK_FINANCE = {'采购入库': '支出', '销售出库': '收入', '销售退货入库': '支出', '采购退货出库': '收入'}


def quantity(data, key, positive=False):
    try:
        value = Decimal(str(data.get(key, '0') or '0'))
        if not value.is_finite() or value < 0 or value > Decimal('999999999') or value != value.quantize(Decimal('.001')):
            raise ValueError()
        if positive and value == 0: raise ValueError()
        return str(value.quantize(Decimal('.001')))
    except (InvalidOperation, ValueError): fail('数量须为非负数，最多三位小数；出入库数量须大于零')


def validate_stock_link(c, movement, transaction):
    if movement.get('project_id', '') != transaction.get('project_id', ''):
        fail('库存流水和财务记录须属于同一项目（或均不关联项目）')
    expected = STOCK_FINANCE.get(movement['movement_type'])
    if not expected: fail('此出入库类型不关联收支，请使用采购、销售或退货类型')
    if transaction['direction'] != expected: fail('关联财务记录的收支方向与出入库类型不一致')


def check_product(c, data, item_id=None, before=None):
    for row in c.records("kind='products'", columns='id, data').fetchall():
        if row['id'] != item_id and json.loads(row['data'])['sku'].casefold() == data['sku'].casefold():
            fail('SKU 已存在，请使用不同的商品编号')
    if before and before['unit'] != data['unit']:
        for row in c.records("kind='stock_movements'", columns='data').fetchall():
            if json.loads(row['data'])['product_id'] == item_id: fail('已有出入库记录，不能改变计量单位；请新建不同单位的商品')


def check_stock_balance(c, data, exclude_id=None):
    # Rebuild affected warehouse/product balances by day. The caller holds the
    # global transaction lock, including while linked finance and audit are saved.
    affected = {(data['warehouse_id'], data['product_id'])}
    rows = c.records("kind='stock_movements'", columns='id, data').fetchall()
    for row in rows:
        if row['id'] == exclude_id:
            old = json.loads(row['data'])
            affected.add((old['warehouse_id'], old['product_id']))
    days = {}
    for item in [json.loads(r['data']) for r in rows if r['id'] != exclude_id] + [data]:
        key = (item['warehouse_id'], item['product_id'])
        if key not in affected: continue
        daily = days.setdefault(key, {})
        daily[item['date']] = daily.get(item['date'], Decimal('0')) + Decimal(item['quantity']) * STOCK_TYPES[item['movement_type']]
    for daily in days.values():
        balance = Decimal('0')
        for day in sorted(daily):
            balance += daily[day]
            if balance < 0: fail(f'库存不足：这次操作会使 {day} 的库存低于零，请核对入库和出库记录')


def validate(kind, data, c):
    if kind == 'warehouses':
        out = {'title': text_field(data, 'title', 120, True), 'description': text_field(data, 'description', 2000)}
    elif kind == 'products':
        out = {'title': text_field(data, 'title', 120, True), 'sku': text_field(data, 'sku', 80, True), 'unit': text_field(data, 'unit', 20, True), 'low_stock': quantity(data, 'low_stock'), 'description': text_field(data, 'description', 2000)}
    elif kind == 'stock_movements':
        out = {k: text_field(data, k, 80) for k in ('warehouse_id', 'project_id', 'linked_transaction_id')}
        out.update(title=text_field(data, 'title', 200, True), product_id=text_field(data, 'product_id', 80, True), date=date_field(data, 'date', True), movement_type=choice(data, 'movement_type', STOCK_TYPES, '采购入库'), quantity=quantity(data, 'quantity', True), note=text_field(data, 'note', 2000))
        if out['date'] > datetime.now(ZoneInfo('America/New_York')).date().isoformat():
            fail('出入库只记录已发生的变动，日期不能晚于今天（纽约时间）')
        if not c.records("id=? AND kind='products'", (out['product_id'],), columns='id').fetchone(): fail('商品不存在')
        if out['warehouse_id'] and not c.records("id=? AND kind='warehouses'", (out['warehouse_id'],), columns='id').fetchone(): fail('仓库不存在')
        if out['linked_transaction_id']:
            transaction = c.records("id=? AND kind='transactions'", (out['linked_transaction_id'],), columns='data').fetchone()
            if not transaction: fail('关联财务记录不存在')
            validate_stock_link(c, out, json.loads(transaction['data']))
    elif kind == 'finance_profiles':
        out = {'title': text_field(data, 'title', 120, True), 'description': text_field(data, 'description', 2000)}
    elif kind == 'projects':
        try: progress = int(data.get('progress', 0))
        except (ValueError, TypeError): fail('进度须为 0–100 的整数')
        if not 0 <= progress <= 100: fail('进度须在 0–100 之间')
        out = {'title': text_field(data, 'title', 120, True), 'description': text_field(data, 'description', 10000), 'status': choice(data, 'status', ['进行中', '待启动', '暂停', '已完成'], '进行中'), 'progress': progress, 'due_date': date_field(data, 'due_date'), 'owner_id': text_field(data, 'owner_id', 80)}
    elif kind == 'subsections':
        out = {'title': text_field(data, 'title', 120, True), 'description': text_field(data, 'description', 5000), 'project_id': text_field(data, 'project_id', 80, True)}
    elif kind == 'transactions':
        out = {k: text_field(data, k, 2000 if k == 'note' else 150) for k in ['event', 'category', 'payment_method', 'responsible', 'note', 'project_id']}
        out.update(title=text_field(data, 'title', 200, True), date=date_field(data, 'date'), direction=choice(data, 'direction', ['收入', '支出', '其他'], '支出'), currency=choice(data, 'currency', ['USD', 'CNY'], 'USD'), amount=money(data, 'amount', True), usd_amount=money(data, 'usd_amount'), booked_amount=money(data, 'booked_amount', signed=True), payment_status=choice(data, 'payment_status', ['已付', '未付', '部分支付', '待确认'], '未付'), posting_status=choice(data, 'posting_status', ['平帐', '未入账', '部分入账', '待入账', '待支出'], '未入账'))
        if out['currency'] == 'USD': out['usd_amount'] = out['amount']
        out['profile_id'] = text_field(data, 'profile_id', 80)
        if out['profile_id'] and not c.records("id=? AND kind='finance_profiles'", (out['profile_id'],), columns='id').fetchone():
            fail('财务账本不存在')
        out['reimbursement_status'] = choice(data, 'reimbursement_status', ['待确认', '不需报销', '待报销', '部分报销', '已报销'], '待确认' if out['direction'] == '支出' else '不需报销')
        out['claim_amount'] = money(data, 'claim_amount')
        out['reimbursed_amount'] = money(data, 'reimbursed_amount')
        out['reimbursement_date'] = date_field(data, 'reimbursement_date')
        out['reimbursement_note'] = text_field(data, 'reimbursement_note', 2000)
        status = out['reimbursement_status']
        claim, paid = Decimal(out['claim_amount'] or '0'), Decimal(out['reimbursed_amount'] or '0')
        if out['direction'] != '支出' and status != '不需报销': fail('只有支出适用报销，请选择不需报销')
        if status in ('待确认', '不需报销'):
            if claim or paid or out['reimbursement_date']: fail('请先选择报销状态，再填写报销金额或日期')
        else:
            if not 0 < claim <= Decimal(out['amount']): fail('应报金额须大于零且不能超过支出原币金额')
            if paid > claim: fail('已报金额不能超过应报金额')
            if status == '待报销' and paid: fail('已有报销金额，请选择部分报销或已报销')
            if status == '部分报销' and not 0 < paid < claim: fail('部分报销金额须大于零且小于应报金额')
            if status == '已报销' and paid != claim: fail('已报销金额须等于应报金额')
            if out['reimbursement_date'] and not paid: fail('尚未报销，不能填写报销日期')
    elif kind == 'notes':
        out = {'title': text_field(data, 'title', 200, True), 'body': text_field(data, 'body', 20000, True), 'contact': text_field(data, 'contact', 150), 'date': date_field(data, 'date', True), 'project_id': text_field(data, 'project_id', 80, True)}
    elif kind == 'tasks':
        out = {'title': text_field(data, 'title', 200, True), 'description': text_field(data, 'description', 5000), 'project_id': text_field(data, 'project_id', 80), 'assignee_id': text_field(data, 'assignee_id', 80), 'due_date': date_field(data, 'due_date'), 'status': choice(data, 'status', ['待办', '进行中', '已完成'], '待办')}
    elif kind == 'files':
        url = text_field(data, 'url', 2000, True)
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('drive.google.com', 'docs.google.com'): fail('请使用 Google Drive 或 Google Docs 的 HTTPS 链接')
        out = {'title': text_field(data, 'title', 200, True), 'url': url, 'project_id': text_field(data, 'project_id', 80, True)}
    else: fail('记录类型无效', 404)
    if out.get('project_id') and not c.records('id=? AND kind=?', (out['project_id'], 'projects'), columns='id').fetchone(): fail('关联项目不存在')
    if kind in ('notes', 'tasks', 'files', 'transactions', 'stock_movements'):
        out['subsection_id'] = text_field(data, 'subsection_id', 80)
        if out['subsection_id']:
            section = c.records("id=? AND kind='subsections'", (out['subsection_id'],), columns='data').fetchone()
            if not section or json.loads(section['data'])['project_id'] != out.get('project_id'):
                fail('分区不存在或不属于所选项目')
    if kind == 'notes':
        out['show_on_timeline'] = data.get('show_on_timeline', True)
        if not isinstance(out['show_on_timeline'], bool):
            fail('加入时间线必须为是或否')
        out['linked_task_id'] = text_field(data, 'linked_task_id', 80)
        if out['linked_task_id']:
            task = c.records("id=? AND kind='tasks'", (out['linked_task_id'],), columns='data').fetchone()
            if not task or json.loads(task['data']).get('project_id') != out['project_id']:
                fail('请选择同一项目中的待办')
    for key in ('owner_id', 'assignee_id'):
        if out.get(key) and out[key] not in {u['id'] for u in scope_roster(c) if u['active']}: fail('成员不属于当前空间或已停用')
    return out


def entity(row):
    return {**json.loads(row['data']), **{k: row[k] for k in ('id', 'kind', 'version', 'created_at', 'updated_at', 'created_by', 'updated_by', 'workspace_id')}}


def can_finance(user): return FINANCE_MEMBERS or user['role'] == 'admin'


def lock_records(c):
    # All entity writes share a short transaction lock so references cannot move
    # to another project between validation and commit. SQLite uses BEGIN IMMEDIATE.
    if DATABASE_URL: c.execute('SELECT pg_advisory_xact_lock(74391003)')


def insert_record(c, kind, data, actor, action='新增'):
    item_id, at = uid(), now()
    c.execute('INSERT INTO entities (id,kind,data,version,created_at,updated_at,created_by,updated_by,workspace_id) VALUES (?,?,?,?,?,?,?,?,?)', (item_id, kind, json.dumps(data, ensure_ascii=False), 1, at, at, actor, actor, c.workspace_id))
    audit(c, actor, action, kind, item_id, after=data)
    return item_id


def prepare_record(c, kind, body, user):
    if kind == 'stock_movements':
        body = dict(body)
        action = choice(body, 'finance_action', ['none', 'link', 'create', 'keep'], 'keep')
        if (action in ('link', 'create') or body.get('linked_transaction_id')) and not can_finance(user): fail('无财务权限', 403)
        if action == 'create' and body.get('linked_transaction_id'): fail('已有财务关联，请先解除关联，避免重复记账')
        if action in ('none', 'create'): body['linked_transaction_id'] = ''
        if action == 'link' and not body.get('linked_transaction_id'): fail('请选择已有财务记录')
        data = validate(kind, body, c)
        if action == 'create':
            direction = STOCK_FINANCE.get(data['movement_type'])
            if not direction: fail('此出入库类型不创建收支，请使用采购、销售或退货类型')
            transaction = validate('transactions', {
                'title': data['title'], 'date': data['date'], 'direction': direction,
                'project_id': data['project_id'], 'subsection_id': data['subsection_id'],
                'amount': body.get('finance_amount'), 'currency': body.get('finance_currency', 'USD'),
                'profile_id': body.get('finance_profile_id', ''), 'payment_status': body.get('finance_payment_status', '未付'),
                'responsible': body.get('finance_responsible', ''), 'note': data['note'],
            }, c)
            data['linked_transaction_id'] = insert_record(c, 'transactions', transaction, user['id'])
        return data
    if kind != 'notes': return validate(kind, body, c)
    body = dict(body)
    action = choice(body, 'task_action', ['none', 'link', 'create', 'keep'], 'keep')
    if action in ('none', 'create'): body['linked_task_id'] = ''
    if action == 'link' and not body.get('linked_task_id'): fail('请选择要关联的待办')
    data = validate(kind, body, c)
    if action == 'create':
        task = validate('tasks', {
            'title': text_field(body, 'task_title', 200) or ('跟进：' + data['title'])[:200],
            'description': '来自笔记：' + data['title'],
            'project_id': data['project_id'], 'subsection_id': data['subsection_id'],
            'assignee_id': body.get('task_assignee_id', user['id']),
            'due_date': body.get('task_due_date', ''), 'status': '待办',
        }, c)
        data['linked_task_id'] = insert_record(c, 'tasks', task, user['id'])
    return data


@app.get('/api/state')
def state(request: Request):
    user = current_user(request)
    with db() as c:
        workspace = scope_request(c, request, user)
        records = [entity(r) for r in c.records(order='updated_at DESC').fetchall() if r['kind'] not in ('transactions', 'finance_profiles') or can_finance(user)]
        spaces = accessible_spaces(c, user)
    team = workspace['kind'] == 'team'
    return {'records': records, 'workspace': workspace, 'workspaces': spaces,
        'config': {'finance_access': can_finance(user), 'can_edit': workspace['access'] != 'viewer',
        'can_manage_team': team and workspace['access'] == 'owner',
        'drive_upload_ready': team and workspace['id'] == COMPANY_SPACE and workspace['access'] != 'viewer' and all(os.getenv(k) for k in ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN']),
        'drive_folder_url': 'https://drive.google.com/drive/folders/' + DRIVE_FOLDER if team and workspace['id'] == COMPANY_SPACE else None,
        'sheet_url': SHEET_URL if team and workspace['id'] == COMPANY_SPACE and can_finance(user) else None}}


@app.get('/api/activity')
def activity(request: Request, entity_id: str = '', limit: int = 100):
    user = current_user(request)
    sql = 'SELECT a.*, u.name AS actor_name FROM audit a LEFT JOIN users u ON a.actor=u.id WHERE a.workspace_id=?'
    params = []
    if entity_id: sql += ' AND a.entity_id=?'; params.append(entity_id)
    if not can_finance(user): sql += " AND a.kind NOT IN ('transactions', 'finance_profiles')"
    sql += ' ORDER BY a.at DESC LIMIT ?'; params.append(max(1, min(limit, 500)))
    with db() as c:
        scope_request(c, request, user)
        return [{**dict(r), 'before': json.loads(r['before_data']), 'after': json.loads(r['after_data'])} for r in c.execute(sql, (c.workspace_id, *params)).fetchall()]


@app.post('/api/records/{kind}')
def create_record(kind: str, body: dict, request: Request):
    user = current_user(request)
    if kind in ('transactions', 'finance_profiles') and not can_finance(user): fail('无财务权限', 403)
    with db() as c:
        lock_records(c)
        scope_request(c, request, user, write=True)
        data = prepare_record(c, kind, body, user)
        if kind == 'products': check_product(c, data)
        if kind == 'stock_movements': check_stock_balance(c, data)
        item_id = insert_record(c, kind, data, user['id'])
        return entity(c.records('id=?', (item_id,)).fetchone())


@app.patch('/api/records/{kind}/{item_id}')
def update_record(kind: str, item_id: str, body: dict, request: Request):
    user = current_user(request)
    if kind in ('transactions', 'finance_profiles') and not can_finance(user): fail('无财务权限', 403)
    with db() as c:
        lock_records(c)
        scope_request(c, request, user, write=True)
        row = c.records('id=? AND kind=?', (item_id, kind)).fetchone()
        if not row: fail('记录不存在', 404)
        if body.get('version') != row['version']: fail('其他成员已更新此记录，请刷新后再编辑', 409)
        before = json.loads(row['data'])
        data = prepare_record(c, kind, {**before, **body}, user)
        if kind == 'products': check_product(c, data, item_id, before)
        if kind == 'stock_movements': check_stock_balance(c, data, item_id)
        if kind == 'transactions':
            # Import provenance is set only by the administrative importer. A
            # member can edit the ledger fields, but cannot rewrite the source.
            if 'source_import' in before: data['source_import'] = before['source_import']
            for movement in c.records("kind='stock_movements'", columns='data').fetchall():
                movement = json.loads(movement['data'])
                if movement.get('linked_transaction_id') == item_id: validate_stock_link(c, movement, data)
        if kind == 'subsections' and data['project_id'] != before['project_id']:
            fail('分区不能移动到其他项目，请在目标项目新建分区')
        if kind == 'tasks' and data.get('project_id') != before.get('project_id'):
            for note in c.records("kind='notes'", columns='data').fetchall():
                if json.loads(note['data']).get('linked_task_id') == item_id:
                    fail('此待办已关联笔记，请先解除关联再更换项目')
        result = c.execute('UPDATE entities SET data=?, version=version+1, updated_at=?, updated_by=? WHERE id=? AND version=? AND workspace_id=?', (json.dumps(data, ensure_ascii=False), now(), user['id'], item_id, row['version'], c.workspace_id))
        if result.rowcount != 1: fail('记录已被更新，请刷新', 409)
        audit(c, user['id'], '修改', kind, item_id, before, data)
        return entity(c.records('id=?', (item_id,)).fetchone())


@app.post('/api/upload')
def upload(request: Request, project_id: str = Form(...), file: UploadFile = File(...), subsection_id: str = Form('')):
    user = current_user(request)
    with db() as c:
        workspace = scope_request(c, request, user, write=True)
        if workspace['kind'] != 'team' or workspace['id'] != COMPANY_SPACE: fail('此空间未配置专属 Drive 上传，请添加自己有权限的文件链接', 403)
        validate('files', {'title': '附件', 'url': 'https://drive.google.com/', 'project_id': project_id, 'subsection_id': subsection_id}, c)
    creds = [os.getenv(k) for k in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN')]
    if not all(creds): fail('管理员尚未连接 Google Drive。可先添加已有文件链接。', 503)
    content = file.file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024: fail('文件不能超过 20 MB', 413)
    title = Path(file.filename or '附件').name[:200]
    try:
        token = requests.post('https://oauth2.googleapis.com/token', data={'client_id': creds[0], 'client_secret': creds[1], 'refresh_token': creds[2], 'grant_type': 'refresh_token'}, timeout=30)
        token.raise_for_status()
        boundary = 'cmp_' + secrets.token_hex(24)
        metadata = json.dumps({'name': title, 'parents': [DRIVE_FOLDER], 'appProperties': {'cmpProjectId': project_id, 'cmpUserId': user['id']}}).encode()
        mime = file.content_type or 'application/octet-stream'
        if not re.fullmatch(r'[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+', mime): mime = 'application/octet-stream'
        payload = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode() + metadata
                   + f'\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n'.encode() + content
                   + f'\r\n--{boundary}--\r\n'.encode())
        result = requests.post('https://www.googleapis.com/upload/drive/v3/files', params={'uploadType': 'multipart', 'fields': 'id,webViewLink'}, headers={'Authorization': 'Bearer ' + token.json()['access_token'], 'Content-Type': 'multipart/related; boundary=' + boundary}, data=payload, timeout=120)
        result.raise_for_status()
    except requests.RequestException: fail('Google Drive 上传失败，请检查授权后重试', 502)
    remote = result.json()
    return create_record('files', {'title': title, 'url': remote.get('webViewLink') or 'https://drive.google.com/file/d/' + remote['id'] + '/view', 'project_id': project_id, 'subsection_id': subsection_id}, request)


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/')
def index(): return FileResponse(ROOT / 'static/index.html', headers={'Cache-Control': 'no-cache'})


@app.get('/sw.js')
def worker(): return FileResponse(ROOT / 'static/sw.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache'})
