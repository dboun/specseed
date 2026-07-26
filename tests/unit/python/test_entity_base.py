"""test_entity_base.py - entity helpers (dependency parsing)."""

from __future__ import annotations

import unittest

from specseed_runtime.entities.entity_base import Entity, parse_depends_on, parse_parent


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

    def test_generic_parent_word(self) -> None:
        # `Parent: #NN` is the intuitive word; accepted as a synonym so a link written
        # that way is not silently dropped (it is also the protocol example's form).
        self.assertEqual(parse_parent("Parent: #6\nImplement storage.py"), "6")
        self.assertEqual(parse_parent("Parent: #TICKET-001"), "TICKET-001")

    def test_none_when_absent(self) -> None:
        self.assertIsNone(parse_parent("Depends on: #5\nno parent here"))
        self.assertIsNone(parse_parent(None))


class TierStatusFromLabelsTest(unittest.TestCase):
    """tier/status resolution from a post's labels. The canonical tracker form is a bare
    tier label PLUS a `<tier>:status:<status>` label, but a post may carry only the combined
    one - tier must still resolve from it, else the dispatcher drops a real issue as untyped."""

    def test_bare_tier_label(self) -> None:
        self.assertEqual(Entity.tier_from_labels(["issue", "issue:status:todo"]), "issue")

    def test_explicit_tier_prefix(self) -> None:
        self.assertEqual(Entity.tier_from_labels(["tier:ticket"]), "ticket")

    def test_tier_from_combined_status_label_only(self) -> None:
        # The regression: a post whose ONLY tier-bearing label is the combined status one.
        self.assertEqual(Entity.tier_from_labels(["issue:status:todo"]), "issue")
        self.assertEqual(Entity.tier_from_labels(["epic:status:in_progress"]), "epic")

    def test_combined_non_work_tier_does_not_resolve(self) -> None:
        # `spec-change:status:done` is not a work tier - must not be mistaken for one.
        self.assertIsNone(Entity.tier_from_labels(["spec-change:status:done"]))

    def test_no_tier_label(self) -> None:
        self.assertIsNone(Entity.tier_from_labels([]))
        self.assertIsNone(Entity.tier_from_labels(["type:feature", "difficulty:hard"]))

    def test_status_from_combined_and_bare(self) -> None:
        self.assertEqual(Entity.status_from_labels(["issue:status:todo"]), "todo")
        self.assertEqual(Entity.status_from_labels(["status:in_review"]), "in_review")
        self.assertIsNone(Entity.status_from_labels(["issue"]))


if __name__ == "__main__":
    unittest.main()
