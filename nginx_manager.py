"""
Nginx 配置管理模块
职责：生成/编辑/校验/重载 nginx 配置文件
"""
import os
import re
import subprocess
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

NGINX_CONF_DIR = "/etc/nginx/conf.d"
NGINX_SSL_DIR = "/etc/nginx/ssl"
NGINX_LOG_DIR = "/var/log/nginx"
NGINX_BIN = "sudo /usr/sbin/nginx"


def ensure_dirs():
    """确保配置目录存在"""
    for d in [NGINX_CONF_DIR, NGINX_SSL_DIR, NGINX_LOG_DIR]:
        Path(d).mkdir(parents=True, exist_ok=True)


def nginx_test():
    """运行 nginx -t 校验配置语法
    
    Returns:
        tuple: (success: bool, output: str)
    """
    try:
        result = subprocess.run(
            NGINX_BIN.split() + ["-t"],
            capture_output=True, text=True, timeout=10
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "nginx -t timeout"
    except FileNotFoundError:
        return False, f"nginx binary not found at {NGINX_BIN}"
    except Exception as e:
        return False, str(e)


def nginx_reload():
    """重载 nginx 配置
    
    Returns:
        tuple: (success: bool, message: str)
    """
    success, output = nginx_test()
    if not success:
        return False, f"Config test failed: {output}"
    
    try:
        result = subprocess.run(
            NGINX_BIN.split() + ["-s", "reload"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True, "Nginx reloaded successfully"
        return False, result.stderr or "Reload failed"
    except Exception as e:
        return False, str(e)


def generate_proxy_conf(
    domain, forward_host, forward_port, forward_scheme="http",
    ssl_cert_path=None, ssl_key_path=None, websocket=False,
    custom_config="", block_common_exploits=True, force_ssl=False,
    backends=None,
):
    """生成 nginx 反向代理配置
    
    backends: 可选，后端列表 [{"host":"1.2.3.4","port":8080,"weight":1,"max_fails":3,"fail_timeout":30,"backup":False}, ...]
    如果传了 backends 则使用 upstream 负载均衡，否则用单个 forward_host:forward_port
    """
    import json as _json
    has_ssl = ssl_cert_path and ssl_key_path
    
    listen_lines = []
    if has_ssl:
        listen_lines.append("    listen 443 ssl;")
        listen_lines.append("    listen [::]:443 ssl;")
        if force_ssl:
            listen_lines.append("    http2 on;")
    else:
        listen_lines.append("    listen 80;")
        listen_lines.append("    listen [::]:80;")
    
    conf_parts = []
    
    # HTTP→HTTPS 跳转
    if has_ssl and force_ssl:
        conf_parts.append(
            "server {\n"
            "    listen 80;\n"
            "    listen [::]:80;\n"
            f"    server_name {domain};\n"
            "    return 301 https://$host$request_uri;\n"
            "}\n"
        )
    
    # upstream 负载均衡块
    upstream_name = None
    if backends and len(backends) > 0:
        # 清理域名作为 upstream 名
        upstream_name = re.sub(r'[^a-zA-Z0-9_]', '_', domain)
        upstream_block = f"upstream {upstream_name} {{\n"
        upstream_block += "    # 负载均衡策略: 轮询(默认) / least_conn / ip_hash\n"
        upstream_block += "    # least_conn;  # 最少连接数\n"
        upstream_block += "    # ip_hash;     # IP会话保持\n"
        for be in backends:
            host = be.get('host', '')
            port = be.get('port', 80)
            weight = be.get('weight', 1)
            max_fails = be.get('max_fails', 3)
            fail_timeout = be.get('fail_timeout', 30)
            backup = be.get('backup', False)
            if not host:
                continue
            line = f"    server {host}:{port}"
            params = []
            if weight and weight != 1:
                params.append(f"weight={weight}")
            if max_fails:
                params.append(f"max_fails={max_fails}")
            if fail_timeout:
                params.append(f"fail_timeout={fail_timeout}s")
            if backup:
                params.append("backup")
            if params:
                line += " " + " ".join(params)
            line += ";\n"
            upstream_block += line
        upstream_block += "}\n"
        conf_parts.append(upstream_block)
    
    # 主 server 块
    server_block = "server {\n"
    server_block += "\n".join(listen_lines) + "\n"
    server_block += f"    server_name {domain};\n"
    
    # SSL 配置
    if has_ssl:
        server_block += (
            f"\n    ssl_certificate {ssl_cert_path};\n"
            f"    ssl_certificate_key {ssl_key_path};\n"
            "    ssl_protocols TLSv1.2 TLSv1.3;\n"
            "    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;\n"
            "    ssl_prefer_server_ciphers off;\n"
            "    ssl_session_cache shared:SSL:10m;\n"
            "    ssl_session_timeout 1d;\n"
        )
    
    # 常见漏洞拦截
    if block_common_exploits:
        server_block += (
            "\n    # Block common exploits\n"
            '    location ~* /(\\.git|\\.env|\\.svn|\\.hg) {\n'
            "        deny all;\n"
            "        return 404;\n"
            "    }\n"
            '    location ~* /(wp-admin|wp-login|xmlrpc|phpmyadmin) {\n'
            "        deny all;\n"
            "        return 404;\n"
            "    }\n"
        )
    
    # 反向代理
    if upstream_name:
        forward_url = f"{forward_scheme}://{upstream_name}"
    else:
        forward_url = f"{forward_scheme}://{forward_host}:{forward_port}"
    
    server_block += (
        f"\n    location / {{\n"
        f"        proxy_pass {forward_url};\n"
        "        proxy_http_version 1.1;\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Real-IP $remote_addr;\n"
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "        proxy_set_header X-Forwarded-Proto $scheme;\n"
    )
    
    if websocket:
        server_block += (
            '        proxy_set_header Upgrade $http_upgrade;\n'
            '        proxy_set_header Connection "upgrade";\n'
        )
    
    server_block += (
        "        proxy_redirect off;\n"
        "        proxy_buffering off;\n"
        "        proxy_read_timeout 300s;\n"
        "        proxy_send_timeout 300s;\n"
        "        client_max_body_size 1024m;\n"
        "    }\n"
    )
    
    # 自定义配置
    if custom_config.strip():
        server_block += f"\n    # Custom configuration\n{custom_config}\n"
    
    server_block += "}\n"
    conf_parts.append(server_block)
    
    return "\n".join(conf_parts)


def generate_redirect_conf(
    domain, target_url, permanent=True, preserve_path=True,
    ssl_cert_path=None, ssl_key_path=None,
):
    """生成重定向配置"""
    has_ssl = ssl_cert_path and ssl_key_path
    code = "301" if permanent else "302"
    
    if has_ssl:
        listen = "    listen 443 ssl;\n    listen [::]:443 ssl;"
    else:
        listen = "    listen 80;\n    listen [::]:80;"
    
    redirect_url = target_url
    if preserve_path:
        redirect_url = target_url.rstrip("/") + "$request_uri"
    
    conf = f"server {{\n{listen}\n    server_name {domain};\n"
    if has_ssl:
        conf += (
            f"\n    ssl_certificate {ssl_cert_path};\n"
            f"    ssl_certificate_key {ssl_key_path};\n"
            "    ssl_protocols TLSv1.2 TLSv1.3;\n"
        )
    conf += f"    return {code} {redirect_url};\n}}\n"
    return conf


def generate_stream_conf(listen_port, target_host, target_port, protocol="tcp"):
    """生成 TCP/UDP 端口转发配置 (stream 模块)"""
    proto = " udp;" if protocol == "udp" else ";"
    return (
        "server {\n"
        f"    listen {listen_port}{proto}\n"
        f"    listen [::{listen_port}]{proto}\n"
        f"    proxy_pass {target_host}:{target_port};\n"
        "}\n"
    )


def save_conf(domain, conf_content, conf_type="proxy"):
    """保存配置文件到 conf.d 目录"""
    ensure_dirs()
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', domain)
    filename = f"{safe_name}.conf"
    if conf_type == "stream":
        filename = f"stream_{safe_name}.conf"
    
    filepath = os.path.join(NGINX_CONF_DIR, filename)
    with open(filepath, 'w') as f:
        f.write(conf_content)
    
    logger.info(f"Config saved: {filepath}")
    return filepath


def delete_conf(domain, conf_type="proxy"):
    """删除配置文件"""
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', domain)
    filename = f"{safe_name}.conf"
    if conf_type == "stream":
        filename = f"stream_{safe_name}.conf"
    
    filepath = os.path.join(NGINX_CONF_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info(f"Config deleted: {filepath}")
        return True
    return False


def list_confs():
    """列出所有配置文件"""
    conf_dir = Path(NGINX_CONF_DIR)
    if not conf_dir.exists():
        return []
    
    confs = []
    for f in sorted(conf_dir.glob("*.conf")):
        content = f.read_text(errors='ignore')
        server_names = re.findall(r'server_name\s+([^;]+);', content)
        proxy_passes = re.findall(r'proxy_pass\s+([^;]+);', content)
        has_ssl = 'ssl_certificate' in content
        is_stream = 'stream_' in f.name
        
        confs.append({
            'filename': f.name,
            'filepath': str(f),
            'server_names': [s.strip() for s in server_names],
            'proxy_passes': [p.strip() for p in proxy_passes],
            'has_ssl': has_ssl,
            'is_stream': is_stream,
            'content': content,
            'size': f.stat().st_size,
            'modified': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return confs


def get_conf(domain, conf_type="proxy"):
    """获取单个配置文件内容"""
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', domain)
    filename = f"{safe_name}.conf"
    if conf_type == "stream":
        filename = f"stream_{safe_name}.conf"
    
    filepath = os.path.join(NGINX_CONF_DIR, filename)
    if not os.path.exists(filepath):
        return None
    
    content = Path(filepath).read_text(errors='ignore')
    return {
        'filename': filename,
        'filepath': filepath,
        'content': content,
        'domain': domain,
    }


def get_nginx_status():
    """获取 nginx 运行状态"""
    status = {
        'running': False,
        'pid': None,
        'version': None,
        'conf_test': None,
    }
    
    try:
        # 读 pid 文件判断是否运行
        pid_file = "/run/nginx.pid"
        if os.path.exists(pid_file):
            with open(pid_file, 'r') as f:
                pid = f.read().strip()
            if pid:
                # 检查进程是否存在
                # os.kill(pid, 0) 返回值:
                #   - 无异常 = 进程存在且有权限
                #   - EPERM = 进程存在但无权限 (容器内非root)
                #   - ESRCH = 进程不存在
                try:
                    os.kill(int(pid), 0)
                    status['running'] = True
                    status['pid'] = pid
                except ProcessLookupError:
                    status['running'] = False
                except PermissionError:
                    # EPERM 说明进程存在,只是没权限发信号
                    status['running'] = True
                    status['pid'] = pid
                except ValueError:
                    status['running'] = False
    except Exception:
        pass
    
    try:
        result = subprocess.run(
            "sudo /usr/sbin/nginx -v", shell=True,
            capture_output=True, text=True, timeout=5
        )
        version_output = result.stderr + result.stdout
        match = re.search(r'nginx version: ([^\s]+)', version_output)
        if match:
            status['version'] = match.group(1)
    except Exception:
        pass
    
    ok, output = nginx_test()
    status['conf_test'] = {'ok': ok, 'output': output}
    
    return status


def parse_access_log(log_file="access.log", lines=100):
    """解析 access.log 最近N条"""
    log_path = os.path.join(NGINX_LOG_DIR, log_file)
    if not os.path.exists(log_path):
        return []
    
    result = []
    try:
        with open(log_path, 'rb') as f:
            f.seek(0, 2)
            fsize = f.tell()
            block = -1024 * lines
            if fsize > abs(block):
                f.seek(block, 2)
            else:
                f.seek(0)
            data = f.read().decode('utf-8', errors='ignore')
        
        for line in data.strip().split('\n')[-lines:]:
            match = re.match(
                r'(\S+) - (\S+) \[([^\]]+)\] "([^"]*)" (\d+) (\d+) "([^"]*)" "([^"]*)"',
                line
            )
            if match:
                result.append({
                    'ip': match.group(1),
                    'time': match.group(3),
                    'request': match.group(4),
                    'status': int(match.group(5)),
                    'bytes': int(match.group(6)),
                    'referer': match.group(7),
                    'user_agent': match.group(8),
                })
            else:
                result.append({'raw': line})
    except Exception as e:
        logger.error(f"Failed to parse log {log_file}: {e}")
    
    return result


def get_log_stats(log_file="access.log"):
    """统计 access.log 数据"""
    log_path = os.path.join(NGINX_LOG_DIR, log_file)
    if not os.path.exists(log_path):
        return {'total': 0, 'status_codes': {}, 'top_ips': {}, 'top_urls': {}}
    
    stats = {
        'total': 0,
        'status_codes': {},
        'top_ips': {},
        'top_urls': {},
    }
    
    try:
        with open(log_path, 'r', errors='ignore') as f:
            for line in f:
                match = re.match(
                    r'(\S+) - (\S+) \[([^\]]+)\] "([^"]*)" (\d+) (\d+)',
                    line
                )
                if match:
                    stats['total'] += 1
                    ip = match.group(1)
                    status = match.group(5)
                    request = match.group(4)
                    
                    stats['status_codes'][status] = stats['status_codes'].get(status, 0) + 1
                    stats['top_ips'][ip] = stats['top_ips'].get(ip, 0) + 1
                    stats['top_urls'][request] = stats['top_urls'].get(request, 0) + 1
    except Exception as e:
        logger.error(f"Failed to stat log: {e}")
    
    stats['top_ips'] = dict(sorted(stats['top_ips'].items(), key=lambda x: -x[1])[:10])
    stats['top_urls'] = dict(sorted(stats['top_urls'].items(), key=lambda x: -x[1])[:10])
    
    return stats
