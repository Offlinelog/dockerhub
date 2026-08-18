import httpx

from . import config

API = "https://api.github.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def trigger_workflow(source: str, tag: str, arch: str, target: str, task_id: str) -> tuple[bool, str]:
    """触发 GitHub Actions workflow_dispatch。返回 (是否成功, 错误信息)。"""
    if not config.GITHUB_TOKEN or not config.GITHUB_REPO:
        return False, "未配置 GITHUB_TOKEN / GITHUB_REPO"
    url = f"{API}/repos/{config.GITHUB_REPO}/actions/workflows/mirror.yml/dispatches"
    payload = {
        "ref": config.GITHUB_REF,
        "inputs": {
            "source": source,
            "tag": tag,
            "arch": arch,
            "target": target,
            "task_id": task_id,
        },
    }
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(url, headers=_headers(), json=payload)
    except httpx.HTTPError as e:
        return False, f"GitHub API 请求失败: {e}"

    if resp.status_code == 204:
        return True, ""
    return False, f"GitHub API {resp.status_code}: {resp.text[:300]}"


def recent_runs(per_page: int = 10) -> list[dict]:
    """查询 mirror workflow 最近 N 次 run（含 run 详情，用于读取 inputs.task_id）。"""
    url = f"{API}/repos/{config.GITHUB_REPO}/actions/workflows/mirror.yml/runs"
    params = {"per_page": per_page}
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, headers=_headers(), params=params)
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []
    return resp.json().get("workflow_runs", [])


def run_detail(run_id: int) -> dict | None:
    """查询单次 run 详情（含 inputs 字段）。run 列表端点不返回 inputs，需单独取详情。"""
    url = f"{API}/repos/{config.GITHUB_REPO}/actions/runs/{run_id}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, headers=_headers())
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return resp.json()

