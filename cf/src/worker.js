// Docker 镜像搬运 Worker 版（v2）
// 控制面：记录任务 → 触发 GitHub Actions → 懒查 run 状态回写
// 重活（pull/push）在 GitHub Actions，本 Worker 不碰 SWR 凭证

const GITHUB_API = "https://api.github.com";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // ---- API 路由 ----
    if (path === "/api/health") return json({ status: "ok" });

    if (path === "/api/mirror" && request.method === "POST") {
      return handleMirror(request, env, ctx);
    }

    if (path === "/api/tasks" && request.method === "GET") {
      return handleList(url, env);
    }

    if (path === "/api/stats" && request.method === "GET") {
      return handleStats(env);
    }

    const taskMatch = path.match(/^\/api\/tasks\/([a-f0-9]+)$/);
    if (taskMatch) {
      return handleGetTask(taskMatch[1], env, ctx);
    }

    // ---- 静态资源（含 index.html）由 Assets 绑定处理 ----
    // 走到这里说明不是 API，交给 ASSETS；无 ASSETS 时返回 404
    if (env.ASSETS) return env.ASSETS.fetch(request);
    return new Response("Not Found", { status: 404 });
  },
};

// ========== 限流：每 IP 每分钟 5 次 ==========
async function checkRate(env, ip) {
  const now = new Date();
  const bucket = now.toISOString().slice(0, 16); // 分钟桶
  const db = env.DB;
  const res = await db.prepare(
    "INSERT INTO rate_limit (ip, bucket, count) VALUES (?, ?, 1) ON CONFLICT(ip, bucket) DO UPDATE SET count = count + 1 RETURNING count"
  ).bind(ip, bucket).first();
  return res.count <= 5;
}

// ========== 镜像名解析（与本地版 normalize_image 一致）==========
function normalizeImage(raw) {
  raw = raw.trim().toLowerCase();
  if (!raw) throw new Error("镜像名不能为空");
  if (raw.includes("@")) throw new Error("暂不支持 digest 引用的镜像");

  const parts = raw.split("/");
  let last = parts[parts.length - 1];
  let tag = "latest";
  const colon = last.lastIndexOf(":");
  if (colon > 0) {
    tag = last.slice(colon + 1);
    last = last.slice(0, colon);
  }
  parts[parts.length - 1] = last;

  // 第一段不含 . 不含 : 不是 localhost → 补 docker.io
  if (!parts[0].includes(".") && !parts[0].includes(":") && parts[0] !== "localhost") {
    parts.unshift("docker.io");
  }
  // library 归一化：docker.io/nginx → docker.io/library/nginx
  if (parts[0] === "docker.io" && parts.length === 2) {
    parts.splice(1, 0, "library");
  }
  return { source: parts.join("/"), tag };
}

// 构造 SWR 目标地址
function buildTarget(source, tag, env) {
  const parts = source.split("/");
  let repoPath = parts.slice(1).join("/");
  if (repoPath.startsWith("library/")) repoPath = repoPath.slice("library/".length);
  return `${env.SWR_REGISTRY}/${env.SWR_NAMESPACE}/${repoPath}:${tag}`;
}

// ========== 触发 GitHub workflow ==========
async function triggerWorkflow(env, source, tag, arch, target, taskId) {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) {
    return { ok: false, err: "未配置 GITHUB_TOKEN / GITHUB_REPO" };
  }
  const url = `${GITHUB_API}/repos/${env.GITHUB_REPO}/actions/workflows/mirror.yml/dispatches`;
  const headers = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "Content-Type": "application/json",
    "User-Agent": "docker-mirror-worker",  // GitHub API 强制要求 UA，Workers fetch 默认不带
    "X-GitHub-Api-Version": "2022-11-28",
  };
  const payload = {
    ref: env.GITHUB_REF || "main",
    inputs: { source, tag, arch, target, task_id: taskId },
  };
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    if (resp.status === 204) return { ok: true };
    const text = (await resp.text()).slice(0, 300);
    const respType = resp.headers.get("content-type") || "no-content-type";
    console.error(`dispatch failed: status=${resp.status} type=${respType} body=${text}`);
    return { ok: false, err: `GitHub API ${resp.status} (${respType}): ${text || "(empty body)"}` };
  } catch (e) {
    return { ok: false, err: `GitHub API 请求失败: ${e.message}` };
  }
}

// ========== 查最近 N 次 run，从 run-name 解析 task_id ==========
async function recentRuns(env, perPage = 10) {
  const url = `${GITHUB_API}/repos/${env.GITHUB_REPO}/actions/workflows/mirror.yml/runs?per_page=${perPage}`;
  const headers = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "docker-mirror-worker",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  try {
    const resp = await fetch(url, { headers });
    if (resp.status !== 200) return [];
    const data = await resp.json();
    return data.workflow_runs || [];
  } catch {
    return [];
  }
}

// ========== POST /api/mirror ==========
async function handleMirror(request, env, ctx) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const allowed = await checkRate(env, ip);
  if (!allowed) return json({ detail: "请求过于频繁，请稍后再试" }, 429);

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ detail: "请求体不是合法 JSON" }, 400);
  }
  const raw = body.image || "";
  const arch = body.arch || "amd64";
  if (!["amd64", "arm64"].includes(arch)) {
    return json({ detail: "不支持的架构" }, 400);
  }

  let source, tag;
  try {
    ({ source, tag } = normalizeImage(raw));
  } catch (e) {
    return json({ detail: e.message }, 400);
  }
  const target = buildTarget(source, tag, env);
  const taskId = crypto.randomUUID().replace(/-/g, "");
  const now = new Date().toISOString();

  await env.DB.prepare(
    "INSERT INTO tasks (id, source, tag, arch, status, pull_command, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)"
  ).bind(taskId, source, tag, arch, target, now, now).run();

  // 触发 workflow（不阻塞响应）
  ctx.waitUntil(
    (async () => {
      const { ok, err } = await triggerWorkflow(env, source, tag, arch, target, taskId);
      const now2 = new Date().toISOString();
      if (ok) {
        await env.DB.prepare(
          "UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ?"
        ).bind(now2, taskId).run();
      } else {
        await env.DB.prepare(
          "UPDATE tasks SET status = 'failed', error = ?, updated_at = ? WHERE id = ?"
        ).bind(err, now2, taskId).run();
      }
    })()
  );

  return json({ task_id: taskId, source, target, status: "pending" });
}

// ========== GET /api/tasks/:id（顺带懒查 GitHub 回写）==========
async function handleGetTask(taskId, env, ctx) {
  const task = await env.DB.prepare("SELECT * FROM tasks WHERE id = ?").bind(taskId).first();
  if (!task) return json({ detail: "任务不存在" }, 404);

  // 任务还在进行中 → 顺带查 GitHub run 状态回写
  if (task.status === "running" || task.status === "pending") {
    ctx.waitUntil(syncTaskStatus(env, taskId));
  }
  return json(task);
}

// 根据 run-name 匹配 task_id 并回写
async function syncTaskStatus(env, taskId) {
  const runs = await recentRuns(env, 10);
  for (const run of runs) {
    const conclusion = run.conclusion;
    if (!conclusion) continue; // 进行中
    const name = run.name || "";
    const runTaskId = name.startsWith("mirror-") ? name.slice("mirror-".length) : "";
    if (runTaskId === taskId) {
      const status = conclusion === "success" ? "done" : "failed";
      const error = conclusion === "success" ? null : `workflow ${conclusion}`;
      const now = new Date().toISOString();
      await env.DB.prepare(
        "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ? AND status IN ('pending','running')"
      ).bind(status, error, now, taskId).run();
      break;
    }
  }
}

// ========== GET /api/tasks ==========
async function handleList(url, env) {
  let limit = parseInt(url.searchParams.get("limit") || "20", 10);
  let offset = parseInt(url.searchParams.get("offset") || "0", 10);
  const search = (url.searchParams.get("search") || "").trim().slice(0, 100);
  limit = Math.max(1, Math.min(limit, 100));
  offset = Math.max(0, offset);

  let sql = "SELECT id, source, tag, arch, status, pull_command, error, created_at, updated_at FROM tasks";
  const params = [];
  if (search) {
    const safe = search.replace(/%/g, "").replace(/_/g, "");
    sql += " WHERE source LIKE ? ESCAPE '\\' OR tag LIKE ? ESCAPE '\\'";
    params.push(`%${safe}%`, `%${safe}%`);
  }
  sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?";
  params.push(limit, offset);

  const { results } = await env.DB.prepare(sql).bind(...params).all();
  return json(results);
}

// ========== GET /api/stats ==========
// 与本地 db.stats() 同构：GROUP BY status 计数 + created_at >= date('now') 计今日。
// created_at 为 ISO UTC（new Date().toISOString()），前缀比较等价 UTC 当日，
// 且可走 idx_tasks_created 范围扫描。
async function handleStats(env) {
  const { results } = await env.DB.prepare(
    "SELECT status, COUNT(*) AS c FROM tasks GROUP BY status"
  ).all();
  const todayRow = await env.DB.prepare(
    "SELECT COUNT(*) AS c FROM tasks WHERE created_at >= date('now')"
  ).first();
  const counts = {};
  let total = 0;
  for (const r of results || []) {
    counts[r.status] = r.c;
    total += r.c;
  }
  const done = counts.done || 0;
  const successRate = total ? Math.round((done * 100) / total) : 0;
  return json({
    total,
    done,
    failed: counts.failed || 0,
    running: counts.running || 0,
    pending: counts.pending || 0,
    today: todayRow ? todayRow.c : 0,
    success_rate: successRate,
  });
}

// ========== 工具 ==========
function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
