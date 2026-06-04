"""test_control.py - CONTROL post command parsing + status posting.

Uses TrackingRemoteLocal seeded with a CONTROL entry. No GitHub/GitLab.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.target_facing.specseed_target_src.executing.control import (
    CONTROL_TITLE,
    ControlChannel,
    ControlCommand,
    find_control_entry,
    parse_control_commands,
    render_status,
)
from src.target_facing.specseed_target_src.executing.permissions import Permissions
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


def _config(*, backend_enabled=False, post_issues=True, post_control=True, approvers=None):
    return {
        "backend": {"enabled": backend_enabled, "provider": None},
        "approvals": {"approver_usernames": list(approvers or ["alice"])},
        "permissions": {
            "remote": {
                "post_issues": post_issues,
                "post_control": post_control,
            }
        },
    }


class ControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        # The CONTROL post author is the bot ("bot"); operators comment as humans.
        self.remote = TrackingRemoteLocal(
            db_path=root / "tracking_remote_local.db", author="bot"
        )
        self.control_id = self.remote.add_entry(CONTROL_TITLE, body="control").data.id

    def _comment(self, author, body):
        self.remote.author = author
        try:
            return self.remote.add_entry_comment(self.control_id, body).data.id
        finally:
            self.remote.author = "bot"

    # -- find / parse ---------------------------------------------------- #
    def test_find_control_entry(self) -> None:
        details = find_control_entry(self.remote)
        self.assertIsNotNone(details)
        self.assertEqual(details.title, CONTROL_TITLE)

    def test_find_control_entry_missing(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = TrackingRemoteLocal(db_path=Path(tmp.name) / "r.db", author="bot")
        self.assertIsNone(find_control_entry(empty))

    def test_parse_recognizes_verbs(self) -> None:
        self._comment("alice", "status")
        self._comment("alice", "PAUSE now please")
        self._comment("alice", "not a command")
        details = find_control_entry(self.remote)
        commands = parse_control_commands(details, {"alice"})
        verbs = [c.verb for c in commands]
        self.assertEqual(verbs, ["status", "pause"])

    def test_parse_author_gating(self) -> None:
        self._comment("mallory", "stop")  # not an approver
        self._comment("alice", "start")
        details = find_control_entry(self.remote)
        commands = parse_control_commands(details, {"alice"})
        self.assertEqual([c.verb for c in commands], ["start"])
        self.assertEqual(commands[0].author, "alice")

    def test_parse_skips_bot_author(self) -> None:
        # Even a verb-shaped comment from the bot itself is skipped.
        self._comment("bot", "status")
        self._comment("alice", "stop")
        details = find_control_entry(self.remote)
        commands = parse_control_commands(details, {"alice", "bot"}, bot_author="bot")
        self.assertEqual([c.verb for c in commands], ["stop"])

    # -- render_status --------------------------------------------------- #
    def test_render_status_contains_state_and_counts(self) -> None:
        text = render_status({"state": "RUNNING", "pending": 3, "active": 1})
        self.assertIn("RUNNING", text)
        self.assertIn("3", text)
        self.assertIn("1", text)

    # -- ControlChannel -------------------------------------------------- #
    def test_poll_cursor_advances_no_reemit(self) -> None:
        perms = Permissions(_config(approvers=["alice"]))
        channel = ControlChannel(self.remote, _config(approvers=["alice"]), perms, bot_author="bot")

        self._comment("alice", "start")
        first = channel.poll()
        self.assertEqual([c.verb for c in first], ["start"])

        # No new comments -> nothing re-emitted.
        self.assertEqual(channel.poll(), [])

        # A new command is emitted exactly once.
        self._comment("alice", "pause")
        second = channel.poll()
        self.assertEqual([c.verb for c in second], ["pause"])
        self.assertEqual(channel.poll(), [])

    def test_poll_filters_non_approvers(self) -> None:
        perms = Permissions(_config(approvers=["alice"]))
        channel = ControlChannel(self.remote, _config(approvers=["alice"]), perms, bot_author="bot")
        self._comment("mallory", "stop")
        self.assertEqual(channel.poll(), [])

    def test_post_status_gated_off(self) -> None:
        # backend enabled + post_control false -> not permitted.
        cfg = _config(backend_enabled=True, post_issues=True, post_control=False)
        perms = Permissions(cfg)
        channel = ControlChannel(self.remote, cfg, perms, bot_author="bot")
        self.assertFalse(channel.post_status("hello"))
        # No comment was written.
        details = find_control_entry(self.remote)
        self.assertEqual(len(details.comments), 0)

    def test_post_status_allowed_local(self) -> None:
        # backend disabled -> local stand-in, posting allowed.
        cfg = _config(backend_enabled=False)
        perms = Permissions(cfg)
        channel = ControlChannel(self.remote, cfg, perms, bot_author="bot")
        ok = channel.post_status(render_status({"state": "PAUSED", "pending": 0}))
        self.assertTrue(ok)
        details = find_control_entry(self.remote)
        self.assertEqual(len(details.comments), 1)
        self.assertIn("PAUSED", details.comments[0].body)

    def test_command_dataclass_frozen(self) -> None:
        cmd = ControlCommand(verb="status", author="alice", comment_id=1, at="t")
        with self.assertRaises(Exception):
            cmd.verb = "stop"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
