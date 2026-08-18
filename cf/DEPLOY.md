# v2 Cloudflare Worker 版部署手册

> 完整部署流程记录，适用于重新部署 / 换账号重迁 / 恢复灾备。
> 线上地址：https://mirror.offlinelog.top

## 架构总览

```
用户浏览器
   │ https://mirror.offlinelog.top（大陆可直连）
   ▼
Cloudflare Worker（控制面，免服务器）
   ├── 静态前端（Assets 绑定 ../static）
   ├── D1 数据库（任务记录 + 搜索 + 限流计数）
   └── GitHub API（触发 dispatch + 查 run 状态懒回写）
          ▼
GitHub Actions 海外 runner（重活，仓库 Offlinelog/dockerhub）
   └── docker pull 源镜像 → docker tag → docker push 华为云 SWR
          ▼
国内用户 docker pull swr.cn-east-3.myhuaweicloud.com/cloud-gt3k/...
```

| 组件 | 免费额度 | 本项目用量级 |
|---|---|---|
| Workers | 10 万请求/天 | 每次搬运约几十个请求 |
| D1 | 5GB 存储 / 500 万行读/天 | 每任务一行 |
| GitHub Actions | 公开仓库无限 | 每次搬运 1~3 分钟 |
| SWR | 按存储计费 | 组织 `cloud-gt3k` |

**成本：0 元**（个人使用规模内）。

## 目录结构

```
cf/
├── wrangler.toml     # Worker 配置（D1 绑定 / Assets / vars）
├── schema.sql        # D1 表结构（tasks + rate_limit）
├── src/worker.js     # Worker 主体
└── DEPLOY.md         # 本文件
static/index.html     # 前端（v1/v2 共用，Assets 从 ../static 读）
.github/workflows/mirror.yml  # GitHub Actions workflow（v1/v2 共用）
```

## 前置条件

1. **Cloudflare 账号** + 一个 DNS 托管在 Cloudflare 的域名（本项目：`offlinelog.top`）
2. **本机 Node.js**（wrangler 依赖）
3. **GitHub 仓库**（本项目：`Offlinelog/dockerhub`），`.github/workflows/mirror.yml` 已推送且含关键行：
   ```yaml
   run-name: mirror-${{ inputs.task_id }}
   ```
   （run 名带 task_id 是状态回写的唯一映射依据——GitHub runs API 不返回 inputs 字段）
4. **GitHub PAT**（classic）：**只勾 `repo` + `workflow` 两个 scope**（不要全量）
5. **华为云 SWR**：组织 `cloud-gt3k` 已设为**公开**（用户免 login 拉取），Actions Secrets 已配 `SWR_REGISTRY` / `SWR_AK` / `SWR_SK`

## 部署步骤

### 1. 安装 wrangler 并登录

```bash
npm install -g wrangler
wrangler login   # 浏览器 OAuth 授权，不需要给任何人密码
```

### 2. 创建 D1 数据库

```bash
cd cf
wrangler d1 create mirror-db
```

输出中的 `database_id` 填进 `wrangler.toml`：

```toml
[[d1_databases]]
binding = "DB"
database_name = "mirror-db"
database_id = "<这里填>"   # 当前：4e99334e-b456-4fc1-b69c-75d48c715d0d
```

### 3. 初始化表结构

```bash
wrangler d1 execute mirror-db --remote --file=schema.sql
```

### 4. 设置 GITHUB_TOKEN secret

**必须用 python-dotenv 读取，不要用 grep 管道**（曾因 .env 重复行导致管道拼接出 46 位脏 token，引发 400 空体错误）：

```bash
# ../.env 中 GITHUB_TOKEN=ghp_xxx（40 位，确认无重复行）
../.venv/bin/python -c "import os; from dotenv import load_dotenv; load_dotenv('../.env'); print(os.environ['GITHUB_TOKEN'], end='')" | wrangler secret put GITHUB_TOKEN
```

Secret 即时生效，无需重新 deploy。

### 5. 部署 Worker

```bash
node --check src/worker.js   # 语法检查
wrangler deploy
```

输出 `https://docker-mirror.<子域>.workers.dev`。**该域名大陆无法访问**，继续下一步。

### 6. 绑定自有域名

Dashboard：Workers & Pages → docker-mirror → Settings → Domains & Routes → Add → **Custom Domain** → `mirror.offlinelog.top`

或写进 wrangler.toml：

```toml
routes = [{ pattern = "mirror.offlinelog.top/*", custom_domain = true }]
```

### 7. 验证

```bash
# 健康检查
curl https://mirror.offlinelog.top/api/health
# 应返回 {"status":"ok"}
```

页面提交一次小镜像（如 `redis:7-alpine`），预期状态流转：排队中 → 搬运中（1~3 分钟）→ 完成 + 可复制 pull 命令。

## 日常运维

### 查看实时日志

```bash
wrangler tail
```

### 更新部署

```bash
# 改 cf/src/worker.js 或 ../static/index.html 后
cd cf && wrangler deploy
```

### 更新 token（轮换）

```bash
# 1. 改 ../.env 里的 GITHUB_TOKEN
# 2. 重传 secret
../.venv/bin/python -c "import os; from dotenv import load_dotenv; load_dotenv('../.env'); print(os.environ['GITHUB_TOKEN'], end='')" | wrangler secret put GITHUB_TOKEN
```

### 查 D1 数据

```bash
wrangler d1 execute mirror-db --remote --command "SELECT id, source, status, created_at FROM tasks ORDER BY created_at DESC LIMIT 10"
```

## 已知坑（踩过的）

| 坑 | 现象 | 解法 |
|---|---|---|
| `*.workers.dev` 被墙 | 页面 `ERR_CONNECTION_TIMED_OUT` | 绑自有域名（Custom Domain） |
| Workers fetch 无 UA/Content-Type | `GitHub API 400 (no-content-type): (empty body)` | 手动加 `User-Agent` + `Content-Type` 头 |
| grep 管道上传 secret + .env 重复行 | token 变 46 位、Authorization 非法 | 用 python-dotenv 读取上传；删 .env 重复行 |
| runs API 不返回 inputs | 状态永远停在搬运中 | workflow 加 `run-name: mirror-${{ inputs.task_id }}`，从 run 名解析 |
| SWR 登录密码不是 SK | `denied: Authenticate Error` | AK+SK 经 HMAC-SHA256 算登录密钥（workflow 已内置该步骤） |
| GitHub workflow 未 push | 触发用旧版 workflow、inputs 不识别 | 改 workflow 后必须 `git push`，Actions 才用新版 |

## 与 v1 本地版的关系

v1（`app/` 目录，FastAPI + SQLite）保留作为本地开发/调试环境，两者共用：
- `static/index.html`（前端）
- `.github/workflows/mirror.yml`（搬运 workflow）
- GitHub 仓库与 Secrets

差异：v1 有内存串行队列 + 后台轮询线程；v2 无队列（并发 dispatch）+ 前端查询时懒回写状态。
