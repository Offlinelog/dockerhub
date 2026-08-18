# Docker Hub 镜像搬运服务

复刻 docker.aityp.com 的基础能力：输入 Docker Hub 镜像名，通过 GitHub Actions 海外拉取后推送到华为云 SWR，返回国内可用的 `docker pull` 命令。

## 架构

```
用户 ──> Web 前端(static/index.html)
          │ POST /api/mirror
          ▼
       后端 FastAPI(app/)
          │ 触发 workflow_dispatch
          ▼
       GitHub Actions(workflow/mirror.yml)  ← 海外 runner
          │ docker pull 源镜像 → tag → push
          ▼
       华为云 SWR  ← 国内用户直接 pull
```

## 目录

```
app/
  main.py          FastAPI 入口 + 限流 + 路由
  config.py        配置读取
  db.py            SQLite 存储
  github_client.py 触发 GitHub workflow
  queue.py         内存任务队列
static/index.html  前端页面
workflow/mirror.yml GitHub Actions workflow
```

## 快速开始

### 1. 准备 GitHub

1. 新建 GitHub 仓库，把 `workflow/mirror.yml` 放到 `.github/workflows/mirror.yml`
2. 仓库 Settings → Secrets → Actions，添加 3 个 secret：
   - `SWR_REGISTRY`: `swr.cn-east-3.myhuaweicloud.com`
   - `SWR_USER`: 华为云 SWR 用户名（`cn-east-3@...`）
   - `SWR_PASSWORD`: 华为云 SWR 密码
3. 生成 Personal Access Token（classic，scope 勾 `repo` + `workflow`）

### 2. 配置后端

```bash
cp .env.example .env
# 编辑 .env 填入 GITHUB_TOKEN / GITHUB_REPO / SWR_REGISTRY / SWR_NAMESPACE
```

### 3. 运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 使用。

## 说明

- v1 只做简单搬运，架构支持 amd64/arm64，默认 amd64
- 任务状态：`pending` → `running`（触发后）→ 后端轮询 GitHub run 状态 → `done`/`failed`
- 内存限流：每 IP 每分钟 5 次

## 局限

- 串行处理：一次只跑一个任务（保证 run 状态可映射），并发留给后续版本
- 状态回查依赖 GitHub API，rate limit 5000/h，批量场景需注意
- 不支持 digest 引用（`name@sha256:...`）
