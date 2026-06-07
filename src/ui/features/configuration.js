import { api } from "./api.js";
import { escapeHtml, toast } from "../ui/components.js";

const CUSTOM = "__custom__"; // sentinel model option that reveals a free-text input

export function createConfiguration({ repo, ctx }) {
  let cfg = null;
  let remote = null;
  let schema = { runner_functions: [], runner_providers: [], provider_homes: {}, model_presets: {}, model_defaults: {}, agent_categories: {}, agent_levels: [], default_spec: {} };
  let gate = { editable: true, reason: "" };
  let onChange = null;

  async function load() {
    const data = await api.getConfig(repo.id);
    cfg = data.config;
    remote = data.remote;
    schema = { ...schema, ...(data.schema || {}) };
    gate = data.gate || gate;
    ensureShape();
  }

  // Backfill any missing keys so every control renders with a value (defaults).
  function ensureShape() {
    const dc = schema.default_config || {};
    cfg.runner = cfg.runner || {};
    for (const fn of schema.runner_functions) {
      if (!Array.isArray(cfg.runner[fn]) || !cfg.runner[fn].length) {
        // A function added after this repo was configured rides the
        // implementation chain (same fallback as the runtime), not the
        // default spec - a codex repo must not silently gain a claude chain.
        const impl = cfg.runner.implementation;
        cfg.runner[fn] =
          Array.isArray(impl) && impl.length
            ? impl.map((spec) => ({ ...spec }))
            : [{ ...(schema.default_spec || {}) }];
      }
    }
    cfg.approvals = cfg.approvals || { approver_usernames: [] };
    cfg.review = { ...(dc.review || {}), ...(cfg.review || {}) };
    const p = (cfg.permissions = cfg.permissions || {});
    p.git = { ...(dc.permissions?.git || {}), ...(p.git || {}) };
    p.remote = { ...(dc.permissions?.remote || {}), ...(p.remote || {}) };
    p.platform = { ...(dc.permissions?.platform || {}), ...(p.platform || {}) };
    p.agents = { ...(dc.permissions?.agents || {}), ...(p.agents || {}) };
    for (const cat of Object.keys(schema.agent_categories)) {
      if (!(cat in p.agents)) p.agents[cat] = dc.permissions?.agents?.[cat] || "block";
    }
  }

  // -- field helpers ---------------------------------------------------- #
  const ro = () => (gate.editable ? "" : "disabled");
  const checked = (v) => (v ? "checked" : "");
  const num = (name, value) => `<input type="number" name="${name}" value="${escapeHtml(value)}" step="any" ${ro()} />`;
  const text = (name, value) => `<input type="text" name="${name}" value="${escapeHtml(value ?? "")}" ${ro()} />`;
  const toggle = (name, value, label) =>
    `<label class="switch-row"><input type="checkbox" name="${name}" ${checked(value)} ${ro()} /><span>${escapeHtml(label)}</span></label>`;
  function select(attr, value, options, labels = {}) {
    return `<select ${attr} ${ro()}>${options
      .map((o) => `<option value="${escapeHtml(o)}" ${o === value ? "selected" : ""}>${escapeHtml(labels[o] || o)}</option>`)
      .join("")}</select>`;
  }

  // -- sections --------------------------------------------------------- #
  function gateBanner() {
    return gate.editable ? "" : `<div class="banner banner-warn">🔒 ${escapeHtml(gate.reason)}</div>`;
  }

  function connectionSection() {
    const rows =
      repo.provider === "local"
        ? `<div class="cfg-row"><span class="cfg-k">provider</span><span class="cfg-v">local <span class="muted">(final)</span></span></div>`
        : `<div class="cfg-row"><span class="cfg-k">provider</span><span class="cfg-v">${escapeHtml(repo.provider)} <span class="muted">(final)</span></span></div>
           <div class="cfg-row"><span class="cfg-k">repository</span><span class="cfg-v mono">${escapeHtml(remote.repo || "—")}</span></div>
           <div class="cfg-row"><span class="cfg-k">token</span><span class="cfg-v"><button type="button" class="btn btn-ghost sm" data-rerun-setup>Re-run setup</button></span></div>`;
    return `<section class="panel"><div class="panel-title">Connection</div>${rows}
      <div class="cfg-row"><span class="cfg-k">specseed dir</span><span class="cfg-v mono muted">${escapeHtml(cfg.specseed_dir || ".specseed")} (fixed)</span></div></section>`;
  }

  function generalSection() {
    return `
      <section class="panel">
        <div class="panel-title">General</div>
        <div class="field"><label>poll interval seconds <span class="req">(how often to check remote for changes)</span></label>${num("poll_interval_seconds", cfg.poll_interval_seconds)}</div>
        <div class="field"><label>primary branch <span class="req">(branch specseed treats as the integration trunk; usually main/master)</span></label>${text("specseed_primary_branch", cfg.specseed_primary_branch)}</div>
        <div class="field"><label>approver usernames <span class="req">(comma separated)</span></label>
          ${text("approver_usernames", (cfg.approvals?.approver_usernames || []).join(", "))}</div>
        <div class="field"><label>platform username <span class="req">(tracker account the platform posts as; blank = detect by "specseed: " prefix)</span></label>
          ${text("platform_username", cfg.platform_username || "")}</div>
      </section>`;
  }

  function specRow(fn, spec, idx, only) {
    const providers = schema.runner_providers;
    return `
      <div class="spec-row" data-spec-fn="${escapeHtml(fn)}">
        <div class="spec-rank">${idx === 0 ? "primary" : "fallback " + idx}</div>
        <div class="spec-fields">
          <label>provider${select(`data-spec="provider"`, spec.provider, providers)}</label>
          <label>model${modelField(spec)}</label>
          <label>effort${select(`data-spec="effort"`, spec.effort || "high", ["low", "medium", "high"])}</label>
          <label class="wide">data dir${text2(`data-spec="dir"`, spec.provider_data_dir)}</label>
        </div>
        <button type="button" class="icon-btn" data-remove-spec data-fn="${escapeHtml(fn)}" data-idx="${idx}" ${only || !gate.editable ? "disabled" : ""} title="remove">✕</button>
      </div>`;
  }
  // input with a data-attr instead of name (read positionally, not by FormData)
  function text2(attr, value) {
    return `<input type="text" ${attr} value="${escapeHtml(value ?? "")}" ${ro()} />`;
  }

  // Model picker: a dropdown of presets (claude tags / codex slugs) + "custom".
  // Picking custom reveals a free-text input. A model outside the presets (or a
  // provider with no cached presets) starts in custom mode.
  function modelField(spec) {
    const presets = schema.model_presets?.[spec.provider] || [];
    const model = spec.model ?? "";
    const isCustom = !presets.length || (model !== "" && !presets.includes(model));
    const options = [...presets, CUSTOM];
    const selValue = isCustom ? CUSTOM : model || schema.model_defaults?.[spec.provider] || presets[0] || CUSTOM;
    const labels = { [CUSTOM]: "custom…" };
    const customAttr = isCustom ? "" : "hidden";
    return (
      select(`data-spec="model-select"`, selValue, options, labels) +
      `<input type="text" data-spec="model-custom" class="model-custom" value="${escapeHtml(isCustom ? model : "")}" placeholder="model name" ${customAttr} ${ro()} />`
    );
  }

  function runnersSection() {
    const blocks = schema.runner_functions
      .map((fn) => {
        const chain = cfg.runner[fn] || [];
        return `
          <div class="runner-fn">
            <div class="runner-fn-head">
              <span class="runner-fn-name">${escapeHtml(fn.replace(/_/g, " "))}</span>
              <button type="button" class="btn btn-ghost sm" data-add-spec data-fn="${escapeHtml(fn)}" ${ro()}>+ fallback</button>
            </div>
            ${chain.map((spec, i) => specRow(fn, spec, i, chain.length === 1)).join("")}
          </div>`;
      })
      .join("");
    return `<section class="panel">
      <div class="panel-title">Agent runners</div>
      <p class="hint">Each function runs an ordered fallback chain — the primary spec first, the rest tried on failure.</p>
      ${blocks}
    </section>`;
  }

  function reviewSection() {
    return `
      <section class="panel">
        <div class="panel-title">Code review</div>
        ${toggle("review_enabled", cfg.review?.enabled, "enable review loop (gates in_review on every issue)")}
        <div class="field"><label>confidence threshold <span class="req">(to consider review passed)</span></label>${num("review_confidence", cfg.review?.confidence_threshold)}</div>
        <div class="field"><label>max attempts <span class="req">(for review loop with implementation agent)</span></label>${num("review_attempts", cfg.review?.max_attempts)}</div>
      </section>`;
  }

  function permissionsSection() {
    const p = cfg.permissions;
    return `
      <section class="panel">
        <div class="panel-title">Permissions — git &amp; remote</div>
        <div class="muted">git is mandatory (branching always allowed)</div>
        ${toggle("git_merge", p.git?.merge_to_primary, "git: merge into primary branch")}
        ${repo.provider === "local" ? "" : toggle("remote_post_control", p.remote?.post_control, "remote: create CONTROL post")}
        ${toggle("remote_push_branches", p.remote?.push_branches, "remote: push branches")}
        ${toggle("remote_push_dev", p.remote?.push_primary, "remote: push primary branch")}
      </section>
      <section class="panel">
        <div class="panel-title">Permissions — platform</div>
        ${toggle("plat_auto_impl", p.platform?.auto_implement_issue, "auto-implement issues")}
        ${toggle("plat_auto_next", p.platform?.auto_proceed_to_next_sprint_if_available, "auto-proceed to next sprint")}
      </section>
      <section class="panel">
        <div class="panel-title">Permissions — agent action gates</div>
        <p class="hint">How the implementation agent treats each class of risky action.</p>
        ${Object.entries(schema.agent_categories)
          .map(
            ([cat, desc]) => `
          <div class="gate-row">
            <div class="gate-info"><div class="gate-name mono">${escapeHtml(cat)}</div><div class="gate-desc muted">${escapeHtml(desc)}</div></div>
            ${select(`data-agent="${escapeHtml(cat)}"`, p.agents?.[cat], schema.agent_levels)}
          </div>`
          )
          .join("")}
      </section>`;
  }

  function formHtml() {
    return `
      ${connectionSection()}
      ${generalSection()}
      ${runnersSection()}
      ${reviewSection()}
      ${permissionsSection()}`;
  }

  function html() {
    return `
      <div class="tab-head">
        <h1>Configuration</h1>
        <div class="tab-head-actions">
          <button class="btn btn-ghost" data-reset-defaults ${ro()}>Reset defaults</button>
          <button class="btn btn-primary" data-save-config ${ro()}>Save</button>
        </div>
      </div>
      ${gateBanner()}
      <form data-config-form class="config ${gate.editable ? "" : "locked"}">
        <div data-config-body>${formHtml()}</div>
      </form>`;
  }

  function paintForm() {
    const body = document.querySelector("[data-config-body]");
    if (body) body.innerHTML = formHtml();
  }

  // Model = the dropdown value, unless "custom" is picked -> the free-text input.
  function readModel(row) {
    const sel = row.querySelector('[data-spec="model-select"]');
    if (!sel) return "";
    if (sel.value === CUSTOM) return row.querySelector('[data-spec="model-custom"]').value.trim();
    return sel.value;
  }

  // -- read the DOM back into cfg --------------------------------------- #
  function syncFromForm() {
    const form = document.querySelector("[data-config-form]");
    if (!form) return;
    const val = (name) => form.querySelector(`[name="${name}"]`)?.value;
    const on = (name) => !!form.querySelector(`[name="${name}"]`)?.checked;

    cfg.poll_interval_seconds = Number(val("poll_interval_seconds")) || cfg.poll_interval_seconds;
    cfg.specseed_primary_branch = String(val("specseed_primary_branch") || cfg.specseed_primary_branch);
    cfg.approvals = cfg.approvals || {};
    cfg.approvals.approver_usernames = String(val("approver_usernames") || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    cfg.platform_username = String(val("platform_username") || "").trim();

    cfg.review = cfg.review || {};
    cfg.review.enabled = on("review_enabled");
    cfg.review.confidence_threshold = Number(val("review_confidence")) || cfg.review.confidence_threshold;
    cfg.review.max_attempts = Number(val("review_attempts")) || cfg.review.max_attempts;

    const p = (cfg.permissions = cfg.permissions || {});
    p.git = p.git || {};
    p.git.merge_to_primary = on("git_merge");
    p.remote = p.remote || {};
    p.remote.post_control = on("remote_post_control");
    p.remote.push_branches = on("remote_push_branches");
    p.remote.push_primary = on("remote_push_dev");
    p.platform = p.platform || {};
    p.platform.auto_implement_issue = on("plat_auto_impl");
    p.platform.auto_proceed_to_next_sprint_if_available = on("plat_auto_next");

    p.agents = p.agents || {};
    form.querySelectorAll("[data-agent]").forEach((el) => {
      p.agents[el.dataset.agent] = el.value;
    });

    cfg.runner = cfg.runner || {};
    for (const fn of schema.runner_functions) {
      const rows = [...form.querySelectorAll(`[data-spec-fn="${fn}"]`)];
      const specs = rows.map((row) => ({
        provider: row.querySelector('[data-spec="provider"]').value,
        provider_data_dir: row.querySelector('[data-spec="dir"]').value.trim(),
        model: readModel(row),
        effort: row.querySelector('[data-spec="effort"]').value.trim(),
      }));
      cfg.runner[fn] = specs.length ? specs : [{ ...(schema.default_spec || {}) }];
    }
  }

  async function save() {
    syncFromForm();
    try {
      await api.putConfig(repo.id, cfg);
      toast("configuration saved", "ok");
      await ctx.refreshRepos?.();
    } catch (err) {
      ctx.onError(err);
    }
  }

  function handleClick(event) {
    const t = event.target;
    if (t.closest("[data-save-config]")) {
      if (!gate.editable) return toast(gate.reason, "error");
      return save();
    }
    if (t.closest("[data-rerun-setup]")) return ctx.openSetup(repo);
    if (!gate.editable) return;
    const add = t.closest("[data-add-spec]");
    if (add) {
      syncFromForm();
      cfg.runner[add.dataset.fn] = [...(cfg.runner[add.dataset.fn] || []), { ...(schema.default_spec || {}) }];
      return paintForm();
    }
    const rm = t.closest("[data-remove-spec]");
    if (rm) {
      syncFromForm();
      const chain = cfg.runner[rm.dataset.fn] || [];
      chain.splice(Number(rm.dataset.idx), 1);
      cfg.runner[rm.dataset.fn] = chain.length ? chain : [{ ...(schema.default_spec || {}) }];
      return paintForm();
    }
    if (t.closest("[data-reset-defaults]")) {
      if (!confirm("Reset every field to defaults? (saved only when you press Save)")) return;
      cfg = JSON.parse(JSON.stringify(schema.default_config || {}));
      ensureShape();
      return paintForm();
    }
  }

  function handleSubmit(event) {
    if (event.target.dataset.configForm !== undefined) {
      event.preventDefault();
      if (gate.editable) save();
    }
  }

  function afterRender(container) {
    onChange = (e) => {
      // Picking "custom" in the model dropdown reveals the free-text input.
      const msel = e.target.closest('[data-spec="model-select"]');
      if (msel) {
        const custom = msel.closest(".spec-row")?.querySelector('[data-spec="model-custom"]');
        if (custom) {
          custom.hidden = msel.value !== CUSTOM;
          if (!custom.hidden) custom.focus();
        }
        return;
      }
      // Switching provider resets the spec's model + data dir to that provider's
      // defaults and repaints (the model dropdown's options are provider-specific).
      const psel = e.target.closest('[data-spec="provider"]');
      if (!psel) return;
      const row = psel.closest(".spec-row");
      const fn = row?.dataset.specFn;
      const idx = [...container.querySelectorAll(`[data-spec-fn="${fn}"]`)].indexOf(row);
      if (fn == null || idx < 0) return;
      syncFromForm();
      const spec = cfg.runner[fn]?.[idx];
      if (!spec) return;
      const provider = psel.value;
      const homes = Object.values(schema.provider_homes || {});
      if (!spec.provider_data_dir?.trim() || homes.includes(spec.provider_data_dir.trim())) {
        spec.provider_data_dir = schema.provider_homes?.[provider] || spec.provider_data_dir;
      }
      spec.provider = provider;
      spec.model = schema.model_defaults?.[provider] || (schema.model_presets?.[provider] || [])[0] || "";
      paintForm();
    };
    container.addEventListener("change", onChange);
  }

  function dispose() {
    /* listener lives on the container, replaced on next mount */
  }

  return { load, html, afterRender, handleClick, handleSubmit, dispose };
}
