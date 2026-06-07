"""test_custom_instructions.py - user-owned custom instructions appended to prompts.

Pure file-in, string-out. No tracker, no agent, no network.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from specseed_runtime.configuring import scaffold
from specseed_runtime.executing import agent_report
from specseed_runtime.executing.prompts import render_custom_instructions


class _Ctx:
    def __init__(self, storage):
        self.storage = str(storage)
        self.config = {"specseed_dir": ".specseed"}


def _seed(root: Path) -> Path:
    """Make <root>/.specseed/storage and stub custom files; return storage path."""
    specseed = root / ".specseed"
    storage = specseed / "storage"
    storage.mkdir(parents=True)
    scaffold.write_custom_instruction_stubs(root, ".specseed")
    return storage


class RenderCustomInstructionsTest(unittest.TestCase):
    def test_pristine_stubs_render_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = _seed(Path(tmp))
            self.assertEqual(render_custom_instructions(_Ctx(storage), agent_report.IMPLEMENT), "")

    def test_no_storage_renders_nothing(self) -> None:
        class _Bare:
            config = {}
        self.assertEqual(render_custom_instructions(_Bare(), agent_report.IMPLEMENT), "")

    def test_edited_per_step_file_is_included(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = _seed(root)
            (root / ".specseed" / "CUSTOM_INSTRUCTIONS_IMPL.md").write_text(
                "Always update cybersecurity_plan.md when the spec changes.\n", encoding="utf-8"
            )
            out = render_custom_instructions(_Ctx(storage), agent_report.IMPLEMENT)
            self.assertIn("USER CUSTOM INSTRUCTIONS", out)
            self.assertIn("cybersecurity_plan.md", out)

    def test_global_and_step_both_included_step_only_for_its_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = _seed(root)
            (root / ".specseed" / "CUSTOM_INSTRUCTIONS.md").write_text(
                "GLOBAL: prefer small diffs.\n", encoding="utf-8"
            )
            (root / ".specseed" / "CUSTOM_INSTRUCTIONS_REVIEW.md").write_text(
                "REVIEW: check error handling.\n", encoding="utf-8"
            )
            impl = render_custom_instructions(_Ctx(storage), agent_report.IMPLEMENT)
            review = render_custom_instructions(_Ctx(storage), agent_report.REVIEW)
            # global appears in both
            self.assertIn("GLOBAL: prefer small diffs.", impl)
            self.assertIn("GLOBAL: prefer small diffs.", review)
            # the review-only file appears only for review
            self.assertNotIn("check error handling", impl)
            self.assertIn("check error handling", review)

    def test_html_marker_line_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = _seed(root)
            (root / ".specseed" / "CUSTOM_INSTRUCTIONS_IMPL.md").write_text(
                "<!-- specseed: user-owned -->\nreal content here\n", encoding="utf-8"
            )
            out = render_custom_instructions(_Ctx(storage), agent_report.IMPLEMENT)
            self.assertIn("real content here", out)
            self.assertNotIn("<!-- specseed:", out)


if __name__ == "__main__":
    unittest.main()
