"""
i18n 多语言支持模块
默认支持中文(zh)和英文(en)，其他语言可按需扩展
"""
import os
from pathlib import Path
from flask import session, request, g, current_app

SUPPORTED_LANGUAGES = {
    'zh': '中文',
    'en': 'English',
}

DEFAULT_LANGUAGE = 'zh'
FALLBACK_LANGUAGE = 'en'


def _load_translations(translations_dir=None):
    """从 locales/ 目录加载所有语言的翻译 JSON"""
    translations = {}
    if translations_dir and Path(translations_dir).exists():
        import json
        for lang_file in Path(translations_dir).glob('*.json'):
            lang_code = lang_file.stem
            with open(lang_file, 'r', encoding='utf-8') as f:
                translations[lang_code] = json.load(f)
    return translations


def get_locale():
    """获取当前语言，优先级：session > cookie > Accept-Language > 默认"""
    lang = session.get('language')
    if lang and lang in SUPPORTED_LANGUAGES:
        return lang

    lang = request.cookies.get('language')
    if lang and lang in SUPPORTED_LANGUAGES:
        return lang

    best = request.accept_languages.best_match(
        list(SUPPORTED_LANGUAGES.keys()),
        default=DEFAULT_LANGUAGE
    )
    return best or DEFAULT_LANGUAGE


def t(key, **kwargs):
    """翻译函数 — 后端 / Jinja2 模板通用"""
    translations = getattr(g, '_translations', None)
    if translations is None:
        translations = getattr(current_app, '_i18n_translations', {})
        g._translations = translations

    lang = get_locale()

    text = _get_value(translations, lang, key)
    if text is None and lang != FALLBACK_LANGUAGE:
        text = _get_value(translations, FALLBACK_LANGUAGE, key)
    if text is None:
        return key

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass

    return text


def _get_value(translations, lang, key):
    """从嵌套字典中按 a.b.c 路径取值"""
    data = translations.get(lang, {})
    for part in key.split('.'):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
        if data is None:
            return None
    return data


def init_i18n(app):
    """初始化 i18n，在 create_app() 中调用"""
    try:
        from i18n.translations import TRANSLATIONS
        app._i18n_translations = TRANSLATIONS
    except ImportError:
        app._i18n_translations = {}

    locales_dir = os.path.join(app.root_path, 'i18n', 'locales')
    if Path(locales_dir).exists():
        file_translations = _load_translations(locales_dir)
        for lang, data in file_translations.items():
            if lang in app._i18n_translations:
                app._i18n_translations[lang].update(data)
            else:
                app._i18n_translations[lang] = data

    app.jinja_env.globals['t'] = t
    app.jinja_env.globals['supported_languages'] = lambda: SUPPORTED_LANGUAGES
    app.jinja_env.globals['current_language'] = get_locale

    app.logger.info(f'i18n initialized: {list(app._i18n_translations.keys())}')


def set_language(lang_code):
    """设置当前语言"""
    if lang_code not in SUPPORTED_LANGUAGES:
        return False
    session['language'] = lang_code
    return True
