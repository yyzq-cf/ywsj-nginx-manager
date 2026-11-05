/**
 * ywsj-nginx-manager 前端主逻辑
 */

// ============ 主题 ============
function toggleTheme() {
    document.body.classList.toggle('light');
    const isLight = document.body.classList.contains('light');
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
    document.getElementById('theme-icon').className = isLight ? 'fas fa-sun' : 'fas fa-moon';
}

(function() {
    const theme = localStorage.getItem('theme') || 'dark';
    if (theme === 'light') {
        document.body.classList.add('light');
        setTimeout(() => {
            const icon = document.getElementById('theme-icon');
            if (icon) icon.className = 'fas fa-sun';
        }, 100);
    }
})();

// ============ 侧边栏 ============
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

// ============ 模态框 ============
function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
}

// 点击遮罩关闭
document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.classList.remove('active');
    });
});

// ============ Tab ============
function switchTab(tabId, btn) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(tabId).classList.add('active');
}

// ============ 设置面板 ============
function showSettings() {
    document.getElementById('settingsModal').classList.add('active');
}

// ============ Toast 通知 ============
function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icon = type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : 'info-circle';
    toast.innerHTML = `<i class="fas fa-${icon}"></i> ${message}`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100px)';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

// ============ Nginx 操作 ============
function reloadNginx() {
    fetch('/api/nginx/reload', { method: 'POST' })
        .then(r => r.json())
        .then(r => {
            if (r.success) {
                showToast(i18n.t('common.reload_success'), 'success');
                setTimeout(() => location.reload(), 1000);
            } else {
                showToast(i18n.t('common.reload_failed') + ': ' + r.message, 'error', 5000);
            }
        });
}

// ============ 修改密码 ============
function changePassword() {
    const newPw = document.getElementById('newPassword').value;
    const confirmPw = document.getElementById('confirmPassword').value;
    const msgEl = document.getElementById('passwordMsg');

    if (newPw !== confirmPw) {
        msgEl.innerHTML = `<span style="color: var(--error)">${i18n.t('settings.password_mismatch')}</span>`;
        return;
    }
    if (newPw.length < 6) {
        msgEl.innerHTML = `<span style="color: var(--error)">${i18n.t('settings.password_mismatch')}</span>`;
        return;
    }

    fetch('/api/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_password: newPw, confirm_password: confirmPw }),
    })
        .then(r => r.json())
        .then(r => {
            if (r.success) {
                msgEl.innerHTML = `<span style="color: var(--success)">${i18n.t('settings.password_changed')}</span>`;
                document.getElementById('newPassword').value = '';
                document.getElementById('confirmPassword').value = '';
                setTimeout(() => {
                    closeModal('settingsModal');
                    msgEl.innerHTML = '';
                }, 1500);
            } else {
                msgEl.innerHTML = `<span style="color: var(--error)">${r.error}</span>`;
            }
        });
}

// ============ ESC 关闭模态框 ============
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
    }
});

// ============ 2FA 管理 ============
function check2FAStatus() {
    fetch('/api/2fa/status')
        .then(r => r.json())
        .then(data => {
            const statusBox = document.getElementById('2fa-status-box');
            const setupBox = document.getElementById('2fa-setup-box');
            const disableBox = document.getElementById('2fa-disable-box');
            if (data.enabled) {
                statusBox.style.display = 'none';
                setupBox.style.display = 'none';
                disableBox.style.display = 'block';
            } else {
                statusBox.style.display = 'none';
                setupBox.style.display = 'block';
                disableBox.style.display = 'none';
                setup2FA();
            }
        });
}

function setup2FA() {
    fetch('/api/2fa/setup', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                document.getElementById('2fa-qr-container').innerHTML =
                    `<img src="${data.qr_code}" alt="QR" style="width:200px;height:200px;border-radius:8px">`;
                document.getElementById('2fa-secret').textContent = data.secret;
            }
        });
}

function confirm2FA() {
    const code = document.getElementById('2faCode').value.trim();
    const msgEl = document.getElementById('2faMsg');
    if (!code || code.length !== 6) {
        msgEl.innerHTML = '<span style="color:var(--error)">6 digits required</span>';
        return;
    }
    fetch('/api/2fa/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                msgEl.innerHTML = '<span style="color:var(--success)">✅ 2FA enabled</span>';
                setTimeout(() => { location.reload(); }, 1500);
            } else {
                msgEl.innerHTML = `<span style="color:var(--error)">${data.error}</span>`;
            }
        });
}

function disable2FA() {
    const password = document.getElementById('2faDisablePassword').value;
    const msgEl = document.getElementById('2faDisableMsg');
    if (!password) {
        msgEl.innerHTML = '<span style="color:var(--error)">Password required</span>';
        return;
    }
    fetch('/api/2fa/disable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: password }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                msgEl.innerHTML = '<span style="color:var(--success)">2FA disabled</span>';
                setTimeout(() => { location.reload(); }, 1500);
            } else {
                msgEl.innerHTML = `<span style="color:var(--error)">${data.error}</span>`;
            }
        });
}

// 打开设置面板时加载 2FA 状态
const origShowSettings = showSettings;
showSettings = function() {
    origShowSettings();
    check2FAStatus();
};
