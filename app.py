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
from urllib.parse import urlparse, quote
from zoneinfo import ZoneInfo

import requests
import attachment_storage
from google.api_core.exceptions import GoogleAPIError, NotFound
from google.auth.exceptions import GoogleAuthError
from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
PRODUCTION = bool(os.getenv('K_SERVICE'))
DATABASE_URL = os.getenv('DATABASE_URL', '')
DRIVE_FOLDER = os.getenv('DRIVE_FOLDER_ID', '1N58QFJPdxpIjsdzZaUIn2tm39_yp4wD2')
STORAGE_BUCKET = os.getenv('ATTACHMENT_BUCKET', '')
STORED_FILE_FIELDS = ('storage_provider', 'storage_bucket', 'storage_object', 'storage_generation', 'upload_sha256', 'mime_type', 'size', 'filename')
SHEET_URL = 'https://docs.google.com/spreadsheets/d/1R9hbqZftVj6BWH82d2l-RVYMUzz6XTed6LEFcibKWWU/edit'
FINANCE_MEMBERS = os.getenv('FINANCE_MEMBERS', 'true').lower() == 'true'
COMPANY_SPACE = 'company'
SYSTEM_SPACE = 'system'


class DB:
    def __init__(self, conn):
        self.conn = conn
        self.workspace_id = COMPANY_SPACE
        self.access = None
    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace('?', '%s') if DATABASE_URL else sql, params)

    def scope(self, workspace_id):
        self.workspace_id = workspace_id
        if DATABASE_URL:
            self.execute("SELECT set_config('cmp.workspace_id', ?, true), set_config('cmp.access_version','2',true)", (workspace_id,))

    def records(self, where='1=1', params=(), columns='*', order='', internal=False, include_deleted=False):
        clause, access_params = resource_filter(self) if not internal else ('1=1', ())
        if not internal and not include_deleted:
            where = f'({where}) AND ({active_record_filter()})'
        return self.execute(f'SELECT {columns} FROM entities WHERE workspace_id=? AND ({where}) AND ({clause})' + (f' ORDER BY {order}' if order else ''), (self.workspace_id, *params, *access_params))


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
        migrate_resource_access(c)


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


def migrate_resource_access(c):
    c.execute('''CREATE TABLE IF NOT EXISTS resource_access (
        workspace_id TEXT NOT NULL, resource_kind TEXT NOT NULL, resource_id TEXT NOT NULL,
        user_id TEXT NOT NULL, role TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(workspace_id,resource_kind,resource_id,user_id))''')
    if not c.execute("SELECT name FROM app_migrations WHERE name='resource-access-v2'").fetchone():
        c.execute("UPDATE memberships SET role='member',version=version+1,updated_at=? WHERE role IN ('editor','viewer')", (now(),))
        c.execute('INSERT INTO app_migrations VALUES (?,?)', ('resource-access-v2', now()))
    if DATABASE_URL:
        # Old revisions don't implement resource authorization. Fail closed if
        # one receives a request during rollout; new connections set version 2.
        for table in ('entities', 'audit'):
            c.execute(f"ALTER POLICY workspace_isolation ON {table} USING (workspace_id=COALESCE(NULLIF(current_setting('cmp.workspace_id',true),''),'company') AND current_setting('cmp.access_version',true)='2') WITH CHECK (workspace_id=COALESCE(NULLIF(current_setting('cmp.workspace_id',true),''),'company') AND current_setting('cmp.access_version',true)='2')")


def configure_access(c, user, workspace, role):
    full = workspace['kind'] == 'personal' or role in ('owner', 'admin')
    grants = c.execute("SELECT resource_kind,resource_id,role FROM resource_access WHERE workspace_id=? AND user_id=? AND role IN ('viewer','editor')", (workspace['id'], user['id'])).fetchall()
    c.access = {'full': full, 'readonly': role == 'viewer', 'user_id': user['id'],
                'projects': {}, 'finance_profiles': {}}
    for grant in grants: c.access[grant['resource_kind']][grant['resource_id']] = grant['role']


def resource_filter(c):
    access = c.access
    if access is None or access['full']: return '1=1', ()
    project_ids, ledger_ids = list(access['projects']), list(access['finance_profiles'])
    def value(key):
        return f"COALESCE(data::jsonb->>'{key}','')" if DATABASE_URL else f"COALESCE(json_extract(data,'$.{key}'),'')"
    def inside(expression, values):
        return (f"{expression} IN ({','.join('?' for _ in values)})", values) if values else ('0=1', [])
    clauses, params = [], []
    for kind, expr, ids in [('projects', 'id', project_ids), ('finance_profiles', 'id', ledger_ids), ('transactions', value('profile_id'), ledger_ids)]:
        sql, args = inside(expr, ids);clauses.append(f"(kind='{kind}' AND {sql})");params.extend(args)
    sql, args = inside(value('project_id'), project_ids)
    clauses.append(f"(kind IN ('notes','files','subsections','stock_movements') AND {sql})");params.extend(args)
    ledger_task = f"({value('profile_id')}!='' OR CAST({value('ledger_task')} AS TEXT) IN ('true','1'))"
    sql, args = inside(value('profile_id'), ledger_ids)
    clauses.append(f"(kind='tasks' AND {ledger_task} AND {sql})");params.extend(args)
    sql, args = inside(value('project_id'), project_ids)
    clauses.append(f"(kind='tasks' AND NOT {ledger_task} AND {sql})");params.extend(args)
    # Shared catalog carries no stock quantities. Movements remain project-scoped.
    if project_ids: clauses.append("kind IN ('products','warehouses')")
    return ' OR '.join(clauses), tuple(params)


def active_record_filter():
    def value(alias, key):
        return f"COALESCE({alias}.data::jsonb->>'{key}','')" if DATABASE_URL else f"COALESCE(json_extract({alias}.data,'$.{key}'),'')"
    # Keep ledger and inventory facts visible and counted. Project content is
    # retained for audit, but no longer appears in active lists or file reads.
    return (f"{value('entities', 'deleted_at')}='' AND NOT ("
            "entities.kind IN ('notes','tasks','files','subsections') AND EXISTS ("
            "SELECT 1 FROM entities p WHERE p.workspace_id=entities.workspace_id AND p.kind='projects' "
            f"AND p.id={value('entities', 'project_id')} AND {value('p', 'deleted_at')}!=''))")


def deleted_project(c, project_id):
    if not project_id: return None
    if hasattr(c, 'deleted_projects'): return c.deleted_projects.get(project_id)
    row = c.records("id=? AND kind='projects'", (project_id,), internal=True).fetchone()
    data = json.loads(row['data']) if row else {}
    return data if data.get('deleted_at') else None


def record_resource(kind, data, item_id=''):
    if kind in ('projects', 'finance_profiles'): return kind, item_id
    if kind == 'transactions' or (kind == 'tasks' and (data.get('ledger_task') or data.get('profile_id'))):
        return 'finance_profiles', data.get('profile_id', '')
    if kind in ('notes', 'files', 'subsections', 'tasks', 'stock_movements') and data.get('project_id'):
        return 'projects', data['project_id']
    return None, ''


def record_role(c, kind, data, item_id=''):
    if c.access is None: return 'editor'
    if c.access['full']: return 'viewer' if c.access['readonly'] else 'editor'
    resource, key = record_resource(kind, data, item_id)
    if resource: return c.access[resource].get(key)
    if kind in ('products', 'warehouses') and c.access['projects']: return 'viewer'
    return None


def authorize_record(c, kind, data, item_id='', write=False, creating=False):
    if c.access is None: return
    if creating and kind in ('projects', 'finance_profiles', 'products', 'warehouses') and not c.access['full']:
        fail('只有公司 Admin 可以创建项目、账本和库存档案', 403)
    role = record_role(c, kind, data, item_id)
    if not role or (write and role != 'editor'): fail('没有此项目或账本的操作权限', 403)
    resource, resource_id = record_resource(kind, data, item_id)
    if write and resource == 'projects' and deleted_project(c, resource_id):
        fail('项目已删除，不能再添加或修改项目记录', 409)


def reference_record(c, key, item_id, kind):
    existing_kind, existing_data = getattr(c, 'existing', (None, {}))
    # A ledger editor may retain an existing opaque project association without
    # obtaining project access; they cannot replace it with an unauthorized one.
    retained = existing_kind in ('transactions', 'stock_movements') and existing_data.get(key) == item_id
    return c.records('id=? AND kind=?', (item_id, kind), internal=retained).fetchone()


def present_record(c, row):
    record = entity(row)
    record['_access'] = record_role(c, row['kind'], record, row['id'])
    if row['kind'] in ('transactions', 'stock_movements'):
        project_id = record.get('project_id', '')
        if project_id and record_role(c, 'projects', {}, project_id):
            deleted = deleted_project(c, project_id)
            if deleted:
                record['_project_deleted'] = True
                record['_project_title'] = deleted['title']
                if row['kind'] == 'stock_movements': record['_access'] = 'viewer'
    return record


def resource_target(c, kind, resource_id):
    if kind not in ('projects', 'finance_profiles'): fail('权限对象必须是项目或账本', 400)
    if kind == 'finance_profiles' and resource_id == 'default': return ''
    if not c.records('id=? AND kind=?', (resource_id, kind), columns='id').fetchone(): fail('项目或账本不存在', 404)
    return resource_id


def apply_resource_members(c, actor, kind, resource_id, changes):
    require_team_owner(c, c.workspace_id, actor)
    if not isinstance(changes, list) or len(changes) > 6: fail('成员权限格式无效')
    seen = set()
    for change in changes:
        if not isinstance(change, dict): fail('成员权限格式无效')
        member_id = text_field(change, 'user_id', 80, True)
        if member_id in seen: fail('成员重复')
        seen.add(member_id)
        role = choice(change, 'role', ['viewer', 'editor', 'removed'], 'editor')
        member = c.execute("SELECT m.role,u.name,u.active FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.user_id=? AND m.role IN ('owner','admin','member')", (c.workspace_id, member_id)).fetchone()
        if not member or not member['active']: fail('请先将该账号加入公司并启用')
        if member['role'] in ('owner', 'admin'): fail('公司 Admin 已有全部权限，无需单独分配')
        before = c.execute('SELECT role,version FROM resource_access WHERE workspace_id=? AND resource_kind=? AND resource_id=? AND user_id=?', (c.workspace_id, kind, resource_id, member_id)).fetchone()
        if 'version' in change and change['version'] != (before['version'] if before else 0): fail('权限已更新，请刷新后再操作', 409)
        version = before['version'] + 1 if before else 1
        c.execute('''INSERT INTO resource_access VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace_id,resource_kind,resource_id,user_id)
            DO UPDATE SET role=excluded.role,version=excluded.version,updated_at=excluded.updated_at''', (c.workspace_id, kind, resource_id, member_id, role, version, now()))
        after = {'resource_kind': kind, 'resource_id': resource_id, 'user_id': member_id, 'name': member['name'], 'role': role, 'version': version}
        audit(c, actor['id'], '移除访问权限' if role == 'removed' else '分配访问权限', 'resource_access', resource_id, {**dict(before), 'resource_kind': kind, 'resource_id': resource_id, 'name': member['name']} if before else None, after)


def accessible_spaces(c, user):
    return [dict(r) for r in c.execute('''SELECT w.*, CASE WHEN w.kind='personal' THEN 'owner' ELSE m.role END AS access
        FROM workspaces w LEFT JOIN memberships m ON m.workspace_id=w.id AND m.user_id=?
        WHERE (w.kind='personal' AND w.owner_id=?) OR (w.kind='team' AND m.role IN ('owner','admin','member'))
        ORDER BY w.kind, w.created_at''', (user['id'], user['id'])).fetchall()]


def scope_request(c, request, user, write=False):
    active = c.execute('SELECT active FROM users WHERE id=?', (user['id'],)).fetchone()
    if not active or not active['active']: fail('请先登录', 401)
    requested = request.headers.get('x-cmp-workspace', '')
    if requested == 'personal': requested = personal_space(user['id'])
    if not requested:
        member = c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?', (COMPANY_SPACE, user['id'])).fetchone()
        if member and member['role'] != 'removed': requested = COMPANY_SPACE
        else:
            joined = c.execute("SELECT w.id FROM workspaces w JOIN memberships m ON m.workspace_id=w.id AND m.user_id=? WHERE w.kind='team' AND m.role IN ('owner','admin','member') ORDER BY w.created_at,w.id LIMIT 1", (user['id'],)).fetchone()
            requested = joined['id'] if joined else personal_space(user['id'])
    workspace = c.execute('SELECT * FROM workspaces WHERE id=?', (requested,)).fetchone()
    if not workspace: fail('无权访问此空间', 403)
    workspace = dict(workspace)
    access = None
    if workspace['kind'] == 'personal' and workspace['owner_id'] == user['id']: access = 'owner'
    elif workspace['kind'] == 'team':
        member = c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?', (requested, user['id'])).fetchone()
        if member and member['role'] in ('owner', 'admin', 'member'): access = member['role']
    maintenance = None
    if not access and workspace['kind'] == 'personal' and user['role'] == 'admin':
        grant_id = request.headers.get('x-cmp-maintenance', '')
        grant = c.execute('SELECT * FROM maintenance_access WHERE id=? AND workspace_id=? AND actor=? AND session_hash=? AND revoked=0 AND expires>?',
            (grant_id, requested, user['id'], hashlib.sha256(request.cookies.get('cmp_session', '').encode()).hexdigest(), time.time())).fetchone()
        if grant: access, maintenance = 'viewer', dict(grant)
    if not access: fail('无权访问此空间', 403)
    if write and access == 'viewer': fail('当前空间为只读权限，不能修改记录', 403)
    c.scope(requested)
    configure_access(c, user, workspace, access)
    if maintenance:
        audit(c, user['id'], '维护读取', 'maintenance', maintenance['id'], after={'reason': maintenance['reason'], 'path': request.url.path})
    return {**workspace, 'access': access, 'maintenance': bool(maintenance), 'maintenance_expires': maintenance['expires'] if maintenance else None}


def require_team_owner(c, workspace_id, user):
    workspace = c.execute("SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id AND m.user_id=? WHERE w.id=? AND w.kind='team' AND m.role IN ('owner','admin')", (user['id'], workspace_id)).fetchone()
    if not workspace: fail('只有此公司的 Admin 可以管理成员和权限', 403)
    c.scope(workspace_id)
    return workspace


def scope_roster(c):
    workspace = c.execute('SELECT kind, owner_id FROM workspaces WHERE id=?', (c.workspace_id,)).fetchone()
    if workspace['kind'] == 'personal':
        return [clean_user(r) for r in c.execute('SELECT * FROM users WHERE id=?', (workspace['owner_id'],)).fetchall()]
    return [{**clean_user(r), 'team_role': r['team_role'], 'membership_version': r['membership_version']} for r in c.execute('''SELECT u.*, m.role AS team_role, m.version AS membership_version FROM users u JOIN memberships m ON m.user_id=u.id
        WHERE m.workspace_id=? AND m.role IN ('owner','admin','member') ORDER BY u.name''', (c.workspace_id,)).fetchall()]


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
    if 'Content-Security-Policy' not in response.headers:
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data: blob:; font-src 'self' blob:; worker-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    if request.url.path.startswith('/api/'): response.headers['Cache-Control'] = 'no-store'
    if PRODUCTION: response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


def current_user(request, allow_change=False, connection=None):
    if connection is None:
        with db() as c: return current_user(request, allow_change, c)
    token = hashlib.sha256(request.cookies.get('cmp_session', '').encode()).hexdigest()
    row = connection.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>? AND u.active=1', (token, time.time())).fetchone()
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
        team_role = choice(body, 'team_role', ['none', 'member', 'admin'], 'member')
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
        member = c.execute("SELECT role FROM memberships WHERE workspace_id=? AND user_id=? AND role IN ('owner','admin','member')", (team_id, user['id'])).fetchone()
        if not member: fail('无权访问此团队', 403)
        c.scope(team_id)
        return scope_roster(c)


@app.post('/api/teams/{team_id}/members')
def add_team_member(team_id: str, body: dict, request: Request):
    user = current_user(request)
    role = choice(body, 'role', ['member', 'admin'], 'member')
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
    role = choice(body, 'role', ['member', 'admin', 'removed'], 'member')
    with db() as c:
        lock_records(c); require_team_owner(c, team_id, user)
        before = c.execute('SELECT role,version FROM memberships WHERE workspace_id=? AND user_id=?', (team_id, member_id)).fetchone()
        if not before: fail('团队成员不存在', 404)
        if before['role'] == 'owner': fail('不能修改团队 owner 的权限')
        if before['version'] != body.get('version'): fail('权限已被更新，请刷新后再编辑', 409)
        c.execute('UPDATE memberships SET role=?,version=version+1,updated_at=? WHERE workspace_id=? AND user_id=?', (role, now(), team_id, member_id))
        if role == 'removed':
            revoked = c.execute("SELECT * FROM resource_access WHERE workspace_id=? AND user_id=? AND role!='removed'", (team_id, member_id)).fetchall()
            for grant in revoked:
                audit(c, user['id'], '移出公司撤销访问', 'resource_access', grant['resource_id'], dict(grant), {**dict(grant), 'role': 'removed', 'version': grant['version'] + 1})
            c.execute("UPDATE resource_access SET role='removed',version=version+1,updated_at=? WHERE workspace_id=? AND user_id=? AND role!='removed'", (now(), team_id, member_id))
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
    rows = c.records("kind='stock_movements'", columns='id, data', internal=True).fetchall()
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
            if balance < 0:
                fail(f'库存不足：这次操作会使 {day} 的库存低于零，请核对入库和出库记录' if c.access is None or c.access['full'] else '库存不足，请联系公司 Admin 核对仓库库存')


def validate(kind, data, c, uploaded=False):
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
            transaction = reference_record(c, 'linked_transaction_id', out['linked_transaction_id'], 'transactions')
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
        out['profile_id'] = text_field(data, 'profile_id', 80)
        if not isinstance(data.get('ledger_task', False), bool): fail('账本待办标记须为是或否')
        out['ledger_task'] = bool(data.get('ledger_task', False) or out['profile_id'])
        if out['ledger_task'] and out['project_id']: fail('待办请选择项目或账本作为归属，不可同时选择')
        if out['profile_id'] and not c.records("id=? AND kind='finance_profiles'", (out['profile_id'],), columns='id').fetchone(): fail('财务账本不存在或无权访问')
    elif kind == 'files':
        previous = getattr(c, 'existing', (None, {}))[1]
        if previous.get('storage_provider') == 'gcs':
            url = previous['url']
            if data.get('url', url) != url: fail('已上传附件不能替换为其他链接，请另行上传文件')
        elif uploaded:
            url = ''
        else:
            url = text_field(data, 'url', 2000, True)
            try:
                parsed = urlparse(url)
                valid = (parsed.scheme == 'https' and parsed.hostname in ('drive.google.com', 'docs.google.com')
                         and not parsed.username and not parsed.password and parsed.port in (None, 443)
                         and not re.search(r'[\\\s]', url))
            except ValueError:
                valid = False
            if not valid: fail('请使用 Google Drive 或 Google Docs 的 HTTPS 分享链接')
        out = {'title': text_field(data, 'title', 200, True), 'url': url, 'project_id': text_field(data, 'project_id', 80, True)}
    else: fail('记录类型无效', 404)
    if out.get('project_id') and not reference_record(c, 'project_id', out['project_id'], 'projects'): fail('关联项目不存在')
    if kind in ('notes', 'tasks', 'files', 'transactions', 'stock_movements'):
        out['subsection_id'] = text_field(data, 'subsection_id', 80)
        if out['subsection_id']:
            section = reference_record(c, 'subsection_id', out['subsection_id'], 'subsections')
            if not section or json.loads(section['data'])['project_id'] != out.get('project_id'):
                fail('分区不存在或不属于所选项目')
    if kind == 'notes':
        attachments = data.get('attachment_ids', [])
        if not isinstance(attachments, list) or len(attachments) > 50 or any(not isinstance(key, str) or not 1 <= len(key) <= 80 for key in attachments):
            fail('附件关联格式无效，每条沟通记录最多关联 50 份附件')
        out['attachment_ids'] = list(dict.fromkeys(attachments))
        for key in out['attachment_ids']:
            attachment = c.records("id=? AND kind='files'", (key,)).fetchone()
            if not attachment or json.loads(attachment['data']).get('project_id') != out['project_id']:
                fail('只能关联同一项目中的附件')
        out['show_on_timeline'] = data.get('show_on_timeline', True)
        if not isinstance(out['show_on_timeline'], bool):
            fail('加入时间线必须为是或否')
        out['linked_task_id'] = text_field(data, 'linked_task_id', 80)
        if out['linked_task_id']:
            task = c.records("id=? AND kind='tasks'", (out['linked_task_id'],), columns='data').fetchone()
            if not task or json.loads(task['data']).get('project_id') != out['project_id']:
                fail('请选择同一项目中的待办')
    if kind == 'tasks' and out.get('assignee_id') and c.access is not None and not c.workspace_id.startswith('personal:'):
        resource, resource_id = record_resource(kind, out)
        if resource:
            assigned = c.execute("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?", (c.workspace_id, out['assignee_id'])).fetchone()
            grant = c.execute("SELECT role FROM resource_access WHERE workspace_id=? AND resource_kind=? AND resource_id=? AND user_id=? AND role IN ('viewer','editor')", (c.workspace_id, resource, resource_id, out['assignee_id'])).fetchone()
            if not assigned or (assigned['role'] not in ('owner','admin') and not grant): fail('待办负责人没有对应项目或账本权限，请先分配访问权限')
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


def insert_record(c, kind, data, actor, action='新增', item_id=None):
    authorize_record(c, kind, data, write=True, creating=True)
    item_id, at = item_id or uid(), now()
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


@app.get('/api/resource-access/{kind}/{resource_id}')
def resource_members(kind: str, resource_id: str, request: Request):
    user = current_user(request)
    with db() as c:
        workspace = scope_request(c, request, user)
        require_team_owner(c, workspace['id'], user)
        resource_id = resource_target(c, kind, resource_id)
        rows = c.execute('''SELECT u.id,u.name,u.username,u.active,m.role AS company_role,
            COALESCE(r.role,'removed') AS access,COALESCE(r.version,0) AS version
            FROM memberships m JOIN users u ON u.id=m.user_id
            LEFT JOIN resource_access r ON r.workspace_id=m.workspace_id AND r.user_id=m.user_id AND r.resource_kind=? AND r.resource_id=?
            WHERE m.workspace_id=? AND m.role IN ('owner','admin','member') ORDER BY u.name''', (kind, resource_id, workspace['id'])).fetchall()
        return {'members': [dict(r) for r in rows]}


@app.patch('/api/resource-access/{kind}/{resource_id}')
def update_resource_members(kind: str, resource_id: str, body: dict, request: Request):
    user = current_user(request)
    with db() as c:
        lock_records(c)
        workspace = scope_request(c, request, user)
        require_team_owner(c, workspace['id'], user)
        resource_id = resource_target(c, kind, resource_id)
        if not isinstance(body.get('members'), list) or any(not isinstance(m, dict) or 'version' not in m for m in body['members']): fail('请提供当前权限版本')
        apply_resource_members(c, user, kind, resource_id, body['members'])
    return {'ok': True}


@app.get('/api/state')
def state(request: Request):
    user = current_user(request)
    with db() as c:
        workspace = scope_request(c, request, user)
        # Resolve deleted project labels once per state read, not once per
        # ledger row. Only authorized project names enter the response cache.
        c.deleted_projects = {r['id']: data for r in c.records("kind='projects'", include_deleted=True).fetchall()
                              if (data := json.loads(r['data'])).get('deleted_at')}
        records = [present_record(c, r) for r in c.records(order='updated_at DESC').fetchall() if r['kind'] not in ('transactions', 'finance_profiles') or can_finance(user)]
        spaces = accessible_spaces(c, user)
        access = c.access
    team = workspace['kind'] == 'team'
    admin = team and workspace['access'] in ('owner', 'admin')
    full_edit = access['full'] and not access['readonly']
    has_edit = full_edit or any(role == 'editor' for kind in ('projects', 'finance_profiles') for role in access[kind].values())
    finance = can_finance(user) and (access['full'] or bool(access['finance_profiles']))
    return {'records': records, 'workspace': workspace, 'workspaces': spaces,
        'config': {'finance_access': finance, 'can_edit': has_edit,
        'full_access': access['full'], 'full_edit': full_edit,
        'project_access': access['projects'], 'ledger_access': access['finance_profiles'],
        'can_manage_team': admin, 'can_manage_resources': admin,
        'upload_ready': bool(STORAGE_BUCKET) and (full_edit or 'editor' in access['projects'].values()),
        'drive_upload_ready': False,
        'drive_folder_url': 'https://drive.google.com/drive/folders/' + DRIVE_FOLDER if admin and workspace['id'] == COMPANY_SPACE else None,
        'sheet_url': SHEET_URL if admin and workspace['id'] == COMPANY_SPACE and finance else None}}


def visible_audit(c, row, visible):
    if c.access['full']: return True
    if row['kind'] == 'resource_access':
        data = json.loads(row['after_data']) or json.loads(row['before_data']) or {}
        return bool(c.access.get(data.get('resource_kind'), {}).get(data.get('resource_id')))
    if row['kind'] not in ('projects','finance_profiles','transactions','subsections','tasks','notes','files','stock_movements'):
        return False
    if row['entity_id'] not in visible: return False
    # Historical moves can contain content from two permission boundaries.
    # Both snapshots and the current record must be authorized.
    for snapshot in ('before_data', 'after_data'):
        data = json.loads(row[snapshot])
        if data and not record_role(c, row['kind'], data, row['entity_id']): return False
    return True


@app.get('/api/activity')
def activity(request: Request, entity_id: str = '', limit: int = 100):
    user = current_user(request)
    with db() as c:
        scope_request(c, request, user)
        sql = 'SELECT a.*, u.name AS actor_name FROM audit a LEFT JOIN users u ON a.actor=u.id WHERE a.workspace_id=?'
        params = [c.workspace_id]
        if entity_id: sql += ' AND a.entity_id=?';params.append(entity_id)
        if not can_finance(user): sql += " AND a.kind NOT IN ('transactions', 'finance_profiles')"
        visible = {r['id'] for r in c.records(columns='id', include_deleted=True).fetchall()}
        rows = c.execute(sql + ' ORDER BY a.at DESC', tuple(params)).fetchall()
        allowed = [r for r in rows if visible_audit(c, r, visible)][:max(1, min(limit, 500))]
        return [{**dict(r), 'before': json.loads(r['before_data']), 'after': json.loads(r['after_data'])} for r in allowed]


@app.post('/api/records/{kind}')
def create_record(kind: str, body: dict, request: Request):
    user = current_user(request)
    if kind in ('transactions', 'finance_profiles') and not can_finance(user): fail('无财务权限', 403)
    with db() as c:
        lock_records(c)
        scope_request(c, request, user, write=True)
        authorize_record(c, kind, body, write=True, creating=True)
        item_id = None
        if kind in ('projects', 'notes', 'files') and body.get('creation_key'):
            item_id = request_record_id(c.workspace_id, user['id'], kind, body['creation_key'])
            existing = c.records('id=? AND kind=?', (item_id, kind), internal=True).fetchone()
            if existing:
                if json.loads(existing['data']).get('deleted_at'): fail('该项目已删除，请重新打开新建项目表单', 409)
                authorize_record(c, kind, json.loads(existing['data']), item_id, write=True)
                if kind == 'notes' and json.loads(existing['data'])['project_id'] != body.get('project_id'):
                    fail('沟通记录已移动，请刷新后查看', 409)
                if kind == 'files':
                    saved = json.loads(existing['data'])
                    if (saved.get('storage_provider') == 'gcs' or saved['project_id'] != body.get('project_id')
                            or saved['url'] != body.get('url') or saved.get('link_note_id', '') != body.get('note_id', '')):
                        fail('链接已变化，请关闭窗口后重新添加', 409)
                    validate('files', saved, c)
                    note = upload_note(c, saved.get('link_note_id', ''), saved['project_id'], saved.get('subsection_id', ''))
                    if note and item_id not in json.loads(note['data']).get('attachment_ids', []):
                        fail('附件关联已解除，请在沟通记录中重新选择', 409)
                return present_record(c, existing)
        if kind in ('projects', 'notes') and body.get('with_attachments'):
            require_storage()
        data = prepare_record(c, kind, body, user)
        linked_note = None
        if kind == 'files':
            note_id = text_field(body, 'note_id', 80)
            linked_note = upload_note(c, note_id, data['project_id'], data.get('subsection_id', ''))
            if linked_note: data['link_note_id'] = note_id
        if kind == 'products': check_product(c, data)
        if kind == 'stock_movements': check_stock_balance(c, data)
        item_id = insert_record(c, kind, data, user['id'], item_id=item_id)
        if linked_note: attach_uploaded_file(c, linked_note, item_id, user['id'])
        if kind in ('projects', 'finance_profiles') and 'member_access' in body:
            apply_resource_members(c, user, kind, item_id, body['member_access'])
        return present_record(c, c.records('id=?', (item_id,)).fetchone())


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
        authorize_record(c, kind, before, item_id, write=True)
        c.existing = (kind, before)
        data = prepare_record(c, kind, {**before, **body}, user)
        if kind == 'files':
            for key in ('drive_file_id', 'upload_note_id', 'link_note_id', *STORED_FILE_FIELDS):
                if key in before: data[key] = before[key]
        authorize_record(c, kind, data, item_id, write=True)
        if kind == 'products': check_product(c, data, item_id, before)
        if kind == 'stock_movements': check_stock_balance(c, data, item_id)
        if kind == 'transactions':
            # Import provenance is set only by the administrative importer. A
            # member can edit the ledger fields, but cannot rewrite the source.
            if 'source_import' in before: data['source_import'] = before['source_import']
            for movement in c.records("kind='stock_movements'", columns='data', internal=True).fetchall():
                movement = json.loads(movement['data'])
                if movement.get('linked_transaction_id') == item_id: validate_stock_link(c, movement, data)
        if kind == 'subsections' and data['project_id'] != before['project_id']:
            fail('分区不能移动到其他项目，请在目标项目新建分区')
        if kind == 'tasks' and data.get('project_id') != before.get('project_id'):
            for note in c.records("kind='notes'", columns='data', internal=True).fetchall():
                if json.loads(note['data']).get('linked_task_id') == item_id:
                    fail('此待办已关联笔记，请先解除关联再更换项目')
        if kind == 'files' and data['project_id'] != before['project_id']:
            for note in c.records("kind='notes'", columns='data', internal=True).fetchall():
                if item_id in json.loads(note['data']).get('attachment_ids', []):
                    fail('此附件已关联沟通记录，请先解除关联再更换项目')
        result = c.execute('UPDATE entities SET data=?, version=version+1, updated_at=?, updated_by=? WHERE id=? AND version=? AND workspace_id=?', (json.dumps(data, ensure_ascii=False), now(), user['id'], item_id, row['version'], c.workspace_id))
        if result.rowcount != 1: fail('记录已被更新，请刷新', 409)
        audit(c, user['id'], '修改', kind, item_id, before, data)
        return present_record(c, c.records('id=?', (item_id,)).fetchone())


@app.delete('/api/records/projects/{item_id}')
def delete_project(item_id: str, body: dict, request: Request):
    user = current_user(request)
    with db() as c:
        lock_records(c)
        scope_request(c, request, user, write=True)
        if not c.access['full']: fail('只有公司 Admin 或个人空间主人可以删除项目', 403)
        row = c.records("id=? AND kind='projects'", (item_id,), include_deleted=True).fetchone()
        if not row: fail('项目不存在', 404)
        before = json.loads(row['data'])
        if before.get('deleted_at'): fail('项目已删除，请刷新列表', 409)
        if body.get('version') != row['version']: fail('项目已被更新，请刷新后重新确认删除', 409)
        if body.get('confirm_title') != before['title']: fail('请输入完整项目名称确认删除')
        at = now()
        after = {**before, 'deleted_at': at, 'deleted_by': user['id']}
        result = c.execute('UPDATE entities SET data=?,version=version+1,updated_at=?,updated_by=? WHERE id=? AND version=? AND workspace_id=?',
                           (json.dumps(after, ensure_ascii=False), at, user['id'], item_id, row['version'], c.workspace_id))
        if result.rowcount != 1: fail('项目已被更新，请刷新后重新确认删除', 409)
        audit(c, user['id'], '删除', 'projects', item_id, before, after)
    return {'ok': True}


def request_record_id(workspace, actor, resource, key):
    try: key = str(uuid.UUID(str(key)))
    except (ValueError, TypeError, AttributeError): fail('提交标识无效，请重新打开表单')
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'cmpmanager:{workspace}:{actor}:{resource}:{key}'))


def require_storage():
    if not STORAGE_BUCKET: fail('附件存储尚未配置，请联系管理员', 503)


def upload_note(c, note_id, project_id, subsection_id):
    if not note_id: return None
    row = c.records("id=? AND kind='notes'", (note_id,)).fetchone()
    if not row: fail('沟通记录不存在或无权访问', 404)
    data = json.loads(row['data'])
    authorize_record(c, 'notes', data, note_id, write=True)
    if data['project_id'] != project_id or data.get('subsection_id', '') != subsection_id:
        fail('沟通记录的项目或分区已变化，请刷新后重新上传', 409)
    return row


def attach_uploaded_file(c, note, file_id, actor):
    if not note: return
    before = json.loads(note['data'])
    attachments = before.get('attachment_ids', [])
    if file_id in attachments: return
    if len(attachments) >= 50: fail('每条沟通记录最多关联 50 份附件')
    after = {**before, 'attachment_ids': [*attachments, file_id]}
    result = c.execute('UPDATE entities SET data=?,version=version+1,updated_at=?,updated_by=? WHERE id=? AND version=? AND workspace_id=?',
                       (json.dumps(after, ensure_ascii=False), now(), actor, note['id'], note['version'], c.workspace_id))
    if result.rowcount != 1: fail('沟通记录已更新，请重试上传', 409)
    audit(c, actor, '关联附件', 'notes', note['id'], before, after)


@app.post('/api/upload')
def upload(request: Request, project_id: str = Form(...), file: UploadFile = File(...), subsection_id: str = Form(''), upload_key: str = Form(''), note_id: str = Form('')):
    user = current_user(request)
    fields = {'title': '附件', 'project_id': project_id, 'subsection_id': subsection_id}
    with db() as c:
        scope_request(c, request, user, write=True)
        authorize_record(c, 'files', fields, write=True, creating=True)
        validate('files', fields, c, uploaded=True)
        upload_note(c, note_id, project_id, subsection_id)
    require_storage()
    content = file.file.read(attachment_storage.MAX_BYTES + 1)
    if len(content) > attachment_storage.MAX_BYTES: fail('文件不能超过 20 MB', 413)
    if not content: fail('不能上传空文件')
    filename = Path((file.filename or '附件').replace(chr(92), '/')).name[:200] or '附件'
    filename = ''.join(ch for ch in filename if ord(ch) >= 32 and ord(ch) != 127) or '附件'
    fields['title'] = filename
    digest = hashlib.sha256(content).hexdigest()
    mime = attachment_storage.content_type(filename, content)
    with db() as c:
        scope_request(c, request, user, write=True)
        item_id = request_record_id(c.workspace_id, user['id'], 'files:' + project_id, upload_key) if upload_key else uid()
        if DATABASE_URL:
            c.execute('SELECT pg_advisory_xact_lock(?)', (int.from_bytes(hashlib.sha256(item_id.encode()).digest()[:8], 'big', signed=True),))
        user = current_user(request, connection=c)
        scope_request(c, request, user, write=True)
        authorize_record(c, 'files', fields, write=True, creating=True)
        note = upload_note(c, note_id, project_id, subsection_id)
        existing = c.records("id=? AND kind='files'", (item_id,)).fetchone()
        if existing:
            previous = json.loads(existing['data'])
            if previous.get('upload_sha256') != digest or previous.get('project_id') != project_id or previous.get('subsection_id', '') != subsection_id or previous.get('upload_note_id', '') != note_id:
                fail('此附件已保存或移动，请刷新项目后检查', 409)
            if note and item_id not in json.loads(note['data']).get('attachment_ids', []):
                fail('此附件与沟通记录的关联已解除，请刷新后按需重新关联', 409)
            return present_record(c, existing)
        if note and len(json.loads(note['data']).get('attachment_ids', [])) >= 50:
            fail('每条沟通记录最多关联 50 份附件')
        name = attachment_storage.object_name(c.workspace_id, item_id)
        try:
            stored = attachment_storage.store(STORAGE_BUCKET, name, content, mime, digest)
        except attachment_storage.ObjectConflict:
            fail('已有附件与本次内容不同，请重新选择文件', 409)
        except (GoogleAPIError, GoogleAuthError, requests.RequestException, ValueError, KeyError, TypeError):
            fail('附件上传未完成，请重试；已保存的附件会自动核对。', 502)
        lock_records(c)
        user = current_user(request, connection=c)
        scope_request(c, request, user, write=True)
        authorize_record(c, 'files', fields, write=True, creating=True)
        note = upload_note(c, note_id, project_id, subsection_id)
        data = validate('files', fields, c, uploaded=True)
        data.update(stored, filename=filename, url='/api/files/' + item_id + '/content')
        if note_id: data['upload_note_id'] = note_id
        insert_record(c, 'files', data, user['id'], item_id=item_id)
        attach_uploaded_file(c, note, item_id, user['id'])
        return present_record(c, c.records('id=?', (item_id,)).fetchone())


def authorized_file(c, request, user, item_id):
    scope_request(c, request, user)
    row = c.records("id=? AND kind='files'", (item_id,)).fetchone()
    if not row: fail('附件不存在或无权访问', 404)
    info = json.loads(row['data'])
    if info.get('storage_provider') != 'gcs': fail('这是外部文件链接，请在原网站打开', 400)
    if info.get('storage_bucket') != STORAGE_BUCKET or info.get('storage_object') != attachment_storage.object_name(c.workspace_id, item_id):
        fail('附件存储信息无效', 409)
    return info


@app.get('/api/files/{item_id}/content')
def file_content(item_id: str, request: Request, download: bool = False):
    user = current_user(request)
    with db() as c:
        info = authorized_file(c, request, user, item_id)
    try:
        content = attachment_storage.read(info)
    except NotFound:
        fail('附件文件不存在，请联系管理员', 404)
    except (GoogleAPIError, GoogleAuthError, requests.RequestException, attachment_storage.ObjectConflict, ValueError, KeyError, TypeError):
        fail('暂时无法读取附件，请稍后重试', 502)
    # Revocations, workspace changes and moved records also apply during I/O.
    with db() as c:
        user = current_user(request, connection=c)
        current = authorized_file(c, request, user, item_id)
        if any(current.get(k) != info.get(k) for k in STORED_FILE_FIELDS): fail('附件已更新，请重新打开', 409)
    mime = attachment_storage.content_type(info['filename'], content)
    disposition = 'attachment' if download or mime == 'application/octet-stream' else 'inline'
    return Response(content, media_type='application/octet-stream' if download else mime, headers={
        'Content-Disposition': disposition + "; filename=attachment; filename*=UTF-8''" + quote(info['filename'], safe=''),
        'Content-Security-Policy': "sandbox; default-src 'none'; frame-ancestors 'none'",
        'Vary': 'Cookie, X-Cmp-Workspace, X-Cmp-Maintenance',
        'Cache-Control': 'private, no-store',
    })


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/')
def index(): return FileResponse(ROOT / 'static/index.html', headers={'Cache-Control': 'no-cache'})


@app.get('/sw.js')
def worker(): return FileResponse(ROOT / 'static/sw.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache'})
