# 部署上线指南（手机可访问）

## 前置提醒（重要）
本仓库是公开的，但 `knowledge/` 语料目录如果被 push 上去，任何知道网址的人都能通过接口读到老师语料。
上线前二选一：
- 语料也一并上传（内容可公开时）
- 或在平台环境变量里加 `ACCESS_PASSWORD`，并给接口加密码校验（需要加代码）

## 方案 A：Railway（免费、最省事，推荐先试）
1. 打开 https://railway.app → 用 GitHub 登录
2. New Project → Deploy from GitHub repo → 选本仓库
3. Railway 会自动读 Dockerfile 构建并给你一个公网地址（https://xxx.up.railway.app）
4. 手机浏览器直接打开这个地址即可
5. 需要的私密内容（如语料、API KEY）通过 Railway 的 Variables 配置，不进 git

## 方案 B：Render（免费档，国内访问偶尔慢）
1. https://render.com → GitHub 登录 → New Web Service
2. 选仓库，Environment 选 Docker
3. 部署后得到 https://xxx.onrender.com，手机直接访问

## 方案 C：国内云服务器（正式运营推荐，速度最快）
1. 买阿里云/腾讯云轻量应用服务器（2核2G 起步够用）
2. 装 Docker：`curl -fsSL https://get.docker.com | bash`
3. 服务器上 `git clone` 本仓库（或用私有仓库）→ `docker build -t fenzi .` → `docker run -d -p 80:8080 fenzi`
4. 直接用 IP 访问；要用域名则需 ICP 备案
5. 用 systemd 或 `--restart unless-stopped` 保证进程常驻，解决"不启动就断"

## 手机访问
部署完成拿到公网地址后，手机浏览器打开同一地址即可（页面已适配移动端，Tailwind 响应式）。
也可以让用户把网页"添加到主屏幕"，效果接近 App。

## 安全检查清单（上线前）
- [ ] API KEY 只放平台 Variables，不进 git
- [ ] `knowledge/` 私有语料确认是否允许公开访问
- [ ] 管理页 `/admin` 建议加访问密码后再上线
- [ ] Git remote 里不要带 GitHub token（检查 `.git/config`）


## P2 · 多实例部署（已支持）

状态全部在数据库里（默认 SQLite 单库多进程；生产用 Postgres：设 DATABASE_URL），应用无状态，可跑多实例：

1. 迁移旧 JSON 状态（首次执行一次）：
   - python migrate.py
2. 生产模式（web 与 worker 分开扩缩容）：
   - DATABASE_URL=postgresql://... python migrate.py
   - EMBED_WORKERS=0，起 N 个 web 实例（任意 WSGI 服务器/负载均衡后面）
   - 独立 worker：python worker.py（WORKERS=8 可调，可多机部署）
3. 本地开发（单命令最省事）：
   - python web_server.py  （内嵌 2 个 worker 线程）
4. Postgres 需要：pip install "psycopg[binary]"（已加入 requirements）

架构：web 实例只做 HTTP + SSE 转发；对话任务写入 jobs 表，worker 认领执行，
token 增量写 job_events，SSE 按游标轮询推送。同一会话 FIFO、跨会话并行由
DB 认领规则保证；任何实例可服务任何连接。


## P3 · 部署与运营手册（国内云 2C4G 起步）

### 0. 你要做的事（代码之外）
- 买云服务器（阿里云/腾讯云 2C4G 起，CentOS/Ubuntu 均可），安全组放行 80/443
- 域名 + 国内服务器必须 ICP 备案（没备案域名会被拦截，这是合规要求，不是技术问题）
- 费用监控：云控制台设预算告警；模型侧 API KEY 设用量上限

### 1. 服务器初始化
- curl -fsSL https://get.docker.com | bash && systemctl enable --now docker
- git clone <你的私有仓库> /app && cd /app
- 写 .env：POSTGRES_PASSWORD / CUSTOM_API_KEY / ADMIN_PASSWORD / SILICONFLOW_API_KEY / ALERT_WEBHOOK（可选钉钉/飞书机器人）
- python migrate.py 前先 docker compose up -d db；然后在容器里跑迁移：
  docker compose run --rm app python migrate.py

### 2. 起服务（Docker Compose）
- docker compose up -d --build        # db + app(可 scale) + worker + nginx
- docker compose up -d --scale app=2  # 扩 web 实例（状态都在 Postgres，随意加）
- docker compose up -d --scale worker=4         # 扩消费能力
- 日志：docker compose logs -f app worker（含 [access]/[alert] 行）

### 3. HTTPS
- 先用 IP 验证：curl http://服务器IP/api/health 应返回 {"ok": true,...}
- 备案通过后：certbot certonly --webroot -w ./deploy/certbot -d 你的域名
- nginx 加 443 server 块（证书在 deploy/certbot/），80 强制跳 443

### 4. 监控 / 告警
- 探活：GET /api/health（公开，供 UptimeRobot/云拨测，建议 1 分钟间隔）
- 运营看板：/ops（仅管理员）——当日 LLM 调用次数、每用户用量/额度、任务成败、平均与 P95 耗时、最近失败原因
- 告警钩子：设环境变量 ALERT_WEBHOOK（企业微信/钉钉/飞书机器人地址），任务执行失败即 POST {"text": "..."}
- 失败任务也会打 [alert] 日志，便于 docker logs 抓取

### 5. 备份（deploy/backup.sh）
- crontab：0 3 * * * /app/deploy/backup.sh
- 内容：knowledge/ 语料打包 + pg_dump（无 DATABASE_URL 时备份 data/app.db）
- 保留最近 14 份；Windows 开发机用 deploy/backup.ps1 + 任务计划
- 建议额外把备份目录挂对象存储（OSS/COS）

### 6. 上线前检查清单
- [ ] ADMIN_PASSWORD 已改强密码
- [ ] .env 里 KEY 均为环境变量，git 里无明文
- [ ] /api/health 外部可访问且 200
- [ ] 配额（每用户每日轮次）已按运营预期设置
- [ ] 备份脚本已跑过一次并验证可恢复
