import { api } from "./api.js";
import { escapeHtml, toast } from "../ui/components.js";

export function createConfiguration({ repo, ctx }) {
  let cfg = null;
  let remote = null;
  let gate = { editable: true, reason: "" };

  async function load() {
    const data = await api.getConfig(repo.id);
    cfg = data.config;
    remote = data.remote;
    gate = data.gate || gate;
  }

  const ro = () => (gate.editable ? "" : "disabled");
  const checked = (v) => (v ? "checked" : "");

  function gateBanner() {
    if (gate.editable) return "";
    return `<div class="banner banner-warn">🔒 ${escapeHtml(gate.reason)}</div>`;
  }

  function remoteBlock() {
    const provider = repo.provider;
    if (provider === "local") {
      return `<div class="cfg-row"><span class="cfg-k">provider</span><span class="cfg-v">local <span class="muted">(final)</span></span></div>`;
    }
    return `
      <div class="cfg-row"><span class="cfg-k">provider</span><span class="cfg-v">${escapeHtml(provider)} <span class="muted">(final)</span></span></div>
      <div class="cfg-row"><span class="cfg-k">repository</span><span class="cfg-v mono">${escapeHtml(remote.repo || "—")}</span></div>
      <div class="cfg-row"><span class="cfg-k">token</span><span class="cfg-v"><button type="button" class="btn btn-ghost sm" data-rerun-setup>Re-run setup</button></span></div>`;
  }

  function num(name, value) {
    return `<input type="number" name="${name}" value="${escapeHtml(value)}" step="any" ${ro()} />`;
  }
  function text(name, value) {
    return `<input type="text" name="${name}" value="${escapeHtml(value ?? "")}" ${ro()} />`;
  }
  function toggle(name, value, label) {
    return `<label class="switch-row"><input type="checkbox" name="${name}" ${checked(value)} ${ro()} /><span>${escapeHtml(label)}</span></label>`;
  }

  function html() {
    const p = cfg.permissions || {};
    return `
      <div class="tab-head">
        <h1>Configuration</h1>
        <div class="tab-head-actions">
          <button class="btn btn-primary" data-save-config ${ro()}>Save</button>
        </div>
      </div>
      ${gateBanner()}
      <form data-config-form class="config ${gate.editable ? "" : "locked"}">
        <section class="panel">
          <div class="panel-title">Connection</div>
          ${remoteBlock()}
        </section>

        <section class="panel">
          <div class="panel-title">Scheduler</div>
          <div class="field"><label>poll interval (seconds)</label>${num("poll_interval_seconds", cfg.poll_interval_seconds)}</div>
          <div class="field"><label>dev branch</label>${text("dev_branch", cfg.dev_branch)}</div>
          <div class="field"><label>approver usernames <span class="req">(comma separated)</span></label>
            ${text("approver_usernames", (cfg.approvals?.approver_usernames || []).join(", "))}</div>
        </section>

        <section class="panel">
          <div class="panel-title">Code review</div>
          ${toggle("review_enabled", cfg.review?.enabled, "enable review loop")}
          <div class="field"><label>confidence threshold</label>${num("review_confidence", cfg.review?.confidence_threshold)}</div>
          <div class="field"><label>max attempts</label>${num("review_attempts", cfg.review?.max_attempts)}</div>
        </section>

        <section class="panel">
          <div class="panel-title">Permissions</div>
          ${toggle("git_merge", p.git?.merge_to_dev_branch, "git: merge into dev branch")}
          ${toggle("remote_post_control", p.remote?.post_control, "remote: post to CONTROL")}
          ${toggle("remote_push_branches", p.remote?.push_branches, "remote: push branches")}
          ${toggle("remote_push_dev", p.remote?.push_dev_branch, "remote: push dev branch")}
          ${toggle("remote_make_prs", p.remote?.make_prs, "remote: make PRs")}
          ${toggle("plat_auto_impl", p.platform?.auto_implement_issue, "platform: auto-implement issues")}
          ${toggle("plat_auto_next", p.platform?.auto_proceed_to_next_sprint_if_available, "platform: auto-proceed to next sprint")}
        </section>
      </form>`;
  }

  function gather(form) {
    const d = Object.fromEntries(new FormData(form).entries());
    const next = JSON.parse(JSON.stringify(cfg));
    next.poll_interval_seconds = Number(d.poll_interval_seconds) || next.poll_interval_seconds;
    next.dev_branch = String(d.dev_branch || next.dev_branch);
    next.approvals = next.approvals || {};
    next.approvals.approver_usernames = String(d.approver_usernames || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    next.review = next.review || {};
    next.review.enabled = form.review_enabled.checked;
    next.review.confidence_threshold = Number(d.review_confidence) || next.review.confidence_threshold;
    next.review.max_attempts = Number(d.review_attempts) || next.review.max_attempts;
    const p = (next.permissions = next.permissions || {});
    p.git = p.git || {};
    p.git.merge_to_dev_branch = form.git_merge.checked;
    p.remote = p.remote || {};
    p.remote.post_control = form.remote_post_control.checked;
    p.remote.push_branches = form.remote_push_branches.checked;
    p.remote.push_dev_branch = form.remote_push_dev.checked;
    p.remote.make_prs = form.remote_make_prs.checked;
    p.platform = p.platform || {};
    p.platform.auto_implement_issue = form.plat_auto_impl.checked;
    p.platform.auto_proceed_to_next_sprint_if_available = form.plat_auto_next.checked;
    return next;
  }

  async function save() {
    const form = document.querySelector("[data-config-form]");
    if (!form) return;
    try {
      const next = gather(form);
      await api.putConfig(repo.id, next);
      cfg = next;
      toast("configuration saved", "ok");
      await ctx.refreshRepos?.();
    } catch (err) {
      ctx.onError(err);
    }
  }

  function handleClick(event) {
    if (event.target.closest("[data-save-config]")) {
      if (!gate.editable) {
        toast(gate.reason, "error");
        return;
      }
      save();
    }
    if (event.target.closest("[data-rerun-setup]")) ctx.openSetup(repo);
  }

  function handleSubmit(event) {
    if (event.target.dataset.configForm !== undefined) {
      event.preventDefault();
      if (gate.editable) save();
    }
  }

  return { load, html, handleClick, handleSubmit, dispose() {} };
}
