"""test_platform_identity.py - the specseed: prefix + platform-comment detection."""

from __future__ import annotations

import unittest

from specseed_runtime.platform_identity import (
    COMMENT_PREFIX,
    human_username,
    infer_owner,
    is_platform_comment,
    needs_comment_prefix,
    platform_comment,
    platform_username,
)


class PlatformCommentTest(unittest.TestCase):
    def test_prefixes_body_when_no_config(self) -> None:
        # No config -> safe default: prefix (can't prove a distinct bot account).
        self.assertEqual(platform_comment("hello"), "specseed: hello")

    def test_idempotent(self) -> None:
        once = platform_comment("hello")
        self.assertEqual(platform_comment(once), once)

    def test_empty_body(self) -> None:
        self.assertEqual(platform_comment(""), COMMENT_PREFIX)

    def test_distinct_bot_account_drops_prefix(self) -> None:
        # platform_username set AND != the human (first approver) -> author tells
        # them apart, so no prefix pollutes the body (the UI renders it verbatim).
        cfg = {"platform_username": "specseed", "approvals": {"approver_usernames": ["user"]}}
        self.assertEqual(platform_comment("hello", cfg), "hello")

    def test_shared_username_keeps_prefix(self) -> None:
        cfg = {"platform_username": "user", "approvals": {"approver_usernames": ["user"]}}
        self.assertEqual(platform_comment("hello", cfg), "specseed: hello")

    def test_unset_platform_username_keeps_prefix(self) -> None:
        self.assertEqual(platform_comment("hi", {"approvals": {"approver_usernames": ["user"]}}),
                         "specseed: hi")


class NeedsCommentPrefixTest(unittest.TestCase):
    def test_distinct_account_no_prefix(self) -> None:
        cfg = {"platform_username": "bot", "approvals": {"approver_usernames": ["alice"]}}
        self.assertFalse(needs_comment_prefix(cfg))

    def test_collision_with_human_needs_prefix(self) -> None:
        cfg = {"platform_username": "alice", "approvals": {"approver_usernames": ["alice"]}}
        self.assertTrue(needs_comment_prefix(cfg))

    def test_unset_needs_prefix(self) -> None:
        self.assertTrue(needs_comment_prefix({}))
        self.assertTrue(needs_comment_prefix(None))

    def test_default_human_is_user(self) -> None:
        # No approvers -> human defaults to "user"; a bot named "user" collides.
        self.assertEqual(human_username({}), "user")
        self.assertTrue(needs_comment_prefix({"platform_username": "user"}))
        self.assertFalse(needs_comment_prefix({"platform_username": "specseed"}))


class IsPlatformCommentTest(unittest.TestCase):
    def test_prefix_detected_without_username(self) -> None:
        self.assertTrue(is_platform_comment(author="anyone", body="specseed: status"))

    def test_prefix_detected_after_leading_whitespace(self) -> None:
        self.assertTrue(is_platform_comment(body="  \nspecseed: status"))

    def test_username_match(self) -> None:
        self.assertTrue(
            is_platform_comment(author="bot", body="no prefix here", username="bot")
        )

    def test_human_comment_is_not_platform(self) -> None:
        self.assertFalse(
            is_platform_comment(author="alice", body="please fix", username="bot")
        )

    def test_no_username_no_prefix_is_human(self) -> None:
        self.assertFalse(is_platform_comment(author="alice", body="please fix"))

    def test_mid_body_mention_is_not_platform(self) -> None:
        self.assertFalse(is_platform_comment(body="ask specseed: about it"))


class PlatformUsernameTest(unittest.TestCase):
    def test_reads_config(self) -> None:
        self.assertEqual(platform_username({"platform_username": "bot"}), "bot")

    def test_blank_and_missing_are_none(self) -> None:
        self.assertIsNone(platform_username({"platform_username": "  "}))
        self.assertIsNone(platform_username({}))
        self.assertIsNone(platform_username(None))


class InferOwnerTest(unittest.TestCase):
    def test_owner_name(self) -> None:
        self.assertEqual(infer_owner("dboun/whatever"), "dboun")

    def test_full_url_drops_host(self) -> None:
        self.assertEqual(infer_owner("https://github.com/dboun/whatever"), "dboun")

    def test_git_ssh_ref(self) -> None:
        self.assertEqual(infer_owner("git@github.com:dboun/whatever.git"), "dboun")

    def test_self_hosted_group(self) -> None:
        self.assertEqual(infer_owner("gitlab.example.com/group/sub/proj"), "group")

    def test_blank_is_none(self) -> None:
        self.assertIsNone(infer_owner(""))
        self.assertIsNone(infer_owner(None))


if __name__ == "__main__":
    unittest.main()
