import { api } from "./api.js";

// Live "Agent output" popup. Tails a work task's stdout feed (work-output API)
// and re-renders the whole tail each tick - the server bounds it, so no diffing.
// Two modes:
//   autoClose:true  - the ephemeral comment-box opener: the popup closes itself
//                     the instant the run finishes (nothing is remembered).
//   autoClose:false - the Monitor 'Output' viewer: keeps streaming a live run,
//                     and for a finished run just shows the final output until the
//                     user closes it.
// It owns its own backdrop (data-agent-output-modal) so its lifecycle is fully
// independent of the generic modal() helper and of tab polling.
export function openAgentOutput(repoId, taskId, { autoClose = false } = {}) {
  document.querySelector("[data-agent-output-modal]")?.remove();
  const node = document.createElement("div");
  node.className = "modal-backdrop";
  node.dataset.agentOutputModal = "true";
  node.innerHTML = `
    <section class="modal-card wide agent-output-card">
      <div class="agent-output-head">
        <span class="agent-output-title"><span class="state-dot running"></span> Agent output</span>
        <button class="icon-btn" type="button" data-ao-close aria-label="close">✕</button>
      </div>
      <pre class="agent-output-body mono" data-ao-body>connecting…</pre>
    </section>`;
  document.body.append(node);

  const bodyEl = node.querySelector("[data-ao-body]");
  const dot = node.querySelector(".state-dot");
  let timer = null;
  let alive = true;
  const close = () => {
    alive = false;
    if (timer) clearTimeout(timer);
    node.remove();
  };
  node.addEventListener("click", (e) => {
    if (e.target === node || e.target.closest("[data-ao-close]")) close();
  });

  async function tick() {
    if (!alive) return;
    try {
      const d = await api.workOutput(repoId, taskId);
      if (!alive) return;
      // keep the view pinned to the bottom only if the user is already there
      const atBottom = bodyEl.scrollHeight - bodyEl.scrollTop - bodyEl.clientHeight < 40;
      bodyEl.textContent = d.text || (d.running ? "waiting for output…" : "(no output captured)");
      if (atBottom) bodyEl.scrollTop = bodyEl.scrollHeight;
      if (!d.running) {
        dot?.classList.remove("running");
        if (autoClose) return close();
        return; // viewer mode: stop polling, leave the final output up
      }
    } catch {
      /* transient; keep polling */
    }
    timer = setTimeout(tick, 1500);
  }
  tick();
  return { close };
}
