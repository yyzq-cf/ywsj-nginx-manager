#!/usr/bin/env python3
"""完整2FA登录流程测试"""
import sys, re, time
sys.path.insert(0, '/app')
import os; os.chdir('/app')
from app import app, _2fa_pending, get_db
import pyotp

with app.test_client() as c:
    # 清除锁定
    with app.app_context():
        db = get_db()
        db.execute('DELETE FROM login_attempts')
        db.commit()

    # Phase 1: 密码登录
    print('=== Phase 1: 密码登录 ===')
    resp = c.post('/login', data={'password': 'asdqwe123!@#'}, follow_redirects=False)
    html = resp.data.decode()
    print(f'Status: {resp.status_code}')
    
    if 'alert-error' in html:
        errs = re.findall(r'alert-error[^>]*>(.*?)</div>', html, re.DOTALL)
        print(f'错误: {[e.strip() for e in errs]}')
    
    token_match = re.search(r'pending_token.*?value="([^"]+)"', html)
    if not token_match:
        print('FAIL: 没有拿到 pending_token')
        sys.exit(1)
    
    token = token_match.group(1)
    print(f'pending_token: {token[:20]}...')
    print('Phase 1: PASS')
    
    # 获取TOTP secret
    with app.app_context():
        db = get_db()
        user = db.execute('SELECT totp_secret FROM users WHERE id = 1').fetchone()
        secret = user['totp_secret']
    
    totp = pyotp.TOTP(secret)
    code = totp.now()
    print(f'\n=== Phase 2: TOTP验证 ===')
    print(f'生成验证码: {code}')
    print(f'当前时间: {time.time()}')
    print(f'_2fa_pending keys: {[k[:20] for k in _2fa_pending.keys()]}')
    
    if token in _2fa_pending:
        p = _2fa_pending[token]
        print(f'token存储: expire={p["expire"]}, attempts={p["attempts"]}')
        print(f'是否过期: {time.time() > p["expire"]}')
    else:
        print('WARNING: token不在_2fa_pending中!')
    
    # Phase 2: TOTP验证
    resp2 = c.post('/login', data={
        'pending_token': token,
        'code': code
    }, follow_redirects=False)
    
    html2 = resp2.data.decode()
    print(f'\nPhase 2 Status: {resp2.status_code}')
    print(f'Location: {resp2.headers.get("Location")}')
    
    if resp2.status_code == 302 and resp2.headers.get('Location') == '/':
        print('Phase 2: PASS - 登录成功!')
    elif 'alert-error' in html2:
        errs = re.findall(r'alert-error[^>]*>(.*?)</div>', html2, re.DOTALL)
        print(f'Phase 2 FAIL: {errs}')
    elif 'totp-digit' in html2:
        print('Phase 2: 仍然在2FA界面')
    else:
        print(f'Phase 2 未知状态')
        print(f'HTML片段: {html2[:500]}')
