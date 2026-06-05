"""control.py - the CONTROL post: STATUS / START / PAUSE / STOP.

The operator drives the running scheduler not with a CLI but by commenting on a
permanent ``CONTROL`` dashboard post (seeded by ``populate_defaults``). The
scheduler polls that post's comments on every tick, recognizes a small command
vocabulary (``status``/``start``/``pause``/``stop``), and acts on the ones written
by configured approvers. It can also post the human-readable STATUS text back as a
comment, gated by ``permissions.can_post_control()``.

Only approver comments matter: a random observer's "stop" is ignored. The bot's
own STATUS replies are skipped so a posted status never re-triggers itself. A
per-instance cursor over comment ids guarantees each command fires exactly once.

Only Python stdlib is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from specseed_runtime.executing.permissions import Permissions


# The recognized operator verbs (first whitespace token of a comment body).
VERBS = {"status", "start", "pause", "stop"}

# Title of the permanent CONTROL dashboard post (see populate_defaults).
CONTROL_TITLE = "CONTROL"


@dataclass(frozen=True)
class ControlCommand:
    """One recognized operator command parsed from a CONTROL comment."""

    verb: str
    author: Optional[str]
    comment_id: Any
    at: Optional[str] = None


def _title_of(summary: Any) -> str:
    return str(getattr(summary, "title", "") or "")


def find_control_entry(tracker: Any) -> Optional[Any]:
    """Return the CONTROL post details, or None if it does not exist.

    ``list_entries`` to locate the entry whose title is exactly ``CONTROL``, then
    ``get_entry`` to fetch its body + comments.
    """
    listed = tracker.list_entries()
    if not getattr(listed, "ok", False) or not listed.data:
        return None
    match = None
    for summary in listed.data:
        if _title_of(summary) == CONTROL_TITLE:
            match = summary
            break
    if match is None:
        return None
    detail = tracker.get_entry(getattr(match, "id", None))
    if not getattr(detail, "ok", False) or detail.data is None:
        return None
    return detail.data


def _first_token(body: Optional[str]) -> Optional[str]:
    if not body:
        return None
    stripped = str(body).strip()
    if not stripped:
        return None
    return stripped.split()[0].casefold()


def parse_control_commands(
    details: Any,
    allowed_authors: set,
    *,
    bot_author: Optional[str] = None,
) -> list:
    """Scan a CONTROL post's comments oldest-first for operator commands.

    A comment is a command when the first token of its body (casefolded) is in
    ``VERBS``. Only comments whose author (casefolded) is in ``allowed_authors``
    are kept, and the bot's own comments are skipped so STATUS replies do not
    loop. Comments are returned in id order (oldest first).
    """
    if details is None:
        return []
    allowed = {str(a).casefold() for a in (allowed_authors or set())}
    bot = None if bot_author is None else str(bot_author).casefold()
    comments = list(getattr(details, "comments", []) or [])

    def _key(comment: Any) -> Any:
        cid = getattr(comment, "id", None)
        try:
            return (0, int(cid))
        except (TypeError, ValueError):
            return (1, str(cid))

    comments.sort(key=_key)

    commands: list = []
    for comment in comments:
        author = getattr(comment, "author", None)
        folded_author = None if author is None else str(author).casefold()
        if bot is not None and folded_author == bot:
            continue
        verb = _first_token(getattr(comment, "body", None))
        if verb not in VERBS:
            continue
        if folded_author not in allowed:
            continue
        commands.append(
            ControlCommand(
                verb=verb,
                author=author,
                comment_id=getattr(comment, "id", None),
                at=getattr(comment, "created_at", None),
            )
        )
    return commands


def render_status(status: dict) -> str:
    """Render a human-readable STATUS comment from ``Scheduler.status()``.

    Keys mirror ``Scheduler.status()`` (state, pending, in_progress,
    poll_interval_seconds, last_poll_at, current_task_id, started_at). Each line
    is emitted only when its key is present, so the function also tolerates a
    partial status dict.
    """
    status = status or {}
    lines = ["specseed scheduler STATUS", "", f"state: {status.get('state', 'unknown')}"]

    def _add(label: str, *keys: str, suffix: str = "") -> None:
        for key in keys:
            value = status.get(key)
            if value is not None:
                lines.append(f"{label}: {value}{suffix}")
                return

    _add("pending tasks", "pending")
    _add("in progress", "in_progress", "active")
    _add("poll interval", "poll_interval_seconds", "interval_seconds", "interval", suffix="s")
    _add("last poll", "last_poll_at", "last_poll")
    _add("current task", "current_task_id")
    _add("started at", "started_at")

    return "\n".join(lines)


class ControlChannel:
    """Stateful reader/writer for the CONTROL post.

    Holds a cursor over the highest comment id it has already emitted so polling
    never re-fires a command. Approver gating is delegated to ``permissions``.
    """

    def __init__(
        self,
        tracker: Any,
        config: dict,
        permissions: Permissions,
        *,
        bot_author: Optional[str] = None,
    ) -> None:
        self.tracker = tracker
        self.config = config or {}
        self.permissions = permissions
        self.bot_author = bot_author
        self._cursor: Optional[Any] = None
        self._control_id: Optional[Any] = None

    @property
    def control_id(self) -> Optional[Any]:
        return self._control_id

    def _cursor_key(self, comment_id: Any) -> Any:
        try:
            return (0, int(comment_id))
        except (TypeError, ValueError):
            return (1, str(comment_id))

    def poll(self) -> list:
        """Return new operator commands since the last poll, advancing the cursor.

        Only commands from configured approvers are considered; the cursor is
        advanced past every command observed (whether or not it was new) so a
        command fires exactly once.
        """
        details = find_control_entry(self.tracker)
        if details is None:
            return []
        self._control_id = getattr(details, "id", None)
        allowed = self.permissions.approver_usernames()
        commands = parse_control_commands(
            details, allowed, bot_author=self.bot_author
        )

        fresh: list = []
        for command in commands:
            key = self._cursor_key(command.comment_id)
            if self._cursor is not None and key <= self._cursor:
                continue
            fresh.append(command)

        for command in commands:
            key = self._cursor_key(command.comment_id)
            if self._cursor is None or key > self._cursor:
                self._cursor = key
        return fresh

    def post_status(self, text: str) -> bool:
        """Post ``text`` as a comment on the CONTROL post if permitted.

        Returns True on a successful post, False if permission is withheld, the
        CONTROL post cannot be found, or the underlying write fails.
        """
        if not self.permissions.can_post_control():
            return False
        control_id = self._control_id
        if control_id is None:
            details = find_control_entry(self.tracker)
            if details is None:
                return False
            control_id = getattr(details, "id", None)
            self._control_id = control_id
        if control_id is None:
            return False
        result = self.tracker.add_entry_comment(control_id, text)
        return bool(getattr(result, "ok", False))
