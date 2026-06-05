import { api } from "./api.js";
import { escapeHtml, labelOptions, modal, toast } from "../ui/components.js";

const filters = ["open", "closed", "all"];
const reactionIcons = {
  eyes: "&#128064;",
  heart: "&#10084;",
  thumbs_down: "&#128078;",
  thumbs_up: "&#128077;",
};

export function createPostsFeature({ onError }) {
  const state = {
    filter: "open",
    posts: [],
    selectedId: null,
    selected: null,
    meta: {
      author: "remote",
      dbPath: "",
      labels: [],
      reactions: ["eyes", "heart", "thumbs_down", "thumbs_up"],
    },
    busy: false,
  };

  function html() {
    return `
      <main class="app-shell">
        <section class="main-pane">
          <header class="topbar">
            <div>
              <div class="brand">specseed</div>
              <div class="db-path" data-db-path>TRACKER_DB_PATH</div>
            </div>
            <div class="actions">
              <div class="filter-tabs" data-filters></div>
              <button data-new class="primary">New post</button>
              <button data-refresh>Refresh</button>
            </div>
          </header>
          <section class="stats-grid" data-stats></section>
          <section class="post-list" data-post-list></section>
        </section>
        <aside data-drawer class="drawer-empty">Pick a post. Details open here.</aside>
      </main>
    `;
  }

  function mount(root) {
    document.addEventListener("click", onClick);
    document.addEventListener("submit", onSubmit);
    refresh();
  }

  async function refresh(keepSelected = true) {
    try {
      state.busy = true;
      render();
      state.meta = await api.meta();
      const payload = await api.listPosts(state.filter);
      state.posts = payload.data;
      state.meta.dbPath = payload.meta?.dbPath || state.meta.dbPath;
      if (keepSelected && state.selectedId) {
        await loadPost(state.selectedId, false);
      } else {
        state.selected = null;
      }
    } catch (error) {
      onError(error);
    } finally {
      state.busy = false;
      render();
    }
  }

  async function loadPost(id, repaint = true) {
    try {
      state.selectedId = id;
      state.selected = await api.getPost(id);
      if (repaint) render();
    } catch (error) {
      state.selectedId = null;
      state.selected = null;
      onError(error);
    }
  }

  function render() {
    const root = document.querySelector("#app");
    root.querySelector("[data-db-path]").textContent = state.meta.dbPath || "TRACKER_DB_PATH not set";
    root.querySelector("[data-filters]").innerHTML = filters
      .map((filter) => `<button data-filter="${filter}" class="${state.filter === filter ? "active" : ""}">${filter}</button>`)
      .join("");
    root.querySelector("[data-stats]").innerHTML = renderStats();
    root.querySelector("[data-post-list]").innerHTML = state.busy ? renderLoading() : renderPosts();
    root.querySelector("[data-drawer]").outerHTML = renderDrawer();
  }

  function renderStats() {
    const total = state.posts.length;
    return [
      ["shown", total],
      ["filter", state.filter],
      ["selected", state.selectedId ? `#${state.selectedId}` : "none"],
    ]
      .map(([label, value]) => `
        <article class="stat-card">
          <div class="stat-label">${escapeHtml(label)}</div>
          <div class="stat-value">${escapeHtml(value)}</div>
        </article>
      `)
      .join("");
  }

  function renderLoading() {
    return `<article class="post-card"><div class="muted">Loading posts...</div></article>`;
  }

  function renderPosts() {
    if (!state.posts.length) {
      return `<article class="post-card"><div class="post-title">No ${state.filter} posts.</div><div class="muted">Create one or change filter.</div></article>`;
    }
    return state.posts.map(renderPostCard).join("");
  }

  function renderPostCard(post) {
    const labels = post.labels || [];
    return `
      <article class="post-card ${String(post.id) === String(state.selectedId) ? "active" : ""}" data-open-post="${escapeHtml(post.id)}">
        <div class="post-line">
          <div>
            <div class="post-title">#${escapeHtml(post.id)} ${escapeHtml(post.title)}</div>
            <div class="post-meta">${escapeHtml(post.author || "unknown")} · ${escapeHtml(post.updated_at || "no update")}</div>
          </div>
          <span class="state ${post.is_open ? "open" : "closed"}">${post.is_open ? "open" : "closed"}</span>
        </div>
        <div class="pill-row">${labels.map((label) => labelChip(label.name)).join("")}</div>
      </article>
    `;
  }

  function renderDrawer() {
    const post = state.selected;
    if (!post) {
      return `<aside data-drawer class="drawer-empty">Pick a post. Details open here.</aside>`;
    }
    const labels = post.labels || [];
    const reactions = post.reactions || [];
    const labelNames = labels.map((label) => label.name);
    return `
      <aside data-drawer class="drawer">
        <div class="drawer-top">
          <span class="state ${post.is_open ? "open" : "closed"}">${post.is_open ? "open" : "closed"}</span>
          <button type="button" class="icon-button mobile-close" data-close-drawer aria-label="Close details">X</button>
        </div>
        <h1>#${escapeHtml(post.id)} ${escapeHtml(post.title)}</h1>
        <div class="post-meta">by ${escapeHtml(post.author || "unknown")} · updated ${escapeHtml(post.updated_at || "unknown")}</div>
        <div class="drawer-grid">
          <div><div class="stat-label">assignees</div><div>${escapeHtml((post.assignees || []).join(", ") || "none")}</div></div>
          <div><div class="stat-label">reactions</div><div class="reaction-strip">${renderReactionButtons(reactions, "post")}</div></div>
        </div>
        <form data-save-post>
          <div class="field"><label>title</label><input name="title" value="${escapeHtml(post.title)}" /></div>
          <div class="field"><label>body</label><textarea name="body">${escapeHtml(post.body || "")}</textarea></div>
          <div class="button-row">
            <button class="primary">Save</button>
            <button type="button" data-toggle-post>${post.is_open ? "Close" : "Reopen"}</button>
            <button type="button" class="danger" data-delete-post>Delete</button>
          </div>
        </form>
        <div class="section-title">labels</div>
        <div class="pill-row label-strip">${labels.map((label) => labelChip(label.name, true)).join("") || `<span class="muted">none</span>`}</div>
        <form data-label-form class="button-row">
          ${labelOptions("label", state.meta.labels, labelNames)}
          <input name="customLabel" placeholder="custom label" />
          <button>Add label</button>
        </form>
        <div class="section-title">comments</div>
        ${(post.comments || []).map(renderComment).join("") || `<div class="muted">No comments.</div>`}
        <form data-comment-form>
          <div class="field"><label>new comment</label><textarea name="body"></textarea></div>
          <button class="primary">Comment</button>
        </form>
      </aside>
    `;
  }

  function renderComment(comment) {
    return `
      <article class="comment">
        <div class="comment-meta">#${escapeHtml(comment.id)} ${escapeHtml(comment.author || "unknown")} · ${escapeHtml(comment.updated_at || comment.created_at || "")}</div>
        <div class="comment-body">${escapeHtml(comment.body || "")}</div>
        <div class="reaction-strip">${renderReactionButtons(comment.reactions || [], "comment", comment.id)}</div>
      </article>
    `;
  }

  function labelChip(name, removable = false) {
    if (!removable) {
      return `<span class="pill label-chip"><span>#</span>${escapeHtml(name)}</span>`;
    }
    return `
      <button
        class="pill label-chip removable"
        type="button"
        data-remove-label="${escapeHtml(name)}"
      >
        <span>#</span>${escapeHtml(name)}<span class="chip-x">x</span>
      </button>
    `;
  }

  function renderReactionButtons(reactions, scope, commentId = "") {
    return state.meta.reactions
      .map((kind) => {
        const reaction = (reactions || []).find((item) => item.kind === kind);
        const count = reaction?.count || 0;
        const active = (reaction?.users || []).includes(state.meta.author);
        const attr =
          scope === "comment"
            ? `data-comment-reaction="${escapeHtml(commentId)}"`
            : `data-post-reaction="${escapeHtml(kind)}"`;
        return `
          <button
            type="button"
            class="reaction-chip ${active ? "active" : ""}"
            ${attr}
            data-reaction="${escapeHtml(kind)}"
            title="${active ? "Remove" : "Add"} ${escapeHtml(kind)}"
          >
            <span class="reaction-icon">${reactionIcons[kind] || "?"}</span>
            <span>${escapeHtml(kind.replace("_", " "))}</span>
            <strong>${count}</strong>
          </button>
        `;
      })
      .join("");
  }

  async function onClick(event) {
    const target = event.target.closest("button, [data-open-post]");
    if (!target) return;
    if (target.dataset.refresh !== undefined) return refresh();
    if (target.dataset.new !== undefined) return openNewPost();
    if (target.dataset.filter) {
      state.filter = target.dataset.filter;
      state.selectedId = null;
      return refresh(false);
    }
    if (target.dataset.openPost) return loadPost(target.dataset.openPost);
    if (target.dataset.closeDrawer !== undefined) {
      state.selectedId = null;
      state.selected = null;
      return render();
    }
    if (target.dataset.togglePost !== undefined) return mutate(() => api.togglePost(state.selectedId));
    if (target.dataset.deletePost !== undefined) {
      if (confirm(`Delete post #${state.selectedId}?`)) {
        await mutate(() => api.deletePost(state.selectedId), false);
        state.selectedId = null;
        state.selected = null;
        await refresh(false);
      }
      return;
    }
    if (target.dataset.removeLabel) {
      return mutate(() => api.updateLabel(state.selectedId, "remove", target.dataset.removeLabel));
    }
    if (target.dataset.postReaction) {
      return mutate(() => api.reactToPost(state.selectedId, target.dataset.reaction, true));
    }
    if (target.dataset.commentReaction) {
      return mutate(() => api.reactToComment(state.selectedId, target.dataset.commentReaction, target.dataset.reaction, true));
    }
    if (target.dataset.closeModal !== undefined) {
      document.querySelector("[data-modal]")?.remove();
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    const form = event.target;
    const data = Object.fromEntries(new FormData(form).entries());
    if (form.dataset.savePost !== undefined) return mutate(() => api.savePost(state.selectedId, data));
    if (form.dataset.labelForm !== undefined) {
      const label = String(data.customLabel || data.label || "").trim();
      if (!label) return;
      return mutate(() => api.updateLabel(state.selectedId, "add", label));
    }
    if (form.dataset.commentForm !== undefined) return mutate(() => api.addComment(state.selectedId, data.body));
    if (form.dataset.newPostForm !== undefined) {
      data.labels = [
        ...new FormData(form).getAll("labelChoices"),
        ...String(data.customLabels || "")
          .split(",")
          .map((part) => part.trim())
          .filter(Boolean),
      ];
      const created = await api.createPost(data);
      document.querySelector("[data-modal]")?.remove();
      state.selectedId = created.id;
      await refresh(true);
      toast(`Created post #${created.id}`);
    }
  }

  async function mutate(action, reloadSelected = true) {
    try {
      await action();
      await refresh(reloadSelected);
      toast("Saved");
    } catch (error) {
      onError(error);
    }
  }

  function openNewPost() {
    modal(`
      <form data-new-post-form>
        <h2>New post</h2>
        <div class="field"><label>title</label><input name="title" required autofocus /></div>
        <div class="field"><label>body</label><textarea name="body"></textarea></div>
        <div class="field">
          <label>labels</label>
          <div class="choice-grid">
            ${state.meta.labels.map((label) => `
              <label class="choice-chip">
                <input type="checkbox" name="labelChoices" value="${escapeHtml(label.name)}" />
                <span># ${escapeHtml(label.name)}</span>
              </label>
            `).join("") || `<span class="muted">No saved labels yet.</span>`}
          </div>
          <input name="customLabels" placeholder="extra labels, comma separated" />
        </div>
        <div class="field"><label>assignees</label><input name="assignees" placeholder="comma separated" /></div>
        <div class="button-row">
          <button class="primary">Create</button>
          <button type="button" data-close-modal>Cancel</button>
        </div>
      </form>
    `);
  }

  return { html, mount };
}
