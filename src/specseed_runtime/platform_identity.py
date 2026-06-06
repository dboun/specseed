"""
platform_identity.py - who the platform is on the tracker, and how to spot it.

The runtime and its workers post comments on the same tracker humans use. If
the platform reacts to its own comments it loops: agent posts a clarification,
the next sync sees a fresh comment, the route re-triggers, forever (observed in
the wild - one post burned four agent runs in five minutes).

Two independent signals, either one marks a comment as the platform's:

1. ``config["platform_username"]`` - the account the platform posts as. Set it
   when the bot has its own account (GitHub/GitLab token user).
2. The ``specseed: `` body prefix - every comment the platform writes starts
   with it. This is the fallback when bot and human share one username (the
   local stand-in, a personal token). It doubles as provenance for readers.

``sync_to_db`` consults :func:`is_platform_comment` before turning a comment
change into work. Writers funnel through :func:`platform_comment`.

Only Python stdlib is used.
"""

from __future__ import annotations

from typing import Any, Optional

COMMENT_PREFIX = "specseed: "


def platform_username(config: Optional[dict[str, Any]]) -> Optional[str]:
    """The configured platform account name, or None when unset/blank."""
    name = (config or {}).get("platform_username")
    name = str(name).strip() if name is not None else ""
    return name or None


def platform_comment(body: str) -> str:
    """Mark ``body`` as platform-authored (idempotent)."""
    body = body or ""
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
