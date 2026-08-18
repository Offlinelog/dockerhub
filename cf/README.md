# Cloudflare Worker 版（v2）

把后端从本机 FastAPI 迁到 Cloudflare Workers，免服务器。GitHub 仓库 / workflow / SWR Secrets 全部不动。

## 与本地版差异

| 项 | 本地版 | Worker 版 |
|---|---|---|
| 后端 | FastAPI + uvicorn | Worker (JS) |
| 数据库 | SQLite 文件 | D1 |
| 任务队列 | 内存串行队列 | 删掉（run-name 带 task_id，任务自识别） |
| 状态回写 | 后台轮询线程 | 查 `/api/tasks/:id` 时懒查 GitHub |
| 限流 | 内存计数 | D1 计数表 |
| 静态前端 | FastAPI StaticFiles | Worker Assets 绑定 |

## 部署步骤

### 1. 装 wrangler 并登录

```bash
npm install -g wrangler
wrangler login   # 浏览器授权，不用给任何人密码
```

### 2. 创建 D1 数据库

```bash
cd cf
wrangler d1 create mirror-db
```

输出会给出 `database_id`，填进 `wrangler.toml` 的 `database_id` 字段。

### 3. 初始化表结构

```bash
wrangler d1 execute mirror-db --remote --file=schema.sql
```

（本地调试用 `--local` 替代 `--remote`）

### 4. 设置 GITHUB_TOKEN（secret，不进代码）

```bash
wrangler secret put GITHUB_TOKEN
# 粘贴你的 GitHub PAT（classic，scope: repo + workflow）
```

### 5. 部署

```bash
wrangler deploy
```

部署成功会输出 `https://docker-mirror.<你的子域>.workers.dev`。

### 6. 绑自有域名（推荐，大陆访问稳定）

两种方式二选一：

**方式 A：Dashboard**
Cloudflare Dashboard → Workers & Pages → docker-mirror → Settings → Triggers → Custom Domains → 添加 `mirror.yourdomain.com`

**方式 B：wrangler.toml**
```toml
routes = [{ pattern = "mirror.yourdomain.com/*", custom_domain = true }]
```
再 `wrangler deploy`。

## 本地调试

```bash
wrangler dev
```
默认用本地 D1（`--local`），访问 http://localhost:8787。

## 前置确认

- GitHub 仓库 `Offlinelog/dockerhub` 的 `.github/workflows/mirror.yml` 已是带 `run-name: mirror-${{ inputs.task_id }}` 的版本（本地版已验证）
- 华为云 SWR 组织 `cloud-gt3k` 已设为公开（用户免 login 拉取）
