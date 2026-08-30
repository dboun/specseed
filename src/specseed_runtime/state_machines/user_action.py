"""user_action.py - the UA-NNNN user-action request: format + parsing (pure, stdlib).

Some work needs a HUMAN to change the machine, not the repo. The implementer that
wrote a correct Maven scaffold and then found no JDK anywhere could not verify it,
and had no way to say so beyond prose: it reported ``blocked`` with a paragraph, the
issue sat there for a week and every dependent starved on the dependency gate.

A *user-action request* is the answer. The agent emits a structured request, the
runtime parks the issue ``needs_user_action``, and the UI renders it as a card with
INSTRUCTIONS the human follows and a **Check** button that re-runs the agent's own
verification. Passing the check un-parks the issue; failing it comes back with the
command's output and the agent's hint, so the human learns how to proceed rather
than seeing a red cross.

Two kinds share the shape:

* ``environment`` - "install a JDK and Maven". Carries a ``check`` command the Check
  button runs (``which mvn && mvn -v``), and OPTIONALLY a ``setup`` command a **Run
  setup** button runs for the human (``apt-get install -y default-jdk maven``) so they
  never have to leave the UI for a terminal. Optional because not everything can be
  automated - plugging in a USB key or buying a licence still needs hands - and without
  it the card degrades to exactly instructions + Check.
* ``directory`` - "I need to write in ``/var/tmp/nooks-build``". Carries the specific
  ``path``; approving it appends that directory to
  ``permissions.agents.allowed_directories`` instead of loosening the blanket
  ``outside_repo`` gate.

This module owns only the request VOCABULARY (token, marker, payload validation,
comment rendering) so both the worker-facing helpers (``executing/user_action``) and
the UI can share it without a circular import - ``state_machines`` must never import
``executing``. Runtime concerns (allocating the next id, actually running the check)
live in ``executing/user_action``.

Token shape: ``UA-`` + a zero-padded, >=4-digit, monotonically allocated number,
e.g. ``UA-0001``. Case-insensitive on read, upper-cased on write.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional, Tuple

# The work status an issue parks in while a user action is outstanding. It is a
# real tracker label (`<tier>:status:needs_user_action`, see tracking/supported_values).
NEEDS_USER_ACTION = "needs_user_action"

# Request kinds. `environment` = change the machine (install a toolchain, start a
# service); `directory` = grant the agent one specific directory outside the repo.
KIND_ENVIRONMENT = "environment"
KIND_DIRECTORY = "directory"
KINDS = (KIND_ENVIRONMENT, KIND_DIRECTORY)

# A check that hangs must not hang the UI request that runs it.
DEFAULT_CHECK_TIMEOUT = 60
MAX_CHECK_TIMEOUT = 600

# Setup gets far longer than a check, and deliberately so: a check is contractually a
# cheap read-only probe, while a setup command is an install that legitimately spends
# minutes fetching packages. Killing `apt-get install default-jdk` at 60s would make the
# button useless.
DEFAULT_SETUP_TIMEOUT = 900
MAX_SETUP_TIMEOUT = 3600

# A bare token anywhere in text, e.g. in a marker or in prose.
UA_RE = re.compile(r"\bUA-(\d{4,})\b", re.IGNORECASE)

# The hidden marker the worker stamps on a user-action request comment. It carries
# the whole payload as JSON so the UI can render the card (and the Check endpoint can
# recover the command) without a side channel.
USER_ACTION_MARKER = "specseed:user-action"
_REQUEST_MARKER_RE = re.compile(
    r"<!--\s*"
    + re.escape(USER_ACTION_MARKER)
    + r"\s+(UA-\d{4,})\s+(\{.*?\})\s*-->",
    re.IGNORECASE | re.DOTALL,
)


def format_ua(n: int) -> str:
    """Render an integer as a canonical ``UA-NNNN`` token (>=4 digits)."""
    return f"UA-{int(n):04d}"


def ua_number(token: str) -> Optional[int]:
    """Return the integer in a ``UA-NNNN`` token, or None if it is not one."""
    match = UA_RE.fullmatch(token.strip()) if token else None
    return int(match.group(1)) if match else None


# -- payload ------------------------------------------------------------------ #
def _validate_setup(data: Any) -> Tuple[Optional[dict], Optional[str]]:
    """Validate the OPTIONAL ``setup`` on an ``environment`` request.

    ``(None, None)`` means "this request has no setup command", which is a legitimate
    answer: some environment work genuinely cannot be scripted. Only an absent or empty
    value says that. A DICT is a deliberate structure, so one that carries no command is
    an ERROR rather than a silent drop - the agent meant to offer the one-click path, and
    dropping it would look identical to never having offered one.
    """
    if data is None or data == "":
        return None, None
    if isinstance(data, str):
        data = {"command": data}
    if not isinstance(data, dict):
        return None, "user_action.setup must be a JSON object"
    command = str(data.get("command") or "").strip()
    if not command:
        return None, (
            "user_action.setup needs a command - omit `setup` entirely when the human "
            "has to do it by hand"
        )
    try:
        timeout = int(data.get("timeout_seconds") or DEFAULT_SETUP_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_SETUP_TIMEOUT
    return {
        "command": command,
        # One line the human reads before deciding to press the button. Not required:
        # a bare `apt-get install -y maven` explains itself.
        "description": str(data.get("description") or "").strip(),
        "timeout_seconds": max(1, min(MAX_SETUP_TIMEOUT, timeout)),
    }, None


def validate_request(data: Any, kind_default: str = KIND_ENVIRONMENT) -> Tuple[Optional[dict], Optional[str]]:
    """Validate an agent-supplied ``user_action`` object.

    Returns ``(request, None)`` or ``(None, error)``. The error is short enough to
    record as a task error and to show the human, because a malformed request is the
    agent's mistake and someone has to see it.

    An ``environment`` request MUST carry a check command: a request that cannot be
    verified degenerates into "human types done", which is exactly the UX this
    replaces. Its ``setup`` command is OPTIONAL, but a malformed one is rejected rather
    than dropped - silently losing the one-click path would look identical to an agent
    that never offered it. A ``directory`` request MUST name one concrete absolute path -
    "somewhere outside the repo" is not something a human can approve.
    """
    if not isinstance(data, dict):
        return None, "user_action must be a JSON object"
    kind = str(data.get("kind") or kind_default).strip().lower()
    if kind not in KINDS:
        return None, "user_action.kind must be one of {0}".format(KINDS)
    title = str(data.get("title") or "").strip()
    if not title:
        return None, "user_action needs a title"
    instructions = str(data.get("instructions") or "").strip()
    if not instructions:
        return None, "user_action needs instructions the human can follow"
    request: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "instructions": instructions,
        "hint": str(data.get("hint") or "").strip(),
    }
    if kind == KIND_ENVIRONMENT:
        check = data.get("check")
        if isinstance(check, str):
            check = {"command": check}
        if not isinstance(check, dict):
            return None, "environment user_action needs a check object"
        command = str(check.get("command") or "").strip()
        if not command:
            return None, "environment user_action needs check.command"
        try:
            timeout = int(check.get("timeout_seconds") or DEFAULT_CHECK_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_CHECK_TIMEOUT
        timeout = max(1, min(MAX_CHECK_TIMEOUT, timeout))
        request["check"] = {"command": command, "timeout_seconds": timeout}
        setup, error = _validate_setup(data.get("setup"))
        if error:
            return None, error
        if setup:
            request["setup"] = setup
        return request, None
    path = str(data.get("path") or "").strip()
    if not path:
        return None, "directory user_action needs the specific path it wants"
    request["path"] = path
    request["reason"] = str(data.get("reason") or "").strip()
    return request, None


def _body(item: Any) -> Optional[str]:
    if isinstance(item, dict):
        return item.get("body")
    return getattr(item, "body", None)


def parse_request(body: Optional[str]) -> Optional[dict]:
    """The user-action payload stamped in ``body``, or None.

    Only a well-formed ``<!-- specseed:user-action UA-NNNN {...} -->`` marker counts,
    so a token merely mentioned in prose is not treated as a live request. The returned
    dict carries the validated fields plus its ``id``.
    """
    if not body:
        return None
    match = _REQUEST_MARKER_RE.search(str(body))
    if not match:
        return None
    try:
        payload = json.loads(match.group(2))
    except ValueError:
        return None
    request, error = validate_request(payload)
    if error:
        return None
    request["id"] = match.group(1).upper()
    return request


def open_requests(conversation: Iterable[Any] | None) -> list[dict]:
    """Every user-action request in a conversation, oldest first, de-duped by id.

    A later comment carrying the same id replaces the earlier one, so an agent that
    re-states a request does not produce two cards.
    """
    found: dict[str, dict] = {}
    for item in conversation or []:
        request = parse_request(_body(item))
        if request:
            found[request["id"]] = request
    return list(found.values())


# -- rendering ---------------------------------------------------------------- #
def request_comment(ua_id: str, request: dict) -> str:
    """The comment body that carries a user-action request.

    Human-readable markdown first (this is what a GitHub/GitLab reader sees), then the
    machine-readable marker. The check command is shown in full: the human decides to
    run it, so they have to be able to read it first.
    """
    payload = dict(request)
    payload.pop("id", None)
    setup = (request.get("setup") or {}) if request.get("kind") != KIND_DIRECTORY else {}
    # With a setup command the human never has to leave the UI, so lead with that button
    # rather than with steps they would have to carry to a terminal.
    how = (
        "Press **Run setup** in the Need feedback tab and specseed runs the command below "
        "for you, then verifies it. Prefer to do it yourself? Follow the steps and press "
        "**Check**. Comment here if anything is unclear."
        if setup.get("command")
        else "Follow the steps, then press **Check** in the Need feedback tab (or comment "
        "here if anything is unclear)."
    )
    lines = [
        "**Needs you: {0}**".format(request.get("title") or ua_id),
        "",
        "This issue is parked `needs_user_action` until this is done. " + how,
        "",
        str(request.get("instructions") or "").strip(),
    ]
    if request.get("kind") == KIND_DIRECTORY:
        lines += [
            "",
            "Directory requested: `{0}`".format(request.get("path")),
        ]
        if request.get("reason"):
            lines += ["", "Why: {0}".format(request["reason"])]
        lines += [
            "",
            "Approving adds exactly this directory to `permissions.agents.allowed_directories`; "
            "the blanket `outside_repo` gate stays as it is.",
        ]
    else:
        check = request.get("check") or {}
        if setup.get("command"):
            lines += ["", "Setup command (**Run setup** runs exactly this, as you):"]
            if setup.get("description"):
                lines += ["", str(setup["description"])]
            lines += ["", "```sh", str(setup["command"]), "```"]
        lines += [
            "",
            "Check command:",
            "",
            "```sh",
            str(check.get("command") or ""),
            "```",
        ]
    if request.get("hint"):
        lines += ["", "If the check fails: {0}".format(request["hint"])]
    lines += [
        "",
        "<!-- {0} {1} {2} -->".format(USER_ACTION_MARKER, ua_id, json.dumps(payload, sort_keys=True)),
    ]
    return "\n".join(lines)
