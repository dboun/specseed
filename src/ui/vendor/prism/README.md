# Prism (vendored)

Syntax highlighter for the Code tab. **Prism v1.30.0**, MIT (see `LICENSE`).

`prism.min.js` is a single bundle: `prism-core.min.js` + the language components
below, concatenated in dependency order (a component `extend`s its base, so order
matters). Built by hand from the v1 `components/` dir — v1 is frozen (the project
moved to a v2 ESM rewrite), so there's no update script; re-bundle by hand if ever
needed.

Loaded as a classic global script (`window.Prism`) by `../../ui/highlight.js`,
which sets `Prism.manual` so core never auto-highlights the DOM. We don't use
Prism's HTML renderer — `highlight.js` calls `Prism.tokenize` and walks the token
tree into per-line HTML so the gutter survives multi-line tokens.

Languages (closure of requested set + curated enhancers):
core, clike, c, cpp, csharp, java, kotlin, scala, dart, objectivec, go, rust,
swift, javascript, typescript, jsx, tsx, js-templates, js-extras, jsdoc,
javadoclike, regex, python, ruby, perl, lua, r, php, markup, markup-templating,
markdown, css, css-extras, scss, sql, graphql, json, yaml, toml, ini, bash,
powershell, diff.

To add a language later: grab `prism-<id>.min.js` (+ its `require` chain) from
Prism v1.30.0 `components/`, append in dependency order to `prism.min.js`, and add
the ext mapping in `../../ui/highlight.js`.
