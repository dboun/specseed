"""context.py - the bundle of dependencies a task handler needs.

Everything is injected so handlers (and tests) never reach for singletons or the
network: the DB queue, the local mirror to read from, the remote source of truth
to write to, the parsed config + programmatic permissions, the agent runner, and
the per-task cancel Event.

Only Python stdlib is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Optional

from specseed_runtime.entities.entity_base import Entity, parse_depends_on, parse_parent
from specseed_runtime.executing.agent_runner import (
    AgentRunner,
    DEFAULT_AGENT_TIMEOUT_S,
    RunnerChains,
)
from specseed_runtime.executing.permissions import Permissions


@dataclass
class ExecutionContext:
    """Dependencies passed to every handler for one task run."""

    db: Any
    local: Any                       # read cache (TrackingLocal)
    remote: Any                      # source of truth (TrackingBase)
    config: dict[str, Any]
    permissions: Permissions
    runner: "AgentRunner | RunnerChains"   # a bare runner (tests) or per-function chains
    repo_root: Path
    storage: Path
    cancel: threading.Event
    agent_timeout_s: float = DEFAULT_AGENT_TIMEOUT_S


def _label_names(labels: Any) -> list[str]:
    """Normalize a labels collection (TrackingLabel objects or strings) to names."""
    names: list[str] = []
    for label in labels or []:
        name = getattr(label, "name", None)
        names.append(str(name if name is not None else label))
    return names


def load_entity(ctx: ExecutionContext, post_id: Optional[str]) -> tuple[Optional[Entity], list[Any]]:
    """Read an entry from the LOCAL mirror and build its Entity + conversation.

    Returns ``(entity, comments)``. ``entity`` is ``None`` if the post id is empty
    or the entry is not in the local cache. We read local (never the remote) so a
    task never hammers the provider API to plan its reaction.
    """
    if post_id in (None, ""):
        return None, []
    result = ctx.local.get_entry(post_id)
    if not getattr(result, "ok", False) or result.data is None:
        return None, []
    details = result.data
    labels = _label_names(getattr(details, "labels", []))
    entity = Entity.for_labels(
        post_id=post_id,
        labels=labels,
        title=getattr(details, "title", None),
    )
    # Attach entry-level reactions so the approval system can read a 👍/👎 on the
    # post itself (state_machines.base.approved_by / rejected_by).
    entity.reactions = list(getattr(details, "reactions", []) or [])
    # Assignees gate work: an issue is only implemented (auto or via the approval
    # gate) once the agent is among them. Carry them so dispatch can judge that.
    entity.assignees = list(getattr(details, "assignees", []) or [])
    # Dependencies + parent are body links (remote-posts.md); parse them so the
    # dispatcher can hold an issue until its own deps AND its parent ticket's
    # dependency-tickets are done.
    body = getattr(details, "body", None)
    entity.depends_on = parse_depends_on(body)
    if not entity.parent_id:
        entity.parent_id = parse_parent(body)
    # The agent reads the post body + thread FROM THE PROMPT now (never the db), so
    # carry the body on the entity for prompts.build_* to inject.
    entity.body = body or ""
    comments = list(getattr(details, "comments", []) or [])
    return entity, comments
