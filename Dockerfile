FROM python:3.12-slim

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 安装 nginx + acme.sh 依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx nginx-extras \
        openssl \
        curl \
        socat \
        cron \
        gosu \
        && rm -rf /var/lib/apt/lists/*

# 创建应用用户 (nginx 用户由 apt nginx 包创建)
RUN groupadd -r appgroup && useradd -r -g appgroup -s /sbin/nologin -m -d /home/appuser appuser \
    && mkdir -p /app /data /etc/nginx/conf.d /etc/nginx/ssl /var/log/nginx /var/www/html

# 允许 appuser 执行 nginx 命令 (用于 -t 校验和 -s reload)
RUN apt-get update && apt-get install -y --no-install-recommends sudo \
    && echo "appuser ALL=(root) NOPASSWD: /usr/sbin/nginx" > /etc/sudoers.d/appuser-nginx \
    && chmod 0440 /etc/sudoers.d/appuser-nginx \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 应用代码
COPY --chown=appuser:appgroup . /app/

# Nginx 配置
COPY nginx/nginx.conf /etc/nginx/nginx.conf

# 修复权限
RUN chown -R appuser:appgroup /app /data /var/www/html

# entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 安装 acme.sh (装到 appuser home 目录)
RUN curl -fsSL https://github.com/acmesh-official/acme.sh/archive/master.tar.gz -o /tmp/acme.tar.gz \
    && tar -xzf /tmp/acme.tar.gz -C /tmp/ \
    && cd /tmp/acme.sh-* && sh acme.sh --install --home /home/appuser/.acme.sh --nocron \
    && rm -rf /tmp/acme.tar.gz /tmp/acme.sh-* \
    && chown -R appuser:appgroup /home/appuser/.acme.sh \
    && /home/appuser/.acme.sh/acme.sh --set-default-ca --server letsencrypt

WORKDIR /app

EXPOSE 80 443 5000

ENTRYPOINT ["/entrypoint.sh"]
