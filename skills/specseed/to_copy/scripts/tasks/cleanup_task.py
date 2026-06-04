"""cleanup_task.py - undo half-done work after an entry is torn down.

When an entry is closed or deleted while a task for it was mid-flight, that work
may have left partial artifacts behind. sync_to_db enqueues a CleanupTask (and,
once execution exists, will interrupt the running task) so the scheduler can tidy
up. This is a synthetic task - it has no originating TrackingSyncChange.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from task_base import Task


@dataclass
class CleanupPayload:
    reason: str
    interrupted_task_id: Optional[int] = None


class CleanupTask(Task):
    ACTION = "cleanup"

    @classmethod
    def for_post(
        cls,
        post_id: str | int,
        reason: str,
        interrupted_task_id: Optional[int] = None,
    ) -> "CleanupTask":
        payload = asdict(CleanupPayload(reason=reason, interrupted_task_id=interrupted_task_id))
        return cls(post_id=post_id, payload=payload)
