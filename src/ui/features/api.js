async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`bad response (${response.status})`);
  }
  if (!payload.ok) {
    const err = new Error(payload.error || "request failed");
    if (payload.code) err.code = payload.code; // machine-readable error kind (e.g. target_missing)
    throw err;
  }
  return payload.data;
}

const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body || {}) });
const enc = encodeURIComponent;
const base = (id) => `/api/repos/${enc(id)}`;

export const api = {
  // server facts (dev mode etc.)
  env: () => request("/api/env"),

  // registry
  repos: () => request("/api/repos"),
  addRepo: (data) => post("/api/repos", data),
  repo: (id) => request(base(id)),
  removeRepo: (id) => request(base(id), { method: "DELETE" }),
  setup: (id, data) => post(`${base(id)}/setup`, data),
  meta: (id) => request(`${base(id)}/meta`),

  // monitor + runner (params: queue/errors/log _offset/_limit pagination)
  monitor: (id, params) => {
    const qs = new URLSearchParams(params || {}).toString();
    return request(`${base(id)}/monitor${qs ? "?" + qs : ""}`);
  },
  runner: (id, action) => post(`${base(id)}/runner`, { action }),
  retryTask: (id, taskId) => post(`${base(id)}/tasks/${enc(taskId)}/retry`),
  // live agent stdout for a work task: { task_id, status, running, text }
  workOutput: (id, taskId) => request(`${base(id)}/work-output/${enc(taskId)}`),

  // configuration
  getConfig: (id) => request(`${base(id)}/config`),
  putConfig: (id, config) => request(`${base(id)}/config`, { method: "PUT", body: JSON.stringify({ config }) }),

  // spec docs (every provider; read-only over <specseed_dir>/spec)
  spec: (id) => request(`${base(id)}/spec`),
  specFile: (id, path) => request(`${base(id)}/spec/file?path=${enc(path)}`),

  // code viewer (every provider; read-only over the local target repo's git)
  codeMeta: (id) => request(`${base(id)}/code/meta`),
  codeTree: (id, ref, path, wt) =>
    request(`${base(id)}/code/tree?ref=${enc(ref)}&path=${enc(path || "")}${wt ? "&worktree=1" : ""}`),
  codeBlob: (id, ref, path, wt) =>
    request(`${base(id)}/code/blob?ref=${enc(ref)}&path=${enc(path)}${wt ? "&worktree=1" : ""}`),
  codeCommits: (id, ref, offset = 0, limit = 50) =>
    request(`${base(id)}/code/commits?ref=${enc(ref)}&commits_offset=${offset}&commits_limit=${limit}`),
  codeCommit: (id, sha) => request(`${base(id)}/code/commit?sha=${enc(sha)}`),
  codeCompare: (id, b, h) => request(`${base(id)}/code/compare?base=${enc(b)}&head=${enc(h)}`),

  // tracker (local provider only)
  listPosts: (id, state) => request(`${base(id)}/posts?state=${enc(state)}`),
  getPost: (id, postId) => request(`${base(id)}/posts/${enc(postId)}`),
  createPost: (id, data) => post(`${base(id)}/posts`, data),
  savePost: (id, postId, data) =>
    request(`${base(id)}/posts/${enc(postId)}`, { method: "PATCH", body: JSON.stringify(data) }),
  deletePost: (id, postId) => request(`${base(id)}/posts/${enc(postId)}`, { method: "DELETE" }),
  togglePost: (id, postId) => post(`${base(id)}/posts/${enc(postId)}/toggle`),
  updateLabel: (id, postId, action, label) => post(`${base(id)}/posts/${enc(postId)}/labels`, { action, label }),
  addComment: (id, postId, body) => post(`${base(id)}/posts/${enc(postId)}/comments`, { body }),
  reactPost: (id, postId, reaction, toggle = true) =>
    post(`${base(id)}/posts/${enc(postId)}/reactions`, { reaction, toggle }),
  reactComment: (id, postId, commentId, reaction, toggle = true) =>
    post(`${base(id)}/posts/${enc(postId)}/comments/${enc(commentId)}/reactions`, { reaction, toggle }),
};
