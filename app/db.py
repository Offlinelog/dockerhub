import sqlite3
from datetime import datetime, timezone

from . import config


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                tag TEXT,
                arch TEXT NOT NULL DEFAULT 'amd64',
                status TEXT NOT NULL DEFAULT 'pending',
                pull_command TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_task(task_id: str, source: str, tag: str, arch: str, pull_command: str) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, source, tag, arch, status, pull_command, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (task_id, source, tag, arch, pull_command, now, now),
        )
        conn.commit()


def get_task(task_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


def find_tasks_by_status(status: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at", (status,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_tasks(limit: int = 20, offset: int = 0, search: str = "") -> list[dict]:
    sql = (
        "SELECT id, source, tag, arch, status, pull_command, error, created_at, updated_at "
        "FROM tasks"
    )
    params: list = []
    if search:
        # 仅接受字母数字及常用分隔符，防 LIKE 通配符注入；tag/source 各匹配一次
        safe = search.replace("%", "").replace("_", "")
        sql += " WHERE source LIKE ? ESCAPE '\\' OR tag LIKE ? ESCAPE '\\'"
        like = f"%{safe}%"
        params = [like, like]
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def update_task_status(task_id: str, status: str, error: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, _now(), task_id),
        )
        conn.commit()


def stats() -> dict:
    """聚合统计：总数、各状态计数、今日新增、成功率。空表返回全 0。

    today 用 created_at >= date('now')：created_at 为 ISO UTC（Python
    isoformat 或 Worker toISOString 均以 'YYYY-MM-DD' 开头），字符串前缀
    比较等价于 UTC 当日，且可走 D1 的 idx_tasks_created 范围扫描。
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM tasks GROUP BY status"
        ).fetchall()
        today = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE created_at >= date('now')"
        ).fetchone()[0]
    counts = {r["status"]: r["c"] for r in rows}
    total = sum(counts.values())
    done = counts.get("done", 0)
    # 半数进一（int(x+0.5)），与 cf/src/worker.js 的 Math.round 行为一致，
    # 保证两后端在 done*100/total 恰为 N.5 时取值完全相同（Python round 为四舍六入五成双）。
    success_rate = int(done * 100 / total + 0.5) if total else 0
    return {
        "total": total,
        "done": done,
        "failed": counts.get("failed", 0),
        "running": counts.get("running", 0),
        "pending": counts.get("pending", 0),
        "today": today,
        "success_rate": success_rate,
    }
