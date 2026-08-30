"""user_action.py - runtime side of a UA-NNNN user-action request.

The request VOCABULARY (token, marker, payload validation, comment rendering) lives
in ``state_machines/user_action`` so the state machine can share it without a circular
import. This module adds the three things that need the machine:

* allocating the next monotonic ``UA`` id from a counter file under storage, so two
  workers never hand out the same token (same flock pattern as ``approvals``);
* RUNNING the agent-supplied check when a human presses **Check**;
* RUNNING the agent-supplied setup command when a human presses **Run setup**, so the
  human never has to leave the UI to install a toolchain;
* granting a ``directory`` request by appending exactly that path to
  ``permissions.agents.allowed_directories`` in ``configuration.json``.

On the gates: the command comes from an agent, so it is rendered verbatim next to the
button and only runs when a human presses it - the press IS the gate, nothing here runs
unattended. Two further limits apply because an agent can be wrong without being
hostile: the request is only honoured when it was stamped by the PLATFORM user (a human
comment cannot smuggle in a command), and every run gets a hard timeout and captured
output.

**Check and setup are not the same thing and must not converge.** A check is
contractually CHEAP and READ-ONLY - it is what un-parks an issue, and it may be re-run
freely. A setup command mutates the machine and is usually privileged. They share a
runner but stay two functions with two timeouts and two comment shapes, precisely so a
later edit cannot quietly teach the check to install things.

Only Python stdlib is used.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from specseed_runtime.storage_paths import config_file, spec_change_root

from specseed_runtime.state_machines.user_action import (  # re-export for callers
    DEFAULT_CHECK_TIMEOUT,
    DEFAULT_SETUP_TIMEOUT,
    KIND_DIRECTORY,
    KIND_ENVIRONMENT,
    KINDS,
    NEEDS_USER_ACTION,
    UA_RE,
    USER_ACTION_MARKER,
    format_ua,
    open_requests,
    parse_request,
    request_comment,
    ua_number,
    validate_request,
)

__all__ = [
    "DEFAULT_CHECK_TIMEOUT",
    "DEFAULT_SETUP_TIMEOUT",
    "KIND_DIRECTORY",
    "KIND_ENVIRONMENT",
    "KINDS",
    "NEEDS_USER_ACTION",
    "UA_RE",
    "USER_ACTION_MARKER",
    "format_ua",
    "open_requests",
    "parse_request",
    "request_comment",
    "ua_number",
    "validate_request",
    "next_ua_id",
    "counter_path",
    "run_check",
    "run_setup",
    "has_setup",
    "check_result_comment",
    "setup_result_comment",
    "grant_directory",
]

_COUNTER_NAME = ".ua_counter"

# Enough of a failing command's output to diagnose it, not enough to blow up a
# tracker comment (a failed build can print megabytes).
_OUTPUT_LIMIT = 4000

# Setup keeps far more: the point of running an install for the human is that they get
# the TRANSCRIPT, not an exit code - "what did it actually do to my machine" is the
# question this button has to answer. Still capped, because apt can be chatty.
_SETUP_OUTPUT_LIMIT = 20000


def counter_path(storage: str | Path) -> Path:
    """The UA counter file under ``<data_root>/spec-change/``."""
    return spec_change_root(storage) / _COUNTER_NAME


def next_ua_id(storage: str | Path) -> str:
    """Allocate and persist the next ``UA-NNNN`` token.

    Increments a counter file under storage atomically (flock), so concurrent
    workers never hand out the same token. The first allocation returns ``UA-0001``.
    """
    path = counter_path(storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_CREAT without O_TRUNC: the file is created only if missing and NEVER clobbered.
    # A separate exists()-then-write_text("0") loses the race - two allocators can both
    # see it missing, and the loser's write resets the counter after the winner bumped it.
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    with os.fdopen(fd, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            raw = handle.read().strip()
            try:
                current = int(raw)
            except ValueError:
                current = 0
            nxt = current + 1
            handle.seek(0)
            handle.truncate()
            handle.write(str(nxt))
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return format_ua(nxt)


def _clip(text: str, limit: int = _OUTPUT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (output truncated)"


def _run(command: str, cwd: str | Path, timeout: int, label: str, limit: int) -> dict:
    """Run one agent-supplied shell command and describe the outcome.

    Shared by the two buttons for the mechanics only - spawning, the hard timeout, and
    capturing output. Everything that makes a check a check (its timeout, its read-only
    contract, the comment it produces) stays with its caller. Returns
    ``{"ok", "command", "exit_code", "output", "error"}``; a timeout or a failure to
    spawn is a FAILED run, never an exception, because a human pressed a button and is
    owed an answer either way.
    """
    try:
        proc = subprocess.run(  # noqa: S602 - a shell is the point; see the module docstring
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(os.environ),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": command, "exit_code": None, "output": "",
                "error": "{0} timed out after {1}s".format(label, timeout)}
    except OSError as exc:
        return {"ok": False, "command": command, "exit_code": None, "output": "",
                "error": "{0} could not run: {1!r}".format(label, exc)}
    output = _clip("\n".join(part for part in (proc.stdout, proc.stderr) if part), limit)
    return {
        "ok": proc.returncode == 0,
        "command": command,
        "exit_code": proc.returncode,
        "output": output,
        "error": "",
    }


def run_check(request: dict, cwd: str | Path) -> dict:
    """Run an ``environment`` request's check command in ``cwd``.

    The check is the thing that un-parks an issue, and it is contractually cheap and
    READ-ONLY - a ``which``/``--version`` probe, on a short timeout. Do not grow this
    into something that can also change the machine; that is what ``run_setup`` is for.
    """
    check = (request or {}).get("check") or {}
    command = str(check.get("command") or "").strip()
    if not command:
        return {"ok": False, "command": "", "exit_code": None, "output": "",
                "error": "this request carries no check command"}
    timeout = int(check.get("timeout_seconds") or DEFAULT_CHECK_TIMEOUT)
    return _run(command, cwd, timeout, "check", _OUTPUT_LIMIT)


def has_setup(request: dict) -> bool:
    """Whether this request offers a one-click setup command. Requests without one are
    normal: some environment work cannot be scripted, and the card falls back to
    instructions + Check."""
    return bool(((request or {}).get("setup") or {}).get("command"))


def run_setup(request: dict, cwd: str | Path) -> dict:
    """Run an ``environment`` request's setup command in ``cwd``, as the human.

    This MUTATES the machine (it is usually an install), which is why it is separate
    from ``run_check``: it gets a much longer timeout, keeps far more output, and only
    ever runs on a deliberate press of its own button. It does not decide anything - the
    caller re-runs the check afterwards, and the check remains the only thing that can
    un-park an issue.
    """
    setup = (request or {}).get("setup") or {}
    command = str(setup.get("command") or "").strip()
    if not command:
        return {"ok": False, "command": "", "exit_code": None, "output": "",
                "error": "this request carries no setup command"}
    timeout = int(setup.get("timeout_seconds") or DEFAULT_SETUP_TIMEOUT)
    return _run(command, cwd, timeout, "setup", _SETUP_OUTPUT_LIMIT)


def check_result_comment(ua_id: str, request: dict, result: dict) -> str:
    """The comment posted after a Check press.

    A failure is not a red cross: it carries the command, its exit code, its output and
    the agent's ``hint``, because the whole point is that the human learns how to proceed.
    """
    if result.get("ok"):
        return (
            "**Check passed** ({0}: {1}).\n\nThe check `{2}` succeeded, so the issue is "
            "back to `todo` and the agent will pick it up on the next poll."
        ).format(ua_id, request.get("title") or "user action", result.get("command") or "")
    lines = [
        "**Check failed** ({0}: {1}). The issue stays parked.".format(
            ua_id, request.get("title") or "user action"
        ),
        "",
    ]
    if result.get("error"):
        lines += [result["error"], ""]
    if result.get("command"):
        lines += ["Command:", "", "```sh", str(result["command"]), "```", ""]
    if result.get("exit_code") is not None:
        lines += ["Exit code: `{0}`".format(result["exit_code"]), ""]
    if result.get("output"):
        lines += ["Output:", "", "```", str(result["output"]), "```", ""]
    if request.get("hint"):
        lines += ["How to proceed: {0}".format(request["hint"]), ""]
    lines.append(
        "Follow the instructions above and press **Check** again, or comment here if "
        "something in them is wrong or unclear."
    )
    return "\n".join(lines)


def setup_result_comment(ua_id: str, request: dict, setup: dict, check: Optional[dict]) -> str:
    """The comment posted after a **Run setup** press: the full transcript, then the verdict.

    The transcript is the point. The human delegated a privileged command to the runtime,
    so what it did to their machine belongs on the record next to the request - not
    summarised, and not left in a toast they click away. ``check`` is None when the setup
    itself failed and there was nothing worth verifying.
    """
    title = request.get("title") or "user action"
    cleared = bool(check and check.get("ok"))
    if not setup.get("ok"):
        head = "**Setup failed** ({0}: {1}). The issue stays parked.".format(ua_id, title)
    elif cleared:
        head = "**Setup ran and the check passed** ({0}: {1}).".format(ua_id, title)
    else:
        head = (
            "**Setup ran, but the check still fails** ({0}: {1}). The issue stays parked."
        ).format(ua_id, title)
    lines = [head, ""]
    if setup.get("command"):
        lines += ["Setup command:", "", "```sh", str(setup["command"]), "```", ""]
    if setup.get("error"):
        lines += [str(setup["error"]), ""]
    if setup.get("exit_code") is not None:
        lines += ["Exit code: `{0}`".format(setup["exit_code"]), ""]
    if setup.get("output"):
        lines += ["Output:", "", "```", str(setup["output"]), "```", ""]
    if check is None:
        lines += [
            "The check was not run - there was nothing to verify while the setup itself "
            "failed.",
            "",
        ]
    else:
        lines += [
            "Then ran the check `{0}`: **{1}**{2}".format(
                check.get("command") or "",
                "passed" if check.get("ok") else "failed",
                "" if check.get("exit_code") is None else " (exit `{0}`)".format(check["exit_code"]),
            ),
            "",
        ]
        if not check.get("ok"):
            if check.get("error"):
                lines += [str(check["error"]), ""]
            if check.get("output"):
                lines += ["```", str(check["output"]), "```", ""]
    if cleared:
        lines.append(
            "The issue is back to `todo` and the agent will pick it up on the next poll."
        )
        return "\n".join(lines)
    if request.get("hint"):
        lines += ["How to proceed: {0}".format(request["hint"]), ""]
    lines.append(
        "Fix what the output points at and press **Run setup** or **Check** again, or "
        "comment here if the request itself is wrong."
    )
    return "\n".join(lines)


def grant_directory(storage: str | Path, path: str) -> dict:
    """Append one approved directory to ``permissions.agents.allowed_directories``.

    Durable and per repo: the grant outlives the run that asked for it, so the agent
    does not re-ask on every retry. Idempotent - granting the same directory twice is a
    no-op. Returns the resulting list.
    """
    directory = str(path or "").strip()
    if not directory:
        raise ValueError("no directory given")
    target = config_file(storage)
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    permissions = config.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        permissions = config["permissions"] = {}
    agents = permissions.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = permissions["agents"] = {}
    allowed = agents.get("allowed_directories")
    if not isinstance(allowed, list):
        allowed = []
    allowed = [str(d) for d in allowed]
    if directory not in allowed:
        allowed.append(directory)
    agents["allowed_directories"] = allowed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return {"allowed_directories": allowed, "granted": directory}


def request_for_post(conversation: Any, ua_id: Optional[str] = None) -> Optional[dict]:
    """The live user-action request on a post: the one matching ``ua_id``, else the
    most recent. Returns None when the conversation carries no request."""
    requests = open_requests(conversation)
    if not requests:
        return None
    if ua_id:
        wanted = str(ua_id).upper()
        for request in requests:
            if request.get("id") == wanted:
                return request
        return None
    return requests[-1]
