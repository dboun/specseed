// Structured feedback rendering: the skill's reply-protocol envelope (main_body +
// questions) rendered as an INTERACTIVE card instead of a raw JSON comment, plus
// the compact user reply rendered as a "you answered" card instead of raw JSON.
//
// Contract: skills/specseed/references/reply-protocol-base.md. In specseed-UI mode
// the skill posts ONE comment whose body is `{schema_version, user_facing_thread_entry}`.
// The human's reply we post back is `{schema_version, specseed_reply}` - compact,
// carries only theme + question + the chosen answer (never the whole options block).
//
// Pure render + parse helpers only. State + events live in tracker.js.

import { escapeHtml } from "../ui/components.js";
import { renderMarkdown } from "../ui/markdown.js";

const SCHEMA_VERSION = 1;
const OTHER = "__other"; // sentinel choice for a free-typed multiple-choice answer

// -- parse ------------------------------------------------------------------ #
// A comment body is one of ours only if it is a clean JSON object with the marker
// key. Anything else (prose, half-JSON) falls through to the plain renderer.
function parseEnvelope(comment) {
  const b = (comment?.body || "").trim();
  if (b[0] !== "{") return null;
  try {
    return JSON.parse(b);
  } catch {
    return null;
  }
}

// The agent's outbound round, or null. Validates shape enough to render safely.
export function feedbackEntry(comment) {
  const e = parseEnvelope(comment)?.user_facing_thread_entry;
  return e && Array.isArray(e.sections) ? e : null;
}

// The human's structured reply, or null.
export function replyPayload(comment) {
  const r = parseEnvelope(comment)?.specseed_reply;
  return r && Array.isArray(r.answers) ? r : null;
}

// Flatten every question across all `questions` sections, in order.
export function entryQuestions(entry) {
  const out = [];
  for (const s of entry.sections || []) {
    if (s.section_type === "questions") for (const q of s.content?.questions || []) out.push(q);
  }
  return out;
}

// -- answer state ----------------------------------------------------------- #
// Seed each answer with the agent's SUGGESTION so a fully-suggested round is
// already "answered" - the human can Send with one click (the protocol's point).
export function initAnswers(entry) {
  const answers = {};
  entryQuestions(entry).forEach((q, i) => {
    if (q.question_type === "multiple_choice") answers[i] = { choice: q.suggested_answer || null, other: "", note: "" };
    else if (q.question_type === "open_ended") answers[i] = { text: "", skip: false };
    else if (q.question_type === "assumptions_check") answers[i] = { rejected: [], note: "" };
    else answers[i] = {};
  });
  return answers;
}

function qAnswered(q, a) {
  if (!a) return false;
  if (q.question_type === "multiple_choice") return a.choice === OTHER ? a.other.trim().length > 0 : !!a.choice;
  if (q.question_type === "open_ended") return a.skip || a.text.trim().length > 0;
  return true; // assumptions_check: default-accept is always a valid answer
}

export function allAnswered(entry, answers) {
  return entryQuestions(entry).every((q, i) => qAnswered(q, answers[i]));
}

// Compact reply: theme + question + a human-readable answer string per question.
// Deliberately NOT the options/confidences/context - that lives in the round above.
export function buildReplyJson(entry, answers) {
  const out = entryQuestions(entry).map((q, i) => {
    const a = answers[i] || {};
    const row = { theme: q.question_theme || "", question: q.question || "", type: q.question_type };
    if (q.question_type === "multiple_choice") {
      if (a.choice === OTHER) row.answer = a.other.trim();
      else {
        const opt = (q.options || {})[a.choice];
        row.answer = opt ? `${a.choice}) ${opt.option}` : String(a.choice ?? "");
      }
      if (a.note?.trim()) row.note = a.note.trim();
    } else if (q.question_type === "open_ended") {
      row.answer = a.skip ? "(skipped)" : a.text.trim();
    } else if (q.question_type === "assumptions_check") {
      const rej = (q.assumptions || []).filter((_, k) => a.rejected.includes(k)).map((x) => x.assumption);
      row.answer = rej.length ? `rejected: ${rej.join("; ")}` : "accepted all";
      if (a.note?.trim()) row.note = a.note.trim();
    }
    return row;
  });
  return JSON.stringify({ schema_version: SCHEMA_VERSION, specseed_reply: { answers: out } }, null, 2);
}

// -- render: shared bits ---------------------------------------------------- #
const pct = (c) => Math.max(0, Math.min(100, Math.round((Number(c) || 0) * 100)));
const confBar = (c) =>
  c == null
    ? ""
    : `<span class="fb-conf" title="confidence ${pct(c)}%"><span class="fb-conf-fill" style="width:${pct(c)}%"></span></span>`;

function sectionsProse(entry) {
  // Every section that carries prose: main_body content, plus each questions
  // section's round header. main_body is always first per the protocol.
  let html = "";
  for (const s of entry.sections || []) {
    const md = s.content?.markdown;
    if (s.section_type === "main_body" && md) html += `<div class="fb-body">${renderMarkdown(md)}</div>`;
  }
  return html;
}

// -- render: one question (active = editable) ------------------------------- #
function mcQuestion(q, i, cid, a, active) {
  const opts = q.options || {};
  const rows = Object.keys(opts)
    .map((key) => {
      const o = opts[key];
      const on = a?.choice === key;
      const sug = q.suggested_answer === key;
      const attrs = active ? `data-fb-act="choice" data-fb-cid="${cid}" data-fb-q="${i}" data-fb-choice="${escapeHtml(key)}"` : "disabled";
      return `<button type="button" class="fb-opt ${on ? "on" : ""}" ${attrs}>
        <span class="fb-opt-key">${escapeHtml(key)}</span>
        <span class="fb-opt-main">
          <span class="fb-opt-label">${escapeHtml(o.option || "")}${sug ? `<span class="fb-sug">suggested</span>` : ""}</span>
          ${o.option_markdown ? `<span class="fb-opt-detail">${renderMarkdown(o.option_markdown)}</span>` : ""}
        </span>
        ${confBar((q.confidence || {})[key])}
      </button>`;
    })
    .join("");
  const otherOn = a?.choice === OTHER;
  const other =
    q.allow_other_option && active
      ? `<div class="fb-opt fb-opt-other ${otherOn ? "on" : ""}" data-fb-act="choice" data-fb-cid="${cid}" data-fb-q="${i}" data-fb-choice="${OTHER}">
          <span class="fb-opt-key">+</span>
          <input class="fb-other" data-fb-act="other" data-fb-cid="${cid}" data-fb-q="${i}" placeholder="something else…" value="${escapeHtml(a?.other || "")}" />
        </div>`
      : "";
  const note =
    q.allow_user_selection_comment && active
      ? `<input class="fb-note" data-fb-act="note" data-fb-cid="${cid}" data-fb-q="${i}" placeholder="add a note (optional)" value="${escapeHtml(a?.note || "")}" />`
      : "";
  const justify =
    q.suggested_answer && q.suggested_answer_justification
      ? `<div class="fb-justify"><b>${escapeHtml(q.suggested_answer)}</b> — ${escapeHtml(q.suggested_answer_justification)}</div>`
      : "";
  return `<div class="fb-opts">${rows}${other}</div>${justify}${note}`;
}

function openQuestion(q, i, cid, a, active) {
  if (!active) return "";
  const skip = q.allow_skip
    ? `<label class="fb-skip"><input type="checkbox" data-fb-act="skip" data-fb-cid="${cid}" data-fb-q="${i}" ${a?.skip ? "checked" : ""} /> skip this</label>`
    : "";
  return `<textarea class="fb-text" data-fb-act="text" data-fb-cid="${cid}" data-fb-q="${i}" placeholder="your answer…" ${a?.skip ? "disabled" : ""}>${escapeHtml(a?.text || "")}</textarea>${skip}`;
}

function assumeQuestion(q, i, cid, a, active) {
  const rows = (q.assumptions || [])
    .map((x, k) => {
      const rejected = a?.rejected.includes(k);
      const btn = active
        ? `<button type="button" class="fb-check ${rejected ? "off" : ""}" data-fb-act="assume" data-fb-cid="${cid}" data-fb-q="${i}" data-fb-a="${k}" aria-label="${rejected ? "rejected" : "holds"}">${rejected ? "✕" : "✓"}</button>`
        : `<span class="fb-check ${rejected ? "off" : ""}">${rejected ? "✕" : "✓"}</span>`;
      return `<div class="fb-assume ${rejected ? "off" : ""}">
        ${btn}<span class="fb-assume-text">${escapeHtml(x.assumption || "")}</span>${confBar(x.confidence)}</div>`;
    })
    .join("");
  const note =
    q.allow_user_deselection_comment && active
      ? `<input class="fb-note" data-fb-act="note" data-fb-cid="${cid}" data-fb-q="${i}" placeholder="why? (optional)" value="${escapeHtml(a?.note || "")}" />`
      : "";
  const hint = active ? `<div class="fb-assume-hint">Keep the ones that hold; clear any that are wrong.</div>` : "";
  return `${hint}<div class="fb-assumes">${rows}</div>${note}`;
}

function questionBody(q, i, cid, a, active) {
  if (q.question_type === "multiple_choice") return mcQuestion(q, i, cid, a, active);
  if (q.question_type === "open_ended") return openQuestion(q, i, cid, a, active);
  if (q.question_type === "assumptions_check") return assumeQuestion(q, i, cid, a, active);
  return "";
}

function questionCard(q, i, cid, a, active) {
  const ctx = q.question_context_markdown ? `<div class="fb-q-ctx">${renderMarkdown(q.question_context_markdown)}</div>` : "";
  return `<section class="fb-q">
    <div class="fb-q-head"><span class="fb-q-num">${i + 1}</span><span class="fb-q-theme">${escapeHtml(q.question_theme || "")}</span></div>
    ${ctx}
    ${q.question ? `<div class="fb-q-ask">${escapeHtml(q.question)}</div>` : ""}
    ${questionBody(q, i, cid, a, active)}
  </section>`;
}

// -- render: the active interactive card ------------------------------------ #
// `fb` = {mode, commentDraft, answers}. `active` true => editable + owns composer.
export function renderFeedbackCard({ comment, entry, active, fb }) {
  const cid = escapeHtml(String(comment.id));
  const questions = entryQuestions(entry);

  if (!active) return resolvedCard(comment, entry, questions);

  const roundHeader = (() => {
    const s = (entry.sections || []).find((x) => x.section_type === "questions");
    return s?.content?.markdown ? `<div class="fb-round">${renderMarkdown(s.content.markdown)}</div>` : "";
  })();
  const footMsg = (() => {
    const s = (entry.sections || []).find((x) => x.section_type === "questions");
    return s?.content?.post_questions_message?.markdown
      ? `<div class="fb-footmsg">${renderMarkdown(s.content.post_questions_message.markdown)}</div>`
      : "";
  })();

  const qs = questions.map((q, i) => questionCard(q, i, String(comment.id), fb.answers[i], true)).join("");
  const ready = allAnswered(entry, fb.answers);

  const actions =
    fb.mode === "comment"
      ? `<form class="fb-comment-form" data-fb-comment-form="${cid}">
          <textarea data-fb-act="comment-text" data-fb-cid="${cid}" placeholder="comment instead of answering…" required>${escapeHtml(fb.commentDraft || "")}</textarea>
          <div class="button-row">
            <button type="submit" class="btn btn-primary">Comment</button>
            <button type="button" class="btn btn-ghost" data-fb-act="undo" data-fb-cid="${cid}">Undo</button>
          </div>
        </form>`
      : `<div class="button-row">
          <button type="button" class="btn btn-primary" data-fb-act="send" data-fb-cid="${cid}" ${ready ? "" : "disabled"}>Send reply</button>
          <button type="button" class="btn btn-ghost" data-fb-act="comment-mode" data-fb-cid="${cid}">Comment instead</button>
        </div>`;

  return `<article class="fb-card" data-feedback-card data-comment-id="${cid}">
    <div class="fb-eyebrow"><span class="fb-spark"></span>specseed · needs your input</div>
    ${sectionsProse(entry)}
    ${roundHeader}
    ${qs}
    ${footMsg}
    <div class="fb-actions">${actions}</div>
  </article>`;
}

// Past round, already answered: keep the thread clean - prose + a foldaway recap.
function resolvedCard(comment, entry, questions) {
  const cid = escapeHtml(String(comment.id));
  const recap = questions.length
    ? `<details class="fb-recap"><summary>${questions.length} question${questions.length > 1 ? "s" : ""} asked</summary>
        <ol class="fb-recap-list">${questions.map((q) => `<li><b>${escapeHtml(q.question_theme || "")}</b> — ${escapeHtml(q.question || "")}</li>`).join("")}</ol>
       </details>`
    : "";
  return `<article class="fb-card resolved" data-comment-id="${cid}">
    <div class="fb-eyebrow muted"><span class="fb-spark off"></span>specseed · answered</div>
    ${sectionsProse(entry)}
    ${recap}
  </article>`;
}

// -- render: the human's structured reply ----------------------------------- #
export function renderReplyCard({ comment, reply }) {
  const cid = escapeHtml(String(comment.id));
  const rows = (reply.answers || [])
    .map(
      (r) => `<li>
        <span class="fb-reply-theme">${escapeHtml(r.theme || r.question || "")}</span>
        <span class="fb-reply-ans">${escapeHtml(r.answer ?? "")}</span>
        ${r.note ? `<span class="fb-reply-note">“${escapeHtml(r.note)}”</span>` : ""}
      </li>`
    )
    .join("");
  return `<article class="fb-reply" data-comment-id="${cid}">
    <div class="fb-eyebrow"><span class="fb-spark sent"></span>${escapeHtml(comment.author || "you")} · replied</div>
    <ul class="fb-reply-list">${rows || `<li class="muted">no answers</li>`}</ul>
  </article>`;
}
