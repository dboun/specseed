export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function toast(message, kind = "info") {
  document.querySelector("[data-toast]")?.remove();
  const node = document.createElement("div");
  node.className = `toast toast-${kind}`;
  node.dataset.toast = "true";
  node.textContent = message;
  document.body.append(node);
  setTimeout(() => node.remove(), 3200);
}

export function modal(innerHtml, { wide = false } = {}) {
  document.querySelector("[data-modal]")?.remove();
  const node = document.createElement("div");
  node.className = "modal-backdrop";
  node.dataset.modal = "true";
  node.innerHTML = `<section class="modal-card ${wide ? "wide" : ""}">${innerHtml}</section>`;
  document.body.append(node);
  node.addEventListener("click", (e) => {
    if (e.target === node) node.remove();
  });
  return node;
}

export function closeModal() {
  document.querySelector("[data-modal]")?.remove();
}

export function relativeTime(value) {
  if (!value) return "never";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return String(value);
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 0) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// Absolute local time, "YYYY-MM-DD HH:MM:SS" (browser timezone, not UTC).
export function formatTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

const REACTION_ICONS = {
  eyes: "\u{1F440}",
  heart: "❤️",
  thumbs_up: "\u{1F44D}",
  thumbs_down: "\u{1F44E}",
};

export const reactionIcon = (kind) => REACTION_ICONS[kind] || "•";
