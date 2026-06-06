import { api } from "../features/api.js";
import { escapeHtml, toast } from "../ui/components.js";
import { createMonitor } from "../features/monitor.js";
import { createTracker } from "../features/tracker.js";
import { createConfiguration } from "../features/configuration.js";
import { openAddRepo, openSetup } from "../features/repos.js";

const TABS = [
  { id: "monitor", label: "Monitor" },
  { id: "tracker", label: "Tracker" },
  { id: "configuration", label: "Configuration" },
];

const state = {
  repos: [],
  currentId: null,
  tab: localStorage.getItem("ss.tab") || "monitor",
  feature: null,
  dev: false,
  env: {},
};

function brandHtml() {
  return `<span class="brand-mark">specseed${state.dev ? ' <span class="dev-tag">DEV</span>' : ""}</span>`;
}

const root = document.querySelector("#app");

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
    await refreshRepos();
    writeHash();
    render();
  },
};

async function refreshRepos() {
  state.repos = await api.repos();
  if (!currentRepo() && state.repos.length) {
    state.currentId = state.repos[state.repos.length - 1].id;
  }
}

function applyHash() {
  // #<repoId>/<tab> — deep-linkable, shareable, back-button friendly.
  const raw = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  if (!raw) return;
  const [repoId, tab] = raw.split("/");
  if (repoId && state.repos.some((r) => r.id === repoId)) state.currentId = repoId;
  if (TABS.some((t) => t.id === tab)) state.tab = tab;
}

function writeHash() {
  if (!state.currentId) return;
  const next = `#${state.currentId}/${state.tab}`;
  if (location.hash !== next) history.replaceState(null, "", next);
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

function switcherHtml() {
  return `
    <details class="switcher" data-switcher>
      <summary>
        <span class="switcher-current">${escapeHtml(currentRepo()?.name || "select repo")}</span>
        <span class="caret">▾</span>
      </summary>
      <div class="switcher-menu">
        ${state.repos
          .map(
            (r) => `
          <button class="switcher-item ${r.id === state.currentId ? "active" : ""}" data-pick-repo="${escapeHtml(r.id)}">
            <span class="dot dot-${r.runner.alive ? r.runner.state : "off"}"></span>
            <span class="switcher-item-name">${escapeHtml(r.name)}</span>
            <span class="provider-tag">${escapeHtml(r.provider)}</span>
          </button>`
          )
          .join("")}
        <button class="switcher-item add" data-add-repo>+ Add repository</button>
      </div>
    </details>`;
}

function runnerPill(repo) {
  if (!repo) return "";
  const st = repo.runner.alive ? repo.runner.state : "stopped";
  return `<span class="pill pill-${st}" data-runner-pill><span class="dot dot-${st}"></span>${escapeHtml(st)}</span>`;
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
    state.tab === "tracker" ? createTracker : state.tab === "configuration" ? createConfiguration : createMonitor;
  state.feature = factory({ repo, ctx, refreshTopbar });
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

function switchTab(tabId) {
  state.tab = tabId;
  localStorage.setItem("ss.tab", tabId);
  writeHash();
  // re-render tab bar active states without a full reload
  root.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabId));
  mountFeature();
}

async function pickRepo(id) {
  state.currentId = id;
  localStorage.setItem("ss.repo", id);
  await refreshRepos();
  const repo = currentRepo();
  if (repo && !repo.configured) state.tab = "configuration";
  writeHash();
  render();
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
  const el = event.target.closest("[data-add-repo],[data-pick-repo],[data-tab]");
  if (el) {
    if (el.dataset.addRepo !== undefined) {
      event.preventDefault();
      openAddRepo(ctx);
      return;
    }
    if (el.dataset.pickRepo) {
      root.querySelector("[data-switcher]")?.removeAttribute("open");
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

// Back/forward + manual hash edits navigate. replaceState (writeHash) does NOT
// fire hashchange, so this never loops with our own updates.
window.addEventListener("hashchange", () => {
  if (!state.repos.length) return;
  const before = `${state.currentId}/${state.tab}`;
  applyHash();
  if (`${state.currentId}/${state.tab}` !== before) render();
});

// restore last repo selection before first boot
state.currentId = localStorage.getItem("ss.repo");
boot();
