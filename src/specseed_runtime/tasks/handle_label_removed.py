"""handle_label_removed.py - a label was detached from an entry."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from specseed_target_src.tasks.task_base import Task, entity_ref_for_label


@dataclass
class LabelRemovedPayload:
    label: str
    at: Optional[str] = None
    entity: Optional[dict] = None


class HandleLabelRemoved(Task):
    ACTION = "handle_label_removed"

    @classmethod
    def from_change(cls, change: Any) -> "HandleLabelRemoved":
        label = str(change.old)
        ref = entity_ref_for_label(change.parent_id, label)
        payload = asdict(
            LabelRemovedPayload(label=label, at=change.at, entity=ref.to_dict() if ref else None)
        )
        return cls(post_id=change.parent_id, payload=payload)
