"""handle_label_added.py - a label was attached to an entry.

When the label is a ``tier:``/``status:`` label, the payload carries the entity
context it implies, so the scheduler learns the new tier/status for free.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from specseed_runtime.tasks.task_base import Task, entity_ref_for_label


@dataclass
class LabelAddedPayload:
    label: str
    at: Optional[str] = None
    entity: Optional[dict] = None


class HandleLabelAdded(Task):
    ACTION = "handle_label_added"

    @classmethod
    def from_change(cls, change: Any) -> "HandleLabelAdded":
        label = str(change.new)
        ref = entity_ref_for_label(change.parent_id, label)
        payload = asdict(
            LabelAddedPayload(label=label, at=change.at, entity=ref.to_dict() if ref else None)
        )
        return cls(post_id=change.parent_id, payload=payload)
