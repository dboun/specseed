import { api } from "./api.js";
import { escapeHtml, relativeTime, formatTime, toast, modal, closeModal } from "../ui/components.js";
import { renderMarkdown } from "../ui/markdown.js";
import { loadPrism, langFor, highlightLines } from "../ui/highlight.js";

// The Code tab: a read-only browser over the target repo's OWN git. No remote is
// assumed or contacted - we read committed trees, so untracked/gitignored files
// never show. Everything is addressed by a ref (branch/tag/sha) so a file or a
// commit is directly deep-linkable, and two commits can be compared by URL.
//
// Subroute grammar (after #<repo>/code/):
//   ""                         -> repo root at the default branch
//   tree/<ref>/<path...>       -> a directory
//   blob/<ref>/<path...>       -> a file
//   commits/<ref>              -> history
//   commit/<sha>               -> one commit + its diff
//   compare/<base>..<head>     -> diff between two commits
// The ref/sha is encodeURIComponent'd so a slashy branch survives as one segment.

const MAX_BLOB_LINES = 5000; // guard the DOM on a huge file (bytes already capped server-side)

function parseSub(sub) {
  const parts = String(sub || "").split("/").filter(Boolean);
  const dec = (s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  };
  const [head, ...rest] = parts;
  if (head === "blob" && rest.length) return { view: "blob", ref: dec(rest[0]), path: rest.slice(1).join("/") };
  if (head === "tree" && rest.length) return { view: "tree", ref: dec(rest[0]), path: rest.slice(1).join("/") };
  if (head === "commits") return { view: "commits", ref: rest[0] ? dec(rest[0]) : "", path: "" };
  if (head === "commit" && rest[0]) return { view: "commit", sha: dec(rest[0]) };
  if (head === "compare" && rest[0]) {
    const [b, h] = rest[0].split("..");
    return { view: "compare", base: dec(b || ""), head: dec(h || "") };
  }
  return { view: "tree", ref: "", path: "" };
}

export function createCode({ repo, ctx, sub }) {
  const state = {
    meta: null, // { is_git, head, default_ref, branches, tags, empty, ... }
    route: parseSub(sub),
    data: null, // view payload (tree|blob|commits|commit|compare)
    error: null,
    loading: false,
    moreLoading: false,
    wrap: localStorage.getItem("ss.code.wrap") !== "0", // default: wrap on
    worktree: false, // opt-in: show live on-disk (staged/uncommitted) files. default OFF
    untracked: false, // opt-in: list straight from disk incl. gitignored files. default OFF
    container: null, // the tab content host, for re-paint on in-tab navigation
    root: null, // the .code-root inside it (re-found after every paint)
  };

  const refOf = () => state.route.ref || state.meta?.default_ref || "HEAD";

  function routeToSub(r) {
    const e = encodeURIComponent;
    if (r.view === "blob") return `blob/${e(r.ref)}${r.path ? "/" + r.path : ""}`;
    if (r.view === "commits") return `commits/${e(r.ref)}`;
    if (r.view === "commit") return `commit/${e(r.sha)}`;
    if (r.view === "compare") return `compare/${e(r.base)}..${e(r.head)}`;
    if (!r.path && r.ref === state.meta?.default_ref) return ""; // clean root URL
    return `tree/${e(r.ref)}${r.path ? "/" + r.path : ""}`;
  }

  // The working-tree toggle only applies to the checked-out branch (its on-disk
  // state); for any other ref it's silently inert, so committed trees still show.
  const refIsWorktree = (ref) => !!state.meta?.worktree_branch && ref === state.meta.worktree_branch;
  const canWorktree = () =>
    !!state.meta?.dirty && ["tree", "blob"].includes(state.route.view) && refIsWorktree(refOf());
  // Untracked browsing reads the live disk, so (unlike working-tree) it's offered
  // whenever we're on the checked-out branch — gitignored files exist even when
  // `git status` is clean.
  const canUntracked = () => ["tree", "blob"].includes(state.route.view) && refIsWorktree(refOf());

  async function fetchRoute(r) {
    const ref = r.ref || state.meta?.default_ref;
    const onBranch = refIsWorktree(ref);
    const ut = state.untracked && onBranch;
    const wt = (state.worktree || ut) && onBranch; // untracked implies live
    if (r.view === "blob") return { blob: await api.codeBlob(repo.id, ref, r.path, wt, ut) };
    if (r.view === "commits") return { commits: await api.codeCommits(repo.id, ref) };
    if (r.view === "commit") return { commit: await api.codeCommit(repo.id, r.sha) };
    if (r.view === "compare") return { compare: await api.codeCompare(repo.id, r.base, r.head) };
    return { tree: await api.codeTree(repo.id, ref, r.path, wt, ut) };
  }

  // The one navigation primitive: set the route, fetch its data, repaint. The
  // server echoes the resolved ref back (default branch -> a concrete name) so
  // crumbs and links stay stable. `push` controls history: true = a real
  // navigation (grows the back-stack), "replace" = a refinement (initial load,
  // a toggle), false = reacting to a back/forward (don't touch history).
  async function go(route, { initial = false, push = true } = {}) {
    state.route = route;
    state.error = null;
    state.loading = true;
    if (!initial) paint();
    try {
      const data = await fetchRoute(route);
      const resolved = data.tree?.ref || data.blob?.ref || data.commits?.ref;
      if (resolved) state.route.ref = resolved;
      state.data = data;
      await highlightBlob(data.blob, route.path);
      // diff views highlight per-line at paint time — make sure Prism is ready
      if (route.view === "commit" || route.view === "compare") {
        try {
          await loadPrism();
        } catch {
          /* highlighting optional; diff falls back to plain escaped text */
        }
      }
    } catch (err) {
      state.error = err.message || String(err);
      state.data = null;
    } finally {
      state.loading = false;
    }
    if (push !== false) ctx.setSub(routeToSub(state.route), { replace: initial || push === "replace" });
    if (!initial) {
      paint();
      state.root?.scrollIntoView?.({ block: "nearest" });
    }
  }

  // Back/forward landed on a code subroute — re-derive the view in place without
  // re-writing history (the URL is already correct).
  function onSubRoute(sub) {
    return go(parseSub(sub), { push: false });
  }

  async function load() {
    try {
      state.meta = await api.codeMeta(repo.id);
    } catch (err) {
      state.meta = { is_git: false, error: err.message };
      return;
    }
    if (!state.meta.is_git || state.meta.empty) return;
    await go(state.route, { initial: true });
  }

  // -- render ----------------------------------------------------------- #
  function html() {
    return `<div class="code-root ${state.wrap ? "wrap" : "nowrap"}" data-code-root>
      ${headHtml()}
      ${bodyHtml()}
    </div>`;
  }

  function headHtml() {
    const showRef = ["tree", "blob", "commits"].includes(state.route.view) && state.meta?.is_git && !state.meta?.empty;
    const showWrap = ["blob", "commit", "compare"].includes(state.route.view);
    return `<div class="tab-head">
      <h1>Code</h1>
      <div class="tab-head-actions">
        ${canWorktree() ? worktreeToggleHtml() : ""}
        ${canUntracked() ? untrackedToggleHtml() : ""}
        ${showRef ? refSwitcherHtml() : ""}
        ${showWrap ? wrapToggleHtml() : ""}
      </div>
    </div>`;
  }

  // Opt-in: surfaces only when the checked-out branch has uncommitted work. Lets
  // the user peek at staged/on-disk files the committed tree doesn't carry.
  function worktreeToggleHtml() {
    return `<label class="code-wt-toggle" title="show live, uncommitted files on disk for ${escapeHtml(state.meta.worktree_branch)}">
      <input type="checkbox" data-toggle-worktree ${state.worktree ? "checked" : ""} />
      <span>Working tree</span>
    </label>`;
  }

  // Opt-in: lists straight from disk, gitignored files included (e.g. an
  // untracked data dir the committed tree never carries). Checked-out branch only.
  function untrackedToggleHtml() {
    return `<label class="code-wt-toggle" title="list everything on disk for ${escapeHtml(state.meta.worktree_branch)}, including gitignored files">
      <input type="checkbox" data-toggle-untracked ${state.untracked ? "checked" : ""} />
      <span>Include untracked</span>
    </label>`;
  }

  function refSwitcherHtml() {
    const ref = refOf();
    const group = (label, names) =>
      names.length
        ? `<div class="code-ref-group">${escapeHtml(label)}</div>` +
          names
            .map(
              (n) =>
                `<button class="code-ref-item ${n === ref ? "active" : ""}" data-pick-ref="${escapeHtml(n)}">${escapeHtml(n)}</button>`
            )
            .join("")
        : "";
    const total = (state.meta.branches || []).length + (state.meta.tags || []).length;
    return `<details class="code-ref" data-code-ref>
      <summary><span class="code-ref-icon">⎇</span><span class="code-ref-current">${escapeHtml(ref)}</span><span class="caret">▾</span></summary>
      <div class="code-ref-menu">
        ${total > 6 ? `<input class="code-ref-search" type="search" placeholder="Search branches…" data-ref-search aria-label="Search branches" />` : ""}
        <div class="code-ref-scroll" data-ref-scroll>
          ${group("Branches", state.meta.branches || [])}
          ${group("Tags", state.meta.tags || [])}
        </div>
      </div>
    </details>`;
  }

  function wrapToggleHtml() {
    return `<div class="seg code-wrap" role="group" aria-label="line wrapping">
      <button class="seg-btn ${state.wrap ? "active" : ""}" data-wrap="wrap" aria-pressed="${state.wrap}">Wrap</button>
      <button class="seg-btn ${state.wrap ? "" : "active"}" data-wrap="scroll" aria-pressed="${!state.wrap}">Scroll</button>
    </div>`;
  }

  function bodyHtml() {
    if (!state.meta || !state.meta.is_git) return notGitHtml();
    if (state.meta.empty) return emptyRepoHtml();
    if (state.loading && !state.data) return `<div class="loading">loading…</div>`;
    if (state.error) return errorHtml();
    switch (state.route.view) {
      case "blob":
        return blobHtml();
      case "commits":
        return commitsHtml();
      case "commit":
        return commitHtml();
      case "compare":
        return compareHtml();
      default:
        return treeHtml();
    }
  }

  function notGitHtml() {
    return `<div class="code-empty">
      <div class="code-empty-mark">⎇</div>
      <div class="code-empty-title">Not a git repository</div>
      <p class="muted">The Code tab reads the target repo's local git. <code>${escapeHtml(repo.target || "")}</code> isn't a git repo${state.meta?.error ? ` (${escapeHtml(state.meta.error)})` : ""}.</p>
    </div>`;
  }

  function emptyRepoHtml() {
    return `<div class="code-empty">
      <div class="code-empty-mark">⎇</div>
      <div class="code-empty-title">No commits yet</div>
      <p class="muted">This repo has no history to browse.</p>
    </div>`;
  }

  function errorHtml() {
    return `<div class="banner banner-warn">${escapeHtml(state.error)}</div>${barHtml()}`;
  }

  // breadcrumb + ref + history affordances; shared by tree/blob
  function barHtml({ trailingFile = false } = {}) {
    const ref = refOf();
    const path = state.route.path || "";
    const segs = path ? path.split("/") : [];
    const crumbs = [`<button class="code-crumb" data-nav-root>${escapeHtml(repo.name)}</button>`];
    let acc = "";
    segs.forEach((seg, i) => {
      acc = acc ? `${acc}/${seg}` : seg;
      const last = i === segs.length - 1;
      crumbs.push(`<span class="code-crumb-sep">/</span>`);
      if (last && trailingFile) {
        crumbs.push(`<span class="code-crumb current">${escapeHtml(seg)}</span>`);
      } else {
        crumbs.push(`<button class="code-crumb" data-nav-tree="${escapeHtml(acc)}">${escapeHtml(seg)}</button>`);
      }
    });
    const live = state.data?.tree?.working_tree || state.data?.blob?.working_tree;
    const ut = state.data?.tree?.untracked || state.data?.blob?.untracked;
    return `<div class="code-bar">
      <div class="code-crumbs">${crumbs.join("")}</div>
      <div class="code-bar-actions">
        <span class="code-ref-pill mono" title="viewing ref">${escapeHtml(ref)}</span>
        ${live ? `<span class="code-wt-pill" title="${ut ? "live on-disk view — gitignored files included" : "live working tree — includes staged & uncommitted files on disk"}">${ut ? "on-disk · untracked" : "working tree"}</span>` : ""}
        ${compareBtnHtml()}
        <button class="btn sm btn-ghost" data-history title="commit history for ${escapeHtml(ref)}">History</button>
        <button class="btn sm btn-ghost" data-copy-link title="copy a deep link to this view">Link</button>
      </div>
    </div>`;
  }

  const compareBtnHtml = () =>
    `<button class="btn sm btn-ghost" data-open-compare title="compare two branches/commits">Compare</button>`;

  function treeHtml() {
    const t = state.data?.tree;
    if (!t) return barHtml();
    const rows = t.entries.length
      ? t.entries.map(entryRow).join("")
      : `<div class="code-empty-row">empty directory</div>`;
    return `${barHtml()}
      <div class="code-list">${rows}</div>
      ${t.readme ? readmeHtml(t.readme) : ""}`;
  }

  function entryRow(e) {
    const dir = e.type === "tree";
    const attr = dir ? `data-nav-tree="${escapeHtml(e.path)}"` : `data-nav-blob="${escapeHtml(e.path)}"`;
    return `<button class="code-entry ${dir ? "is-dir" : "is-file"}" ${attr}>
      <span class="code-entry-icon">${dir ? "▸" : "·"}</span>
      <span class="code-entry-name">${escapeHtml(e.name)}</span>
      <span class="code-entry-size">${dir ? "" : fmtSize(e.size)}</span>
    </button>`;
  }

  function readmeHtml(readme) {
    return `<section class="code-readme">
      <div class="code-readme-head"><span class="panel-title">Readme</span><span class="mono muted">${escapeHtml(readme.name)}</span></div>
      <div class="code-readme-body md-host">${renderMarkdown(readme.text)}</div>
      ${readme.truncated ? `<div class="code-trunc">readme truncated</div>` : ""}
    </section>`;
  }

  function blobHtml() {
    const b = state.data?.blob;
    if (!b) return barHtml({ trailingFile: true });
    return `${barHtml({ trailingFile: true })}
      <div class="code-file-meta mono">
        <span>${escapeHtml(fmtSize(b.size))}</span>
        <span>·</span>
        <span>${b.binary ? "binary" : pluralLines(b.text)}</span>
        ${b.ext ? `<span>·</span><span>${escapeHtml(b.ext)}</span>` : ""}
        ${b.truncated ? `<span class="code-trunc">· truncated</span>` : ""}
      </div>
      ${renderBlob(b)}`;
  }

  // Best-effort syntax highlighting: resolve a language from the ext, lazy-load
  // Prism, tokenize into per-line HTML, cache it on the blob (so wrap-toggle
  // repaints reuse it). Any failure leaves _hl unset -> plain escaped fallback.
  async function highlightBlob(b, path) {
    if (!b || b.binary || !b.text) return;
    const lang = langFor(b.ext, path);
    if (!lang) return;
    try {
      await loadPrism();
      b._hl = highlightLines(b.text, lang) || undefined;
    } catch {
      /* highlighting is optional; fall back to plain */
    }
  }

  function renderBlob(b) {
    if (b.binary) return `<div class="code-binary">Binary file — ${escapeHtml(fmtSize(b.size))}, not shown.</div>`;
    let lines = b.text.split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop(); // drop trailing-newline ghost line
    let note = "";
    if (lines.length > MAX_BLOB_LINES) {
      note = `<div class="code-trunc">showing first ${MAX_BLOB_LINES} of ${lines.length} lines</div>`;
      lines = lines.slice(0, MAX_BLOB_LINES);
    }
    const hl = b._hl; // per-line pre-highlighted HTML, aligned 1:1 with `lines`
    const rows = lines
      .map((ln, i) => {
        const lc = hl && hl[i] != null ? hl[i] || " " : escapeHtml(ln) || " ";
        return `<div class="code-line"><span class="ln">${i + 1}</span><span class="lc">${lc}</span></div>`;
      })
      .join("");
    return `<div class="code-blob">${rows}</div>${note}`;
  }

  function commitsHtml() {
    const c = state.data?.commits;
    if (!c) return commitsBarHtml();
    const rows = c.items.length ? c.items.map(commitRow).join("") : `<div class="code-empty-row">no commits</div>`;
    const more = c.has_more
      ? `<button class="btn btn-ghost code-more" data-load-more ${state.moreLoading ? "disabled" : ""}>${state.moreLoading ? "loading…" : `Load more (${c.total - c.items.length} older)`}</button>`
      : "";
    return `${commitsBarHtml()}
      <div class="code-commit-list">${rows}</div>
      ${more}`;
  }

  function commitsBarHtml() {
    const ref = refOf();
    const c = state.data?.commits;
    return `<div class="code-bar">
      <div class="code-crumbs">
        <button class="code-crumb" data-back-tree>${escapeHtml(repo.name)}</button>
        <span class="code-crumb-sep">/</span>
        <span class="code-crumb current">history</span>
      </div>
      <div class="code-bar-actions">
        <span class="code-ref-pill mono">${escapeHtml(ref)}</span>
        ${c ? `<span class="mono muted">${c.total} commit${c.total === 1 ? "" : "s"}</span>` : ""}
      </div>
    </div>`;
  }

  function commitRow(cm) {
    return `<button class="commit-row" data-nav-commit="${escapeHtml(cm.short)}">
      <span class="commit-sha mono">${escapeHtml(cm.short)}</span>
      <span class="commit-main">
        <span class="commit-subject">${escapeHtml(cm.subject)}</span>
        <span class="commit-sub">${escapeHtml(cm.author)} · <span title="${escapeHtml(formatTime(cm.date))}">${escapeHtml(relativeTime(cm.date))}</span></span>
      </span>
      ${cm.parents.length > 1 ? `<span class="commit-merge mono" title="merge commit">merge</span>` : ""}
    </button>`;
  }

  function commitHtml() {
    const cm = state.data?.commit;
    if (!cm) return "";
    const compareHead = state.meta?.default_ref;
    const canCompare = compareHead && compareHead !== cm.short && compareHead !== cm.sha;
    return `<div class="code-bar">
        <div class="code-crumbs">
          <button class="code-crumb" data-back-tree>${escapeHtml(repo.name)}</button>
          <span class="code-crumb-sep">/</span>
          <button class="code-crumb" data-back-history>history</button>
          <span class="code-crumb-sep">/</span>
          <span class="code-crumb current mono">${escapeHtml(cm.short)}</span>
        </div>
        <div class="code-bar-actions">
          ${canCompare ? `<button class="btn sm btn-ghost" data-compare="${escapeHtml(cm.sha)}|${escapeHtml(compareHead)}" title="diff this commit against ${escapeHtml(compareHead)}">Compare → ${escapeHtml(compareHead)}</button>` : ""}
          ${compareBtnHtml()}
          <button class="btn sm btn-ghost" data-copy-link title="copy a deep link to this commit">Link</button>
        </div>
      </div>
      <header class="commit-head">
        <div class="commit-head-title">${escapeHtml(cm.subject)}</div>
        <div class="commit-head-meta mono">
          <span>${escapeHtml(cm.author)}</span><span>·</span>
          <span title="${escapeHtml(formatTime(cm.date))}">${escapeHtml(relativeTime(cm.date))}</span><span>·</span>
          <span class="commit-sha-full">${escapeHtml(cm.short)}</span>
        </div>
        ${cm.body ? `<div class="commit-body">${escapeHtml(cm.body)}</div>` : ""}
      </header>
      ${fileSummaryHtml(cm.files)}
      ${renderPatch(cm.patch, cm.truncated)}`;
  }

  function compareHtml() {
    const cp = state.data?.compare;
    if (!cp) return "";
    return `<div class="code-bar">
        <div class="code-crumbs">
          <button class="code-crumb" data-back-tree>${escapeHtml(repo.name)}</button>
          <span class="code-crumb-sep">/</span>
          <span class="code-crumb current">compare</span>
        </div>
        <div class="code-bar-actions">
          <button class="btn sm btn-ghost" data-copy-link>Link</button>
        </div>
      </div>
      <header class="commit-head">
        <div class="compare-range mono">
          <span class="code-ref-pill">${escapeHtml(cp.base)}</span>
          <span class="compare-arrow">→</span>
          <span class="code-ref-pill">${escapeHtml(cp.head)}</span>
        </div>
        <div class="commit-head-meta mono">
          <span>${cp.ahead} commit${cp.ahead === 1 ? "" : "s"}</span><span>·</span>
          <span>${cp.files.length} file${cp.files.length === 1 ? "" : "s"} changed</span>
        </div>
      </header>
      ${cp.commits.length ? `<div class="code-commit-list compact">${cp.commits.map(commitRow).join("")}</div>` : ""}
      ${fileSummaryHtml(cp.files)}
      ${renderPatch(cp.patch, cp.truncated)}`;
  }

  const STATUS_LABEL = { A: "added", M: "modified", D: "deleted", R: "renamed", C: "copied", T: "type" };

  function fileSummaryHtml(files) {
    if (!files || !files.length) return "";
    const rows = files
      .map((f) => {
        const code = f.status[0];
        const name = f.old_path ? `${f.old_path} → ${f.path}` : f.path;
        return `<div class="diff-stat"><span class="diff-stat-mark s-${code}" title="${STATUS_LABEL[code] || code}">${escapeHtml(code)}</span><span class="diff-stat-path mono">${escapeHtml(name)}</span></div>`;
      })
      .join("");
    return `<div class="diff-stats">${rows}</div>`;
  }

  function renderPatch(patch, truncated) {
    if (!patch || !patch.trim()) return `<div class="code-empty-row">No textual diff (binary, merge, or empty change).</div>`;
    const parts = patch.split(/^(?=diff --git )/m).filter((p) => p.trim());
    const blocks = parts.map(renderFileDiff).join("");
    return `<div class="diff">${blocks}${truncated ? `<div class="code-trunc">diff truncated — too large to show in full</div>` : ""}</div>`;
  }

  function renderFileDiff(chunk) {
    const lines = chunk.split("\n");
    const m = lines[0].match(/^diff --git a\/(.+) b\/(.+)$/);
    const path = m ? m[2] : lines[0].replace(/^diff --git /, "");
    // language for the NEW side; null -> plain escaped fallback
    const ext = path.includes(".") ? path.split(".").pop().toLowerCase() : "";
    const lang = langFor(ext, path);
    // each line is highlighted on its own (no cross-line state) — fine for diffs.
    // The +/- prefix is split into a sign gutter so it never feeds the tokenizer
    // (a leading '+' would mis-lex as an operator) and add/remove stays readable.
    const codeHtml = (text) => {
      if (!lang) return escapeHtml(text) || " ";
      const hl = highlightLines(text, lang);
      return (hl && hl[0]) || escapeHtml(text) || " ";
    };
    let body = "";
    for (const ln of lines) {
      if (/^(diff --git |index |--- |\+\+\+ |new file |deleted file |old mode |new mode |similarity |rename |copy |Binary files )/.test(ln)) {
        if (ln.startsWith("Binary files")) body += `<div class="diff-line meta">${escapeHtml(ln)}</div>`;
        continue;
      }
      if (ln.startsWith("@@")) {
        body += `<div class="diff-line hunk">${escapeHtml(ln) || " "}</div>`;
        continue;
      }
      let cls = "ctx";
      let sign = " ";
      let rest = ln.startsWith(" ") ? ln.slice(1) : ln;
      if (ln.startsWith("+")) {
        cls = "add";
        sign = "+";
        rest = ln.slice(1);
      } else if (ln.startsWith("-")) {
        cls = "del";
        sign = "-";
        rest = ln.slice(1);
      }
      body += `<div class="diff-line ${cls}"><span class="diff-sign">${sign}</span>${codeHtml(rest)}</div>`;
    }
    return `<div class="diff-file">
      <div class="diff-file-head mono">${escapeHtml(path)}</div>
      <div class="diff-body">${body}</div>
    </div>`;
  }

  // -- helpers ---------------------------------------------------------- #
  function fmtSize(bytes) {
    if (bytes == null) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  function pluralLines(text) {
    const n = text ? text.split("\n").length : 0;
    return `${n} line${n === 1 ? "" : "s"}`;
  }

  function paint() {
    if (!state.container) return;
    state.container.innerHTML = html();
    state.root = state.container.querySelector("[data-code-root]");
  }

  // -- events ----------------------------------------------------------- #
  async function handleClick(event) {
    const t = event.target;

    const refBtn = t.closest("[data-pick-ref]");
    if (refBtn) {
      t.closest("[data-code-ref]")?.removeAttribute("open");
      const ref = refBtn.dataset.pickRef;
      const view = state.route.view === "blob" ? "blob" : state.route.view === "commits" ? "commits" : "tree";
      return go({ view, ref, path: state.route.path || "" });
    }

    const wtToggle = t.closest("[data-toggle-worktree]");
    if (wtToggle) {
      state.worktree = wtToggle.checked;
      return go({ ...state.route }, { push: "replace" });
    }

    const utToggle = t.closest("[data-toggle-untracked]");
    if (utToggle) {
      state.untracked = utToggle.checked;
      return go({ ...state.route }, { push: "replace" });
    }

    if (t.closest("[data-open-compare]")) return openComparePopup();

    const wrapBtn = t.closest("[data-wrap]");
    if (wrapBtn) {
      const wrap = wrapBtn.dataset.wrap === "wrap";
      if (wrap === state.wrap) return;
      state.wrap = wrap;
      localStorage.setItem("ss.code.wrap", wrap ? "1" : "0");
      state.root?.classList.toggle("wrap", wrap);
      state.root?.classList.toggle("nowrap", !wrap);
      state.root?.querySelectorAll("[data-wrap]").forEach((b) => {
        const on = b.dataset.wrap === (wrap ? "wrap" : "scroll");
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", String(on));
      });
      return;
    }

    if (t.closest("[data-nav-root]")) return go({ view: "tree", ref: refOf(), path: "" });
    const navTree = t.closest("[data-nav-tree]");
    if (navTree) return go({ view: "tree", ref: refOf(), path: navTree.dataset.navTree });
    const navBlob = t.closest("[data-nav-blob]");
    if (navBlob) return go({ view: "blob", ref: refOf(), path: navBlob.dataset.navBlob });
    if (t.closest("[data-history]")) return go({ view: "commits", ref: refOf(), path: "" });
    if (t.closest("[data-back-tree]")) return go({ view: "tree", ref: refOf(), path: "" });
    if (t.closest("[data-back-history]")) return go({ view: "commits", ref: refOf(), path: "" });
    const navCommit = t.closest("[data-nav-commit]");
    if (navCommit) return go({ view: "commit", sha: navCommit.dataset.navCommit });
    const cmp = t.closest("[data-compare]");
    if (cmp) {
      const [base, head] = cmp.dataset.compare.split("|");
      return go({ view: "compare", base, head });
    }
    if (t.closest("[data-load-more]")) return loadMore();
    if (t.closest("[data-copy-link]")) {
      try {
        await navigator.clipboard.writeText(location.href);
        toast("link copied", "ok");
      } catch {
        toast("copy failed — copy the URL manually", "error");
      }
      return;
    }
  }

  async function loadMore() {
    const c = state.data?.commits;
    if (!c || state.moreLoading) return;
    state.moreLoading = true;
    paint();
    try {
      const next = await api.codeCommits(repo.id, refOf(), c.items.length);
      c.items = c.items.concat(next.items);
      c.has_more = next.has_more;
      c.total = next.total;
    } catch (err) {
      toast(err.message || "could not load more", "error");
    } finally {
      state.moreLoading = false;
      paint();
    }
  }

  // -- compare popup ---------------------------------------------------- #
  // A self-contained mini-picker: two symmetric sides (base/head), each a branch
  // dropdown + that branch's commit list. Base is prefilled to what we're viewing;
  // head defaults to the same branch. Picking a commit pins that side to a sha.
  // "Compare" routes to the existing compare view. Owns its own listeners on the
  // modal node so it stays out of the global delegation.
  function openComparePopup() {
    const branches = state.meta?.branches || [];
    const refs = [...branches, ...(state.meta?.tags || [])];
    if (!refs.length) return toast("no branches to compare", "error");
    const curBranch = branches.includes(refOf()) ? refOf() : state.meta?.default_ref || refs[0];
    const baseRef = state.route.view === "commit" ? state.route.sha : refOf();
    const sides = {
      base: { branch: curBranch, ref: baseRef },
      head: { branch: curBranch, ref: curBranch },
    };
    const node = modal(comparePopHtml(sides, refs), { wide: true });
    const pickedEl = (w) => node.querySelector(`[data-cmp-picked="${w}"]`);
    const commitsEl = (w) => node.querySelector(`[data-cmp-commits="${w}"]`);

    function markPicked(which) {
      pickedEl(which).textContent = sides[which].ref;
      commitsEl(which)
        .querySelectorAll("[data-cmp-pick]")
        .forEach((b) => b.classList.toggle("active", b.dataset.sha === sides[which].ref));
    }
    async function loadSide(which) {
      const host = commitsEl(which);
      host.innerHTML = `<div class="loading">loading…</div>`;
      try {
        const c = await api.codeCommits(repo.id, sides[which].branch);
        host.innerHTML =
          (c.items || []).map((cm) => comparePickRow(which, cm)).join("") || `<div class="code-empty-row">no commits</div>`;
        markPicked(which);
      } catch (err) {
        host.innerHTML = `<div class="banner banner-warn">${escapeHtml(err.message || "could not load commits")}</div>`;
      }
    }

    node.addEventListener("change", (e) => {
      const sel = e.target.closest("[data-cmp-branch]");
      if (!sel) return;
      const which = sel.dataset.cmpBranch;
      sides[which].branch = sel.value;
      sides[which].ref = sel.value; // branch tip until a commit is picked
      loadSide(which);
    });
    node.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) return closeModal();
      const pick = e.target.closest("[data-cmp-pick]");
      if (pick) {
        sides[pick.dataset.cmpPick].ref = pick.dataset.sha;
        return markPicked(pick.dataset.cmpPick);
      }
      if (e.target.closest("[data-cmp-go]")) {
        closeModal();
        go({ view: "compare", base: sides.base.ref, head: sides.head.ref });
      }
    });
    loadSide("base");
    loadSide("head");
  }

  function comparePopHtml(sides, refs) {
    return `<h2>Compare</h2>
      <div class="code-compare-pop">
        ${compareSideHtml("base", "Base", sides.base, refs)}
        <span class="compare-arrow">→</span>
        ${compareSideHtml("head", "Head", sides.head, refs)}
      </div>
      <div class="button-row">
        <button class="btn btn-primary" type="button" data-cmp-go>Compare</button>
        <button class="btn btn-ghost" type="button" data-close>Cancel</button>
      </div>`;
  }

  function compareSideHtml(which, label, side, refs) {
    const opts = refs
      .map((r) => `<option value="${escapeHtml(r)}" ${r === side.branch ? "selected" : ""}>${escapeHtml(r)}</option>`)
      .join("");
    return `<div class="cmp-side" data-cmp-side="${which}">
      <div class="cmp-side-head">${escapeHtml(label)}</div>
      <select class="cmp-branch" data-cmp-branch="${which}" aria-label="${escapeHtml(label)} branch">${opts}</select>
      <div class="cmp-picked mono" data-cmp-picked="${which}">${escapeHtml(side.ref)}</div>
      <div class="cmp-commits" data-cmp-commits="${which}"></div>
    </div>`;
  }

  function comparePickRow(which, cm) {
    return `<button type="button" class="commit-row" data-cmp-pick="${which}" data-sha="${escapeHtml(cm.short)}">
      <span class="commit-sha mono">${escapeHtml(cm.short)}</span>
      <span class="commit-main">
        <span class="commit-subject">${escapeHtml(cm.subject)}</span>
        <span class="commit-sub">${escapeHtml(relativeTime(cm.date))}</span>
      </span>
    </button>`;
  }

  // live-filter the ref switcher; hides group labels whose items all vanish
  function handleInput(event) {
    const search = event.target.closest("[data-ref-search]");
    if (!search) return;
    const q = search.value.trim().toLowerCase();
    const scroll = search.closest(".code-ref-menu")?.querySelector("[data-ref-scroll]");
    if (!scroll) return;
    scroll.querySelectorAll(".code-ref-item").forEach((it) => {
      it.style.display = it.textContent.toLowerCase().includes(q) ? "" : "none";
    });
    // a group label stays only if a visible item follows it before the next label
    const kids = [...scroll.children];
    kids.forEach((el, i) => {
      if (!el.classList.contains("code-ref-group")) return;
      let visible = false;
      for (let j = i + 1; j < kids.length && !kids[j].classList.contains("code-ref-group"); j++) {
        if (kids[j].style.display !== "none") visible = true;
      }
      el.style.display = visible ? "" : "none";
    });
  }

  function afterRender(container) {
    state.container = container;
    state.root = container.querySelector("[data-code-root]");
  }

  return { load, html, afterRender, handleClick, handleInput, onSubRoute };
}
