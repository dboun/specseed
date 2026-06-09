"""test_dashboards.py - scheduler-maintained ROADMAP / CURRENT SPRINT.

TrackingRemoteLocal only. No GitHub/GitLab.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from specseed_runtime.executing import dashboards
from specseed_runtime.tracking.tracking_remote_local import (
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
        self._mk("CURRENT SPRINT", ["management", "current_sprint"], "# CURRENT SPRINT\n\nold\n")

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

    # --- SCHEDULE refresh (derived bits only) ---------------------------------

    _SCHEDULE_SEED = (
        "# SCHEDULE\n\nintro line\n\n"
        "## SPRINT_2026_W24_A — Foundation sprint  (ongoing)\n"
        "- [PROJ-0001](#{ticket}) Todo CLI — 2h (0/2) ★\n"
    )

    def _seed_schedule(self, ticket_id, done=0, total=2):
        body = self._SCHEDULE_SEED.format(ticket=ticket_id).replace("(0/2)", "({0}/{1})".format(done, total))
        self._mk("SCHEDULE", ["management"], body)

    def test_schedule_counter_and_state_refresh_on_completion(self) -> None:
        self._seed_dashboards()
        t = self._mk("Ship", ["ticket", "ticket:status:done"], "Issues: #5, #6\n")
        self._mk("A", ["issue", "issue:status:done"], "Ticket: #{0}\n".format(t))
        self._mk("B", ["issue", "issue:status:done"], "Ticket: #{0}\n".format(t))
        self._seed_schedule(t)

        out = dashboards.refresh_dashboards(self.remote)
        self.assertIn("SCHEDULE", out["updated"])
        schedule = self._body(self._id_of("SCHEDULE"))
        self.assertIn("(2/2)", schedule)
        self.assertIn("(done)", schedule)
        # skill-owned bits untouched
        self.assertIn("Foundation sprint", schedule)
        self.assertIn("★", schedule)

    def test_schedule_partial_progress_stays_ongoing(self) -> None:
        self._seed_dashboards()
        t = self._mk("Ship", ["ticket", "ticket:status:in_progress"], "Issues: #5, #6\n")
        self._mk("A", ["issue", "issue:status:done"], "Ticket: #{0}\n".format(t))
        self._mk("B", ["issue", "issue:status:in_progress"], "Ticket: #{0}\n".format(t))
        self._seed_schedule(t)

        dashboards.refresh_dashboards(self.remote)
        schedule = self._body(self._id_of("SCHEDULE"))
        self.assertIn("(1/2)", schedule)
        self.assertIn("(ongoing)", schedule)

    def test_schedule_not_started_is_planned(self) -> None:
        self._seed_dashboards()
        t = self._mk("Ship", ["ticket", "ticket:status:todo"], "Issues: #5, #6\n")
        self._mk("A", ["issue", "issue:status:todo"], "Ticket: #{0}\n".format(t))
        self._mk("B", ["issue", "issue:status:todo"], "Ticket: #{0}\n".format(t))
        self._seed_schedule(t)

        dashboards.refresh_dashboards(self.remote)
        schedule = self._body(self._id_of("SCHEDULE"))
        self.assertIn("(0/2)", schedule)
        self.assertIn("(planned)", schedule)

    def test_schedule_counter_notes_cancelled_issues(self) -> None:
        self._seed_dashboards()
        t = self._mk("Ship", ["ticket", "ticket:status:in_progress"], "Issues: #5, #6, #7, #8\n")
        self._mk("A", ["issue", "issue:status:done"], "Ticket: #{0}\n".format(t))
        self._mk("B", ["issue", "issue:status:in_progress"], "Ticket: #{0}\n".format(t))
        self._mk("C", ["issue", "issue:status:wont_do"], "Ticket: #{0}\n".format(t))
        self._mk("D", ["issue", "issue:status:deprecated"], "Ticket: #{0}\n".format(t))
        self._seed_schedule(t)

        dashboards.refresh_dashboards(self.remote)
        schedule = self._body(self._id_of("SCHEDULE"))
        # cancelled issues drop out of the denominator and are noted, deprecated first
        self.assertIn("(1/2 + 1 deprecated + 1 wont_do)", schedule)

        # idempotent on its own richer output
        second = dashboards.refresh_dashboards(self.remote)
        self.assertNotIn("SCHEDULE", second["updated"])

    def test_schedule_seed_placeholder_untouched(self) -> None:
        self._seed_dashboards()
        placeholder = "# SCHEDULE\n\n_No sprints planned yet._\n"
        self._mk("SCHEDULE", ["management"], placeholder)
        self._mk("E", ["epic", "epic:status:done"], "# Epic\n")
        out = dashboards.refresh_dashboards(self.remote)
        self.assertNotIn("SCHEDULE", out["updated"])
        self.assertEqual(self._body(self._id_of("SCHEDULE")), placeholder)

    def test_schedule_refresh_is_idempotent(self) -> None:
        self._seed_dashboards()
        t = self._mk("Ship", ["ticket", "ticket:status:done"], "Issues: #5\n")
        self._mk("A", ["issue", "issue:status:done"], "Ticket: #{0}\n".format(t))
        self._seed_schedule(t, total=1)
        first = dashboards.refresh_dashboards(self.remote)
        self.assertIn("SCHEDULE", first["updated"])
        second = dashboards.refresh_dashboards(self.remote)
        self.assertNotIn("SCHEDULE", second["updated"])

    def _id_of(self, title):
        for summary in self.remote.list_entries(is_open=None).data:
            if summary.title == title:
                return summary.id
        raise AssertionError("no post titled " + title)


if __name__ == "__main__":
    unittest.main()
