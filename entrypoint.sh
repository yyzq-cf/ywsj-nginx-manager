#!/bin/sh
set -e

echo "[entrypoint] ywsj-nginx-manager starting..."

# 确保目录存在
mkdir -p /data /etc/nginx/conf.d /etc/nginx/ssl /var/log/nginx /var/www/html /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi /var/lib/nginx/uwsgi /var/lib/nginx/scgi /run

# 检查 nginx 是否支持 stream 模块 (在改权限前以 root 执行)
if nginx -t 2>&1 | grep -q 'unknown directive "stream"'; then
    echo "[entrypoint] nginx does not support stream module, removing stream block..."
    sed -i '/^stream {/,/^}$/d' /etc/nginx/nginx.conf
fi

# 先以 root 启动 nginx (前台 daemon off, 后台运行)
nginx -t 2>/dev/null && echo "[entrypoint] nginx config OK" || echo "[entrypoint] nginx config not ready yet"
nginx -g 'daemon off;' &

# 等 nginx 启动
sleep 1

# 修复权限 (在 nginx 启动后, 给 Flask appuser 读写权限)
chown -R appuser:appgroup /data /var/www/html
chown -R appuser:appgroup /etc/nginx
chown -R appuser:appgroup /var/log/nginx
chown -R appuser:appgroup /var/lib/nginx
chown -R appuser:appgroup /home/appuser/.acme.sh 2>/dev/null || true
chown appuser:appgroup /run/nginx.pid 2>/dev/null || true
chmod -R 755 /var/log/nginx /etc/nginx/ssl /etc/nginx/conf.d

# 确保 cron spool 目录存在
mkdir -p /var/spool/cron/crontabs
chmod 1730 /var/spool/cron/crontabs
chown root:crontab /var/spool/cron/crontabs 2>/dev/null || true

# 为 appuser 安装 acme.sh cron (每天 3:00 UTC 续期检查)
ACME_BIN="/home/appuser/.acme.sh/acme.sh"
if [ -x "$ACME_BIN" ]; then
    su -s /bin/bash appuser -c "$ACME_BIN --install-cronjob --home /home/appuser/.acme.sh" 2>/dev/null || true
    echo "[entrypoint] acme.sh cron installed"
fi

# 启动 cron 守护进程 (SSL 自动续期)
echo "[entrypoint] Starting cron daemon..."
cron

# 启动 Flask
if [ "$(id -u)" = '0' ]; then
    echo "[entrypoint] Starting Flask as appuser..."
    exec gosu appuser gunicorn -w 4 -b 0.0.0.0:5000 --timeout 120 app:app
else
    echo "[entrypoint] Starting Flask..."
    exec gunicorn -w 4 -b 0.0.0.0:5000 --timeout 120 app:app
fi
