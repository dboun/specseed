"""
platform_identity.py - who the platform is on the tracker, and how to spot it.

The runtime and its workers post comments on the same tracker humans use. If
the platform reacts to its own comments it loops: agent posts a clarification,
the next sync sees a fresh comment, the route re-triggers, forever (observed in
the wild - one post burned four agent runs in five minutes).

Two independent signals, either one marks a comment as the platform's:

1. ``config["platform_username"]`` - the account the platform posts as. Set it
   when the bot has its own account (GitHub/GitLab token user).
2. The ``specseed: `` body prefix - the fallback for when bot and human share
   one username (the local stand-in, a personal token), so author alone can't
   tell them apart. Applied ONLY then (see :func:`needs_comment_prefix`): with a
   distinct bot account the author signal suffices and the prefix is omitted, so
   it never leaks into a comment body the UI renders verbatim.

``sync_to_db`` consults :func:`is_platform_comment` before turning a comment
change into work. Writers funnel through :func:`platform_comment`.

Only Python stdlib is used.
"""

from __future__ import annotations

import re
from typing import Any, Optional

COMMENT_PREFIX = "specseed: "


def infer_owner(repo: Optional[str]) -> Optional[str]:
    """Best-effort tracker username from a repo reference.

    ``dboun/whatever`` -> ``dboun``; a full URL or ``git@`` ref drops the host
    (``https://github.com/dboun/x`` -> ``dboun``); self-hosted ``host/group/proj``
    drops the leading host segment. Returns None when nothing parses.
    """
    if not repo:
        return None
    ref = str(repo).strip()
    if ref.startswith("git@"):
        ref = ref.split(":", 1)[-1]
    ref = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", ref)  # strip scheme
    parts = [p for p in ref.rstrip("/").split("/") if p]
    if not parts:
        return None
    if "." in parts[0] and len(parts) >= 2:  # leading host (github.com, gitlab.x)
        parts = parts[1:]
    owner = parts[0]
    if owner.endswith(".git"):
        owner = owner[:-4]
    return owner or None


def platform_username(config: Optional[dict[str, Any]]) -> Optional[str]:
    """The configured platform account name, or None when unset/blank."""
    name = (config or {}).get("platform_username")
    name = str(name).strip() if name is not None else ""
    return name or None


def human_username(config: Optional[dict[str, Any]]) -> str:
    """The human identity for UI/CLI writes: the first approver, else ``user``.

    Mirrors the UI's ``_ui_user`` so the prefix decision below matches the author
    the UI actually stamps on human writes.
    """
    approvers = ((config or {}).get("approvals") or {}).get("approver_usernames") or []
    return str(approvers[0]).strip() if approvers else "user"


def agent_assignee(config: Optional[dict[str, Any]]) -> str:
    """The tracker username that marks an issue as the agent's to work.

    A distinct bot account (``platform_username``) is the agent; without one
    (local stand-in / a personal token where bot and human share a name) the
    agent IS the human, so the human's own username doubles as the agent marker.
    An issue is "assigned to the agent" when this name is among its assignees -
    the precondition for auto-implement / the implement approval gate.
    """
    return platform_username(config) or human_username(config)


def needs_comment_prefix(config: Optional[dict[str, Any]]) -> bool:
    """Whether platform comments need the ``specseed: `` body prefix to be told apart.

    A distinct bot account (``platform_username`` set AND different from the human
    identity) is already distinguishable by author, so the prefix is noise - the UI
    shows it verbatim in the body, which mangles a structured-envelope comment. The
    prefix is only the dedup signal when there is NO distinct account: ``platform_username``
    unset, or it collides with the human's username (the local-stand-in / personal-token
    case the prefix was built for).
    """
    bot = platform_username(config)
    if not bot:
        return True
    return bot == human_username(config)


def platform_comment(body: str, config: Optional[dict[str, Any]] = None) -> str:
    """Mark ``body`` as platform-authored (idempotent).

    Prepends the ``specseed: `` prefix ONLY when the platform can't be told apart by
    author (see :func:`needs_comment_prefix`). ``config`` omitted -> assume the prefix
    is needed (safe default: an unconfigured caller keeps the old always-prefix behavior).
    """
    body = body or ""
    if not needs_comment_prefix(config):
        return body
    if body.startswith(COMMENT_PREFIX):
        return body
    return COMMENT_PREFIX + body


def is_platform_comment(
    *,
    author: Optional[str] = None,
    body: Optional[str] = None,
    username: Optional[str] = None,
) -> bool:
    """True when the comment came from the platform itself.

    Author match needs a configured ``username``; the body prefix works always.
    """
    if username and author and str(author).strip() == str(username).strip():
        return True
    return bool(body) and str(body).lstrip().startswith(COMMENT_PREFIX)
