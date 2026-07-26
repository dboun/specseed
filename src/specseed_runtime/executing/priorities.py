"""priorities.py - the priority scale for the two-lane work queue.

Priorities are arbitrary integers; higher is claimed first within a lane
(``Database.claim_next`` orders by ``priority`` desc, then ``task_id`` asc for
FIFO within a band). They are gathered here so every enqueue site picks a
deliberate, comparable value and the ordering is tunable in one place.

The control lane drains to empty every tick, so its priorities only decide order
WITHIN one drain - they matter when a burst lands together (e.g. a freshly
finished work job's result should be digested before newly synced events).

Default (`DEFAULT_PRIORITY` on ``enqueue``) is 50, used for ordinary
sync-enqueued control events.
"""

from __future__ import annotations

# ---- control lane ---------------------------------------------------------- #
# A finished work job's outcome jumps the queue: record + advance state before
# any newly synced event is processed.
CONTROL_WORK_RESULT = 100
# A closed/deleted entry tears down its pending work and interrupts the running
# job - handle before normal events so we stop wasted work fast.
CONTROL_TEARDOWN = 90
CONTROL_CLEANUP = 85
# Human approvals / reactions resolve fast and are what the user is waiting on.
CONTROL_APPROVAL = 70
# Ordinary remote-change events.
CONTROL_DEFAULT = 50
# apply.py subprocess: no agent/git, but the slowest control item - let faster
# items pass it within a drain.
CONTROL_SPEC_CHANGE_APPLY = 40

# ---- work lane ------------------------------------------------------------- #
# Merges / gate actions clear in-flight branches and unblock dependents, so they
# go ahead of fresh implement/review runs.
WORK_MERGE = 60
WORK_DEFAULT = 50
