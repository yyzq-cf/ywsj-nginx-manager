"""
ywsj-nginx-manager — Nginx WebUI 管理面板
Flask 主应用
"""
import os
import sqlite3
import logging
import threading
import time
import io
import base64
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, make_response, g, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
import pyotp
import qrcode

from i18n import init_i18n, get_locale, set_language, SUPPORTED_LANGUAGES
from nginx_manager import (
    ensure_dirs, nginx_test, nginx_reload,
    generate_proxy_conf, generate_redirect_conf, generate_stream_conf,
    save_conf, delete_conf, list_confs, get_conf,
    get_nginx_status, parse_access_log, get_log_stats,
    NGINX_CONF_DIR, NGINX_SSL_DIR, NGINX_LOG_DIR,
)
from ssl_manager import (
    issue_cert, renew_cert, revoke_cert, list_certs, get_cert_info,
    ensure_acme, setup_cron_renew,
)

# ============ 配置 ============
APP_VERSION = os.environ.get('APP_VERSION', 'dev')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ywsj-nginx-manager-change-me')
app.permanent_session_lifetime = timedelta(days=7)
app.config['SQLITE_DB'] = '/data/nginx_manager.db'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# 日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('ywsj-nginx-manager')

# i18n
init_i18n(app)

# ============ 2FA 全局状态 ============
_2fa_pending = {}   # {pending_token: {user_id, expire, attempts}}
_2fa_lock = threading.Lock()
_2FA_EXPIRE = 180   # 3分钟
_2FA_MAX_ATTEMPTS = 5

# ============ 数据库 ============
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['SQLITE_DB'])
        db.row_factory = sqlite3.Row
    return db


def init_db():
    os.makedirs('/data', exist_ok=True)
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS proxy_hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL UNIQUE,
            forward_host TEXT NOT NULL,
            forward_port INTEGER NOT NULL,
            forward_scheme TEXT DEFAULT 'http',
            ssl_cert_path TEXT,
            ssl_key_path TEXT,
            websocket INTEGER DEFAULT 0,
            block_exploits INTEGER DEFAULT 1,
            force_ssl INTEGER DEFAULT 0,
            custom_config TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS redirects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL UNIQUE,
            target_url TEXT NOT NULL,
            permanent INTEGER DEFAULT 1,
            preserve_path INTEGER DEFAULT 1,
            ssl_cert_path TEXT,
            ssl_key_path TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listen_port INTEGER NOT NULL UNIQUE,
            target_host TEXT NOT NULL,
            target_port INTEGER NOT NULL,
            protocol TEXT DEFAULT 'tcp',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            ip TEXT PRIMARY KEY,
            attempts INTEGER DEFAULT 0,
            locked_until TEXT,
            last_attempt TEXT
        );
    ''')
    # 自动迁移：添加缺失的列
    cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if 'totp_secret' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
    if 'username' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        # 给已有用户设置默认用户名
        db.execute("UPDATE users SET username = 'admin' WHERE username IS NULL")
    db.commit()


@app.before_request
def before_request():
    init_db()


@app.teardown_appcontext
def close_db(error):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


# ============ 认证 ============
def is_auth_enabled():
    db = get_db()
    user = db.execute('SELECT COUNT(*) as count FROM users').fetchone()
    return user['count'] > 0


def is_locked(ip):
    db = get_db()
    row = db.execute('SELECT * FROM login_attempts WHERE ip = ?', (ip,)).fetchone()
    if row and row['locked_until']:
        locked_until = datetime.fromisoformat(row['locked_until'])
        if datetime.now() < locked_until:
            return True
        # 锁定过期，重置
        db.execute('UPDATE login_attempts SET attempts = 0, locked_until = NULL WHERE ip = ?', (ip,))
        db.commit()
    return False


def record_failed_attempt(ip):
    db = get_db()
    now = datetime.now()
    row = db.execute('SELECT * FROM login_attempts WHERE ip = ?', (ip,)).fetchone()
    if row:
        attempts = row['attempts'] + 1
        locked_until = None
        if attempts >= 5:
            locked_until = (now + timedelta(minutes=15)).isoformat()
        db.execute(
            'UPDATE login_attempts SET attempts = ?, locked_until = ?, last_attempt = ? WHERE ip = ?',
            (attempts, locked_until, now.isoformat(), ip)
        )
    else:
        db.execute(
            'INSERT INTO login_attempts (ip, attempts, last_attempt) VALUES (?, 1, ?)',
            (ip, now.isoformat())
        )
    db.commit()


def clear_attempts(ip):
    db = get_db()
    db.execute('DELETE FROM login_attempts WHERE ip = ?', (ip,))
    db.commit()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_auth_enabled():
            return redirect(url_for('setup'))
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# ============ 路由：页面 ============
@app.route('/')
@login_required
def index():
    status = get_nginx_status()
    confs = list_confs()
    certs = list_certs()
    
    proxy_count = len([c for c in confs if not c['is_stream'] and 'proxy_pass' in c['content']])
    redirect_count = len([c for c in confs if 'return 301' in c['content'] or 'return 302' in c['content']])
    stream_count = len([c for c in confs if c['is_stream']])
    
    log_stats = get_log_stats()
    recent_logs = parse_access_log(lines=10)
    
    return render_template('index.html',
        status=status,
        proxy_count=proxy_count,
        redirect_count=redirect_count,
        stream_count=stream_count,
        ssl_count=len(certs),
        log_stats=log_stats,
        recent_logs=recent_logs,
        version=APP_VERSION,
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not is_auth_enabled():
        return redirect(url_for('setup'))
    if 'user_id' in session:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        pending_token = request.form.get('pending_token', '')
        ip = request.remote_addr

        if pending_token:
            # === Phase 2: TOTP 验证（从 session 读取 pending 状态）===
            code = request.form.get('code', '').strip()

            pending = session.pop('_2fa_pending', None)
            if not pending or pending.get('token') != pending_token:
                session.clear()
                return render_template('login.html', error_key='login.expired', twofa_stage=False)

            if time.time() > pending.get('expire', 0):
                session.clear()
                return render_template('login.html', error_key='login.expired', twofa_stage=False)

            if pending.get('attempts', 0) >= _2FA_MAX_ATTEMPTS:
                session.clear()
                return render_template('login.html', error_key='login.locked', twofa_stage=False)

            db = get_db()
            user = db.execute('SELECT * FROM users WHERE id = ?', (pending['user_id'],)).fetchone()

            if user and user['totp_secret']:
                totp = pyotp.TOTP(user['totp_secret'])
                if totp.verify(code, valid_window=1):
                    # 验证成功
                    session.clear()
                    session.permanent = True
                    session['user_id'] = user['id']
                    clear_attempts(ip)
                    return redirect(url_for('index'))
                else:
                    # 验证失败，保留在 2FA 阶段
                    pending['attempts'] = pending.get('attempts', 0) + 1
                    session['_2fa_pending'] = pending
                    return render_template('login.html', error_key='login.totp_error',
                                           pending_token=pending_token, twofa_stage=True)
            else:
                session.clear()
                return render_template('login.html', error_key='login.expired', twofa_stage=False)

        else:
            # === Phase 1: 用户名+密码验证 ===
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')

            if is_locked(ip):
                return render_template('login.html', error_key='login.locked')

            db = get_db()
            user = db.execute('SELECT * FROM users WHERE username = ? OR id = 1', (username,)).fetchone()
            if user and user['username'] and user['username'] != username:
                record_failed_attempt(ip)
                return render_template('login.html', error_key='login.error')
            if user and check_password_hash(user['password_hash'], password):
                clear_attempts(ip)
                # 检查是否启用 2FA
                if user['totp_secret']:
                    # 用 session 存储 pending 状态（跨 worker 可靠）
                    token = os.urandom(16).hex()
                    session['_2fa_pending'] = {
                        'token': token,
                        'user_id': user['id'],
                        'expire': time.time() + _2FA_EXPIRE,
                        'attempts': 0,
                    }
                    return render_template('login.html', pending_token=token, twofa_stage=True)
                else:
                    # 未启用 2FA，直接登录
                    session.permanent = True
                    session['user_id'] = user['id']
                    return redirect(url_for('index'))
            else:
                record_failed_attempt(ip)
                return render_template('login.html', error_key='login.error')

    return render_template('login.html', twofa_stage=False)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if is_auth_enabled():
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        
        if not username or len(username) < 2:
            return render_template('setup.html', error_key='setup.username_short')
        if password != confirm:
            return render_template('setup.html', error_key='setup.mismatch')
        if len(password) < 6:
            return render_template('setup.html', error_key='setup.mismatch')
        
        db = get_db()
        pw_hash = generate_password_hash(password, method='scrypt')
        db.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', (username, pw_hash))
        db.commit()
        
        # 初始化 nginx 和 acme
        ensure_dirs()
        ensure_acme()
        setup_cron_renew()
        
        return render_template('setup.html', done=True)
    
    return render_template('setup.html')


@app.route('/hosts')
@login_required
def hosts_page():
    confs = list_confs()
    proxies = [c for c in confs if not c['is_stream'] and 'proxy_pass' in c['content']]
    certs = list_certs()
    return render_template('hosts.html', proxies=proxies, certs=certs)


@app.route('/ssl')
@login_required
def ssl_page():
    certs = list_certs()
    return render_template('ssl.html', certs=certs)


@app.route('/redirects')
@login_required
def redirects_page():
    confs = list_confs()
    redirects = [c for c in confs if 'return 301' in c['content'] or 'return 302' in c['content']]
    return render_template('redirects.html', redirects=redirects)


@app.route('/streams')
@login_required
def streams_page():
    confs = list_confs()
    streams = [c for c in confs if c['is_stream']]
    return render_template('streams.html', streams=streams)


@app.route('/logs')
@login_required
def logs_page():
    lines = int(request.args.get('lines', 100))
    access_logs = parse_access_log(lines=lines)
    stats = get_log_stats()
    return render_template('logs.html', access_logs=access_logs, stats=stats, lines=lines)


# ============ API: 语言切换 ============
@app.route('/api/language', methods=['GET'])
def get_language():
    return jsonify({
        'current': get_locale(),
        'supported': SUPPORTED_LANGUAGES,
    })


@app.route('/api/language', methods=['POST'])
def change_language():
    data = request.get_json()
    lang = data.get('language')
    if set_language(lang):
        resp = make_response(jsonify({'success': True, 'language': lang}))
        resp.set_cookie('language', lang, max_age=365*24*3600)
        return resp
    return jsonify({'success': False, 'error': 'Unsupported language'}), 400


# ============ API: Nginx 控制 ============
@app.route('/api/nginx/status')
@login_required
def api_nginx_status():
    return jsonify(get_nginx_status())


@app.route('/api/nginx/test')
@login_required
def api_nginx_test():
    ok, output = nginx_test()
    return jsonify({'ok': ok, 'output': output})


@app.route('/api/nginx/reload', methods=['POST'])
@login_required
def api_nginx_reload():
    ok, message = nginx_reload()
    return jsonify({'success': ok, 'message': message})


# ============ API: 代理站点 ============
@app.route('/api/hosts', methods=['GET'])
@login_required
def api_hosts_list():
    db = get_db()
    rows = db.execute('SELECT * FROM proxy_hosts ORDER BY created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/hosts', methods=['POST'])
@login_required
def api_hosts_create():
    data = request.get_json()
    domain = data.get('domain', '').strip()
    forward_host = data.get('forward_host', '').strip()
    forward_port = int(data.get('forward_port', 80))
    forward_scheme = data.get('forward_scheme', 'http')
    ssl_cert_path = data.get('ssl_cert_path')
    ssl_key_path = data.get('ssl_key_path')
    websocket = bool(data.get('websocket', False))
    block_exploits = bool(data.get('block_exploits', True))
    force_ssl = bool(data.get('force_ssl', False))
    custom_config = data.get('custom_config', '')
    
    if not domain or not forward_host:
        return jsonify({'success': False, 'error': 'domain and forward_host required'}), 400
    
    # 生成 nginx 配置
    conf = generate_proxy_conf(
        domain=domain,
        forward_host=forward_host,
        forward_port=forward_port,
        forward_scheme=forward_scheme,
        ssl_cert_path=ssl_cert_path,
        ssl_key_path=ssl_key_path,
        websocket=websocket,
        block_common_exploits=block_exploits,
        force_ssl=force_ssl,
        custom_config=custom_config,
    )
    
    # 保存配置文件
    filepath = save_conf(domain, conf, "proxy")
    
    # 校验配置
    ok, output = nginx_test()
    if not ok:
        # 校验失败，回滚
        os.remove(filepath)
        return jsonify({'success': False, 'error': f'Nginx config test failed: {output}'}), 400
    
    # 重载
    ok, msg = nginx_reload()
    if not ok:
        os.remove(filepath)
        return jsonify({'success': False, 'error': msg}), 500
    
    # 保存到数据库
    db = get_db()
    try:
        db.execute(
            '''INSERT INTO proxy_hosts 
            (domain, forward_host, forward_port, forward_scheme, ssl_cert_path, ssl_key_path,
             websocket, block_exploits, force_ssl, custom_config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (domain, forward_host, forward_port, forward_scheme, ssl_cert_path, ssl_key_path,
             int(websocket), int(block_exploits), int(force_ssl), custom_config)
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Domain already exists'}), 409
    
    return jsonify({'success': True, 'message': 'Proxy host created'})


@app.route('/api/hosts/<domain>', methods=['GET'])
@login_required
def api_hosts_get(domain):
    db = get_db()
    row = db.execute('SELECT * FROM proxy_hosts WHERE domain = ?', (domain,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    return jsonify(dict(row))


@app.route('/api/hosts/<domain>', methods=['PUT'])
@login_required
def api_hosts_update(domain):
    data = request.get_json()
    forward_host = data.get('forward_host', '').strip()
    forward_port = int(data.get('forward_port', 80))
    forward_scheme = data.get('forward_scheme', 'http')
    ssl_cert_path = data.get('ssl_cert_path')
    ssl_key_path = data.get('ssl_key_path')
    websocket = bool(data.get('websocket', False))
    block_exploits = bool(data.get('block_exploits', True))
    force_ssl = bool(data.get('force_ssl', False))
    custom_config = data.get('custom_config', '')
    
    if not forward_host:
        return jsonify({'success': False, 'error': 'forward_host required'}), 400
    
    # 重新生成 nginx 配置
    conf = generate_proxy_conf(
        domain=domain,
        forward_host=forward_host,
        forward_port=forward_port,
        forward_scheme=forward_scheme,
        ssl_cert_path=ssl_cert_path,
        ssl_key_path=ssl_key_path,
        websocket=websocket,
        block_common_exploits=block_exploits,
        force_ssl=force_ssl,
        custom_config=custom_config,
    )
    
    # 保存配置文件（覆盖）
    filepath = save_conf(domain, conf, "proxy")
    
    # 校验配置
    ok, output = nginx_test()
    if not ok:
        return jsonify({'success': False, 'error': f'Nginx config test failed: {output}'}), 400
    
    # 重载
    ok, msg = nginx_reload()
    if not ok:
        return jsonify({'success': False, 'error': msg}), 500
    
    # 更新数据库
    db = get_db()
    db.execute(
        '''UPDATE proxy_hosts SET 
        forward_host=?, forward_port=?, forward_scheme=?, ssl_cert_path=?, ssl_key_path=?,
        websocket=?, block_exploits=?, force_ssl=?, custom_config=?, updated_at=datetime('now','localtime')
        WHERE domain=?''',
        (forward_host, forward_port, forward_scheme, ssl_cert_path, ssl_key_path,
         int(websocket), int(block_exploits), int(force_ssl), custom_config, domain)
    )
    db.commit()
    
    return jsonify({'success': True, 'message': 'Proxy host updated'})


@app.route('/api/hosts/<domain>', methods=['DELETE'])
@login_required
def api_hosts_delete(domain):
    # 删除配置文件
    delete_conf(domain, "proxy")
    
    # 删除数据库记录
    db = get_db()
    db.execute('DELETE FROM proxy_hosts WHERE domain = ?', (domain,))
    db.commit()
    
    # 重载 nginx
    nginx_reload()
    
    return jsonify({'success': True, 'message': 'Proxy host deleted'})


# ============ API: 重定向 ============
@app.route('/api/redirects', methods=['GET'])
@login_required
def api_redirects_list():
    confs = list_confs()
    redirects = [c for c in confs if 'return 301' in c['content'] or 'return 302' in c['content']]
    return jsonify(redirects)


@app.route('/api/redirects', methods=['POST'])
@login_required
def api_redirects_create():
    data = request.get_json()
    domain = data.get('domain', '').strip()
    target_url = data.get('target_url', '').strip()
    permanent = bool(data.get('permanent', True))
    preserve_path = bool(data.get('preserve_path', True))
    ssl_cert_path = data.get('ssl_cert_path')
    ssl_key_path = data.get('ssl_key_path')
    
    if not domain or not target_url:
        return jsonify({'success': False, 'error': 'domain and target_url required'}), 400
    
    conf = generate_redirect_conf(
        domain=domain,
        target_url=target_url,
        permanent=permanent,
        preserve_path=preserve_path,
        ssl_cert_path=ssl_cert_path,
        ssl_key_path=ssl_key_path,
    )
    
    filepath = save_conf(domain, conf, "proxy")
    
    ok, output = nginx_test()
    if not ok:
        os.remove(filepath)
        return jsonify({'success': False, 'error': f'Config test failed: {output}'}), 400
    
    ok, msg = nginx_reload()
    if not ok:
        os.remove(filepath)
        return jsonify({'success': False, 'error': msg}), 500
    
    db = get_db()
    try:
        db.execute(
            '''INSERT INTO redirects (domain, target_url, permanent, preserve_path, ssl_cert_path, ssl_key_path)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (domain, target_url, int(permanent), int(preserve_path), ssl_cert_path, ssl_key_path)
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Domain already exists'}), 409
    
    return jsonify({'success': True, 'message': 'Redirect created'})


@app.route('/api/redirects/<domain>', methods=['DELETE'])
@login_required
def api_redirects_delete(domain):
    delete_conf(domain, "proxy")
    db = get_db()
    db.execute('DELETE FROM redirects WHERE domain = ?', (domain,))
    db.commit()
    nginx_reload()
    return jsonify({'success': True, 'message': 'Redirect deleted'})


# ============ API: 端口转发 ============
@app.route('/api/streams', methods=['GET'])
@login_required
def api_streams_list():
    confs = list_confs()
    streams = [c for c in confs if c['is_stream']]
    return jsonify(streams)


@app.route('/api/streams', methods=['POST'])
@login_required
def api_streams_create():
    data = request.get_json()
    listen_port = int(data.get('listen_port', 0))
    target_host = data.get('target_host', '').strip()
    target_port = int(data.get('target_port', 0))
    protocol = data.get('protocol', 'tcp')
    
    if not listen_port or not target_host or not target_port:
        return jsonify({'success': False, 'error': 'All fields required'}), 400
    
    conf = generate_stream_conf(listen_port, target_host, target_port, protocol)
    
    # stream 配置用不同文件名前缀
    domain_name = f"stream_{listen_port}"
    filepath = save_conf(domain_name, conf, "stream")
    
    ok, output = nginx_test()
    if not ok:
        os.remove(filepath)
        return jsonify({'success': False, 'error': f'Config test failed: {output}'}), 400
    
    ok, msg = nginx_reload()
    if not ok:
        os.remove(filepath)
        return jsonify({'success': False, 'error': msg}), 500
    
    db = get_db()
    try:
        db.execute(
            'INSERT INTO streams (listen_port, target_host, target_port, protocol) VALUES (?, ?, ?, ?)',
            (listen_port, target_host, target_port, protocol)
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Port already exists'}), 409
    
    return jsonify({'success': True, 'message': 'Stream created'})


@app.route('/api/streams/<int:port>', methods=['DELETE'])
@login_required
def api_streams_delete(port):
    delete_conf(f"stream_{port}", "stream")
    db = get_db()
    db.execute('DELETE FROM streams WHERE listen_port = ?', (port,))
    db.commit()
    nginx_reload()
    return jsonify({'success': True, 'message': 'Stream deleted'})


# ============ API: SSL 证书 ============
@app.route('/api/ssl', methods=['GET'])
@login_required
def api_ssl_list():
    certs = list_certs()
    return jsonify(certs)


@app.route('/api/ssl/issue', methods=['POST'])
@login_required
def api_ssl_issue():
    data = request.get_json()
    domain = data.get('domain', '').strip()
    dns_provider = data.get('dns_provider')
    dns_api_key = data.get('dns_api_key')
    dns_api_secret = data.get('dns_api_secret')
    standalone = bool(data.get('standalone', False))
    key_length = data.get('key_length', 'ec-256')
    test = bool(data.get('test', False))
    email = data.get('email', '').strip()
    
    if not domain:
        return jsonify({'success': False, 'error': 'Domain required'}), 400
    
    # 泛域名必须用 DNS API
    is_wildcard = domain.startswith('*.')
    if is_wildcard and not dns_provider:
        return jsonify({'success': False, 'error': 'Wildcard domains require DNS API verification'}), 400
    
    ok, msg, cert_path, key_path = issue_cert(
        domain=domain,
        dns_provider=dns_provider,
        dns_api_key=dns_api_key,
        dns_api_secret=dns_api_secret,
        standalone=standalone,
        key_length=key_length,
        test=test,
        email=email,
    )
    
    if ok:
        return jsonify({
            'success': True,
            'message': msg,
            'cert_path': cert_path,
            'key_path': key_path,
        })
    return jsonify({'success': False, 'error': msg}), 500


@app.route('/api/ssl/<domain>/renew', methods=['POST'])
@login_required
def api_ssl_renew(domain):
    ok, msg = renew_cert(domain)
    return jsonify({'success': ok, 'message': msg})


@app.route('/api/ssl/<domain>/revoke', methods=['DELETE'])
@login_required
def api_ssl_revoke(domain):
    ok, msg = revoke_cert(domain)
    return jsonify({'success': ok, 'message': msg})


@app.route('/api/ssl/<domain>/info')
@login_required
def api_ssl_info(domain):
    certs = list_certs()
    for cert in certs:
        if cert['domain'] == domain:
            if cert.get('installed_cer'):
                info = get_cert_info(cert['installed_cer'])
                if info:
                    cert.update(info)
            return jsonify(cert)
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/ssl/<domain>/content')
@login_required
def api_ssl_content(domain):
    """获取证书和密钥的文本内容 + DNS 凭证"""
    certs = list_certs()
    for cert in certs:
        if cert['domain'] == domain:
            result = {
                'domain': domain,
                'cert_content': None,
                'key_content': None,
                'cert_path': cert.get('installed_cer'),
                'key_path': cert.get('installed_key'),
                'dns_provider': None,
                'dns_api_key_masked': None,
            }
            # 读证书
            if cert.get('installed_cer') and os.path.exists(cert['installed_cer']):
                try:
                    with open(cert['installed_cer'], 'r') as f:
                        result['cert_content'] = f.read()
                except Exception as e:
                    result['cert_error'] = str(e)
            # 读密钥
            if cert.get('installed_key') and os.path.exists(cert['installed_key']):
                try:
                    with open(cert['installed_key'], 'r') as f:
                        result['key_content'] = f.read()
                except Exception as e:
                    result['key_error'] = str(e)
            # 从 acme.sh account.conf 读取已保存的 DNS 凭证
            account_conf = os.path.join('/home/appuser/.acme.sh', 'account.conf')
            if os.path.exists(account_conf):
                try:
                    with open(account_conf, 'r') as f:
                        for line in f:
                            line = line.strip()
                            # SAVED_CF_Token='xxx'
                            if line.startswith("SAVED_CF_Token="):
                                token = line.split("=", 1)[1].strip("'\"")
                                result['dns_provider'] = 'dns_cf'
                                result['dns_api_key'] = token or None
                            elif line.startswith("SAVED_CF_Account_ID="):
                                acid = line.split("=", 1)[1].strip("'\"")
                                if acid:
                                    result['dns_api_secret'] = acid
                            elif line.startswith("SAVED_DP_Id="):
                                token = line.split("=", 1)[1].strip("'\"")
                                result['dns_provider'] = 'dns_dp'
                                result['dns_api_key'] = token or None
                            elif line.startswith("SAVED_DP_Key="):
                                token = line.split("=", 1)[1].strip("'\"")
                                result['dns_api_secret'] = token
                            elif line.startswith("SAVED_Ali_Key="):
                                token = line.split("=", 1)[1].strip("'\"")
                                result['dns_provider'] = 'dns_ali'
                                result['dns_api_key'] = token or None
                            elif line.startswith("SAVED_Ali_Secret="):
                                token = line.split("=", 1)[1].strip("'\"")
                                result['dns_api_secret'] = token
                except Exception:
                    pass
            return jsonify(result)
    return jsonify({'error': 'Not found'}), 404


# ============ API: 日志 ============
@app.route('/api/logs/access')
@login_required
def api_logs_access():
    lines = int(request.args.get('lines', 100))
    logs = parse_access_log(lines=lines)
    return jsonify(logs)


@app.route('/api/logs/stats')
@login_required
def api_logs_stats():
    stats = get_log_stats()
    return jsonify(stats)


# ============ API: 账户设置（用户名+密码） ============
@app.route('/api/account', methods=['GET', 'POST'])
@login_required
def api_update_account():
    if request.method == 'GET':
        db = get_db()
        user = db.execute('SELECT username FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        return jsonify({'username': user['username'] if user else ''})

    data = request.get_json()
    new_username = data.get('username', '').strip()
    new_password = data.get('new_password', '')
    confirm = data.get('confirm_password', '')

    db = get_db()

    # 更新用户名
    if new_username:
        if len(new_username) < 2:
            return jsonify({'success': False, 'error': 'Username too short (min 2)'}), 400
        db.execute('UPDATE users SET username = ? WHERE id = ?', (new_username, session['user_id']))

    # 更新密码（可选）
    if new_password or confirm:
        if new_password != confirm:
            return jsonify({'success': False, 'error': 'Passwords do not match'}), 400
        if len(new_password) < 6:
            return jsonify({'success': False, 'error': 'Password too short (min 6)'}), 400
        pw_hash = generate_password_hash(new_password, method='scrypt')
        db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (pw_hash, session['user_id']))

    db.commit()
    return jsonify({'success': True, 'message': 'Account updated'})


# ============ API: 2FA 管理 ============
@app.route('/api/2fa/status')
@login_required
def api_2fa_status():
    db = get_db()
    user = db.execute('SELECT totp_secret FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    return jsonify({'enabled': bool(user and user['totp_secret'])})


@app.route('/api/2fa/setup', methods=['POST'])
@login_required
def api_2fa_setup():
    """生成 TOTP secret + QR 码"""
    secret = pyotp.random_base32()
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    # 生成 otpauth URI
    totp = pyotp.TOTP(secret)
    issuer = 'ywsj-nginx-manager'
    uri = totp.provisioning_uri(name='admin', issuer_name=issuer)

    # 生成 QR 码 (data URI)
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    qr_data_uri = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()

    # 临时存储 secret 到 session（不保存到 DB，等确认后再存）
    session['_2fa_pending_secret'] = secret

    return jsonify({
        'success': True,
        'secret': secret,
        'qr_code': qr_data_uri,
        'uri': uri,
    })


@app.route('/api/2fa/confirm', methods=['POST'])
@login_required
def api_2fa_confirm():
    """验证 TOTP 码并保存 secret"""
    data = request.get_json()
    code = data.get('code', '').strip()
    secret = session.get('_2fa_pending_secret')

    if not secret:
        return jsonify({'success': False, 'error': 'No pending 2FA setup. Call /api/2fa/setup first.'}), 400

    totp = pyotp.TOTP(secret)
    if totp.verify(code, valid_window=1):
        db = get_db()
        db.execute('UPDATE users SET totp_secret = ? WHERE id = ?', (secret, session['user_id']))
        db.commit()
        session.pop('_2fa_pending_secret', None)
        return jsonify({'success': True, 'message': '2FA enabled'})
    return jsonify({'success': False, 'error': 'Invalid code'}), 400


@app.route('/api/2fa/disable', methods=['POST'])
@login_required
def api_2fa_disable():
    """关闭 2FA（需要验证密码）"""
    data = request.get_json()
    password = data.get('password', '')

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'success': False, 'error': 'Invalid password'}), 403

    db.execute('UPDATE users SET totp_secret = NULL WHERE id = ?', (session['user_id'],))
    db.commit()
    return jsonify({'success': True, 'message': '2FA disabled'})


# ============ Health ============
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': APP_VERSION})


# ============ 启动 ============
if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true')
    app.run(host='0.0.0.0', port=5000, debug=debug)
