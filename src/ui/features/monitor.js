import { api } from "./api.js";
import { closeModal, escapeHtml, formatTime, modal, toast } from "../ui/components.js";

const POLL_MS = 3000;

export function createMonitor({ repo, ctx, refreshTopbar }) {
  let data = null;
  let container = null;
  let timer = null;
  let busy = false; // a runner action is mid-flight (suspend polling)
  let polling = false; // a poll fetch is in flight (no overlap)

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
    const env = ctx.env || {};
    const alive = !!r.alive;
    const items = [
      ["poll", r.poll_interval_seconds != null ? `${r.poll_interval_seconds}s` : "—"],
      // last poll is only meaningful while the runner is live; a stopped runner's
      // value is frozen and would otherwise just keep aging from its last sync.
      ["last poll", alive && r.last_poll_at ? formatTime(r.last_poll_at) : "—"],
      ["current task", r.current_task_id != null ? `#${r.current_task_id}` : "idle"],
      ["pid (repo)", r.pid || "—"],
      ["pid (specseed)", env.pid || "—"],
    ];
    let chips = items
      .map(([k, v]) => `<span class="meta-chip"><b>${escapeHtml(k)}</b> ${escapeHtml(v)}</span>`)
      .join("");
    // The LAN address stays hidden (kept off-screen) - this is a copy-only
    // affordance so it can be pasted into another device's browser.
    if (env.lan_ip && env.port) {
      chips += `<button type="button" class="meta-chip copy" data-copy-addr title="copy this machine's LAN address">
        <b>ip+port</b> <span class="copy-hint">click to copy</span></button>`;
    }
    return chips;
  }

  async function copyText(text, label = "copied") {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.append(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      toast(label, "ok");
    } catch {
      toast("copy failed", "error");
    }
  }

  function queueTable() {
    const tasks = data?.queue || [];
    if (!tasks.length) return `<div class="empty-state">Queue is empty.</div>`;
    return `
      <div class="table-wrap"><table class="table">
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
              <td class="muted">${escapeHtml(t.last_attempted_at ? formatTime(t.last_attempted_at) : "—")}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table></div>`;
  }

  function errorsList() {
    const errs = data?.errors || [];
    if (!errs.length) return `<div class="empty-state">No errors recorded.</div>`;
    return errs
      .map((e) => {
        const head = String(e.message || "").split("\n")[0].trim();
        const ctxbits = [e.action, e.post_id ? "#" + e.post_id : null].filter(Boolean).join(" · ");
        const multiline = String(e.message || "").includes("\n") || String(e.message || "").length > 160;
        return `
          <div class="error-row">
            <div class="error-main">
              <div class="error-meta">task #${escapeHtml(e.task_id)}${ctxbits ? " · " + escapeHtml(ctxbits) : ""} · ${escapeHtml(formatTime(e.executed_at))}</div>
              <div class="error-msg mono">${escapeHtml(head) || "(no message)"}</div>
            </div>
            <button class="btn btn-ghost sm" data-error-details="${escapeHtml(e.error_id)}" title="full error">${multiline ? "Details" : "View"}</button>
          </div>`;
      })
      .join("");
  }

  function openErrorModal(id) {
    const e = (data?.errors || []).find((x) => String(x.error_id) === String(id));
    if (!e) return;
    const row = (k, v) => `<div class="err-meta-row"><span class="cfg-k">${k}</span><span class="mono">${escapeHtml(v)}</span></div>`;
    modal(
      `
      <h2>Task #${escapeHtml(e.task_id)} — error</h2>
      <div class="err-meta">
        ${row("action", e.action || "—")}
        ${row("post", e.post_id ? "#" + e.post_id : "—")}
        ${row("attempts", e.attempts ?? "—")}
        ${row("when", formatTime(e.executed_at))}
      </div>
      <pre class="error-full mono">${escapeHtml(e.message || "(no message)")}</pre>
      <div class="button-row">
        <button class="btn btn-ghost" data-copy-error="${escapeHtml(e.error_id)}">Copy</button>
        <button class="btn btn-primary" data-close>Close</button>
      </div>`,
      { wide: true }
    );
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
    if (!slot) return;
    // preserve the log scroll position across a repaint
    const scroll = slot.querySelector(".log")?.scrollTop ?? 0;
    slot.innerHTML = body();
    const log = slot.querySelector(".log");
    if (log) log.scrollTop = scroll;
  }

  // Single-flight: never let a slow poll stack behind another (no overlap,
  // out-of-order paints, or unbounded fetch backlog).
  async function refresh(silent = false) {
    if (polling) return;
    polling = true;
    try {
      await load();
      paint();
      refreshTopbar?.();
    } catch (err) {
      if (!silent) ctx.onError(err); // background polls fail quietly (no toast spam)
    } finally {
      polling = false;
    }
  }

  function afterRender(node) {
    container = node;
    // Poll only when this tab is foregrounded and no runner action is mid-flight.
    timer = setInterval(() => {
      if (!busy && !document.hidden) refresh(true);
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
    if (event.target.closest("[data-copy-addr]")) {
      const env = ctx.env || {};
      if (env.lan_ip && env.port) copyText(`${env.lan_ip}:${env.port}`, "address copied");
      return;
    }
    const det = event.target.closest("[data-error-details]");
    if (det) return openErrorModal(det.dataset.errorDetails);
    const ce = event.target.closest("[data-copy-error]");
    if (ce) {
      const e = (data?.errors || []).find((x) => String(x.error_id) === String(ce.dataset.copyError));
      if (e) copyText(e.message || "", "error copied");
      return;
    }
    if (event.target.closest("[data-close]")) return closeModal();
    if (event.target.closest("[data-monitor-refresh]")) refresh();
  }

  function dispose() {
    if (timer) clearInterval(timer);
  }

  return { load, html, afterRender, handleClick, dispose };
}
