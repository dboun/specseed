async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!payload.ok) {
    throw new Error(payload.error || "tracking op failed");
  }
  return payload;
}

export const api = {
  async meta() {
    return (await request("/api/meta")).data;
  },

  async listPosts(state) {
    return request(`/api/posts?state=${encodeURIComponent(state)}`);
  },

  async getPost(id) {
    return (await request(`/api/posts/${encodeURIComponent(id)}`)).data;
  },

  async createPost(data) {
    return (await request("/api/posts", { method: "POST", body: JSON.stringify(data) })).data;
  },

  async savePost(id, data) {
    return (await request(`/api/posts/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    })).data;
  },

  async deletePost(id) {
    return (await request(`/api/posts/${encodeURIComponent(id)}`, { method: "DELETE" })).data;
  },

  async togglePost(id) {
    return (await request(`/api/posts/${encodeURIComponent(id)}/toggle`, { method: "POST" })).data;
  },

  async updateLabel(id, action, label) {
    return (await request(`/api/posts/${encodeURIComponent(id)}/labels`, {
      method: "POST",
      body: JSON.stringify({ action, label }),
    })).data;
  },

  async addComment(id, body) {
    return (await request(`/api/posts/${encodeURIComponent(id)}/comments`, {
      method: "POST",
      body: JSON.stringify({ body }),
    })).data;
  },

  async reactToPost(id, reaction) {
    return (await request(`/api/posts/${encodeURIComponent(id)}/reactions`, {
      method: "POST",
      body: JSON.stringify({ reaction }),
    })).data;
  },

  async reactToComment(id, commentId, reaction) {
    return (await request(
      `/api/posts/${encodeURIComponent(id)}/comments/${encodeURIComponent(commentId)}/reactions`,
      {
        method: "POST",
        body: JSON.stringify({ reaction }),
      },
    )).data;
  },
};
