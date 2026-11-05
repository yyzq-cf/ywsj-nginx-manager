/**
 * 前端 i18n 翻译模块
 */

const I18N_TRANSLATIONS = {
    zh: {
        'common.success': '操作成功',
        'common.error': '操作失败',
        'common.loading': '加载中...',
        'common.confirm_delete': '确认删除？此操作不可恢复',
        'common.reload': '重载 Nginx',
        'common.reload_success': '✅ Nginx 重载成功',
        'common.reload_failed': '❌ Nginx 重载失败',
        'settings.password_changed': '密码修改成功',
        'settings.password_mismatch': '两次密码不一致',
    },
    en: {
        'common.success': 'Success',
        'common.error': 'Error',
        'common.loading': 'Loading...',
        'common.confirm_delete': 'Are you sure? This cannot be undone',
        'common.reload': 'Reload Nginx',
        'common.reload_success': '✅ Nginx reloaded successfully',
        'common.reload_failed': '❌ Nginx reload failed',
        'settings.password_changed': 'Password changed successfully',
        'settings.password_mismatch': 'Passwords do not match',
    },
};

const i18n = {
    lang: window.I18N_LANG || 'zh',
    fallback: 'en',

    t(key, params = {}) {
        const dict = I18N_TRANSLATIONS[this.lang] || {};
        let text = dict[key];
        if (text === undefined && this.lang !== this.fallback) {
            text = (I18N_TRANSLATIONS[this.fallback] || {})[key];
        }
        if (text === undefined) return key;
        for (const [k, v] of Object.entries(params)) {
            text = text.replace(`{${k}}`, v);
        }
        return text;
    },

    async switch(lang) {
        try {
            const resp = await fetch('/api/language', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ language: lang }),
            });
            if (resp.ok) {
                this.lang = lang;
                window.location.reload();
            }
        } catch (e) {
            console.error('Language switch failed:', e);
        }
    },
};

function switchLanguage(lang) {
    i18n.switch(lang);
}
