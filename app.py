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


class DB:
    def __init__(self, conn): self.conn = conn
    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace('?', '%s') if DATABASE_URL else sql, params)


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
        yield DB(conn)
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
    c.execute('INSERT INTO audit VALUES (?,?,?,?,?,?,?,?)',
              (uid(), now(), actor, action, kind, entity_id, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))


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
    with db() as c: c.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(request.cookies.get('cmp_session', '').encode()).hexdigest(),))
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
        row = c.execute('SELECT password FROM users WHERE id=?', (user['id'],)).fetchone()
        if not check_password(str(body.get('current_password', '')), row['password']): fail('当前密码不正确')
        c.execute('UPDATE users SET password=?, must_change=0 WHERE id=?', (password_hash(password), user['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
        audit(c, user['id'], '修改密码', 'user', user['id'])
    return {'ok': True}


@app.get('/api/users')
def users(request: Request):
    current_user(request)
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
        if c.execute('SELECT count(*) AS n FROM users WHERE active=1').fetchone()['n'] >= 6: fail('最多启用 6 个账号（含管理员）')
        if c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone(): fail('该账号已存在')
        item = {'id': uid(), 'username': username, 'name': name, 'role': 'member', 'active': 1, 'must_change': 1}
        c.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?)', (item['id'], username, name, 'member', password_hash(password), 1, 1))
        audit(c, user['id'], '创建账号', 'user', item['id'], after=item)
    return item


@app.patch('/api/users/{user_id}')
def update_user(user_id: str, body: dict, request: Request):
    actor = current_user(request); administrator(actor)
    with db() as c:
        if DATABASE_URL: c.execute('SELECT pg_advisory_xact_lock(74391002)')
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
        after = clean_user(c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())
        audit(c, actor['id'], '重置密码' if 'password' in body else '更新账号', 'user', user_id, before, after)
    return after


def text_field(data, key, maximum=1000, required=False):
    value = str(data.get(key, '') or '').strip()
    if len(value) > maximum or (required and not value): fail(f'{key} 内容为空或过长')
    return value


def choice(data, key, choices, default):
    value = data.get(key, default)
    if value not in choices: fail(f'{key} 选项无效')
    return value


def money(data, key, required=False):
    value = data.get(key)
    if value in (None, ''):
        if required: fail('请填写金额')
        return None
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0 or value > Decimal('999999999999') or value != value.quantize(Decimal('.01')): raise ValueError()
        return str(value.quantize(Decimal('.01')))
    except (InvalidOperation, ValueError): fail('金额须为非负数，最多两位小数')


def date_field(data, key, required=False):
    value = text_field(data, key, 10, required)
    if value:
        try: date.fromisoformat(value)
        except ValueError: fail('日期格式应为 YYYY-MM-DD')
    return value


def validate(kind, data, c):
    if kind == 'projects':
        try: progress = int(data.get('progress', 0))
        except (ValueError, TypeError): fail('进度须为 0–100 的整数')
        if not 0 <= progress <= 100: fail('进度须在 0–100 之间')
        out = {'title': text_field(data, 'title', 120, True), 'description': text_field(data, 'description', 10000), 'status': choice(data, 'status', ['进行中', '待启动', '暂停', '已完成'], '进行中'), 'progress': progress, 'due_date': date_field(data, 'due_date'), 'owner_id': text_field(data, 'owner_id', 80)}
    elif kind == 'subsections':
        out = {'title': text_field(data, 'title', 120, True), 'description': text_field(data, 'description', 5000), 'project_id': text_field(data, 'project_id', 80, True)}
    elif kind == 'transactions':
        out = {k: text_field(data, k, 2000 if k == 'note' else 150) for k in ['event', 'category', 'payment_method', 'responsible', 'note', 'project_id']}
        out.update(title=text_field(data, 'title', 200, True), date=date_field(data, 'date', True), direction=choice(data, 'direction', ['收入', '支出'], '支出'), currency=choice(data, 'currency', ['USD', 'CNY'], 'USD'), amount=money(data, 'amount', True), usd_amount=money(data, 'usd_amount'), booked_amount=money(data, 'booked_amount'), payment_status=choice(data, 'payment_status', ['已付', '未付', '部分支付'], '未付'), posting_status=choice(data, 'posting_status', ['平帐', '未入账', '部分入账'], '未入账'))
        if out['currency'] == 'USD': out['usd_amount'] = out['amount']
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
    if out.get('project_id') and not c.execute('SELECT id FROM entities WHERE id=? AND kind=?', (out['project_id'], 'projects')).fetchone(): fail('关联项目不存在')
    if kind in ('notes', 'tasks', 'files', 'transactions'):
        out['subsection_id'] = text_field(data, 'subsection_id', 80)
        if out['subsection_id']:
            section = c.execute("SELECT data FROM entities WHERE id=? AND kind='subsections'", (out['subsection_id'],)).fetchone()
            if not section or json.loads(section['data'])['project_id'] != out.get('project_id'):
                fail('分区不存在或不属于所选项目')
    if kind == 'notes':
        out['show_on_timeline'] = data.get('show_on_timeline', True)
        if not isinstance(out['show_on_timeline'], bool):
            fail('加入时间线必须为是或否')
        out['linked_task_id'] = text_field(data, 'linked_task_id', 80)
        if out['linked_task_id']:
            task = c.execute("SELECT data FROM entities WHERE id=? AND kind='tasks'", (out['linked_task_id'],)).fetchone()
            if not task or json.loads(task['data']).get('project_id') != out['project_id']:
                fail('请选择同一项目中的待办')
    for key in ('owner_id', 'assignee_id'):
        if out.get(key) and not c.execute('SELECT id FROM users WHERE id=? AND active=1', (out[key],)).fetchone(): fail('成员不存在或已停用')
    return out


def entity(row):
    return {**json.loads(row['data']), **{k: row[k] for k in ('id', 'kind', 'version', 'created_at', 'updated_at', 'created_by', 'updated_by')}}


def can_finance(user): return FINANCE_MEMBERS or user['role'] == 'admin'


def lock_records(c):
    # All entity writes share a short transaction lock so references cannot move
    # to another project between validation and commit. SQLite uses BEGIN IMMEDIATE.
    if DATABASE_URL: c.execute('SELECT pg_advisory_xact_lock(74391003)')


def insert_record(c, kind, data, actor):
    item_id, at = uid(), now()
    c.execute('INSERT INTO entities VALUES (?,?,?,?,?,?,?,?)', (item_id, kind, json.dumps(data, ensure_ascii=False), 1, at, at, actor, actor))
    audit(c, actor, '新增', kind, item_id, after=data)
    return item_id


def prepare_record(c, kind, body, user):
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
        records = [entity(r) for r in c.execute('SELECT * FROM entities ORDER BY updated_at DESC').fetchall() if r['kind'] != 'transactions' or can_finance(user)]
    return {'records': records, 'config': {'finance_access': can_finance(user), 'drive_upload_ready': all(os.getenv(k) for k in ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN']), 'drive_folder_url': 'https://drive.google.com/drive/folders/' + DRIVE_FOLDER, 'sheet_url': SHEET_URL if can_finance(user) else None}}


@app.get('/api/activity')
def activity(request: Request, entity_id: str = '', limit: int = 100):
    user = current_user(request)
    sql = 'SELECT a.*, u.name AS actor_name FROM audit a LEFT JOIN users u ON a.actor=u.id WHERE 1=1'
    params = []
    if entity_id: sql += ' AND a.entity_id=?'; params.append(entity_id)
    if not can_finance(user): sql += " AND a.kind <> 'transactions'"
    sql += ' ORDER BY a.at DESC LIMIT ?'; params.append(max(1, min(limit, 500)))
    with db() as c:
        return [{**dict(r), 'before': json.loads(r['before_data']), 'after': json.loads(r['after_data'])} for r in c.execute(sql, params).fetchall()]


@app.post('/api/records/{kind}')
def create_record(kind: str, body: dict, request: Request):
    user = current_user(request)
    if kind == 'transactions' and not can_finance(user): fail('无财务权限', 403)
    with db() as c:
        lock_records(c)
        data = prepare_record(c, kind, body, user)
        item_id = insert_record(c, kind, data, user['id'])
        return entity(c.execute('SELECT * FROM entities WHERE id=?', (item_id,)).fetchone())


@app.patch('/api/records/{kind}/{item_id}')
def update_record(kind: str, item_id: str, body: dict, request: Request):
    user = current_user(request)
    if kind == 'transactions' and not can_finance(user): fail('无财务权限', 403)
    with db() as c:
        lock_records(c)
        row = c.execute('SELECT * FROM entities WHERE id=? AND kind=?', (item_id, kind)).fetchone()
        if not row: fail('记录不存在', 404)
        if body.get('version') != row['version']: fail('其他成员已更新此记录，请刷新后再编辑', 409)
        before = json.loads(row['data'])
        data = prepare_record(c, kind, {**before, **body}, user)
        if kind == 'subsections' and data['project_id'] != before['project_id']:
            fail('分区不能移动到其他项目，请在目标项目新建分区')
        if kind == 'tasks' and data.get('project_id') != before.get('project_id'):
            for note in c.execute("SELECT data FROM entities WHERE kind='notes'").fetchall():
                if json.loads(note['data']).get('linked_task_id') == item_id:
                    fail('此待办已关联笔记，请先解除关联再更换项目')
        result = c.execute('UPDATE entities SET data=?, version=version+1, updated_at=?, updated_by=? WHERE id=? AND version=?', (json.dumps(data, ensure_ascii=False), now(), user['id'], item_id, row['version']))
        if result.rowcount != 1: fail('记录已被更新，请刷新', 409)
        audit(c, user['id'], '修改', kind, item_id, before, data)
        return entity(c.execute('SELECT * FROM entities WHERE id=?', (item_id,)).fetchone())


@app.post('/api/upload')
def upload(request: Request, project_id: str = Form(...), file: UploadFile = File(...), subsection_id: str = Form('')):
    user = current_user(request)
    with db() as c:
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
