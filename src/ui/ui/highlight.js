// Syntax highlighting for the Code tab, over vendored Prism (v1.30.0, MIT) at
// vendor/prism/prism.min.js. No build, no deps - the bundle is a classic global
// script we inject once on first use. We DON'T use Prism's own DOM highlighter:
// we call Prism.tokenize to get the structured token tree, then walk it into ONE
// HTML string per source line so the Code tab's per-line gutter stays intact
// (a naive highlight()+split breaks spans that cross newlines - block comments,
// template strings). Every leaf is HTML-escaped, same XSS posture as plain view.

const BUNDLE = "../vendor/prism/prism.min.js"; // relative to this module

// ext (no leading dot, lowercased) -> prism language id. Only ids the bundle
// actually carries; an unknown ext returns null and the caller falls back to
// plain escaped text, so this is never worse than no highlighting.
const EXT_LANG = {
  js: "javascript", mjs: "javascript", cjs: "javascript",
  jsx: "jsx",
  ts: "typescript", mts: "typescript", cts: "typescript",
  tsx: "tsx",
  py: "python", pyw: "python", pyi: "python",
  rb: "ruby",
  go: "go",
  rs: "rust",
  java: "java",
  kt: "kotlin", kts: "kotlin",
  swift: "swift",
  c: "c", h: "c",
  cpp: "cpp", cc: "cpp", cxx: "cpp", "c++": "cpp",
  hpp: "cpp", hh: "cpp", hxx: "cpp",
  cs: "csharp",
  php: "php", phtml: "php",
  sql: "sql",
  css: "css",
  scss: "scss",
  html: "markup", htm: "markup", xml: "markup", svg: "markup",
  xhtml: "markup", vue: "markup", xsl: "markup", xslt: "markup",
  md: "markdown", markdown: "markdown",
  json: "json", json5: "json", jsonc: "json",
  yaml: "yaml", yml: "yaml",
  toml: "toml",
  ini: "ini", cfg: "ini", conf: "ini",
  sh: "bash", bash: "bash", zsh: "bash",
  lua: "lua",
  pl: "perl", pm: "perl",
  r: "r",
  dart: "dart",
  scala: "scala", sc: "scala",
  m: "objectivec", mm: "objectivec",
  graphql: "graphql", gql: "graphql",
  diff: "diff", patch: "diff",
  ps1: "powershell", psm1: "powershell", psd1: "powershell",
};

// a few extension-less filenames worth recognising
const NAME_LANG = {
  dockerfile: null, // not bundled; left here as a documented no-op marker
  makefile: null,
  ".bashrc": "bash", ".zshrc": "bash", ".profile": "bash",
};

let prismPromise = null;

// Inject the bundle once. Prism core auto-highlights the DOM unless told not to;
// we set manual BEFORE it runs (and the bundle is built data-manual-friendly).
export function loadPrism() {
  if (prismPromise) return prismPromise;
  if (typeof window !== "undefined" && window.Prism && window.Prism.tokenize) {
    prismPromise = Promise.resolve(window.Prism);
    return prismPromise;
  }
  prismPromise = new Promise((resolve, reject) => {
    window.Prism = window.Prism || {};
    window.Prism.manual = true; // read by core at init -> no auto highlightAll
    const url = new URL(BUNDLE, import.meta.url).href;
    const s = document.createElement("script");
    s.src = url;
    s.async = false;
    s.onload = () => resolve(window.Prism);
    s.onerror = () => reject(new Error("failed to load prism bundle"));
    document.head.appendChild(s);
  });
  return prismPromise;
}

// Resolve a prism language id from a file's ext (and optionally its full path,
// for extension-less names). Returns null when we don't highlight it.
export function langFor(ext, path) {
  const e = String(ext || "").toLowerCase();
  if (e && EXT_LANG[e]) return EXT_LANG[e];
  const name = String(path || "").split("/").pop().toLowerCase();
  if (name in NAME_LANG) return NAME_LANG[name];
  return null;
}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

// Walk a Prism token stream into one HTML string per source line. A token whose
// content spans a newline (block comment, multi-line string) is closed at the
// line break and re-opened on the next line, so every emitted line is its own
// balanced fragment - exactly what the gutter renderer wants. Returns null if
// Prism isn't loaded yet or the language is unknown.
export function highlightLines(text, lang) {
  const P = typeof window !== "undefined" ? window.Prism : null;
  if (!P || !P.tokenize || !lang || !P.languages[lang]) return null;
  const tokens = P.tokenize(String(text ?? ""), P.languages[lang]);
  const rows = [""];
  const stack = []; // class strings of currently-open spans
  const emit = (h) => (rows[rows.length - 1] += h);
  const reopen = () => stack.map((c) => `<span class="token ${c}">`).join("");

  function addText(s) {
    const parts = s.split("\n");
    for (let i = 0; i < parts.length; i++) {
      if (i > 0) {
        emit("</span>".repeat(stack.length)); // close on the old line
        rows.push("");
        emit(reopen()); // re-open on the new line
      }
      emit(escapeHtml(parts[i]));
    }
  }
  function walk(t) {
    if (typeof t === "string") return addText(t);
    if (Array.isArray(t)) return t.forEach(walk);
    const cls = t.type + (t.alias ? " " + (Array.isArray(t.alias) ? t.alias.join(" ") : t.alias) : "");
    stack.push(cls);
    emit(`<span class="token ${cls}">`);
    walk(t.content);
    stack.pop();
    emit("</span>");
  }
  tokens.forEach(walk);
  if (rows.length && rows[rows.length - 1] === "") rows.pop(); // drop trailing-newline ghost
  return rows;
}
