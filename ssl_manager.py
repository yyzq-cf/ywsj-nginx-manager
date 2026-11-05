"""
SSL 证书管理模块
职责：通过 acme.sh 申请/续期/管理 Let's Encrypt 证书
"""
import os
import subprocess
import logging
from pathlib import Path
from datetime import datetime
import re

logger = logging.getLogger(__name__)

ACME_BIN = "/home/appuser/.acme.sh/acme.sh"
ACME_HOME = "/home/appuser/.acme.sh"
SSL_INSTALL_DIR = "/etc/nginx/ssl"

# acme.sh 默认 CA (Let's Encrypt)
DEFAULT_CA = "letsencrypt"


def ensure_acme():
    """确保 acme.sh 已安装"""
    if not os.path.exists(ACME_BIN):
        logger.info("Installing acme.sh...")
        try:
            # 下载并安装到 ACME_HOME
            result = subprocess.run(
                ["curl", "-fsSL", "https://github.com/acmesh-official/acme.sh/archive/master.tar.gz", "-o", "/tmp/acme.tar.gz"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                return False, f"Failed to download acme.sh: {result.stderr}"
            
            result = subprocess.run(
                ["tar", "-xzf", "/tmp/acme.tar.gz", "-C", "/tmp/"],
                capture_output=True, text=True, timeout=30
            )
            
            # 找到解压目录
            import glob
            acme_dirs = glob.glob("/tmp/acme.sh-*")
            if not acme_dirs:
                return False, "Failed to extract acme.sh"
            
            result = subprocess.run(
                ["sh", "acme.sh", "--install", "--home", ACME_HOME, "--nocron"],
                capture_output=True, text=True, timeout=60,
                cwd=acme_dirs[0],
                env={**os.environ}
            )
            
            # 清理
            subprocess.run(["rm", "-rf", "/tmp/acme.tar.gz"] + acme_dirs, capture_output=True)
            
            if result.returncode != 0:
                return False, f"Failed to install acme.sh: {result.stderr}"
            
            logger.info("acme.sh installed successfully")
            return True, "acme.sh installed"
        except Exception as e:
            return False, str(e)
    
    return True, "acme.sh already installed"


def set_default_ca(ca=DEFAULT_CA):
    """设置默认 CA"""
    try:
        result = subprocess.run(
            [ACME_BIN, "--set-default-ca", "--server", ca],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def issue_cert(
    domain,
    dns_provider=None,
    dns_api_key=None,
    dns_api_secret=None,
    standalone=False,
    key_length="ec-256",
    test=False,
    email=None,
):
    """申请 SSL 证书
    
    Args:
        domain: 域名 (支持泛域名 *.example.com)
        dns_provider: DNS API 提供商 (如 dns_cf, dns_dp)
        dns_api_key: DNS API Key
        dns_api_secret: DNS API Secret
        standalone: 是否用 standalone 模式 (需要 80 端口)
        key_length: 密钥长度 ec-256 / ec-384 / 2048
        test: 是否使用测试环境 (不消耗正式额度)
        email: 注册邮箱 (Let's Encrypt 过期通知用)
    
    Returns:
        tuple: (success: bool, message: str, cert_path: str, key_path: str)
    """
    ok, msg = ensure_acme()
    if not ok:
        return False, msg, None, None
    
    # 注册账户邮箱 (acme.sh 需要)
    if email:
        try:
            subprocess.run(
                [ACME_BIN, "--register-account", "-m", email],
                capture_output=True, text=True, timeout=30, env=os.environ
            )
        except Exception:
            pass

    # 清除 acme.sh 缓存的旧 DNS 凭证（防止上次填错的值残留）
    account_conf = os.path.join(ACME_HOME, "account.conf")
    if os.path.exists(account_conf):
        try:
            import re as _re
            with open(account_conf, 'r') as f:
                lines = f.readlines()
            with open(account_conf, 'w') as f:
                for line in lines:
                    if not _re.match(r'^SAVED_(CF_|DP_|Ali_)', line):
                        f.write(line)
        except Exception:
            pass
    
    # 确保安装目录存在
    Path(SSL_INSTALL_DIR).mkdir(parents=True, exist_ok=True)
    
    cmd = [ACME_BIN, "--issue"]
    
    # 密钥类型
    if key_length.startswith("ec-"):
        cmd.extend(["--keylength", key_length])
    else:
        cmd.extend(["--keylength", key_length])
    
    # 域名
    if domain.startswith("*."):
        # 泛域名
        main_domain = domain[2:]
        cmd.extend(["-d", main_domain, "-d", domain])
    else:
        cmd.extend(["-d", domain])
    
    # 验证方式
    if standalone:
        cmd.extend(["--standalone", "--listen-v4"])
    elif dns_provider:
        cmd.extend(["--dns", dns_provider])
        if test:
            cmd.extend(["--dnssleep", "60"])
    else:
        # 默认 webroot (需要 nginx 运行)
        cmd.extend(["--webroot", "/var/www/html"])
    
    # 测试环境
    if test:
        cmd.append("--test")
    
    # 环境变量传递 DNS API 凭证
    env = {**os.environ}
    if dns_provider and dns_api_key:
        # 根据 provider 设置对应环境变量
        if dns_provider == "dns_cf":
            # Cloudflare: CF_Token (API Token) 必填, CF_Account_ID 可选
            env["CF_Token"] = dns_api_key
            if dns_api_secret:
                env["CF_Account_ID"] = dns_api_secret
        elif dns_provider == "dns_dp":
            env["DP_Id"] = dns_api_key
            env["DP_Key"] = dns_api_secret
        elif dns_provider == "dns_ali":
            env["Ali_Key"] = dns_api_key
            env["Ali_Secret"] = dns_api_secret
    
    try:
        mode = 'standalone' if standalone else 'dns' if dns_provider else 'webroot'
        logger.info(f"Issuing cert for {domain}, mode: {mode}")
        if dns_provider:
            logger.info(f"DNS provider: {dns_provider}, CF_Token set: {'CF_Token' in env}, CF_Account_ID set: {'CF_Account_ID' in env}")
        logger.info(f"acme.sh cmd: {ACME_BIN} {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, env=env
        )
        output = result.stdout + result.stderr
        logger.info(f"acme.sh exit code: {result.returncode}")
        logger.info(f"acme.sh output (last 500 chars): {output[-500:]}")
        
        if result.returncode != 0:
            return False, output, None, None
        
        # 安装证书到 nginx ssl 目录
        cert_path, key_path = install_cert(domain, key_length)
        if cert_path:
            return True, f"Certificate issued successfully\n{output}", cert_path, key_path
        return False, f"Cert issued but install failed\n{output}", None, None
    
    except subprocess.TimeoutExpired:
        return False, "Cert issue timeout (300s)", None, None
    except Exception as e:
        return False, str(e), None, None


def install_cert(domain, key_length="ec-256"):
    """安装证书到 nginx 目录"""
    is_ec = key_length.startswith("ec-")
    
    # acme.sh 证书路径
    if domain.startswith("*."):
        acme_domain = domain[2:] + "_ecc" if is_ec else domain[2:]
    else:
        acme_domain = domain + "_ecc" if is_ec else domain
    
    acme_cert_dir = os.path.join(ACME_HOME, acme_domain)
    fullchain = os.path.join(acme_cert_dir, "fullchain.cer")
    keyfile = os.path.join(acme_cert_dir, f"{domain[2:] if domain.startswith('*.') else domain}.key") if is_ec else None
    
    if not os.path.exists(fullchain):
        return None, None
    
    # 安全文件名
    safe_domain = re.sub(r'[^a-zA-Z0-9._-]', '_', domain)
    if is_ec:
        safe_domain = safe_domain.replace("*.", "wildcard_")
    
    install_cert_path = os.path.join(SSL_INSTALL_DIR, f"{safe_domain}.cer")
    install_key_path = os.path.join(SSL_INSTALL_DIR, f"{safe_domain}.key")
    
    # 用 acme.sh --installcert 安装
    cmd = [
        ACME_BIN, "--installcert",
        "-d", domain[2:] if domain.startswith("*.") else domain,
        "--fullchainpath", install_cert_path,
        "--keypath", install_key_path,
        "--ecc" if is_ec else "",
    ]
    cmd = [c for c in cmd if c]  # 去空
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=os.environ
        )
        if result.returncode == 0:
            logger.info(f"Cert installed: {install_cert_path}")
            return install_cert_path, install_key_path
        return None, None
    except Exception:
        return None, None


def renew_cert(domain, key_length="ec-256"):
    """续期证书"""
    ok, msg = ensure_acme()
    if not ok:
        return False, msg
    
    is_ec = key_length.startswith("ec-")
    actual_domain = domain[2:] if domain.startswith("*.") else domain
    
    cmd = [ACME_BIN, "--renew", "-d", actual_domain]
    if is_ec:
        cmd.append("--ecc")
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        output = result.stdout + result.stderr
        
        if result.returncode == 0:
            # 重新安装证书
            install_cert(domain, key_length)
            return True, output
        return False, output
    except subprocess.TimeoutExpired:
        return False, "Renew timeout"
    except Exception as e:
        return False, str(e)


def revoke_cert(domain, key_length="ec-256"):
    """吊销证书"""
    ok, msg = ensure_acme()
    if not ok:
        return False, msg
    
    is_ec = key_length.startswith("ec-")
    actual_domain = domain[2:] if domain.startswith("*.") else domain
    
    cmd = [ACME_BIN, "--revoke", "-d", actual_domain]
    if is_ec:
        cmd.append("--ecc")
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def list_certs():
    """列出所有已签发的证书"""
    ok, msg = ensure_acme()
    if not ok:
        return []
    
    certs = []
    acme_dir = Path(ACME_HOME)
    
    try:
        acme_entries = sorted(acme_dir.iterdir())
    except (PermissionError, OSError) as e:
        logger.warning(f"Cannot read acme dir {ACME_HOME}: {e}")
        # 尝试用 sudo 列出
        try:
            result = subprocess.run(
                ["sudo", "-n", "ls", "-1", ACME_HOME],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                acme_entries = [acme_dir / line.strip() for line in result.stdout.split('\n') if line.strip()]
            else:
                return []
        except Exception:
            return []
    
    for d in acme_entries:
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name in ["account.conf", "acme.sh", "acme.sh.log", "http.header"]:
            continue
        
        fullchain = d / "fullchain.cer"
        if not fullchain.exists():
            continue
        
        is_ecc = d.name.endswith("_ecc")
        domain = d.name.replace("_ecc", "")
        
        stat = fullchain.stat()
        
        # 检查是否是泛域名证书（通过 openssl 看 SAN）
        is_wildcard = False
        try:
            result = subprocess.run(
                ["openssl", "x509", "-text", "-noout", "-in", str(fullchain)],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and re.search(r'DNS:\*\.', result.stdout):
                is_wildcard = True
                domain = "*." + domain
        except Exception:
            pass
        
        # 读取证书过期时间
        expire_date = None
        try:
            result = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout", "-in", str(fullchain)],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # notAfter=Nov  5 12:34:56 2026 GMT
                match = re.search(r'notAfter=(.+)', result.stdout)
                if match:
                    expire_date = match.group(1).strip()
        except Exception:
            pass
        
        # 安装路径
        # 安装路径 (泛域名的 * 在文件名中用 _ 替代)
        safe_domain = re.sub(r'[^a-zA-Z0-9._-]', '_', domain)
        installed_cer = os.path.join(SSL_INSTALL_DIR, f"{safe_domain}.cer")
        installed_key = os.path.join(SSL_INSTALL_DIR, f"{safe_domain}.key")
        
        certs.append({
            'domain': domain,
            'is_ecc': is_ecc,
            'is_wildcard': is_wildcard,
            'acme_dir': str(d),
            'fullchain': str(fullchain),
            'installed_cer': installed_cer if os.path.exists(installed_cer) else None,
            'installed_key': installed_key if os.path.exists(installed_key) else None,
            'issued_date': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
            'expire_date': expire_date,
            'expire_timestamp': _parse_openssl_date(expire_date) if expire_date else None,
        })
    
    return certs


def _parse_openssl_date(date_str):
    """解析 openssl 日期格式为 timestamp"""
    try:
        # Nov  5 12:34:56 2026 GMT
        dt = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
        return dt.timestamp()
    except Exception:
        try:
            dt = datetime.strptime(date_str.replace("  ", " "), "%b %d %H:%M:%S %Y %Z")
            return dt.timestamp()
        except Exception:
            return None


def get_cert_info(cert_path):
    """获取证书详细信息"""
    if not os.path.exists(cert_path):
        return None
    
    info = {}
    try:
        result = subprocess.run(
            ["openssl", "x509", "-text", "-noout", "-in", cert_path],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            text = result.stdout
            # Subject
            match = re.search(r'Subject:\s*(.+)', text)
            if match:
                info['subject'] = match.group(1).strip()
            # Issuer
            match = re.search(r'Issuer:\s*(.+)', text)
            if match:
                info['issuer'] = match.group(1).strip()
            # 有效期
            match = re.search(r'Not Before:\s*(.+)', text)
            if match:
                info['not_before'] = match.group(1).strip()
            match = re.search(r'Not After\s*:\s*(.+)', text)
            if match:
                info['not_after'] = match.group(1).strip()
            # SAN
            sans = re.findall(r'DNS:([^\s,]+)', text)
            if sans:
                info['san'] = sans
    except Exception as e:
        logger.error(f"Failed to get cert info: {e}")
    
    return info


def setup_cron_renew():
    """设置自动续期 cron (acme.sh 自带)"""
    ok, msg = ensure_acme()
    if not ok:
        return False, msg
    
    try:
        result = subprocess.run(
            [ACME_BIN, "--install-cronjob"],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)
