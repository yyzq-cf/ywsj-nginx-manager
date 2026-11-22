# 🌐 ywsj-nginx-manager

可视化管理 Nginx 反向代理、SSL 证书和多站点的 WebUI 面板，Docker 一键部署。

## ✨ 功能特性

- 🔄 **反向代理管理** — 可视化添加/编辑/删除代理站点，自动生成 Nginx 配置
- 🔒 **SSL 证书管理** — 集成 acme.sh，支持 DNS API 验证和 Standalone 模式申请 Let's Encrypt 证书
- ↪️ **重定向管理** — 301/302 重定向，支持路径保留
- 🔀 **端口转发** — TCP/UDP stream 代理
- 📊 **状态监控** — Nginx 运行状态、配置检查、一键重载
- 🖥️ **系统资源监控** — 仪表盘实时 CPU/内存/磁盘/SWAP 指针式仪表（纯原生 SVG，2s 自动刷新）
- 📋 **日志查看** — 访问日志实时查看 + 状态码/IP/URL 统计
- 🌐 **多语言** — 默认支持中文/英文，可扩展其他语言
- 🌓 **暗色/亮色主题** — 自动记忆用户偏好
- 🔐 **安全认证** — 首次初始化密码、防暴力破解、scrypt 哈希
- 🐳 **Docker 部署** — 多架构(amd64+arm64)，一键启动

## 🚀 快速部署

### Docker Compose (推荐)

> **🖥️ 系统监控说明**：CPU/内存/SWAP 直接读取宿主机真实数据（无需额外配置）。默认磁盘显示**数据卷**用量；如需监控**宿主机根分区**精确容量，在 volumes 加一行 `- /:/host:ro`（只读挂载，可选）：

```yaml
services:
  nginx-manager:
    image: ywsj/nginx-manager:latest
    container_name: nginx-manager
    ports:
      - "80:80"
      - "443:443"
      - "5400:5000"
    volumes:
      - ./data:/data
      - ./conf.d:/etc/nginx/conf.d
      - ./ssl:/etc/nginx/ssl
      - ./logs:/var/log/nginx
      - ./acme.sh:/home/appuser/.acme.sh
      # - /:/host:ro   # 可选：监控宿主机磁盘精确容量
    environment:
      - SECRET_KEY=change...random...string
    restart: always
```

```bash
docker compose up -d
```

### Docker Run

```bash
docker run -d \
  --name nginx-manager \
  -p 80:80 -p 443:443 -p 5400:5000 \
  -v ./data:/data \
  -v ./conf.d:/etc/nginx/conf.d \
  -v ./ssl:/etc/nginx/ssl \
  -v ./logs:/var/log/nginx \
  -v ./acme.sh:/home/appuser/.acme.sh \
  # -v /:/host:ro \                    # 可选：监控宿主机磁盘精确容量
  -e SECRET_KEY=change...random...string \
  --restart always \
  ywsj/nginx-manager:latest
```

## 📖 使用方法

1. **首次访问** — 打开 `http://<IP>:5400`，设置管理密码
2. **添加代理站点** — 进入「代理站点」→ 填写域名和后端地址
3. **申请 SSL 证书** — 进入「SSL 证书」→ 选择 DNS 提供商 → 填入 API Key → 申请
4. **管理面板** — 仪表盘查看 Nginx 状态和访问统计

## 🛠️ 技术栈

- **后端**: Python + Flask + Gunicorn
- **前端**: HTML/CSS/JS (暗色/亮色主题, 响应式, 原生 SVG 指针仪表)
- **Nginx**: 内置于容器
- **SSL**: acme.sh (Let's Encrypt / ZeroSSL)
- **数据库**: SQLite
- **监控**: /proc + statvfs（无第三方依赖）
- **部署**: Docker (amd64 + arm64)

## 📁 项目结构

```
ywsj-nginx-manager/
├── app.py                 # Flask 主应用
├── monitor.py             # 系统监控 (CPU/内存/磁盘/SWAP)
├── nginx_manager.py       # Nginx 配置管理
├── ssl_manager.py         # SSL 证书管理 (acme.sh)
├── i18n/                  # 多语言
│   ├── __init__.py
│   └── translations.py
├── templates/             # Jinja2 模板
│   ├── base.html
│   ├── index.html         # 仪表盘
│   ├── hosts.html         # 代理站点
│   ├── ssl.html           # SSL 证书
│   ├── redirects.html     # 重定向
│   ├── streams.html       # 端口转发
│   ├── logs.html          # 日志
│   ├── login.html
│   └── setup.html
├── static/
│   ├── css/style.css
│   └── js/
│       ├── app.js
│       └── i18n.js
├── nginx/nginx.conf       # Nginx 基础配置
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── requirements.txt
├── .dockerignore
└── .github/workflows/
    └── docker-publish.yml
```

## 🙏 致谢

- [NginxProxyManager](https://github.com/NginxProxyManager/nginx-proxy-manager) — 功能设计参考
- [acme.sh](https://github.com/acmesh-official/acme.sh) — SSL 证书自动签发
- [Flask](https://flask.palletsprojects.com/) — Web 框架

## License

MIT
