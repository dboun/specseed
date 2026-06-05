"""cancellation.py - process-wide cooperative-cancellation registry.

When ``sync_to_db`` learns that an entry was closed or deleted while a task about
it is *in progress*, it must signal the runner to abort that task before the
``CleanupTask`` runs. It must never edit the in-progress DB row directly.

This registry is the signalling channel. The scheduler ``register()``s a
``threading.Event`` when it claims a task and ``clear()``s it when the task
finishes; ``sync_to_db._request_interrupt`` calls ``cancel(task_id)`` to set that
Event. The agent runner polls the Event in its wait loop and tears the agent down.

Keeping this in its own tiny, dependency-free module lets ``scheduling`` import it
lazily without an import cycle (scheduling <- executing <- scheduling).

Only Python stdlib is used.
"""

from __future__ import annotations

import threading
from typing import Optional


_lock = threading.Lock()
_events: dict[int, threading.Event] = {}


def register(task_id: int) -> threading.Event:
    """Create (or return the existing) cancel Event for ``task_id``."""
    key = int(task_id)
    with _lock:
        event = _events.get(key)
        if event is None:
            event = threading.Event()
            _events[key] = event
        return event


def event_for(task_id: int) -> Optional[threading.Event]:
    """Return the cancel Event for ``task_id`` if one is registered."""
    with _lock:
        return _events.get(int(task_id))


def cancel(task_id: int) -> bool:
    """Signal cancellation for ``task_id``. Returns True if an Event was set.

    A registered-but-not-yet-set Event is set; an unregistered task is recorded
    as already-cancelled so a slightly-later ``register`` still observes it.
    """
    key = int(task_id)
    with _lock:
        event = _events.get(key)
        if event is None:
            # Pre-arm: register an already-set Event so a racing claim sees it.
            event = threading.Event()
            _events[key] = event
        event.set()
        return True


def is_cancelled(task_id: int) -> bool:
    with _lock:
        event = _events.get(int(task_id))
        return bool(event and event.is_set())


def clear(task_id: int) -> None:
    """Forget ``task_id`` (called when a task finishes)."""
    with _lock:
        _events.pop(int(task_id), None)


def cancel_all() -> None:
    """Set every registered Event - used on a hard STOP."""
    with _lock:
        for event in _events.values():
            event.set()


def reset() -> None:
    """Drop all registered Events. Intended for tests."""
    with _lock:
        _events.clear()
