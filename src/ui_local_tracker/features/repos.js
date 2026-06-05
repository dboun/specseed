import { api } from "./api.js";
import { closeModal, escapeHtml, modal, toast } from "../ui/components.js";

const TOKEN_HELP = {
  github: {
    title: "GitHub access token",
    lines: [
      "Create a fine-grained personal access token scoped to this repo with:",
      "• Issues — read & write",
      "• Contents — read & write (if the agent should push branches)",
      "• Metadata — read",
    ],
  },
  gitlab: {
    title: "GitLab access token",
    lines: [
      "Create a personal or project access token with:",
      "• api scope (or read_api + write for issues)",
      "• role: Developer or above",
    ],
  },
};

function providerFields(provider) {
  if (provider === "local") {
    return `<p class="hint">Local provider — an in-repo tracker, no token needed.</p>`;
  }
  const help = TOKEN_HELP[provider];
  return `
    <div class="field">
      <label>Repository <span class="req">(owner/name)</span></label>
      <input name="repo" placeholder="acme/widget" autocomplete="off" required />
    </div>
    <div class="token-help">
      <div class="token-help-title">${escapeHtml(help.title)}</div>
      ${help.lines.map((l) => `<div>${escapeHtml(l)}</div>`).join("")}
    </div>
    <div class="field">
      <label>Access token</label>
      <input name="token" type="password" placeholder="paste token" autocomplete="off" required />
    </div>`;
}

export function openAddRepo(ctx) {
  const node = modal(
    `
    <form data-add-form>
      <h2>Add repository</h2>
      <div class="field">
        <label>Repository path</label>
        <input name="target" placeholder="/abs/path/to/repo" autocomplete="off" required autofocus />
      </div>
      <div class="field">
        <label>Display name <span class="req">(optional)</span></label>
        <input name="name" placeholder="defaults to folder name" autocomplete="off" />
      </div>
      <div class="field">
        <label>Tracker provider</label>
        <div class="seg" data-provider-seg>
          <button type="button" class="seg-btn active" data-provider="local">Local</button>
          <button type="button" class="seg-btn" data-provider="github">GitHub</button>
          <button type="button" class="seg-btn" data-provider="gitlab">GitLab</button>
        </div>
        <p class="final-note">⚠ The provider is <strong>final</strong>. Switching between local / GitHub / GitLab later is not supported.</p>
      </div>
      <div data-provider-fields>${providerFields("local")}</div>
      <div class="button-row">
        <button class="btn btn-primary" type="submit">Add &amp; continue</button>
        <button class="btn btn-ghost" type="button" data-close>Cancel</button>
      </div>
    </form>`,
    { wide: true }
  );

  let provider = "local";

  node.addEventListener("click", (e) => {
    const seg = e.target.closest("[data-provider]");
    if (seg) {
      provider = seg.dataset.provider;
      node.querySelectorAll("[data-provider]").forEach((b) => b.classList.toggle("active", b === seg));
      node.querySelector("[data-provider-fields]").innerHTML = providerFields(provider);
      return;
    }
    if (e.target.closest("[data-close]")) closeModal();
  });

  node.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    const submit = e.target.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const created = await api.addRepo({
        target: String(data.target || "").trim(),
        provider,
        name: String(data.name || "").trim() || undefined,
      });
      await api.setup(created.id, {
        repo: String(data.repo || "").trim(),
        token: String(data.token || "").trim(),
      });
      closeModal();
      toast(`Added ${created.name}`, "ok");
      await ctx.selectRepo(created.id, "configuration");
    } catch (err) {
      submit.disabled = false;
      toast(err.message || String(err), "error");
    }
  });
}

// Re-run provider setup (e.g. rotate a token). Provider itself stays final.
export function openSetup(repo, ctx) {
  if (repo.provider === "local") {
    toast("Local repos need no setup.", "info");
    return;
  }
  const node = modal(`
    <form data-setup-form>
      <h2>${escapeHtml(repo.provider)} setup — ${escapeHtml(repo.name)}</h2>
      ${providerFields(repo.provider)}
      <div class="button-row">
        <button class="btn btn-primary" type="submit">Save</button>
        <button class="btn btn-ghost" type="button" data-close>Cancel</button>
      </div>
    </form>`);
  node.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) closeModal();
  });
  node.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      await api.setup(repo.id, { repo: String(data.repo || "").trim(), token: String(data.token || "").trim() });
      closeModal();
      await ctx.selectRepo(repo.id, "configuration");
    } catch (err) {
      toast(err.message || String(err), "error");
    }
  });
}
