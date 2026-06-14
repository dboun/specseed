import { api } from "./api.js";
import { escapeHtml, relativeTime, formatTime, toast } from "../ui/components.js";
import { renderMarkdown } from "../ui/markdown.js";

// The Spec tab: a reading room over the target's generated spec docs. The engine
// writes whatever it writes under <specseed_dir>/spec; we list whatever exists
// (no schema assumed) and render it. Works for every provider — the spec is
// local regardless of tracker backend.

// The known canon, in the order the spec is actually built (vision -> reqs ->
// architecture -> design -> decisions). That order is real information about the
// docs, not decoration, so it drives the index. Anything unmatched falls under
// "Other files", listed plainly. Match is on the filename stem with a leading
// "todo-" stripped (the runtime stages drafts as todo-<name>).
const CANON = {
  vision: { role: "Vision", blurb: "north star, scope, who it's for", order: 10 },
  srs: { role: "Requirements", blurb: "what the software must do", order: 20 },
  reqs: { role: "Requirements data", blurb: "structured, machine-read", order: 25 },
  sad: { role: "Architecture", blurb: "how the system is shaped", order: 30 },
  sdd: { role: "Design", blurb: "how each part is built", order: 40 },
  adr: { role: "Decisions", blurb: "choices made and why", order: 50 },
};

function classify(file) {
  const stem = file.path.replace(/\.[^.]*$/, "").split("/").pop().toLowerCase();
  const draft = stem.startsWith("todo-");
  const key = draft ? stem.slice(5) : stem;
  const canon = CANON[key];
  return {
    ...file,
    stem,
    draft,
    role: canon ? canon.role : null,
    blurb: canon ? canon.blurb : null,
    order: canon ? canon.order : 1000,
  };
}

// Pull a leading `--- ... ---` frontmatter block off a doc. The runtime stamps
// `settled`/`settled_at` there; surface it as a header badge and never render
// the raw delimiters into the body (markdown would turn them into rules).
function splitFrontmatter(text) {
  const m = /^---\n([\s\S]*?)\n---\n?/.exec(text);
  if (!m) return { meta: {}, body: text };
  const meta = {};
  for (const line of m[1].split("\n")) {
    const at = line.indexOf(":");
    if (at === -1) continue;
    meta[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return { meta, body: text.slice(m[0].length) };
}

// Tiny CSV parser (adr.csv is a decisions table). Handles quoted fields with
// embedded commas/newlines and "" escapes. Stdlib-free, like everything here.
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = false;
      } else field += c;
      continue;
    }
    if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (c !== "\r") field += c;
  }
  if (field.length || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => !(r.length === 1 && r[0].trim() === ""));
}

export function createSpec({ repo, ctx, sub }) {
  const state = {
    dir: "",
    exists: false,
    files: [], // classified, sorted
    selected: sub || null, // open doc path
    content: null, // { path, text, ext, size, mtime, ... }
    missing: false, // selected vanished from disk
    seen: {}, // path -> mtime, for change detection across polls
  };

  function indexFiles(list) {
    return (list || [])
      .map(classify)
      .sort((a, b) => a.order - b.order || a.path.localeCompare(b.path));
  }

  async function load() {
    const data = await api.spec(repo.id);
    state.dir = data.dir;
    state.exists = data.exists;
    state.files = indexFiles(data.files);
    state.seen = Object.fromEntries(state.files.map((f) => [f.path, f.mtime]));
    // Resolve the initial selection: a deep-linked file if it exists, else the
    // first canon doc, so the pane is never blank on a populated spec.
    const want = state.selected && state.files.find((f) => f.path === state.selected);
    const pick = want || state.files[0];
    if (pick) await select(pick.path, { silent: true });
    else state.selected = null;
  }

  // write=false when reacting to a back/forward (URL already correct); silent
  // refinements (initial load / poll re-read) replace rather than push.
  async function select(path, { silent = false, write = true } = {}) {
    state.selected = path;
    state.missing = false;
    try {
      state.content = await api.specFile(repo.id, path);
    } catch (err) {
      state.content = null;
      state.missing = true;
      if (!silent) ctx.onError(err);
    }
    if (write) ctx.setSub(path, { replace: silent }); // keep the URL on the open doc
  }

  // Back/forward landed on a spec subroute (= open doc path).
  function onSubRoute(next) {
    const path = next || "";
    if (path === state.selected || (path && !state.files.some((f) => f.path === path))) return;
    if (path)
      select(path, { write: false }).then(() => {
        repaintIndex();
        repaintPane();
      });
  }

  // -- render ----------------------------------------------------------- #
  function html() {
    return `
    <div class="spec-root ${state.selected ? "reading" : ""}" data-spec-root>
      <div class="tab-head">
        <h1>Spec</h1>
        <div class="tab-head-actions">
          ${state.exists ? `<span class="spec-live" title="re-checks the spec dir every 5s"><span class="auto-dot"></span>watching</span>` : ""}
        </div>
      </div>
      ${headerHtml()}
      ${state.exists && state.files.length ? gridHtml() : emptyHtml()}
    </div>`;
  }

  function headerHtml() {
    if (!state.exists) return "";
    const n = state.files.length;
    return `<div class="spec-bar">
      <button class="spec-dir" data-copy-dir title="copy spec path">
        <span class="spec-dir-icon">▍</span>${escapeHtml(state.dir)}
      </button>
      <span class="spec-count">${n} ${n === 1 ? "document" : "documents"}</span>
    </div>`;
  }

  function emptyHtml() {
    if (!state.exists) {
      return `<div class="spec-empty">
        <div class="spec-empty-mark">▍</div>
        <div class="spec-empty-title">No spec yet</div>
        <p class="muted">The engine writes spec docs to <code>${escapeHtml(state.dir || "&lt;specseed_dir&gt;/spec")}</code> once a spec-change run is approved.</p>
      </div>`;
    }
    return `<div class="spec-empty">
      <div class="spec-empty-mark">▍</div>
      <div class="spec-empty-title">Spec dir is empty</div>
      <p class="muted">No files under <code>${escapeHtml(state.dir)}</code> yet.</p>
    </div>`;
  }

  function gridHtml() {
    return `<div class="spec-grid">
      <nav class="spec-index" data-spec-index>${indexHtml()}</nav>
      <section class="spec-pane" data-spec-pane>${paneHtml()}</section>
    </div>`;
  }

  function indexHtml() {
    const canon = state.files.filter((f) => f.role);
    const other = state.files.filter((f) => !f.role);
    let out = "";
    if (canon.length) out += `<div class="spec-spine">${canon.map(indexItem).join("")}</div>`;
    if (other.length) {
      out += `<div class="spec-group-label">Other files</div>`;
      out += `<div class="spec-others">${other.map(indexItem).join("")}</div>`;
    }
    return out;
  }

  function indexItem(file) {
    const active = file.path === state.selected;
    const primary = file.role || file.path;
    const secondary = file.role ? file.path : file.ext || "file";
    return `<button class="spec-item ${active ? "active" : ""} ${file.role ? "canon" : ""}"
      data-open-doc="${escapeHtml(file.path)}" title="${escapeHtml(file.path)}">
      <span class="spec-node"></span>
      <span class="spec-item-main">
        <span class="spec-item-name">${escapeHtml(primary)}${file.draft ? ` <span class="spec-draft-tag">draft</span>` : ""}</span>
        <span class="spec-item-sub">${escapeHtml(secondary)}</span>
      </span>
      <span class="spec-item-time" title="updated ${escapeHtml(formatTime(file.mtime))}">${escapeHtml(relativeTime(file.mtime))}</span>
    </button>`;
  }

  function paneHtml() {
    if (!state.selected) return `<div class="spec-pane-empty">Select a document to read it.</div>`;
    const file = state.files.find((f) => f.path === state.selected);
    const c = state.content;
    const removed = state.missing
      ? `<div class="banner banner-warn">This document is no longer in the spec dir.</div>`
      : "";
    if (!c) return `<div class="spec-pane-empty">${removed || "Could not load this document."}</div>`;
    const { meta, body } = splitFrontmatter(c.text);
    return `
      ${removed}
      <header class="spec-doc-head">
        ${file?.role ? `<div class="spec-doc-role">${escapeHtml(file.role)}</div>` : ""}
        <div class="spec-doc-title">
          <span class="spec-doc-name">${escapeHtml(c.path)}</span>
          <button class="spec-link" data-copy-link title="copy link to this document">link</button>
        </div>
        <div class="spec-doc-meta">
          ${settledBadge(meta)}
          <span>${escapeHtml(fmtSize(c.size))}</span>
          <span>·</span>
          <span title="${escapeHtml(formatTime(c.mtime))}">updated ${escapeHtml(relativeTime(c.mtime))}</span>
          ${c.truncated ? `<span class="spec-trunc">· truncated</span>` : ""}
        </div>
      </header>
      <div class="spec-doc-body" data-spec-body>${renderBody(c.ext, body)}</div>`;
  }

  function settledBadge(meta) {
    if (String(meta.settled).toLowerCase() === "true") {
      const when = meta.settled_at ? ` ${escapeHtml(meta.settled_at)}` : "";
      return `<span class="spec-flag settled">✓ settled${when}</span>`;
    }
    if ("settled" in meta) return `<span class="spec-flag draft">draft</span>`;
    return "";
  }

  function renderBody(ext, body) {
    if (ext === "md" || ext === "markdown" || ext === "txt" || ext === "") return renderMarkdown(body);
    if (ext === "csv") return csvTable(body);
    if (ext === "json") {
      let pretty = body;
      try {
        pretty = JSON.stringify(JSON.parse(body), null, 2);
      } catch {
        /* not valid JSON — show as-is */
      }
      return `<pre class="spec-code">${escapeHtml(pretty)}</pre>`;
    }
    return `<pre class="spec-code">${escapeHtml(body)}</pre>`;
  }

  function csvTable(body) {
    const rows = parseCsv(body);
    if (!rows.length) return `<div class="spec-pane-empty">Empty table.</div>`;
    const [head, ...rest] = rows;
    return `<div class="spec-table-wrap"><table class="spec-table">
      <thead><tr>${head.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr></thead>
      <tbody>${rest
        .map((r) => `<tr>${head.map((_, i) => `<td>${escapeHtml(r[i] ?? "")}</td>`).join("")}</tr>`)
        .join("")}</tbody>
    </table></div>`;
  }

  function fmtSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  // -- partial repaint -------------------------------------------------- #
  function repaintIndex() {
    const host = document.querySelector("[data-spec-index]");
    if (host) host.innerHTML = indexHtml();
  }
  function repaintPane({ flash = false } = {}) {
    const host = document.querySelector("[data-spec-pane]");
    if (!host) return;
    host.innerHTML = paneHtml();
    if (flash) {
      const body = host.querySelector("[data-spec-body]");
      if (body) {
        body.classList.remove("spec-flash");
        void body.offsetWidth; // restart the animation if mid-flight
        body.classList.add("spec-flash");
      }
    }
    document.querySelector("[data-spec-root]")?.classList.toggle("reading", !!state.selected);
  }

  // -- events ----------------------------------------------------------- #
  async function handleClick(event) {
    const open = event.target.closest("[data-open-doc]");
    if (open) {
      if (open.dataset.openDoc === state.selected) return;
      await select(open.dataset.openDoc);
      repaintIndex();
      repaintPane();
      return;
    }
    if (event.target.closest("[data-copy-dir]")) return copy(state.dir, "spec path copied");
    if (event.target.closest("[data-copy-link]")) {
      const url = `${location.origin}${location.pathname}#${encodeURIComponent(repo.id)}/spec/${state.selected
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`;
      return copy(url, "link copied");
    }
  }

  async function copy(text, ok) {
    try {
      await navigator.clipboard.writeText(text);
      toast(ok, "ok");
    } catch {
      toast("copy failed — select and copy manually", "error");
    }
  }

  // -- polling ---------------------------------------------------------- #
  // Fixed 5s sweep of the spec dir. The index repaints when the file set or any
  // mtime changes; the open doc re-fetches + flashes when its own mtime moves.
  // Single-flight, paused while a tab is hidden or a modal is up.
  let timer = null;
  let polling = false;
  async function poll() {
    if (polling || document.hidden || document.querySelector("[data-modal]")) return;
    polling = true;
    try {
      const data = await api.spec(repo.id);
      const files = indexFiles(data.files);
      const nextSeen = Object.fromEntries(files.map((f) => [f.path, f.mtime]));
      const changed =
        data.exists !== state.exists ||
        JSON.stringify(nextSeen) !== JSON.stringify(state.seen);
      if (!changed) return;
      const selectedMtimeMoved = state.selected && nextSeen[state.selected] !== state.seen[state.selected];
      state.exists = data.exists;
      state.files = files;
      state.seen = nextSeen;
      // Full structural change (dir appeared/emptied) needs the whole tab back.
      const host = document.querySelector("[data-spec-index]");
      if (!host) {
        const content = document.querySelector("[data-spec-root]")?.parentElement;
        if (content) content.innerHTML = html();
        return;
      }
      repaintIndex();
      if (state.selected && nextSeen[state.selected] && selectedMtimeMoved) {
        await select(state.selected, { silent: true });
        repaintPane({ flash: true });
      } else if (state.selected && !nextSeen[state.selected]) {
        state.missing = true;
        repaintPane();
      }
    } catch {
      /* transient; next tick retries */
    } finally {
      polling = false;
    }
  }

  function afterRender() {
    timer = setInterval(poll, 5000);
  }

  function dispose() {
    if (timer) clearInterval(timer);
  }

  return { load, html, afterRender, handleClick, dispose, onSubRoute };
}
