import { api } from "./api.js";
import { escapeHtml, relativeTime, toast } from "../ui/components.js";

const POLL_MS = 3000;

export function createMonitor({ repo, ctx, refreshTopbar }) {
  let data = null;
  let container = null;
  let timer = null;
  let busy = false;

  async function load() {
    data = await api.monitor(repo.id);
  }

  function runnerState() {
    const r = data?.runner || {};
    return r.alive ? r.state || "stopped" : "stopped";
  }

  function controls() {
    const st = runnerState();
    const btn = (action, label, cls = "btn-ghost") =>
      `<button class="btn ${cls}" data-runner-action="${action}">${label}</button>`;
    if (st === "running") return btn("pause", "Pause") + btn("stop", "Stop", "btn-danger");
    if (st === "paused") return btn("resume", "Resume", "btn-primary") + btn("stop", "Stop", "btn-danger");
    return btn("start", "Start runner", "btn-primary");
  }

  function statCards() {
    const c = data?.counts || {};
    const r = data?.runner || {};
    const cards = [
      ["queued", c.pending || 0, "accent"],
      ["in progress", c.in_progress || 0, "live"],
      ["done", c.success || 0, "ok"],
      ["failed", c.failed || 0, c.failed ? "bad" : "muted"],
    ];
    return cards
      .map(
        ([label, value, tone]) => `
        <div class="stat stat-${tone}">
          <div class="stat-value">${escapeHtml(value)}</div>
          <div class="stat-label">${escapeHtml(label)}</div>
        </div>`
      )
      .join("");
  }

  function metaRow() {
    const r = data?.runner || {};
    const items = [
      ["pid", r.pid || "—"],
      ["poll", r.poll_interval_seconds != null ? `${r.poll_interval_seconds}s` : "—"],
      ["last poll", r.last_poll_at ? relativeTime(r.last_poll_at) : "—"],
      ["current task", r.current_task_id != null ? `#${r.current_task_id}` : "idle"],
    ];
    return items.map(([k, v]) => `<span class="meta-chip"><b>${escapeHtml(k)}</b> ${escapeHtml(v)}</span>`).join("");
  }

  function queueTable() {
    const tasks = data?.queue || [];
    if (!tasks.length) return `<div class="empty-state">Queue is empty.</div>`;
    return `
      <table class="table">
        <thead><tr><th>#</th><th>action</th><th>post</th><th>status</th><th>att.</th><th>last</th></tr></thead>
        <tbody>
          ${tasks
            .map(
              (t) => `
            <tr class="row-${escapeHtml(t.status)}">
              <td>${escapeHtml(t.task_id)}</td>
              <td class="mono">${escapeHtml(t.action)}</td>
              <td>${t.post_id ? "#" + escapeHtml(t.post_id) : "—"}</td>
              <td><span class="tag tag-${escapeHtml(t.status)}">${escapeHtml(t.status)}</span></td>
              <td>${escapeHtml(t.attempts)}</td>
              <td class="muted">${escapeHtml(t.last_attempted_at ? relativeTime(t.last_attempted_at) : "—")}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function errorsList() {
    const errs = data?.errors || [];
    if (!errs.length) return `<div class="empty-state">No errors recorded.</div>`;
    return errs
      .map(
        (e) => `
      <div class="error-row">
        <div class="error-meta">task #${escapeHtml(e.task_id)} · ${escapeHtml(relativeTime(e.executed_at))}</div>
        <div class="error-msg mono">${escapeHtml(e.message)}</div>
      </div>`
      )
      .join("");
  }

  function logList() {
    const log = (data?.log || []).slice().reverse();
    if (!log.length) return `<div class="empty-state">No activity logged yet.</div>`;
    return `<div class="log">${log
      .map((line) => {
        if (line.raw) return `<div class="log-line mono">${escapeHtml(line.raw)}</div>`;
        const { ts, event, ...rest } = line;
        const detail = Object.entries(rest)
          .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
          .join(" ");
        return `<div class="log-line"><span class="log-ts">${escapeHtml(ts || "")}</span><span class="log-event">${escapeHtml(
          event || ""
        )}</span><span class="log-detail mono">${escapeHtml(detail)}</span></div>`;
      })
      .join("")}</div>`;
  }

  function body() {
    const st = runnerState();
    return `
      <div class="monitor">
        <section class="panel panel-runner">
          <div class="runner-head">
            <div class="runner-state">
              <span class="dot dot-${st} big"></span>
              <div>
                <div class="runner-state-name">${escapeHtml(st)}</div>
                <div class="muted">${escapeHtml(repo.target)}</div>
              </div>
            </div>
            <div class="button-row">${controls()}</div>
          </div>
          <div class="meta-row">${metaRow()}</div>
        </section>

        <section class="stat-grid">${statCards()}</section>

        <section class="panel">
          <div class="panel-title">Queue</div>
          ${queueTable()}
        </section>

        <div class="two-col">
          <section class="panel">
            <div class="panel-title">Errors</div>
            ${errorsList()}
          </section>
          <section class="panel">
            <div class="panel-title">Activity log</div>
            ${logList()}
          </section>
        </div>
      </div>`;
  }

  function html() {
    return `
      <div class="tab-head">
        <h1>Monitor</h1>
        <div class="tab-head-actions">
          <span class="auto-dot" title="auto-refreshing"></span>
          <button class="btn btn-ghost" data-monitor-refresh>Refresh</button>
        </div>
      </div>
      <div data-monitor-body>${body()}</div>`;
  }

  function paint() {
    const slot = container?.querySelector("[data-monitor-body]");
    if (slot) slot.innerHTML = body();
  }

  async function refresh() {
    try {
      await load();
      paint();
      refreshTopbar?.();
    } catch (err) {
      ctx.onError(err);
    }
  }

  function afterRender(node) {
    container = node;
    timer = setInterval(() => {
      if (!busy) refresh();
    }, POLL_MS);
  }

  async function handleClick(event) {
    const action = event.target.closest("[data-runner-action]");
    if (action) {
      busy = true;
      try {
        await api.runner(repo.id, action.dataset.runnerAction);
        toast(`runner: ${action.dataset.runnerAction}`, "ok");
        await refresh();
      } catch (err) {
        ctx.onError(err);
      } finally {
        busy = false;
      }
      return;
    }
    if (event.target.closest("[data-monitor-refresh]")) refresh();
  }

  function dispose() {
    if (timer) clearInterval(timer);
  }

  return { load, html, afterRender, handleClick, dispose };
}
