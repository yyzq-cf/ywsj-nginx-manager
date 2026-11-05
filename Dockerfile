FROM python:3.12-slim

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 安装 nginx + acme.sh 依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        openssl \
        curl \
        socat \
        cron \
        gosu \
        && rm -rf /var/lib/apt/lists/*

# 创建用户
RUN groupadd -r appgroup && useradd -r -g appgroup -s /sbin/nologin -M appuser \
    && mkdir -p /app /data /etc/nginx/conf.d /etc/nginx/ssl /var/log/nginx /var/www/html \
    && chown -R appuser:appgroup /app /data /var/www/html \
    && chown -R nginx:nginx /var/log/nginx /etc/nginx/ssl

# Python 依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 应用代码
COPY --chown=appuser:appgroup . /app/

# Nginx 配置
COPY nginx/nginx.conf /etc/nginx/nginx.conf

# entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 安装 acme.sh
RUN curl -fsSL https://get.acme.sh -o /tmp/acme_install.sh \
    && sh /tmp/acme_install.sh --home /root/.acme.sh \
    && rm /tmp/acme_install.sh \
    && /root/.acme.sh/acme.sh --set-default-ca --server letsencrypt

WORKDIR /app

EXPOSE 80 443 5000

ENTRYPOINT ["/entrypoint.sh"]
