import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .queue import TaskQueue

app = FastAPI(title="Docker Hub Mirror")

# ---- 内存限流：每 IP 每分钟最多 N 次 ----
_rate_lock = threading.Lock()
_rate_buckets: dict[str, deque] = defaultdict(deque)

LIMIT_PER_MIN = 5


def _check_rate(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[ip]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= LIMIT_PER_MIN:
            return False
        bucket.append(now)
        return True


def normalize_image(raw: str) -> tuple[str, str]:
    """返回 (source, tag)。source 含完整 registry 路径，tag 单独提取。"""
    raw = raw.strip().lower()
    if not raw:
        raise ValueError("镜像名不能为空")
    if "@" in raw:
        raise ValueError("暂不支持 digest 引用的镜像")

    parts = raw.split("/")
    last = parts[-1]
    tag = "latest"
    if ":" in last:
        last, tag = last.rsplit(":", 1)
    parts[-1] = last

    # 第一个组件不含 . 不包含 : 也不是 localhost → 补 docker.io
    if "." not in parts[0] and ":" not in parts[0] and parts[0] != "localhost":
        parts = ["docker.io"] + parts

    # library 归一化：docker.io/nginx → docker.io/library/nginx
    if parts[0] == "docker.io" and len(parts) == 2:
        parts.insert(1, "library")

    source = "/".join(parts)
    return source, tag


def build_target(source: str, tag: str) -> str:
    """构造华为云 SWR 目标地址，去掉源 registry 前缀。"""
    # source 形如 docker.io/library/nginx，去掉第一个组件（registry）
    parts = source.split("/")
    repo_path = "/".join(parts[1:])
    if repo_path.startswith("library/"):
        repo_path = repo_path[len("library/"):]
    return f"{config.SWR_REGISTRY}/{config.SWR_NAMESPACE}/{repo_path}:{tag}"


queue = TaskQueue()


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    queue.start()


@app.post("/api/mirror")
async def mirror(request: Request):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate(ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    body = await request.json()
    raw = body.get("image", "")
    arch = body.get("arch", "amd64")
    if arch not in ("amd64", "arm64"):
        raise HTTPException(status_code=400, detail="不支持的架构")

    try:
        source, tag = normalize_image(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    target = build_target(source, tag)
    task_id = uuid.uuid4().hex
    db.create_task(task_id, source, tag, arch, target)
    queue.enqueue(task_id)

    return JSONResponse(
        {
            "task_id": task_id,
            "source": source,
            "target": target,
            "status": "pending",
        }
    )


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.get("/api/tasks")
async def list_tasks(limit: int = 20, offset: int = 0, search: str = ""):
    limit = max(1, min(limit, 100))  # 分页上限，防无界查询
    offset = max(0, offset)
    search = search.strip()[:100]  # 长度上限，防超长输入
    return db.list_tasks(limit=limit, offset=offset, search=search)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
