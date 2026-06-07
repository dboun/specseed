"""test_agent_report.py - the structured agent result contract.

No agent, no tokens: pure parse/validate of the JSON result file per intent.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from specseed_runtime.executing import agent_report as ar


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "result.json"

    def _write(self, obj) -> None:
        self.path.write_text(json.dumps(obj) if not isinstance(obj, str) else obj, encoding="utf-8")


class ImplementParseTest(_Base):
    def test_valid_done(self) -> None:
        self._write({"status": "done", "summary": "did it", "files_changed": ["a.py"]})
        report, err = ar.parse_result_file(self.path, ar.IMPLEMENT)
        self.assertIsNone(err)
        self.assertEqual(report["status"], "done")
        self.assertEqual(report["files_changed"], ["a.py"])

    def test_blocked_is_valid(self) -> None:
        self._write({"status": "blocked", "summary": "sandbox"})
        report, err = ar.parse_result_file(self.path, ar.IMPLEMENT)
        self.assertIsNone(err)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["files_changed"], [])

    def test_recommend_spec_change_defaults_false(self) -> None:
        self._write({"status": "done", "summary": "x"})
        report, _ = ar.parse_result_file(self.path, ar.IMPLEMENT)
        self.assertFalse(report["recommend_spec_change"])

    def test_recommend_spec_change_true(self) -> None:
        self._write({"status": "blocked", "summary": "spec wrong", "recommend_spec_change": True})
        report, _ = ar.parse_result_file(self.path, ar.IMPLEMENT)
        self.assertTrue(report["recommend_spec_change"])

    def test_recommend_spec_change_string_coerced(self) -> None:
        self._write({"status": "blocked", "summary": "x", "recommend_spec_change": "true"})
        report, _ = ar.parse_result_file(self.path, ar.IMPLEMENT)
        self.assertTrue(report["recommend_spec_change"])

    def test_bad_status_rejected(self) -> None:
        self._write({"status": "finished", "summary": "x"})
        report, err = ar.parse_result_file(self.path, ar.IMPLEMENT)
        self.assertIsNone(report)
        self.assertIn("status", err)

    def test_missing_file(self) -> None:
        report, err = ar.parse_result_file(self.path, ar.IMPLEMENT)
        self.assertIsNone(report)
        self.assertIn("no result file", err)

    def test_not_json(self) -> None:
        self._write("not json {")
        report, err = ar.parse_result_file(self.path, ar.IMPLEMENT)
        self.assertIsNone(report)
        self.assertIn("not valid JSON", err)

    def test_empty_file(self) -> None:
        self.path.write_text("   ", encoding="utf-8")
        report, err = ar.parse_result_file(self.path, ar.IMPLEMENT)
        self.assertIsNone(report)
        self.assertIn("empty", err)


class ReviewParseTest(_Base):
    def test_valid_approve(self) -> None:
        self._write({"verdict": "approve", "confidence": 0.9, "summary": "lgtm"})
        report, err = ar.parse_result_file(self.path, ar.REVIEW)
        self.assertIsNone(err)
        self.assertEqual(report["verdict"], "approve")
        self.assertAlmostEqual(report["confidence"], 0.9)
        self.assertEqual(report["summary"], "lgtm")

    def test_confidence_clamped(self) -> None:
        self._write({"verdict": "changes", "confidence": 2.5, "summary": "no"})
        report, _ = ar.parse_result_file(self.path, ar.REVIEW)
        self.assertEqual(report["confidence"], 1.0)

    def test_recommend_spec_change_defaults_false(self) -> None:
        self._write({"verdict": "changes", "confidence": 0.3, "summary": "no"})
        report, _ = ar.parse_result_file(self.path, ar.REVIEW)
        self.assertFalse(report["recommend_spec_change"])

    def test_recommend_spec_change_true(self) -> None:
        self._write({"verdict": "changes", "confidence": 0.3, "summary": "spec wrong",
                     "recommend_spec_change": True})
        report, _ = ar.parse_result_file(self.path, ar.REVIEW)
        self.assertTrue(report["recommend_spec_change"])

    def test_bad_verdict_rejected(self) -> None:
        self._write({"verdict": "meh", "confidence": 0.5})
        report, err = ar.parse_result_file(self.path, ar.REVIEW)
        self.assertIsNone(report)
        self.assertIn("verdict", err)

    def test_nonnumeric_confidence_rejected(self) -> None:
        self._write({"verdict": "approve", "confidence": "high"})
        report, err = ar.parse_result_file(self.path, ar.REVIEW)
        self.assertIsNone(report)
        self.assertIn("confidence", err)


class LooseParseTest(_Base):
    def test_spec_change_is_loose(self) -> None:
        self._write({"status": "anything", "summary": "ok"})
        report, err = ar.parse_result_file(self.path, ar.SPEC_CHANGE)
        self.assertIsNone(err)
        self.assertEqual(report["summary"], "ok")


class InstructionsTest(unittest.TestCase):
    def test_mentions_env_var_and_schema(self) -> None:
        for intent in (ar.IMPLEMENT, ar.REVIEW, ar.SPEC_CHANGE):
            text = ar.result_instructions(intent)
            self.assertIn(ar.RESULT_FILE_ENV, text)
        self.assertIn("verdict", ar.result_instructions(ar.REVIEW))
        self.assertIn("status", ar.result_instructions(ar.IMPLEMENT))


if __name__ == "__main__":
    unittest.main()
