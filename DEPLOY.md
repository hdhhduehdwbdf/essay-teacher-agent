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
