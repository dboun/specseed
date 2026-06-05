import { api } from "./api.js";
import { closeModal, escapeHtml, modal, reactionIcon, relativeTime, toast } from "../ui/components.js";

const PAGE_SIZE = 8;

export function createTracker({ repo, ctx }) {
  const state = {
    meta: null,
    external: false,
    posts: [],
    defaults: [],
    stateFilter: "open",
    search: "",
    labelFilter: new Set(),
    page: 0,
    selectedId: null,
    selected: null,
  };

  async function load() {
    state.meta = await api.meta(repo.id);
    if (repo.provider !== "local") {
      state.external = true;
      return;
    }
    await reloadPosts();
  }

  async function reloadPosts() {
    const all = await api.listPosts(repo.id, "all");
    const managed = new Set(state.meta.default_post_titles || []);
    state.defaults = all.filter((p) => managed.has(p.title));
    state.posts = all.filter((p) => !managed.has(p.title));
  }

  const isManaged = (post) => (state.meta.default_post_titles || []).includes(post?.title);
  const isControl = (post) => post?.title === "CONTROL";

  // -- filtering + pagination ------------------------------------------- #
  function filtered() {
    const q = state.search.trim().toLowerCase();
    return state.posts.filter((p) => {
      if (state.stateFilter === "open" && !p.is_open) return false;
      if (state.stateFilter === "closed" && p.is_open) return false;
      if (q && !(`#${p.id} ${p.title} ${p.body || ""}`.toLowerCase().includes(q))) return false;
      if (state.labelFilter.size) {
        const names = new Set((p.labels || []).map((l) => l.name));
        for (const want of state.labelFilter) if (!names.has(want)) return false;
      }
      return true;
    });
  }

  function pageSlice(list) {
    const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    if (state.page >= pages) state.page = pages - 1;
    const start = state.page * PAGE_SIZE;
    return { items: list.slice(start, start + PAGE_SIZE), pages };
  }

  // -- render ----------------------------------------------------------- #
  function html() {
    if (state.external) return externalHtml();
    return `
      <div class="tab-head">
        <h1>Tracker</h1>
        <div class="tab-head-actions">
          <button class="btn btn-primary" data-new-post>+ New post</button>
          <button class="btn btn-ghost" data-tracker-refresh>Refresh</button>
        </div>
      </div>
      ${quickToggles()}
      ${filters()}
      <div class="tracker-grid">
        <div class="post-list" data-post-list>${listHtml()}</div>
        <aside class="drawer-host" data-drawer>${drawerHtml()}</aside>
      </div>`;
  }

  function externalHtml() {
    const link = state.meta.external_link;
    return `
      <div class="tab-head"><h1>Tracker</h1></div>
      <div class="panel external">
        <div class="external-icon">↗</div>
        <div>
          <div class="external-title">Externally managed</div>
          <p class="muted">This repo's tracker lives on ${escapeHtml(repo.provider)}.</p>
          ${link ? `<a class="btn btn-primary" href="${escapeHtml(link)}" target="_blank" rel="noreferrer">Open issues ↗</a>` : ""}
        </div>
      </div>`;
  }

  function quickToggles() {
    if (!state.defaults.length) return "";
    return `<div class="quick-toggles">${state.defaults
      .map(
        (p) => `<button class="quick-chip ${String(p.id) === String(state.selectedId) ? "active" : ""}"
          data-open-post="${escapeHtml(p.id)}">${escapeHtml(p.title)}</button>`
      )
      .join("")}</div>`;
  }

  function filters() {
    const labels = state.meta.labels || [];
    return `
      <div class="filters">
        <form class="search" data-search-form>
          <input name="q" placeholder="search posts…" value="${escapeHtml(state.search)}" />
        </form>
        <div class="seg" data-state-seg>
          ${["open", "closed", "all"]
            .map((s) => `<button class="seg-btn ${state.stateFilter === s ? "active" : ""}" data-state="${s}">${s}</button>`)
            .join("")}
        </div>
        <details class="label-filter">
          <summary>labels${state.labelFilter.size ? ` · ${state.labelFilter.size}` : ""}</summary>
          <div class="label-filter-menu">
            ${labels
              .map(
                (l) => `<button class="chip ${state.labelFilter.has(l.name) ? "on" : ""}" data-label-filter="${escapeHtml(
                  l.name
                )}">${escapeHtml(l.name)}</button>`
              )
              .join("") || `<span class="muted">no labels</span>`}
          </div>
        </details>
      </div>`;
  }

  function listHtml() {
    const list = filtered();
    const { items, pages } = pageSlice(list);
    if (!items.length) return `<div class="empty-state">No posts match.</div>`;
    return (
      items.map(postCard).join("") +
      `<div class="pager">
        <button class="btn btn-ghost" data-page="prev" ${state.page === 0 ? "disabled" : ""}>← Prev</button>
        <span class="pager-info">page ${state.page + 1} / ${pages} · ${list.length} posts</span>
        <button class="btn btn-ghost" data-page="next" ${state.page >= pages - 1 ? "disabled" : ""}>Next →</button>
      </div>`
    );
  }

  function postCard(post) {
    const labels = post.labels || [];
    return `
      <article class="post-card ${String(post.id) === String(state.selectedId) ? "active" : ""}" data-open-post="${escapeHtml(post.id)}">
        <div class="post-card-top">
          <span class="post-id">#${escapeHtml(post.id)}</span>
          <span class="state-dot ${post.is_open ? "open" : "closed"}"></span>
        </div>
        <div class="post-title">${escapeHtml(post.title)}</div>
        <div class="post-meta">${escapeHtml(post.author || "unknown")} · ${escapeHtml(relativeTime(post.updated_at))}</div>
        <div class="chip-row">${labels.map((l) => `<span class="chip sm">${escapeHtml(l.name)}</span>`).join("")}</div>
      </article>`;
  }

  function drawerHtml() {
    const post = state.selected;
    if (!post) return `<div class="drawer-empty">Select a post to see details.</div>`;
    if (isManaged(post)) return managedDrawer(post);
    return editableDrawer(post);
  }

  function drawerHead(post) {
    return `
      <div class="drawer-top">
        <span class="tag tag-${post.is_open ? "open" : "closed"}">${post.is_open ? "open" : "closed"}</span>
        <button class="icon-btn" data-close-drawer aria-label="close">✕</button>
      </div>
      <h2 class="drawer-title">#${escapeHtml(post.id)} ${escapeHtml(post.title)}</h2>
      <div class="post-meta">by ${escapeHtml(post.author || "unknown")} · updated ${escapeHtml(relativeTime(post.updated_at))}</div>`;
  }

  function commentsBlock(post, composer) {
    const comments = post.comments || [];
    return `
      <div class="section-title">comments</div>
      ${comments.map(commentHtml).join("") || `<div class="muted">No comments.</div>`}
      ${composer
        ? `<form data-comment-form><textarea name="body" placeholder="add a comment…" required></textarea><button class="btn btn-primary">Comment</button></form>`
        : ""}`;
  }

  function managedDrawer(post) {
    return `
      <div class="drawer">
        ${drawerHead(post)}
        <div class="managed-note">Managed dashboard · read-only${isControl(post) ? " · operator comments allowed" : ""}</div>
        <pre class="post-body">${escapeHtml(post.body || "")}</pre>
        ${commentsBlock(post, isControl(post))}
      </div>`;
  }

  function editableDrawer(post) {
    const labels = post.labels || [];
    return `
      <div class="drawer">
        ${drawerHead(post)}
        <div class="reaction-strip">${reactionButtons(post.reactions, "post")}</div>
        <form data-save-post>
          <div class="field"><label>title</label><input name="title" value="${escapeHtml(post.title)}" /></div>
          <div class="field"><label>body</label><textarea name="body" rows="6">${escapeHtml(post.body || "")}</textarea></div>
          <div class="button-row">
            <button class="btn btn-primary">Save</button>
            <button type="button" class="btn btn-ghost" data-toggle-post>${post.is_open ? "Close" : "Reopen"}</button>
            <button type="button" class="btn btn-danger" data-delete-post>Delete</button>
          </div>
        </form>
        <div class="section-title">labels</div>
        <div class="chip-row">
          ${labels
            .map((l) => `<button class="chip removable" data-remove-label="${escapeHtml(l.name)}">${escapeHtml(l.name)} ✕</button>`)
            .join("") || `<span class="muted">none</span>`}
        </div>
        <form class="add-label" data-label-form>
          <input name="label" list="ss-labels" placeholder="add label" />
          <datalist id="ss-labels">${(state.meta.labels || []).map((l) => `<option value="${escapeHtml(l.name)}">`).join("")}</datalist>
          <button class="btn btn-ghost">Add</button>
        </form>
        ${commentsBlock(post, true)}
      </div>`;
  }

  function commentHtml(comment) {
    return `
      <article class="comment">
        <div class="comment-meta">${escapeHtml(comment.author || "unknown")} · ${escapeHtml(relativeTime(comment.updated_at || comment.created_at))}</div>
        <div class="comment-body">${escapeHtml(comment.body || "")}</div>
        <div class="reaction-strip sm">${reactionButtons(comment.reactions, "comment", comment.id)}</div>
      </article>`;
  }

  function reactionButtons(reactions, scope, commentId = "") {
    return (state.meta.reactions || [])
      .map((kind) => {
        const r = (reactions || []).find((x) => x.kind === kind);
        const count = r?.count || 0;
        const attr = scope === "comment" ? `data-comment-react="${escapeHtml(commentId)}"` : `data-post-react="1"`;
        return `<button class="react ${count ? "has" : ""}" ${attr} data-reaction="${escapeHtml(kind)}">
          ${reactionIcon(kind)}<span>${count}</span></button>`;
      })
      .join("");
  }

  // -- partial repaint -------------------------------------------------- #
  function repaintList() {
    const host = document.querySelector("[data-post-list]");
    if (host) host.innerHTML = listHtml();
    const qt = document.querySelector(".quick-toggles");
    if (qt) qt.outerHTML = quickToggles();
  }
  function repaintDrawer() {
    const host = document.querySelector("[data-drawer]");
    if (host) host.innerHTML = drawerHtml();
  }

  async function openPost(id) {
    try {
      state.selectedId = id;
      state.selected = await api.getPost(repo.id, id);
      repaintDrawer();
      repaintList();
    } catch (err) {
      ctx.onError(err);
    }
  }

  async function mutate(action, { reopen = true } = {}) {
    try {
      await action();
      await reloadPosts();
      if (reopen && state.selectedId) state.selected = await api.getPost(repo.id, state.selectedId);
      repaintList();
      repaintDrawer();
      toast("saved", "ok");
    } catch (err) {
      ctx.onError(err);
    }
  }

  // -- events ----------------------------------------------------------- #
  async function handleClick(event) {
    const t = event.target;
    const open = t.closest("[data-open-post]");
    if (open) return openPost(open.dataset.openPost);
    if (t.closest("[data-tracker-refresh]")) {
      await reloadPosts();
      repaintList();
      return;
    }
    if (t.closest("[data-new-post]")) return openNewPost();
    if (t.closest("[data-close-drawer]")) {
      state.selectedId = null;
      state.selected = null;
      repaintDrawer();
      repaintList();
      return;
    }
    const stateSeg = t.closest("[data-state]");
    if (stateSeg) {
      state.stateFilter = stateSeg.dataset.state;
      state.page = 0;
      document.querySelectorAll("[data-state]").forEach((b) => b.classList.toggle("active", b === stateSeg));
      repaintList();
      return;
    }
    const lf = t.closest("[data-label-filter]");
    if (lf) {
      const name = lf.dataset.labelFilter;
      state.labelFilter.has(name) ? state.labelFilter.delete(name) : state.labelFilter.add(name);
      lf.classList.toggle("on");
      state.page = 0;
      repaintList();
      return;
    }
    const pg = t.closest("[data-page]");
    if (pg) {
      state.page += pg.dataset.page === "next" ? 1 : -1;
      if (state.page < 0) state.page = 0;
      repaintList();
      return;
    }
    if (t.closest("[data-toggle-post]")) return mutate(() => api.togglePost(repo.id, state.selectedId));
    if (t.closest("[data-delete-post]")) {
      if (!confirm(`Delete post #${state.selectedId}?`)) return;
      await mutate(() => api.deletePost(repo.id, state.selectedId), { reopen: false });
      state.selectedId = null;
      state.selected = null;
      repaintDrawer();
      return;
    }
    const rm = t.closest("[data-remove-label]");
    if (rm) return mutate(() => api.updateLabel(repo.id, state.selectedId, "remove", rm.dataset.removeLabel));
    const pr = t.closest("[data-post-react]");
    if (pr) return mutate(() => api.reactPost(repo.id, state.selectedId, pr.dataset.reaction));
    const cr = t.closest("[data-comment-react]");
    if (cr) return mutate(() => api.reactComment(repo.id, state.selectedId, cr.dataset.commentReact, cr.dataset.reaction));
    if (t.closest("[data-close]")) closeModal();
    if (t.closest("[data-advanced-labels]")) {
      document.querySelector("[data-advanced-block]")?.classList.toggle("show");
    }
  }

  async function handleSubmit(event) {
    const form = event.target;
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    if (form.dataset.searchForm !== undefined) {
      state.search = String(data.q || "");
      state.page = 0;
      repaintList();
      return;
    }
    if (form.dataset.commentForm !== undefined) {
      return mutate(() => api.addComment(repo.id, state.selectedId, data.body));
    }
    if (form.dataset.savePost !== undefined) {
      return mutate(() => api.savePost(repo.id, state.selectedId, { title: data.title, body: data.body }));
    }
    if (form.dataset.labelForm !== undefined) {
      const label = String(data.label || "").trim();
      if (label) return mutate(() => api.updateLabel(repo.id, state.selectedId, "add", label));
      return;
    }
    if (form.dataset.newPostForm !== undefined) {
      const labels = [...new FormData(form).getAll("labels")];
      try {
        const created = await api.createPost(repo.id, {
          title: data.title,
          body: data.body,
          assignees: data.assignees,
          labels,
        });
        closeModal();
        await reloadPosts();
        repaintList();
        openPost(created.id);
        toast(`created #${created.id}`, "ok");
      } catch (err) {
        ctx.onError(err);
      }
    }
  }

  function openNewPost() {
    const human = state.meta.human_labels || [];
    const advanced = (state.meta.labels || []).map((l) => l.name).filter((n) => !human.includes(n));
    const checkboxes = (names) =>
      names
        .map(
          (n) => `<label class="check-chip"><input type="checkbox" name="labels" value="${escapeHtml(n)}" /><span>${escapeHtml(n)}</span></label>`
        )
        .join("");
    modal(
      `
      <form data-new-post-form>
        <h2>New post</h2>
        <div class="field"><label>title</label><input name="title" required autofocus /></div>
        <div class="field"><label>body</label><textarea name="body" rows="5"></textarea></div>
        <div class="field">
          <label>labels</label>
          <div class="check-grid">${checkboxes(human) || `<span class="muted">no labels</span>`}</div>
          ${advanced.length ? `<button type="button" class="btn btn-ghost sm" data-advanced-labels>+ advanced labels</button>
          <div class="check-grid advanced" data-advanced-block>${checkboxes(advanced)}</div>` : ""}
        </div>
        <div class="field"><label>assignees</label><input name="assignees" placeholder="comma separated" /></div>
        <div class="button-row">
          <button class="btn btn-primary">Create</button>
          <button type="button" class="btn btn-ghost" data-close>Cancel</button>
        </div>
      </form>`,
      { wide: true }
    );
  }

  return { load, html, handleClick, handleSubmit, dispose() {} };
}
