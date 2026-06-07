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

# Env var the runtime sets per run; the agent writes its JSON result here.
RESULT_FILE_ENV = "SPECSEED_RESULT_FILE"

# Intents that own a strict result schema (match dispatch.AgentIntent values).
IMPLEMENT = "implement"
REVIEW = "review"
SPEC_CHANGE = "spec_change"
PLATFORM_ERROR = "platform_error"

IMPLEMENT_STATUSES = ("done", "blocked", "needs_input")
REVIEW_VERDICTS = ("approve", "changes")

# Intents the runtime hard-gates on a valid report (a missing one is a failure).
STRICT_INTENTS = (IMPLEMENT, REVIEW)


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
        }, None
    if intent == IMPLEMENT:
        status = str(data.get("status") or "").strip().lower()
        if status not in IMPLEMENT_STATUSES:
            return None, "implement result needs status in {0}".format(IMPLEMENT_STATUSES)
        files = data.get("files_changed")
        files = [str(f) for f in files] if isinstance(files, list) else []
        return {
            "status": status,
            "summary": str(data.get("summary") or "").strip(),
            "files_changed": files,
        }, None
    # Loose schema for spec_change / platform_error and anything else: a summary
    # is handy but nothing is hard-gated on it.
    return {
        "status": str(data.get("status") or "").strip().lower(),
        "summary": str(data.get("summary") or "").strip(),
    }, None


_SCHEMA_BLOCK = {
    IMPLEMENT: (
        '  {"status": "done"|"blocked"|"needs_input", "summary": "<one paragraph: '
        'what you did, or why you are blocked>", "files_changed": ["<path>", ...]}\n'
        "  Use status=done ONLY if you actually edited the repo to satisfy the issue and "
        "left it building/test-passing. Use status=blocked if you could not make the "
        "change (say why in summary); status=needs_input if you need a human decision."
    ),
    REVIEW: (
        '  {"verdict": "approve"|"changes", "confidence": <0.0-1.0>, "summary": '
        '"<concise findings: what is right/wrong vs the acceptance criteria>"}\n'
        "  The summary is posted to the tracker for the next implementer - make it the "
        "actionable findings, not a transcript."
    ),
    SPEC_CHANGE: (
        '  {"status": "<short status>", "summary": "<what the run decided>"}'
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
