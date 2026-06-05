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
  if (!payload.ok) throw new Error(payload.error || "request failed");
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

  // monitor + runner
  monitor: (id) => request(`${base(id)}/monitor`),
  runner: (id, action) => post(`${base(id)}/runner`, { action }),

  // configuration
  getConfig: (id) => request(`${base(id)}/config`),
  putConfig: (id, config) => request(`${base(id)}/config`, { method: "PUT", body: JSON.stringify({ config }) }),

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
