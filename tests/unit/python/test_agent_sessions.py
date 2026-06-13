"""test_agent_sessions.py - per-post conversation-id store (resume continuity)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from specseed_runtime.executing import agent_sessions
from specseed_runtime.storage_paths import sessions_file


class AgentSessionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)

    def test_roundtrip_and_miss(self) -> None:
        self.assertIsNone(agent_sessions.resume_id_for(self.storage, "7"))
        agent_sessions.remember(self.storage, "7", "sess-9", "claude")
        self.assertEqual(agent_sessions.resume_id_for(self.storage, "7"), "sess-9")
        self.assertIsNone(agent_sessions.resume_id_for(self.storage, "8"))
        # stored under runtime/sessions.json
        self.assertTrue(sessions_file(self.storage).exists())

    def test_provider_filter(self) -> None:
        agent_sessions.remember(self.storage, "7", "sess-9", "claude")
        self.assertEqual(agent_sessions.resume_id_for(self.storage, "7", provider="claude"), "sess-9")
        # a different provider can't resume a claude session id
        self.assertIsNone(agent_sessions.resume_id_for(self.storage, "7", provider="codex"))

    def test_overwrite_and_clear(self) -> None:
        agent_sessions.remember(self.storage, "7", "a", "claude")
        agent_sessions.remember(self.storage, "7", "b", "claude")
        self.assertEqual(agent_sessions.resume_id_for(self.storage, "7"), "b")
        agent_sessions.remember(self.storage, "7", None)  # clear
        self.assertIsNone(agent_sessions.resume_id_for(self.storage, "7"))

    def test_blank_post_id_is_noop(self) -> None:
        agent_sessions.remember(self.storage, "", "x", "claude")
        agent_sessions.remember(self.storage, None, "x", "claude")
        self.assertIsNone(agent_sessions.resume_id_for(self.storage, ""))


if __name__ == "__main__":
    unittest.main()
