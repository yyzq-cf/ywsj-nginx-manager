"""IP 地区查询模块 — 使用 ip2region 离线库 (无外部API调用)"""
import os
import threading

# lazy singleton
_searcher = None
_lock = threading.Lock()
_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ip2region')


def _get_searcher():
    """懒加载 ip2region searcher (线程安全单例)"""
    global _searcher
    if _searcher is not None:
        return _searcher
    with _lock:
        if _searcher is not None:
            return _searcher
        try:
            from ip2region.searcher import new_with_file_only
            from ip2region.util import IPv4
            db_path = os.path.join(_DB_DIR, 'ip2region_v4.xdb')
            if not os.path.exists(db_path):
                return None
            _searcher = new_with_file_only(IPv4, db_path)
        except Exception as e:
            print(f"[ip_region] init failed: {e}")
            _searcher = None
    return _searcher


def ip_region(ip):
    """查询 IP 地区, 返回简短字符串 (如 '中国 北京' / '美国 加利福尼亚')"""
    if not ip:
        return ''
    # IPv6 直接跳过 (v4 库不支持)
    if ':' in ip:
        return ''
    try:
        searcher = _get_searcher()
        if searcher is None:
            return ''
        region = searcher.search(ip)
        if not region:
            return ''
        parts = [p.strip() for p in region.split('|')]
        # parts: [国家, 省, 市, ISP, 代码]
        country = parts[0] if len(parts) > 0 else ''
        province = parts[1] if len(parts) > 1 else ''
        city = parts[2] if len(parts) > 2 else ''
        isp = parts[3] if len(parts) > 3 else ''

        # 组装简短地区
        if country in ('0', 'Reserved', ''):
            return '内网/保留地址'
        text = country
        if province and province not in ('0', country):
            text += ' ' + province
        if city and city not in ('0', province, country):
            text += ' ' + city
        if isp and isp not in ('0',):
            text += ' ' + isp
        return text.strip()
    except Exception:
        return ''


# 简单缓存 (最多 512 条, 防止重复查询)
_cache = {}
_CACHE_MAX = 512


def ip_region_cached(ip):
    if not ip or ':' in ip:
        return ''
    if ip in _cache:
        return _cache[ip]
    r = ip_region(ip)
    if len(_cache) < _CACHE_MAX:
        _cache[ip] = r
    return r