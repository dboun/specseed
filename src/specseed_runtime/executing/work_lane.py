"""work_lane.py - action names shared across the control/work lane split.

Tiny, dependency-free so both ``dispatch`` (which enqueues work jobs and routes
their results) and ``work_runner`` (which executes them) can import the names
without an import cycle.

* ``WORK_RUN`` / ``WORK_GATE_ACTION`` run on the WORK lane (agents + git).
* ``PROCESS_WORK_RESULT`` runs on the CONTROL lane: it digests a finished work
  job's outcome (advance state, schedule follow-up git, run recovery).
"""

from __future__ import annotations

# work lane
WORK_RUN = "work_run"  # run an agent for an intent (implement/review/spec_change)
WORK_GATE_ACTION = "work_gate_action"  # git: ready+gate or merge a branch

# control lane
PROCESS_WORK_RESULT = "process_work_result"

WORK_LANE_ACTIONS = {WORK_RUN, WORK_GATE_ACTION}
