"""系统监控模块 — 读取宿主/容器 /proc 与 statvfs 获取 CPU/内存/交换/磁盘指标

设计要点：
- 数据源全部来自 /proc (CPU/MEM/SWAP) 与 statvfs (磁盘)，无需 root、无需额外依赖
- CPU 通过两次 /proc/stat 采样差值计算瞬时使用率（0.4s 间隔）
- 磁盘自动探测：容器内挂载 /host (只读宿主机根目录) 则读取宿主根分区；否则回退 /data 或 /
- 模块级缓存 + 锁：多 worker (gunicorn) 下避免重复采样风暴
"""
import os
import threading
import time
from pathlib import Path

_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 2.0  # 秒

# /proc/stat 字段索引: user nice system idle iowait irq softirq steal
_STAT_FIELDS = ('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal')


def _read_cpu_times():
    """读取一次 CPU 总时间 (jiffies)。失败返回 None"""
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        if not line.startswith('cpu '):
            return None
        parts = line.split()[1:]
        vals = [int(x) for x in parts[:8]]
        if len(vals) < 4:
            return None
        total = sum(vals)
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
        return total, idle
    except Exception:
        return None


def cpu_usage():
    """瞬时 CPU 使用率 (0-100)。连续两次采样差值计算"""
    t1 = _read_cpu_times()
    if not t1:
        return 0.0
    time.sleep(0.4)
    t2 = _read_cpu_times()
    if not t2:
        return 0.0
    total_delta = t2[0] - t1[0]
    idle_delta = t2[1] - t1[1]
    if total_delta <= 0:
        return 0.0
    return round((1 - idle_delta / total_delta) * 100, 1)


def _parse_meminfo():
    """解析 /proc/meminfo，返回 dict。失败返回 None"""
    try:
        mem = {}
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    val_parts = parts[1].strip().split()
                    try:
                        mem[key] = int(val_parts[0])  # kB
                    except (ValueError, IndexError):
                        pass
        return mem
    except Exception:
        return None


def memory_usage():
    """内存使用 {total_mb, used_mb, percent}。used = total - available"""
    mem = _parse_meminfo()
    if not mem or 'MemTotal' not in mem:
        # 回退: 容器只看到自身 cgroup ? 直接返回 None 由上层处理
        return None
    total_kb = mem['MemTotal']
    available_kb = mem.get('MemAvailable', mem.get('MemFree', 0))
    used_kb = max(total_kb - available_kb, 0)
    total_mb = round(total_kb / 1024, 1)
    used_mb = round(used_kb / 1024, 1)
    percent = round(used_kb / total_kb * 100, 1) if total_kb else 0.0
    return {'total_mb': total_mb, 'used_mb': used_mb, 'percent': percent}


def swap_usage():
    """Swap 使用 {total_mb, used_mb, percent}，无 swap 时 percent=0"""
    mem = _parse_meminfo()
    if not mem:
        return None
    total_kb = mem.get('SwapTotal', 0)
    free_kb = mem.get('SwapFree', 0)
    if not total_kb:
        return {'total_mb': 0.0, 'used_mb': 0.0, 'percent': 0.0, 'enabled': False}
    used_kb = max(total_kb - free_kb, 0)
    total_mb = round(total_kb / 1024, 1)
    used_mb = round(used_kb / 1024, 1)
    percent = round(used_kb / total_kb * 100, 1) if total_kb else 0.0
    return {'total_mb': total_mb, 'used_mb': used_mb, 'percent': percent, 'enabled': True}


def _disk_usage(path):
    """statvfs 获取 path 的磁盘使用。失败返回 None"""
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        percent = round(used / total * 100, 1) if total else 0.0
        return {
            'path': path,
            'total_mb': round(total / 1024 / 1024, 1),
            'used_mb': round(used / 1024 / 1024, 1),
            'percent': percent,
        }
    except Exception:
        return None


def _host_root_fs():
    """探测宿主机根分区挂载点：
    - 容器内挂了 /host (只读宿主机根目录) → /host
    - 否则用 /data（项目数据卷，用户最关心）→ 没有则 /
    """
    for candidate in ('/host', '/data', '/'):
        if os.path.isdir(candidate):
            return candidate
    return '/'


def disk_usage():
    """磁盘使用，自动探测。返回 {path, total_mb, used_mb, percent}"""
    path = _host_root_fs()
    return _disk_usage(path)


def get_monitor_data():
    """采集全部指标（带缓存）。返回 dict"""
    now = time.time()
    with _cache_lock:
        cached = _cache.get('data')
        if cached and now - cached['ts'] < CACHE_TTL:
            return cached['data']

    data = {
        'cpu': cpu_usage(),
        'memory': memory_usage(),
        'swap': swap_usage(),
        'disk': disk_usage(),
    }

    with _cache_lock:
        _cache['data'] = {'ts': now, 'data': data}
    return data