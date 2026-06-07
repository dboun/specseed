"""test_entity_base.py - entity helpers (dependency parsing)."""

from __future__ import annotations

import unittest

from specseed_runtime.entities.entity_base import parse_depends_on, parse_parent


class ParseDependsOnTest(unittest.TestCase):
    def test_html_comment_single(self) -> None:
        body = "Some issue body\n\n<!-- Ticket: #41   Depends on: #61 -->\n"
        self.assertEqual(parse_depends_on(body), ["61"])

    def test_html_comment_multiple(self) -> None:
        body = "<!-- Epic: #12   Issues: #61, #62   Depends on: #40, #41 -->"
        # Issues:... is a different phrase; only the Depends-on ids are captured
        self.assertEqual(parse_depends_on(body), ["40", "41"])

    def test_plain_line(self) -> None:
        body = "Parent ticket: #5\nDepends on: #7, #8\nMore text #9 here\n"
        self.assertEqual(parse_depends_on(body), ["7", "8"])

    def test_human_ids(self) -> None:
        self.assertEqual(parse_depends_on("Depends on: #FEAT-0001, #CHORE-0002"),
                         ["FEAT-0001", "CHORE-0002"])

    def test_case_insensitive_and_no_colon(self) -> None:
        self.assertEqual(parse_depends_on("depends on #3"), ["3"])

    def test_none_and_empty(self) -> None:
        self.assertEqual(parse_depends_on(None), [])
        self.assertEqual(parse_depends_on(""), [])
        self.assertEqual(parse_depends_on("no deps here #5 mentioned"), [])

    def test_dedupes_preserving_order(self) -> None:
        body = "Depends on: #7, #7, #8"
        self.assertEqual(parse_depends_on(body), ["7", "8"])


class ParseParentTest(unittest.TestCase):
    def test_issue_ticket_link(self) -> None:
        self.assertEqual(parse_parent("<!-- Ticket: #41   Depends on: #61 -->"), "41")

    def test_ticket_epic_link(self) -> None:
        self.assertEqual(parse_parent("<!-- Epic: #12   Issues: #61, #62 -->"), "12")

    def test_human_id(self) -> None:
        self.assertEqual(parse_parent("Ticket: #PROJ-0007"), "PROJ-0007")

    def test_none_when_absent(self) -> None:
        self.assertIsNone(parse_parent("Depends on: #5\nno parent here"))
        self.assertIsNone(parse_parent(None))


if __name__ == "__main__":
    unittest.main()
