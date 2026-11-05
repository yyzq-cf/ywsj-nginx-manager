#!/bin/sh
set -e

echo "[entrypoint] ywsj-nginx-manager starting..."

# 确保目录存在
mkdir -p /data /etc/nginx/conf.d /etc/nginx/ssl /var/log/nginx /var/www/html

# 修复权限
chown -R appuser:appgroup /data /var/www/html
chown -R nginx:nginx /var/log/nginx /etc/nginx/ssl /etc/nginx/conf.d

# 启动 nginx (前台 daemon off)
nginx -t 2>/dev/null && echo "[entrypoint] nginx config OK" || echo "[entrypoint] nginx config not ready yet"
nginx -g 'daemon off;' &

# 等待 nginx 启动
sleep 1

# 启动 Flask
if [ "$(id -u)" = '0' ]; then
    echo "[entrypoint] Starting Flask as appuser..."
    exec gosu appuser gunicorn -w 4 -b 0.0.0.0:5000 --timeout 120 app:app
else
    echo "[entrypoint] Starting Flask..."
    exec gunicorn -w 4 -b 0.0.0.0:5000 --timeout 120 app:app
fi
