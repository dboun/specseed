// Minimal markdown renderer (no deps). Escape-first: every text fragment is
// HTML-escaped before any tags are added, so post/comment content can't inject
// markup. Covers: headings, lists+sublists, blockquotes, fenced code, inline
// code, bold/italic, links (+bare-url autolink), tables (wrapped, x-scroll),
// hr. Single newlines inside a paragraph stay visible as line breaks - tracker
// posts are often plain text.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

// Opt-in (per renderMarkdown call): linkify bare #NN post refs. Off by default so
// docs/readmes outside the tracker (where the popup click handler isn't wired)
// don't grow dead links. Set synchronously around a single render - renderMarkdown
// is sync, so no reentrancy.
let LINK_POST_REFS = false;

// inline transforms over an already-escaped line
function inline(raw) {
  let text = escapeHtml(raw);
  // protect code spans from the other inline rules
  const slots = [];
  text = text.replace(/`([^`]+)`/g, (_m, code) => {
    slots.push(`<code>${code}</code>`);
    return `\x00${slots.length - 1}\x00`;
  });
  // [label](url) - allow http(s)/mailto/anchor/relative, never javascript:
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) =>
    /^(https?:|mailto:|#|\/|\.)/i.test(url)
      ? `<a href="${url}" target="_blank" rel="noreferrer">${label}</a>`
      : m
  );
  // bare urls (not the href we just emitted: those are preceded by " or >)
  text = text.replace(
    /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    `$1<a href="$2" target="_blank" rel="noreferrer">$2</a>`
  );
  // bare post refs #NN -> in-app popup opener (github/gitlab autolink these
  // natively; we mirror that on the UI). The [^\w&"] guard skips word-internal
  // hits, HTML entities (escapeHtml turns ' into &#39, so never match #39 right
  // after &), and the inside of an emitted href="#frag" (a `#` right after a
  // quote). Code spans are already slotted out above, so `#12` stays literal.
  if (LINK_POST_REFS) {
    text = text.replace(
      /(^|[^\w&"])#(\d+)\b/g,
      `$1<a class="post-ref" data-popup-post="$2">#$2</a>`
    );
  }
  text = text
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/__([^_]+)__/g, "<b>$1</b>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/(^|[^_\w])_([^_\n]+)_(?=[^_\w]|$)/g, "$1<i>$2</i>");
  return text.replace(/\x00(\d+)\x00/g, (_m, i) => slots[+i]);
}

const LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP_RE = /^\s*\|?[\s:-]+(\|[\s:-]+)+\|?\s*$/;

// items: [{indent, ordered, text}] -> nested <ul>/<ol>; a marker-type flip at
// the same level starts a new list (ol after ul, etc.)
function buildList(items) {
  let out = "";
  let k = 0;
  while (k < items.length) {
    const ordered = items[k].ordered;
    out += ordered ? "<ol>" : "<ul>";
    while (k < items.length && items[k].ordered === ordered) {
      const base = items[k].indent;
      let j = k + 1;
      while (j < items.length && items[j].indent > base) j++;
      const kids = items.slice(k + 1, j);
      out += `<li>${inline(items[k].text)}${kids.length ? buildList(kids) : ""}</li>`;
      k = j;
    }
    out += ordered ? "</ol>" : "</ul>";
  }
  return out;
}

function tableBlock(lines, start) {
  const row = (line) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim());
  const head = row(lines[start]);
  let i = start + 2; // skip the |---| separator
  let body = "";
  for (; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) {
    body += `<tr>${row(lines[i]).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`;
  }
  const html = `<div class="md-table"><table>
    <thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>
    <tbody>${body}</tbody></table></div>`;
  return { html, next: i };
}

function blocks(lines) {
  let html = "";
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    // fenced code
    if (/^```/.test(line.trim())) {
      let j = i + 1;
      const buf = [];
      while (j < lines.length && !/^```/.test(lines[j].trim())) buf.push(lines[j++]);
      html += `<pre class="md-code">${escapeHtml(buf.join("\n"))}</pre>`;
      i = j + 1;
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`;
      i++;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      html += "<hr>";
      i++;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      html += `<blockquote>${blocks(buf)}</blockquote>`;
      continue;
    }
    if (LIST_RE.test(line)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(LIST_RE);
        if (!m) break;
        items.push({ indent: m[1].length, ordered: /\d/.test(m[2]), text: m[3] });
        i++;
      }
      html += buildList(items);
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1])) {
      const t = tableBlock(lines, i);
      html += t.html;
      i = t.next;
      continue;
    }
    // paragraph: consecutive plain lines, single newlines kept as <br>
    const buf = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,6}\s|```|\s*>)/.test(lines[i]) &&
      !LIST_RE.test(lines[i]) &&
      !/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i]) &&
      !(lines[i].includes("|") && TABLE_SEP_RE.test(lines[i + 1] || ""))
    ) {
      buf.push(lines[i++]);
    }
    html += `<p>${buf.map(inline).join("<br>")}</p>`;
  }
  return html;
}

export function renderMarkdown(source, { postRefs = false } = {}) {
  LINK_POST_REFS = postRefs;
  try {
    const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
    return `<div class="md">${blocks(lines)}</div>`;
  } finally {
    LINK_POST_REFS = false;
  }
}
