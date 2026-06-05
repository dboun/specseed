export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function selectOptions(name, options) {
  return `
    <select name="${escapeHtml(name)}">
      ${(options || []).map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`).join("")}
    </select>
  `;
}

export function toast(message) {
  document.querySelector("[data-toast]")?.remove();
  const node = document.createElement("div");
  node.className = "toast";
  node.dataset.toast = "true";
  node.textContent = message;
  document.body.append(node);
  setTimeout(() => node.remove(), 2600);
}

export function modal(innerHtml) {
  document.querySelector("[data-modal]")?.remove();
  const node = document.createElement("div");
  node.className = "modal-backdrop";
  node.dataset.modal = "true";
  node.innerHTML = `<section class="modal-card">${innerHtml}</section>`;
  document.body.append(node);
}
