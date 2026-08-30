"""agent_report.py - the structured result contract every agent run emits.

rc==0 is too weak a success signal: an agent can print "Blocked: cannot make
changes", touch nothing, exit 0 - and the old code advanced it to ``in_review``.
So each run must write a small JSON result file to the path in
``$SPECSEED_RESULT_FILE``; the runtime reads it to learn what ACTUALLY happened:

* implement -> ``status`` (done / blocked / needs_input) + a human ``summary``,
* review    -> ``verdict`` (approve / changes) + ``confidence`` + ``summary``.

The review ``summary`` is what lands on the tracker (no more head-truncated codex
banner + echoed prompt). No / invalid result file => the run did not really
finish => the caller treats it as a retryable failure.

Only Python stdlib is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Tuple

from specseed_runtime.state_machines.user_action import validate_request

# Env var the runtime sets per run; the agent writes its JSON result here.
RESULT_FILE_ENV = "SPECSEED_RESULT_FILE"

# Intents that own a strict result schema (match dispatch.AgentIntent values).
IMPLEMENT = "implement"
REVIEW = "review"
SPEC_CHANGE = "spec_change"
ASK = "ask"
PLATFORM_ERROR = "platform_error"

IMPLEMENT_STATUSES = ("done", "blocked", "needs_input", "needs_user_action")
REVIEW_VERDICTS = ("approve", "changes")
ASK_STATUSES = ("answered", "needs_input")

# Intents the runtime hard-gates on a valid report (a missing one is a failure).
STRICT_INTENTS = (IMPLEMENT, REVIEW, ASK)


def parse_result_file(path: str | Path, intent: str) -> Tuple[Optional[dict], Optional[str]]:
    """Read + validate the agent's result file for ``intent``.

    Returns ``(report, None)`` on success or ``(None, error)`` when the file is
    missing, unreadable, not JSON, or fails the per-intent schema. The error is a
    short string suitable for a recorded task error.
    """
    p = Path(path)
    if not p.exists():
        return None, "no result file at {0} (agent did not report)".format(p)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        return None, "result file unreadable: {0!r}".format(exc)
    raw = raw.strip()
    if not raw:
        return None, "result file is empty"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, "result file is not valid JSON: {0}".format(exc)
    if not isinstance(data, dict):
        return None, "result file must be a JSON object"
    return _validate(data, intent)


def _as_bool(value: Any) -> bool:
    """Coerce a JSON-ish flag to bool. Accepts real bools and "true"/"false"/1/0.

    Agents sometimes emit the value as a string. Anything not clearly truthy is
    False - the safe default (no spec-change recommended).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return False


def _validate(data: dict, intent: str) -> Tuple[Optional[dict], Optional[str]]:
    if intent == REVIEW:
        verdict = str(data.get("verdict") or "").strip().lower()
        if verdict not in REVIEW_VERDICTS:
            return None, "review result needs verdict in {0}".format(REVIEW_VERDICTS)
        try:
            confidence = float(data.get("confidence"))
        except (TypeError, ValueError):
            return None, "review result needs a numeric confidence"
        confidence = max(0.0, min(1.0, confidence))
        return {
            "verdict": verdict,
            "confidence": confidence,
            "summary": str(data.get("summary") or "").strip(),
            # Reviewer's recommendation that repeated failure is a SPEC problem, not
            # a coding miss. Only consulted once the review loop is exhausted.
            "recommend_spec_change": _as_bool(data.get("recommend_spec_change")),
        }, None
    if intent == IMPLEMENT:
        status = str(data.get("status") or "").strip().lower()
        if status not in IMPLEMENT_STATUSES:
            return None, "implement result needs status in {0}".format(IMPLEMENT_STATUSES)
        files = data.get("files_changed")
        files = [str(f) for f in files] if isinstance(files, list) else []
        report = {
            "status": status,
            "summary": str(data.get("summary") or "").strip(),
            "files_changed": files,
            # Implementer's up-front "I can't satisfy this; the spec is wrong" flag.
            # When set, the runtime blocks + drafts an adapt instead of reviewing.
            "recommend_spec_change": _as_bool(data.get("recommend_spec_change")),
        }
        # A needs_user_action run must say WHAT the human should do and how the runtime
        # will know it is done. Without that it is just `blocked` with extra prose - the
        # exact failure this status exists to replace - so the report is rejected.
        if status == "needs_user_action":
            request, error = validate_request(data.get("user_action"))
            if error:
                return None, "implement result status=needs_user_action: {0}".format(error)
            report["user_action"] = request
        return report, None
    if intent == ASK:
        status = str(data.get("status") or "").strip().lower()
        if status not in ASK_STATUSES:
            return None, "ask result needs status in {0}".format(ASK_STATUSES)
        answer = str(data.get("answer") or "").strip()
        if not answer:
            return None, "ask result needs a non-empty answer"
        return {"status": status, "answer": answer}, None
    # Loose schema for spec_change / platform_error and anything else: a summary
    # is handy but nothing is hard-gated on it.
    return {
        "status": str(data.get("status") or "").strip().lower(),
        "summary": str(data.get("summary") or "").strip(),
    }, None


_SCHEMA_BLOCK = {
    IMPLEMENT: (
        '  {"status": "done"|"blocked"|"needs_input"|"needs_user_action", "summary": '
        '"<one paragraph: what you did, or why you are blocked>", "files_changed": '
        '["<path>", ...], "recommend_spec_change": false}\n'
        "  Use status=done ONLY if you actually edited the repo to satisfy the issue and "
        "left it building/test-passing. Use status=blocked if you could not make the "
        "change (say why in summary); status=needs_input if you need a human decision. "
        "Set recommend_spec_change=true ONLY when the issue cannot be done as written "
        "because the SPEC itself is wrong/unclear (not a coding obstacle) - it blocks the "
        "issue and opens a draft spec-adapt for a human instead of reviewing.\n"
        "  Use status=needs_user_action when the obstacle is the MACHINE, not the code or "
        "the spec: a toolchain that is not installed, a service that is not running, or a "
        "directory outside the repo you need. Do NOT write a paragraph about what you are "
        "not allowed to do - add a `user_action` object and the human gets a card with a "
        "button instead of prose:\n"
        '    "user_action": {"kind": "environment", "title": "<short imperative, e.g. '
        'Install a JDK and Maven>", "instructions": "<markdown steps the human follows>", '
        '"setup": {"command": "<the ONE command that would do it, e.g. apt-get install -y '
        'default-jdk maven>", "description": "<one line: what it does>"}, '
        '"check": {"command": "<read-only shell probe that exits 0 once it is done, e.g. '
        'which mvn && mvn -v>", "timeout_seconds": 60}, "hint": "<what to try if the check '
        'fails>"}\n'
        '    "user_action": {"kind": "directory", "title": "<short imperative>", '
        '"instructions": "<why you need it / what you will write there>", "path": '
        '"<the ONE absolute directory outside the repo you need>", "reason": "<one line>"}\n'
        "  The check command is what un-parks the issue, so make it CHEAP and read-only "
        "(a `which`/`--version` probe, not a full build) - a human presses a button to run "
        "it, and it runs on their machine. ALWAYS include `setup` when the fix is a command "
        "you could name: a Run setup button then does it for the human in one click instead "
        "of sending them to a terminal, and the check runs straight after. It is not you "
        "running the command - a human presses the button and it runs as them. Omit `setup` "
        "ONLY when no single command could do it (plug in a device, obtain a licence, free "
        "up disk). Commit whatever work you did finish first: the issue resumes from your "
        "branch once the check passes."
    ),
    REVIEW: (
        '  {"verdict": "approve"|"changes", "confidence": <0.0-1.0>, "summary": '
        '"<concise findings: what is right/wrong vs the acceptance criteria>", '
        '"recommend_spec_change": false}\n'
        "  The summary is posted to the tracker for the next implementer - make it the "
        "actionable findings, not a transcript. Set recommend_spec_change=true only if "
        "the work keeps missing because the SPEC/issue scope is wrong rather than the "
        "code; it is consulted only after the review loop is exhausted."
    ),
    SPEC_CHANGE: (
        '  {"status": "<short status>", "summary": "<what the run decided>"}'
    ),
    ASK: (
        '  {"status": "answered"|"needs_input", "answer": "<the reply to post as a comment '
        'on the request post>"}\n'
        "  Put the FULL reply you composed (the answer, or a clarification round if the "
        "question is ambiguous) in `answer`, rendered in the form your reply protocol "
        "specifies for this mode - the runtime posts it verbatim as a comment. Use "
        "status=answered when you answered the question; status=needs_input when `answer` is a "
        "clarification round you need the human to reply to. You are READ-ONLY: do not post "
        "anything yourself, and do not edit code, spec, labels, or run git."
    ),
}


def result_instructions(intent: str) -> str:
    """The prompt block telling the agent to write its JSON result file.

    The path is runtime-owned (the ``$SPECSEED_RESULT_FILE`` env var), so the
    prompt stays static and the agent never needs to invent one.
    """
    schema = _SCHEMA_BLOCK.get(intent, _SCHEMA_BLOCK[SPEC_CHANGE])
    return (
        "RESULT FILE (required): when finished, write a single JSON object to the file "
        "whose path is in the {0} environment variable. Schema:\n{1}\n"
        "Write exactly that file (overwrite if it exists). The run is judged from this "
        "file, not from your console output, so do not skip it."
    ).format(RESULT_FILE_ENV, schema)
