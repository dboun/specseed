import { api } from "../features/api.js";
import { escapeHtml, toast, modal, closeModal, confirmDialog } from "../ui/components.js";
import { createMonitor } from "../features/monitor.js";
import { createTracker } from "../features/tracker.js";
import { createSpec } from "../features/spec.js";
import { createCode } from "../features/code.js";
import { createConfiguration } from "../features/configuration.js";
import { openAddRepo, openSetup } from "../features/repos.js";

const TABS = [
  { id: "monitor", label: "Monitor" },
  { id: "tracker", label: "Tracker" },
  { id: "spec", label: "Spec" },
  { id: "code", label: "Code" },
  { id: "configuration", label: "Configuration" },
];

const state = {
  repos: [],
  currentId: null,
  tab: localStorage.getItem("ss.tab") || "monitor",
  sub: "", // subroute after the tab (e.g. the open spec file) — deep-linkable
  feature: null,
  dev: false,
  env: {},
};

function brandHtml() {
  return `<span class="brand-mark">specseed${state.dev ? ' <span class="dev-tag">DEV</span>' : ""}</span>`;
}

const root = document.querySelector("#app");

// The hash we last wrote/applied. Lets the popstate+hashchange handlers dedupe
// (our own pushState/replaceState updates fire no events, but back/forward fire
// BOTH) and lets the unsaved-edit guard bounce a declined back-button.
let lastHash = location.hash;

function currentRepo() {
  return state.repos.find((r) => r.id === state.currentId) || null;
}

const ctx = {
  onError: (err) => toast(err.message || String(err), "error"),
  reload: boot,
  switchTab,
  openSetup: (repo) => openSetup(repo, ctx),
  refreshRepos,
  get env() {
    return state.env;
  },
  async selectRepo(id, tab) {
    state.currentId = id;
    localStorage.setItem("ss.repo", id);
    if (tab) {
      state.tab = tab;
      localStorage.setItem("ss.tab", tab);
    }
    state.sub = "";
    await refreshRepos();
    writeHash(true);
    render();
  },
  // A feature owns the subroute after its tab (e.g. the spec file / open post).
  // It sets it as the user navigates so the URL stays copy-pasteable. Default
  // PUSHES a history entry (opening a file/post is a real navigation the
  // back-button should retrace); pass {replace:true} for a refinement that
  // shouldn't grow the stack (initial load, default-ref resolution, a toggle).
  setSub(sub, { replace = false } = {}) {
    state.sub = sub || "";
    writeHash(!replace);
  },
};

async function refreshRepos() {
  state.repos = await api.repos();
  if (!currentRepo() && state.repos.length) {
    state.currentId = state.repos[state.repos.length - 1].id;
  }
}

const decode = (s) => {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
};

function applyHash() {
  // #<repoId>/<tab>[/<sub...>] — deep-linkable, shareable, back-button friendly.
  // Anything after the tab is the feature's subroute (e.g. a spec file path,
  // which may itself contain "/"), so split first, decode each segment.
  const raw = location.hash.replace(/^#\/?/, "");
  if (!raw) return;
  const parts = raw.split("/").map(decode);
  const [repoId, tab, ...rest] = parts;
  if (repoId && state.repos.some((r) => r.id === repoId)) state.currentId = repoId;
  if (TABS.some((t) => t.id === tab)) {
    state.tab = tab;
    state.sub = rest.join("/");
  }
}

// push=true grows the history stack (a real navigation back/forward should
// retrace); push=false replaces in place (refinement). Either way we record the
// hash so the history listeners can tell our own writes from a back/forward.
function writeHash(push) {
  if (!state.currentId) return;
  let next = `#${encodeURIComponent(state.currentId)}/${state.tab}`;
  if (state.sub) next += "/" + state.sub.split("/").map(encodeURIComponent).join("/");
  if (location.hash !== next) {
    if (push) history.pushState(null, "", next);
    else history.replaceState(null, "", next);
  }
  lastHash = location.hash;
}

async function boot() {
  try {
    state.env = await api.env();
    state.dev = !!state.env.dev;
    document.title = state.dev ? "specseed DEV" : "specseed";
  } catch {
    // old server without /api/env - assume installed
  }
  try {
    await refreshRepos();
  } catch (err) {
    ctx.onError(err);
    return;
  }
  if (!state.repos.length) {
    renderEmpty();
    return;
  }
  applyHash();
  const repo = currentRepo();
  if (repo && !repo.configured) state.tab = "configuration";
  writeHash();
  render();
}

function renderEmpty() {
  state.feature?.dispose?.();
  state.feature = null;
  root.innerHTML = `
    <div class="landing">
      <div class="landing-glow"></div>
      <div class="landing-card">
        <div class="brand-mark">specseed${state.dev ? ' <span class="dev-tag">DEV</span>' : ""}</div>
        <p class="landing-lead">A headless spec &amp; work engine. Register a repo to begin.</p>
        <button class="btn btn-primary" data-add-repo>+ Add repository</button>
      </div>
    </div>`;
}

function render() {
  const repo = currentRepo();
  root.innerHTML = `
    <div class="app">
      <header class="topbar">
        <div class="topbar-left">
          ${brandHtml()}
          ${switcherHtml()}
        </div>
        <div class="topbar-right">
          ${runnerPill(repo)}
        </div>
      </header>
      <nav class="tabbar">
        ${TABS.map((t) => tabButton(t, repo)).join("")}
      </nav>
      <main class="content" data-content></main>
    </div>`;
  mountFeature();
}

// Switcher ordering: live runners first (running, then otherwise-alive), then by
// most-recently-added. Keeps the repos you're actually working sitting on top.
function repoRank(r) {
  if (r.runner.alive && r.runner.state === "running") return 0;
  if (r.runner.alive) return 1;
  return 2;
}
function sortedRepos() {
  return [...state.repos].sort(
    (a, b) => repoRank(a) - repoRank(b) || String(b.added_at || "").localeCompare(String(a.added_at || ""))
  );
}

const SWITCHER_MAX = 5; // beyond this the list spills into a searchable popup

function repoItemHtml(r, cls = "switcher-item") {
  return `
    <button class="${cls} ${r.id === state.currentId ? "active" : ""}" data-pick-repo="${escapeHtml(r.id)}" data-repo-name="${escapeHtml((r.name || "").toLowerCase())}">
      <span class="dot dot-${r.runner.alive ? r.runner.state : "off"}"></span>
      <span class="switcher-item-name">${escapeHtml(r.name)}</span>
      <span class="provider-tag">${escapeHtml(r.provider)}</span>
    </button>`;
}

function switcherHtml() {
  const sorted = sortedRepos();
  const shown = sorted.slice(0, SWITCHER_MAX);
  const overflow = sorted.length - shown.length;
  return `
    <details class="switcher" data-switcher>
      <summary>
        <span class="switcher-current">${escapeHtml(currentRepo()?.name || "select repo")}</span>
        <span class="caret">▾</span>
      </summary>
      <div class="switcher-menu">
        ${shown.map((r) => repoItemHtml(r)).join("")}
        ${overflow > 0 ? `<button class="switcher-item more" data-repo-more>More… (${overflow})</button>` : ""}
        <button class="switcher-item add" data-add-repo>+ Add repository</button>
      </div>
    </details>`;
}

// All repos in a searchable popup, same active-first / recent ordering.
function openRepoPicker() {
  const list = sortedRepos().map((r) => repoItemHtml(r, "repo-pick-item")).join("");
  const node = modal(`
    <h2>Select repository</h2>
    <input class="repo-pick-search" type="search" placeholder="Search repositories…" aria-label="Search repositories" />
    <div class="repo-pick-list">${list}</div>`);
  const input = node.querySelector(".repo-pick-search");
  input?.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    node.querySelectorAll(".repo-pick-item").forEach((it) => {
      it.style.display = (it.dataset.repoName || "").includes(q) ? "" : "none";
    });
  });
  setTimeout(() => input?.focus(), 0);
}

// One shared web UI fronts many SEPARATE runner processes. The chip reports the
// foreground (selected) repo's runner, plus how many OTHER repos are running so a
// background runner is never invisible. Liveness is computed server-side (pid +
// fresh heartbeat) and refreshed on an interval.
function runnerPill(repo) {
  if (!repo) return "";
  const isRunning = (r) => r.runner.alive && r.runner.state === "running";
  const st = repo.runner.alive ? repo.runner.state : "stopped";
  const others = state.repos.filter((r) => r.id !== repo.id && isRunning(r)).length;
  let label = st;
  let compact = ""; // mobile: dot color alone covers the plain running/stopped case
  if (isRunning(repo)) {
    label = others > 0 ? `running (${others + 1})` : "running"; // never "(1)"
    if (others > 0) compact = `(${others + 1})`;
  } else if (others > 0) {
    label = `${st} (${others} other running)`;
    compact = `(${others} other)`;
  }
  return `<span class="pill pill-${st}" data-runner-pill><span class="dot dot-${st}"></span><span class="pill-label">${escapeHtml(label)}</span>${compact ? `<span class="pill-label-sm">${escapeHtml(compact)}</span>` : ""}</span>`;
}

function tabButton(tab, repo) {
  const locked = repo && !repo.configured && tab.id !== "configuration";
  return `
    <button class="tab ${state.tab === tab.id ? "active" : ""} ${locked ? "locked" : ""}"
      data-tab="${tab.id}" ${locked ? "disabled" : ""} title="${locked ? "Configure the repo first" : ""}">
      ${tab.label}${locked ? " 🔒" : ""}
    </button>`;
}

function mountFeature() {
  state.feature?.dispose?.();
  const container = root.querySelector("[data-content]");
  const repo = currentRepo();
  if (!repo) return;
  const factory =
    state.tab === "tracker"
      ? createTracker
      : state.tab === "spec"
        ? createSpec
        : state.tab === "code"
          ? createCode
          : state.tab === "configuration"
            ? createConfiguration
            : createMonitor;
  state.feature = factory({ repo, ctx, refreshTopbar, sub: state.sub });
  container.innerHTML = `<div class="loading">loading…</div>`;
  state.feature
    .load()
    .then(() => {
      container.innerHTML = state.feature.html();
      state.feature.afterRender?.(container);
    })
    .catch((err) => {
      ctx.onError(err);
      container.innerHTML = `<div class="empty-state">Could not load. <button class="btn btn-ghost" data-tab="${state.tab}">Retry</button></div>`;
    });
}

async function switchTab(tabId) {
  // Same-tab is the error-state "Retry": re-mount in place, no guard, no new
  // history entry. A real switch guards unsaved edits and pushes.
  const same = state.tab === tabId;
  if (!same && !(await guardLeave())) return;
  state.tab = tabId;
  state.sub = ""; // a fresh tab has no subroute until its feature sets one
  localStorage.setItem("ss.tab", tabId);
  writeHash(!same);
  // re-render tab bar active states without a full reload
  root.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabId));
  mountFeature();
}

async function pickRepo(id) {
  if (id === state.currentId) return;
  if (!(await guardLeave())) return;
  state.currentId = id;
  state.sub = "";
  localStorage.setItem("ss.repo", id);
  await refreshRepos();
  const repo = currentRepo();
  if (repo && !repo.configured) state.tab = "configuration";
  writeHash(true);
  render();
}

// -- unsaved-edit guard ----------------------------------------------------- #
// A feature may expose isDirty() -> a short reason string when it holds unsaved
// edits (e.g. a half-written post/comment). We refuse to navigate away from it
// without an explicit ok.
const dirtyReason = () => {
  try {
    return state.feature?.isDirty?.() || "";
  } catch {
    return "";
  }
};

// click-driven leave (tab/repo switch): a nice modal confirm. Returns true to go.
async function guardLeave() {
  const reason = dirtyReason();
  if (!reason) return true;
  return confirmDialog(`${reason} Leave and lose them?`, { confirmLabel: "Leave", cancelLabel: "Stay" });
}

// refresh the top bar pill + switcher dots from latest summaries (called by Monitor polling)
async function refreshTopbar() {
  try {
    state.repos = await api.repos();
  } catch {
    return;
  }
  const repo = currentRepo();
  const pill = root.querySelector("[data-runner-pill]");
  if (pill && repo) {
    const st = repo.runner.alive ? repo.runner.state : "stopped";
    pill.outerHTML = runnerPill(repo);
  }
}

// -- global event delegation ------------------------------------------------ #
document.addEventListener("click", (event) => {
  const more = event.target.closest("[data-repo-more]");
  if (more) {
    root.querySelector("[data-switcher]")?.removeAttribute("open");
    openRepoPicker();
    return;
  }
  const el = event.target.closest("[data-add-repo],[data-pick-repo],[data-tab]");
  if (el) {
    if (el.dataset.addRepo !== undefined) {
      event.preventDefault();
      openAddRepo(ctx);
      return;
    }
    if (el.dataset.pickRepo) {
      root.querySelector("[data-switcher]")?.removeAttribute("open");
      closeModal(); // close the picker popup if the click came from it
      pickRepo(el.dataset.pickRepo);
      return;
    }
    if (el.dataset.tab && !el.disabled) {
      switchTab(el.dataset.tab);
      return;
    }
  }
  state.feature?.handleClick?.(event);
});

document.addEventListener("submit", (event) => {
  state.feature?.handleSubmit?.(event);
});

document.addEventListener("input", (event) => {
  state.feature?.handleInput?.(event);
});

// Reconcile app state to the URL after a back/forward or a manual hash edit.
// A repo/tab change remounts; a sub-only change hands off to the feature's
// onSubRoute (in place) when it has one, else remounts.
function applyRoute() {
  if (!state.repos.length) return;
  const prevRepo = state.currentId,
    prevTab = state.tab,
    prevSub = state.sub;
  applyHash();
  lastHash = location.hash;
  if (state.currentId !== prevRepo || state.tab !== prevTab) {
    const repo = currentRepo();
    if (repo && !repo.configured) state.tab = "configuration";
    render();
  } else if (state.sub !== prevSub) {
    if (state.feature?.onSubRoute) state.feature.onSubRoute(state.sub);
    else mountFeature();
  }
}

// popstate (our pushState entries) AND hashchange (manual URL edits) both route
// here. Back/forward fire BOTH, so we dedupe on lastHash. Our own writeHash
// updates fire neither but bump lastHash, so they're inert here too. A dirty
// feature bounces a declined navigation by restoring the previous hash.
function onHistoryNav() {
  if (!state.repos.length) return;
  if (location.hash === lastHash) return;
  const reason = dirtyReason();
  if (reason && !window.confirm(`${reason} Leave and lose them?`)) {
    history.pushState(null, "", lastHash); // bounce back into place
    return;
  }
  applyRoute();
}
window.addEventListener("popstate", onHistoryNav);
window.addEventListener("hashchange", onHistoryNav);

// Native prompt covers tab close / reload / hard URL change — things JS can't veto.
window.addEventListener("beforeunload", (e) => {
  if (dirtyReason()) {
    e.preventDefault();
    e.returnValue = "";
  }
});

// Keep the runner chip live across ALL repos' background processes, even when the
// Monitor tab (which also polls) isn't open.
setInterval(refreshTopbar, 5000);

// restore last repo selection before first boot
state.currentId = localStorage.getItem("ss.repo");
boot();
