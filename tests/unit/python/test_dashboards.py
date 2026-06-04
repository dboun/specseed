"""test_dashboards.py - scheduler-maintained ROADMAP / Current sprint.

TrackingRemoteLocal only. No GitHub/GitLab.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.target_facing.specseed_target_src.executing import dashboards
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import (
    TrackingRemoteLocal,
)


class DashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = TrackingRemoteLocal(db_path=Path(self.tmp.name) / "remote.db", author="bot")

    def _mk(self, title, labels, body=None):
        for label in labels:
            self.remote.create_label(label)
        return self.remote.add_entry(title, body=body, labels=labels).data.id

    def _seed_dashboards(self):
        self._mk("ROADMAP", ["management"], "# ROADMAP\n\nold body\n")
        self._mk("Current sprint", ["management", "current_sprint"], "# Current sprint\n\nold\n")

    def _body(self, post_id):
        return self.remote.get_entry(post_id).data.body

    def test_roadmap_renders_tree_with_statuses(self) -> None:
        self._seed_dashboards()
        epic = self._mk("Build it", ["epic", "epic:status:in_progress"], "# Epic\n\n## Tickets\n#4\n")
        ticket = self._mk("Ship value", ["ticket", "ticket:status:todo"], "Epic: #{0}\nIssues: #5, #6\n".format(epic))
        i1 = self._mk("Code A", ["issue", "issue:status:done"], "Ticket: #{0}\n".format(ticket))
        i2 = self._mk("Code B", ["issue", "issue:status:todo"], "Ticket: #{0}\n".format(ticket))

        out = dashboards.refresh_dashboards(self.remote)
        self.assertTrue(out["ok"])
        self.assertIn("ROADMAP", out["updated"])
        roadmap = self._body(1)
        self.assertIn("#{0} Build it — `in_progress`".format(epic), roadmap)
        self.assertIn("#{0} Ship value — `todo`".format(ticket), roadmap)
        self.assertIn("#{0} Code A — `done`".format(i1), roadmap)
        # nesting: the ticket line is indented under the epic
        self.assertIn("  - #{0} Ship value".format(ticket), roadmap)
        self.assertIn("    - #{0} Code A".format(i1), roadmap)

    def test_current_sprint_lists_only_active_issues(self) -> None:
        self._seed_dashboards()
        t = self._mk("T", ["ticket", "ticket:status:todo"], "Issues: #4, #5\n")
        done = self._mk("Done one", ["issue", "issue:status:done"], "Ticket: #{0}\n".format(t))
        active = self._mk("Active one", ["issue", "issue:status:in_progress"], "Ticket: #{0}\n".format(t))

        dashboards.refresh_dashboards(self.remote)
        sprint = self._body(2)
        self.assertIn("#{0} Active one".format(active), sprint)
        self.assertNotIn("Done one", sprint)

    def test_idempotent_no_rewrite_on_second_pass(self) -> None:
        self._seed_dashboards()
        self._mk("E", ["epic", "epic:status:todo"], "# Epic\n")
        first = dashboards.refresh_dashboards(self.remote)
        self.assertIn("ROADMAP", first["updated"])
        second = dashboards.refresh_dashboards(self.remote)
        self.assertEqual(second["updated"], [])

    def test_signature_changes_with_status(self) -> None:
        self._seed_dashboards()
        eid = self._mk("E", ["epic", "epic:status:todo"], "# Epic\n")
        sig1 = dashboards.work_signature(self.remote)
        self.remote.remove_entry_label(eid, "epic:status:todo")
        self.remote.add_entry_label(eid, "epic:status:done")
        sig2 = dashboards.work_signature(self.remote)
        self.assertNotEqual(sig1, sig2)

    def test_empty_breakdown_is_safe(self) -> None:
        self._seed_dashboards()
        out = dashboards.refresh_dashboards(self.remote)
        self.assertTrue(out["ok"])
        self.assertIn("No work posts yet", self._body(1))


if __name__ == "__main__":
    unittest.main()
