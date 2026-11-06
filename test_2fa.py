import sys, re
sys.path.insert(0, '/app')
import os; os.chdir('/app')
from app import app, _2fa_pending
with app.test_client() as c:
    resp = c.post('/login', data={'password': 'asdqwe123!@#'}, follow_redirects=False)
    html = resp.data.decode()
    token_match = re.search(r'pending_token.*?value="([^"]+)"', html)
    if token_match:
        token = token_match.group(1)
        print('HTML token:', token[:30])
        print('Stored keys:', [k[:30] for k in _2fa_pending.keys()])
        print('Match:', token in _2fa_pending)
    else:
        print('No pending_token in HTML')
        if 'alert-error' in html:
            errs = re.findall(r'alert-error[^>]*>(.*?)</div>', html, re.DOTALL)
            print('Errors:', errs)
        print('Status:', resp.status_code)
