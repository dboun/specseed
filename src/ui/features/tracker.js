import { api } from "./api.js";
import { openAgentOutput } from "./agent_output.js";
import { closeModal, escapeHtml, formatTime, modal, reactionIcon, toast } from "../ui/components.js";
import { renderMarkdown } from "../ui/markdown.js";

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
    editing: false, // drawer title/body edit mode
    addLabelOpen: false, // add-label dropdown stays open across repaints
    seenPostIds: null, // baseline of known post ids; new ids trigger a radar sweep
    seenCommentIds: null, // baseline of comment ids on the open post (radar sweep on new ones)
  };

  async function load() {
    state.meta = await api.meta(repo.id);
    if (repo.provider !== "local") {
      state.external = true;
      return;
    }
    await reloadPosts();
    // Baseline the known posts so the very first paint never sweeps everything;
    // only posts that appear AFTER this get the radar treatment.
    state.seenPostIds = new Set(state.posts.map((p) => String(p.id)));
  }

  async function reloadPosts() {
    // CONTROL is a tracker-comment command channel for github/gitlab. The local
    // provider drives the runner from the Monitor tab (control.json), so CONTROL
    // is noise here - hide it from both the chips and the list entirely.
    const all = (await api.listPosts(repo.id, "all")).filter((p) => p.title !== "CONTROL");
    const managed = new Set(state.meta.default_post_titles || []);
    state.defaults = all.filter((p) => managed.has(p.title));
    state.posts = all.filter((p) => !managed.has(p.title));
  }

  const isManaged = (post) => (state.meta.default_post_titles || []).includes(post?.title);

  // -- identity --------------------------------------------------------- #
  // A post/comment is the platform's when it is authored by the configured
  // platform account AND that account differs from the human (UI) user.
  const isPlatformAuthored = (post) => {
    const pu = state.meta.platform_username;
    return !!pu && post?.author === pu && pu !== state.meta.ui_user;
  };

  // -- filtering + pagination ------------------------------------------- #
  function filtered() {
    const q = state.search.trim().toLowerCase();
    const list = state.posts.filter((p) => {
      if (state.stateFilter === "open" && !p.is_open) return false;
      if (state.stateFilter === "closed" && p.is_open) return false;
      if (state.stateFilter === "need_approval" && !p.needs_approval) return false;
      if (q && !(`#${p.id} ${p.title} ${p.body || ""} ${p.comments_text || ""}`.toLowerCase().includes(q))) return false;
      if (state.labelFilter.size) {
        const names = new Set((p.labels || []).map((l) => l.name));
        for (const want of state.labelFilter) if (!names.has(want)) return false;
      }
      return true;
    });
    // Newest activity first (entry edit OR latest comment), stamped server-side.
    list.sort((a, b) => String(b.last_activity_at || "").localeCompare(String(a.last_activity_at || "")));
    return list;
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
    <div class="tracker-root ${state.selected ? "detail" : ""}" data-tracker-root>
      <div class="tab-head">
        <h1>Tracker</h1>
        <div class="tab-head-actions">
          <span data-queue-dot></span>
          <button class="btn btn-primary" data-new-post>+ New post</button>
        </div>
      </div>
      ${quickToggles()}
      ${filters()}
      <div class="tracker-grid">
        <div class="post-list" data-post-list>${listHtml()}</div>
        <aside class="drawer-host" data-drawer>${drawerHtml()}</aside>
      </div>
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

  const STATE_TABS = { open: "open", closed: "closed", all: "all", need_approval: "approvals" };

  // Split available label names into the human-facing "important" set (kept in the
  // server's order) and the rest (alphabetical), so every picker leads with what a
  // human actually reaches for and tucks the machinery under an "Others:" divider.
  function splitImportant(names) {
    const important = (state.meta.important_labels || []).filter((n) => names.includes(n));
    const seen = new Set(important);
    const others = names.filter((n) => !seen.has(n)).sort();
    return { important, others };
  }
  function labelPicker(names, chip) {
    if (!names.length) return "";
    const { important, others } = splitImportant(names);
    return (
      important.map(chip).join("") +
      (others.length ? `<div class="label-sep">Others:</div>` + others.map(chip).join("") : "")
    );
  }

  function filters() {
    const names = (state.meta.labels || []).map((l) => l.name);
    const filterChip = (n) =>
      `<button class="chip ${state.labelFilter.has(n) ? "on" : ""}" data-label-filter="${escapeHtml(n)}">${escapeHtml(n)}</button>`;
    return `
      <div class="filters">
        <form class="search" data-search-form>
          <input type="search" enterkeyhint="search" name="q" placeholder="search posts…" value="${escapeHtml(state.search)}" />
        </form>
        <div class="seg" data-state-seg>
          ${Object.entries(STATE_TABS)
            .map(([s, label]) => `<button class="seg-btn ${state.stateFilter === s ? "active" : ""}" data-state="${s}">${label}</button>`)
            .join("")}
        </div>
        <details class="label-filter ${state.labelFilter.size ? "filtering" : ""}">
          <summary>labels${state.labelFilter.size ? ` (${state.labelFilter.size})` : ""}</summary>
          <div class="label-filter-menu">
            ${labelPicker(names, filterChip) || `<span class="muted">no labels</span>`}
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
    const isDraft = labels.some((l) => l.name === "draft");
    const cls = [
      "post-card",
      String(post.id) === String(state.selectedId) ? "active" : "",
      isDraft ? "draft" : "",
      post.needs_approval ? "needs-approval" : "",
    ]
      .filter(Boolean)
      .join(" ");
    return `
      <article class="${cls}" data-open-post="${escapeHtml(post.id)}">
        <div class="post-card-top">
          <span class="post-id">#${escapeHtml(post.id)}</span>
          ${post.agent_running ? `<span class="state-dot running" title="agent running"></span>` : ""}
        </div>
        <div class="post-title">${escapeHtml(post.title)}</div>
        <div class="post-meta">${escapeHtml(post.author || "unknown")} · ${escapeHtml(formatTime(post.updated_at))}</div>
        <div class="post-foot">
          <div class="chip-row">${labels.map((l) => `<span class="chip sm">${escapeHtml(l.name)}</span>`).join("")}</div>
          ${post.comment_count ? `<span class="reply-count" title="replies">💬 ${escapeHtml(post.comment_count)}</span>` : ""}
        </div>
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
      <div class="post-meta">by ${escapeHtml(post.author || "unknown")} · updated ${escapeHtml(formatTime(post.updated_at))}</div>`;
  }

  // A post under code review is talking to the REVIEW agent, not a human: a comment
  // there re-runs the reviewer, never the implementer, so the box is locked until
  // review ends. (Bounce work back by approving/commenting once it parks.)
  const inReview = (post) =>
    (post.labels || []).some((l) => /(^|:)status:in_review$/.test(l.name));

  // Ephemeral, our-UI-only: while an agent works this post, an outlined box sits at
  // the end of the comments (just above the composer) and opens the live 'Agent
  // output' popup. It vanishes on its own once the run ends (agent_running clears on
  // the next poll) - nothing kept.
  function agentWorkingBox(post) {
    if (!post.agent_running || !post.agent_task_id) return "";
    return `
      <button type="button" class="agent-working" data-agent-output="${escapeHtml(post.agent_task_id)}">
        <span class="state-dot running"></span>
        <span class="agent-working-text">Agent is working on this…</span>
        <span class="agent-working-cta">View output</span>
      </button>`;
  }

  function commentsBlock(post, composer) {
    const comments = post.comments || [];
    const locked = composer && inReview(post);
    return `
      <div class="section-title">comments</div>
      ${comments.map((c) => commentHtml(c, post)).join("") || `<div class="muted">No comments.</div>`}
      ${agentWorkingBox(post)}
      ${locked
        ? `<div class="muted comment-locked">Comments are locked while this issue is in code review. They reopen once it finishes.</div>`
        : composer
        ? `<form data-comment-form><textarea name="body" placeholder="add a comment…" required></textarea><button class="btn btn-primary">Comment</button></form>`
        : ""}`;
  }

  function managedDrawer(post) {
    return `
      <div class="drawer">
        ${drawerHead(post)}
        <div class="managed-note">Managed dashboard · read-only</div>
        <div class="post-body">${renderMarkdown(post.body || "")}</div>
        ${commentsBlock(post, false)}
      </div>`;
  }

  function editableDrawer(post) {
    const labels = post.labels || [];
    // Platform-authored posts are read-only: no title/body/label edits. Delete,
    // close/reopen, reactions and comments stay open. Removing the `draft` label
    // is the one allowed label change (the explicit "process this draft" action).
    const readOnly = isPlatformAuthored(post);
    const isDraft = labels.some((l) => l.name === "draft");
    return `
      <div class="drawer ${isDraft ? "draft" : ""}">
        ${drawerHead(post)}
        <div class="reaction-strip">${reactionButtons(post.reactions, "post")}</div>
        ${state.editing && !readOnly ? editForm(post) : postView(post, readOnly)}
        <div class="section-title">labels</div>
        <div class="chip-row">
          ${labels
            .map((l) =>
              readOnly
                ? `<span class="chip">${escapeHtml(l.name)}</span>`
                : `<button class="chip removable" data-remove-label="${escapeHtml(l.name)}">${escapeHtml(l.name)} ✕</button>`
            )
            .join("") || `<span class="muted">none</span>`}
        </div>
        ${readOnly ? "" : addLabelDropdown(post)}
        ${isDraft ? draftBox() : ""}
        ${commentsBlock(post, true)}
      </div>`;
  }

  // read view: title lives bold in the drawer head; body renders as markdown
  function postView(post, readOnly) {
    return `
      <div class="post-body">${renderMarkdown(post.body || "")}</div>
      <div class="button-row">
        ${readOnly ? "" : `<button type="button" class="btn btn-ghost" data-edit-post>Edit</button>`}
        <button type="button" class="btn btn-ghost" data-toggle-post>${post.is_open ? "Close" : "Reopen"}</button>
        <button type="button" class="btn btn-danger" data-delete-post>Delete</button>
      </div>`;
  }

  function draftBox() {
    return `
      <div class="draft-box">
        <span class="draft-box-label">Draft.</span>
        <button type="button" class="btn btn-ghost sm" data-remove-draft>Remove draft label to process</button>
      </div>`;
  }

  function editForm(post) {
    return `
      <form data-save-post>
        <div class="field"><label>title</label><input name="title" value="${escapeHtml(post.title)}" /></div>
        <div class="field"><label>body</label><textarea name="body" rows="6">${escapeHtml(post.body || "")}</textarea></div>
        <div class="button-row">
          <button class="btn btn-primary">Save</button>
          <button type="button" class="btn btn-ghost" data-cancel-edit>Cancel</button>
        </div>
      </form>`;
  }

  // multi-select dropdown: each click adds instantly; menu stays open so several
  // labels can be picked in a row (open state survives the mutate repaint).
  function addLabelDropdown(post) {
    const have = new Set((post.labels || []).map((l) => l.name));
    const avail = (state.meta.labels || []).map((l) => l.name).filter((n) => !have.has(n));
    const addChip = (n) => `<button type="button" class="chip" data-add-label="${escapeHtml(n)}">${escapeHtml(n)}</button>`;
    return `
      <details class="label-filter add-label-dd" ${state.addLabelOpen ? "open" : ""} data-add-label-dd>
        <summary>+ add labels</summary>
        <div class="label-filter-menu">
          ${labelPicker(avail, addChip) || `<span class="muted">no labels left</span>`}
        </div>
      </details>`;
  }

  // The approval request is a comment; surface it as a box with approve/reject.
  // EVERY gate votes on its request COMMENT (not the post), so a new gate comment needs
  // a fresh vote and a standing reaction can't re-fire. Kinds: a plain work/HITL gate
  // (👍 approve, 👎 reject); a pure merge gate (👍 merges, 👎 leaves the branch for a
  // manual merge); a combined work+merge gate (❤️ approves AND merges, 👍 approves the
  // work only, 👎 rejects).
  const MERGE_GATE_MARKER = "<!-- specseed:merge-gate -->";
  const WORK_MERGE_GATE_MARKER = "<!-- specseed:work-merge-gate -->";

  const isApprovalComment = (comment) => {
    const b = comment.body || "";
    return /Approval required:/i.test(b) || b.includes(MERGE_GATE_MARKER) || b.includes(WORK_MERGE_GATE_MARKER);
  };

  // "combined" = work+merge (3 buttons); "merge" = pure merge gate; else a plain
  // work/HITL approval gate.
  function gateType(comment) {
    const b = comment.body || "";
    if (b.includes(WORK_MERGE_GATE_MARKER)) return "combined";
    if (b.includes(MERGE_GATE_MARKER)) return "merge";
    return "work";
  }

  function approvalResolution(post, comment) {
    const names = (post.labels || []).map((l) => l.name);
    // Terminal labels win (the runtime moved the issue on).
    if (names.some((n) => n.endsWith(":status:done") || n.endsWith(":status:approved"))) return "approved";
    if (names.some((n) => n.endsWith(":status:rejected"))) return "rejected";
    // Every gate reads the vote off ITS request comment, never the post — a new gate
    // is a new comment with no reactions, so an old approval never carries over.
    const reacted = (k) => ((comment && comment.reactions) || []).some((r) => r.kind === k && r.count > 0);
    if (reacted("thumbs_up") || reacted("heart")) return "approved";
    if (reacted("thumbs_down")) return "rejected";
    return null;
  }

  function approvalButtons(comment) {
    const kind = gateType(comment);
    const cid = escapeHtml(String(comment.id));
    // Every gate votes on THIS request comment (data-gate-react), never the post.
    if (kind === "combined") {
      // ❤️ approve+merge, 👍 approve work only, 👎 reject.
      return `
        <button type="button" class="btn btn-primary" data-gate-react="heart" data-gate-comment="${cid}">Approve &amp; merge</button>
        <button type="button" class="btn" data-gate-react="thumbs_up" data-gate-comment="${cid}">Approve</button>
        <button type="button" class="btn btn-danger" data-gate-react="thumbs_down" data-gate-comment="${cid}">Reject</button>`;
    }
    if (kind === "merge") {
      // pure merge gate: 👍 on this comment merges into primary, 👎 leaves the branch.
      return `
        <button type="button" class="btn btn-primary" data-gate-react="thumbs_up" data-gate-comment="${cid}">Approve merge</button>
        <button type="button" class="btn btn-danger" data-gate-react="thumbs_down" data-gate-comment="${cid}">Decline</button>`;
    }
    // plain work / HITL / spec-change gate.
    return `
      <button type="button" class="btn btn-primary" data-gate-react="thumbs_up" data-gate-comment="${cid}">Approve</button>
      <button type="button" class="btn btn-danger" data-gate-react="thumbs_down" data-gate-comment="${cid}">Reject</button>`;
  }

  function approvalBox(comment, post) {
    const resolved = approvalResolution(post, comment);
    const body = (comment.body || "")
      .split(MERGE_GATE_MARKER).join("")
      .split(WORK_MERGE_GATE_MARKER).join("");
    return `
      <article class="approval-box ${resolved ? "resolved" : ""}" data-comment-id="${escapeHtml(String(comment.id))}">
        <div class="approval-body">${renderMarkdown(body)}</div>
        ${resolved
          ? `<div class="approval-status">${resolved === "approved" ? "✅ Approved" : "🚫 Rejected"}</div>`
          : `<div class="button-row">${approvalButtons(comment)}</div>`}
      </article>`;
  }

  function commentHtml(comment, post) {
    if (isApprovalComment(comment)) return approvalBox(comment, post);
    return `
      <article class="comment" data-comment-id="${escapeHtml(String(comment.id))}">
        <div class="comment-meta">${escapeHtml(comment.author || "unknown")} · ${escapeHtml(formatTime(comment.updated_at || comment.created_at))}</div>
        <div class="comment-body">${renderMarkdown(comment.body || "")}</div>
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

  // -- radar sweep ------------------------------------------------------ #
  // One clockwise scan across `container`; each target glows the instant the
  // sweep line crosses its angle (delay = sweep position). Pure CSS animation +
  // a little geometry, no deps. Self-cleaning, honours reduced-motion.
  const RADAR_DUR = 2200;
  function radarSweep(container, targets) {
    if (!container || !targets.length) return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    container.querySelector(":scope > .radar-sweep")?.remove(); // one sweep at a time
    const sweep = document.createElement("div");
    sweep.className = "radar-sweep";
    sweep.style.setProperty("--radar-dur", `${RADAR_DUR}ms`);
    container.appendChild(sweep);
    const box = container.getBoundingClientRect();
    const cx = box.left + box.width / 2;
    const cy = box.top + box.height / 2;
    for (const el of targets) {
      const r = el.getBoundingClientRect();
      // angle clockwise from 12 o'clock to the target's centre
      let ang = (Math.atan2(r.left + r.width / 2 - cx, cy - (r.top + r.height / 2)) * 180) / Math.PI;
      if (ang < 0) ang += 360;
      el.style.setProperty("--ping-delay", `${Math.round((RADAR_DUR * ang) / 360)}ms`);
      el.classList.remove("radar-ping");
      void el.offsetWidth; // restart the animation if mid-flight
      el.classList.add("radar-ping");
      el.addEventListener("animationend", () => el.classList.remove("radar-ping"), { once: true });
    }
    setTimeout(() => sweep.remove(), RADAR_DUR + 200);
  }

  // -- partial repaint -------------------------------------------------- #
  function repaintList() {
    const host = document.querySelector("[data-post-list]");
    if (host) host.innerHTML = listHtml();
    const qt = document.querySelector(".quick-toggles");
    if (qt) qt.outerHTML = quickToggles();
  }
  // Snapshot the comment composer (value + caret + focus) so a drawer repaint can
  // run WHILE the user is typing without wiping a half-written comment or stealing
  // focus. This is what lets comments stream in live regardless of composer state.
  function captureComposer(host) {
    const ta = host.querySelector("[data-comment-form] textarea");
    if (!ta) return null;
    return {
      value: ta.value,
      start: ta.selectionStart,
      end: ta.selectionEnd,
      focused: document.activeElement === ta,
    };
  }
  function restoreComposer(host, saved) {
    if (!saved) return;
    const ta = host.querySelector("[data-comment-form] textarea");
    if (!ta) return;
    if (saved.value) ta.value = saved.value;
    if (saved.focused) {
      ta.focus({ preventScroll: true });
      try {
        ta.setSelectionRange(saved.start, saved.end);
      } catch {
        /* setSelectionRange unsupported on some input types */
      }
    }
  }
  function repaintDrawer() {
    const host = document.querySelector("[data-drawer]");
    if (host) {
      const composer = captureComposer(host);
      host.innerHTML = drawerHtml();
      restoreComposer(host, composer);
    }
    // On mobile the detail view takes over the content area (CSS hides the list);
    // toggling this class drives that. Desktop layout ignores it.
    document.querySelector("[data-tracker-root]")?.classList.toggle("detail", !!state.selected);
  }

  async function openPost(id) {
    try {
      state.selectedId = id;
      state.selected = await api.getPost(repo.id, id);
      state.editing = false;
      state.addLabelOpen = false;
      // Baseline existing comments so opening a post doesn't sweep its history;
      // only comments that stream in afterwards ping.
      state.seenCommentIds = new Set((state.selected.comments || []).map((c) => String(c.id)));
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
    const ao = t.closest("[data-agent-output]");
    if (ao) return openAgentOutput(repo.id, ao.dataset.agentOutput, { autoClose: true });
    const open = t.closest("[data-open-post]");
    if (open) return openPost(open.dataset.openPost);
    if (t.closest("[data-new-post]")) return openNewPost();
    if (t.closest("[data-close-drawer]")) {
      state.selectedId = null;
      state.selected = null;
      state.editing = false;
      state.addLabelOpen = false;
      repaintDrawer();
      repaintList();
      return;
    }
    if (t.closest("[data-edit-post]")) {
      state.editing = true;
      repaintDrawer();
      return;
    }
    if (t.closest("[data-cancel-edit]")) {
      state.editing = false;
      repaintDrawer();
      return;
    }
    // mirror the native <details> toggle so repaints keep the menu open/closed
    if (t.closest("[data-add-label-dd] > summary")) {
      state.addLabelOpen = !state.addLabelOpen;
      return;
    }
    const add = t.closest("[data-add-label]");
    if (add) return mutate(() => api.updateLabel(repo.id, state.selectedId, "add", add.dataset.addLabel));
    if (t.closest("[data-remove-draft]")) return mutate(() => api.updateLabel(repo.id, state.selectedId, "remove", "draft"));
    const gr = t.closest("[data-gate-react]");
    if (gr) return mutate(() => api.reactComment(repo.id, state.selectedId, gr.dataset.gateComment, gr.dataset.gateReact));
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
      // the filter bar never repaints (keeps the dropdown open) — sync the summary in place
      const dd = lf.closest(".label-filter");
      if (dd) {
        dd.classList.toggle("filtering", state.labelFilter.size > 0);
        const sum = dd.querySelector("summary");
        if (sum) sum.textContent = state.labelFilter.size ? `labels (${state.labelFilter.size})` : "labels";
      }
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
      state.editing = false;
      state.addLabelOpen = false;
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

  // Live search: filtering is client-side (no network), so fire as the user
  // types — Enter/iOS Done key stop mattering. Small debounce avoids repaint churn.
  let searchTimer = null;
  function handleInput(event) {
    const input = event.target.closest("[data-search-form] input");
    if (!input) return;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = input.value;
      state.page = 0;
      repaintList();
    }, 150);
  }

  async function handleSubmit(event) {
    const form = event.target;
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    if (form.dataset.searchForm !== undefined) {
      clearTimeout(searchTimer);
      state.search = String(data.q || "");
      state.page = 0;
      repaintList();
      return;
    }
    if (form.dataset.commentForm !== undefined) {
      return mutate(() => api.addComment(repo.id, state.selectedId, data.body));
    }
    if (form.dataset.savePost !== undefined) {
      state.editing = false;
      return mutate(() => api.savePost(repo.id, state.selectedId, { title: data.title, body: data.body }));
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
    const names = (state.meta.labels || []).map((l) => l.name);
    const human = (state.meta.important_labels || []).filter((n) => names.includes(n));
    const humanSet = new Set(human);
    // type:* and difficulty:* are platform-driven work classifiers, never set by a
    // human at post-creation — keep them out of the new-post form entirely.
    const advanced = names
      .filter((n) => !humanSet.has(n) && !n.startsWith("type:") && !n.startsWith("difficulty:"))
      .sort();
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

  // Auto-refresh, single-flight, only while the tab is visible and no modal is up.
  // List view: repaints just the list + quick toggles — never the filter bar — so
  // search text, open dropdowns and scroll are untouched. Open post: re-fetches it
  // so new comments/labels show up live (guarded by drawerBusy below).
  let timer = null;
  let polling = false;
  // A drawer repaint rebuilds its DOM. The comment composer survives it
  // (capture/restoreComposer), so only the title/body EDIT FORM needs protection -
  // rebuilding it mid-edit would discard the in-progress edit. Everything else
  // (incoming comments, reactions, labels, the agent-working box) repaints freely.
  function drawerBusy() {
    return state.editing;
  }
  // Queue dot in the tab head: glows only while the repo has queued/running work.
  async function refreshQueueDot() {
    const slot = document.querySelector("[data-queue-dot]");
    if (!slot) return;
    try {
      const q = (await api.repo(repo.id)).queue || {};
      const busy = (q.pending || 0) + (q.in_progress || 0) > 0;
      const lanes = q.lanes || {};
      const detail = ["control", "work"]
        .map((lane) => `${lane} ${lanes[lane]?.pending || 0}/${lanes[lane]?.in_progress || 0}`)
        .join(" · ");
      slot.innerHTML = busy ? `<span class="auto-dot" title="tasks queued or running: ${escapeHtml(detail)}"></span>` : "";
    } catch {
      /* transient; next tick retries */
    }
  }

  async function autoRefresh() {
    if (state.external || polling) return;
    if (document.querySelector("[data-modal]") || document.hidden) return;
    polling = true;
    try {
      await refreshQueueDot();
      // Always keep the card list current - even with a post open in the drawer.
      // The list carries no input state, so a repaint is safe every tick; this is
      // what makes cards (and their running-dots) update live as posts change.
      await reloadPosts();
      repaintList();
      // Radar: any post id we hadn't seen last tick gets a sweep + ping (only the
      // cards actually on the current page can be lit; off-page ones just update).
      if (state.seenPostIds) {
        const fresh = state.posts.map((p) => String(p.id)).filter((id) => !state.seenPostIds.has(id));
        if (fresh.length) {
          const cards = fresh
            .map((id) => document.querySelector(`.post-card[data-open-post="${id}"]`))
            .filter(Boolean);
          radarSweep(document.querySelector(".tracker-grid"), cards);
        }
      }
      state.seenPostIds = new Set(state.posts.map((p) => String(p.id)));
      // Additionally refresh the open post so new comments / the agent-working box
      // stream in. Guarded only by an active edit form (see drawerBusy).
      if (state.selectedId && !drawerBusy()) {
        const id = state.selectedId;
        const fresh = await api.getPost(repo.id, id);
        // re-check after the await: the user may have started editing or switched posts
        if (String(state.selectedId) !== String(id) || drawerBusy()) return;
        if (JSON.stringify(fresh) !== JSON.stringify(state.selected)) {
          state.selected = fresh;
          repaintDrawer();
          // Radar: ping comments that arrived since the last tick.
          const ids = (fresh.comments || []).map((c) => String(c.id));
          if (state.seenCommentIds) {
            const added = ids.filter((cid) => !state.seenCommentIds.has(cid));
            if (added.length) {
              const host = document.querySelector("[data-drawer]");
              const els = added.map((cid) => host?.querySelector(`[data-comment-id="${cid}"]`)).filter(Boolean);
              radarSweep(host, els);
            }
          }
          state.seenCommentIds = new Set(ids);
        }
      }
    } catch {
      /* transient; next tick retries */
    } finally {
      polling = false;
    }
  }

  function afterRender() {
    if (!state.external) {
      refreshQueueDot(); // initial paint; the interval keeps it current
      timer = setInterval(autoRefresh, 5000);
    }
  }

  function dispose() {
    if (timer) clearInterval(timer);
    clearTimeout(searchTimer);
  }

  return { load, html, afterRender, handleClick, handleSubmit, handleInput, dispose };
}
