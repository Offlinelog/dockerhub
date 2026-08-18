import threading
import time

from . import db, github_client

POLL_INTERVAL = 5  # 轮询 GitHub run 状态的间隔（秒）


class TaskQueue:
    def __init__(self, concurrency: int = 1, interval: float = 1.0):
        # v1 串行：一次只跑一个任务，避免并发触发造成 run 混淆
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._active = 0
        self._concurrency = concurrency
        self._interval = interval
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._poller = threading.Thread(target=self._poll, daemon=True)

    def start(self) -> None:
        self._worker.start()
        self._poller.start()

    def enqueue(self, task_id: str) -> None:
        with self._lock:
            self._queue.append(task_id)

    def _run(self) -> None:
        while True:
            with self._lock:
                task_id = self._queue.pop(0) if self._queue else None
                if task_id:
                    self._active += 1
            if task_id:
                self._process(task_id)
                with self._lock:
                    self._active -= 1
            time.sleep(self._interval)

    def _process(self, task_id: str) -> None:
        task = db.get_task(task_id)
        if not task:
            return
        ok, err = github_client.trigger_workflow(
            task["source"], task["tag"], task["arch"], task["pull_command"], task_id
        )
        if ok:
            db.update_task_status(task_id, "running")
        else:
            db.update_task_status(task_id, "failed", err)

    def _poll(self) -> None:
        while True:
            time.sleep(POLL_INTERVAL)
            try:
                self._poll_once()
            except Exception:
                pass

    def _poll_once(self) -> None:
        running = db.find_tasks_by_status("running")
        if not running:
            return
        # run 名形如 mirror-<task_id>（见 workflow 的 run-name），从列表 API 即可拿到，无需逐个查详情
        runs = github_client.recent_runs(per_page=max(10, len(running) * 2))
        pending_ids = {t["id"] for t in running}
        for run in runs:
            if not pending_ids:
                break
            conclusion = run.get("conclusion")
            if not conclusion:
                continue  # 还在进行中
            name = run.get("name", "")
            run_task_id = name[len("mirror-"):] if name.startswith("mirror-") else ""
            if run_task_id in pending_ids:
                status = "done" if conclusion == "success" else "failed"
                error = None if conclusion == "success" else f"workflow {conclusion}"
                db.update_task_status(run_task_id, status, error)
                pending_ids.discard(run_task_id)
