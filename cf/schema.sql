-- D1 初始化 schema（与本地 SQLite 一致）
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
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

-- 限流计数表（跨请求无内存，用 D1 计数）
CREATE TABLE IF NOT EXISTS rate_limit (
  ip TEXT NOT NULL,
  bucket TEXT NOT NULL,  -- 分钟桶，格式 YYYY-MM-DDTHH:MM
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (ip, bucket)
);
