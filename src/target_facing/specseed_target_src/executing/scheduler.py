"""scheduler.py - the poll -> sync -> drain loop, in its own thread.

The Scheduler is the consumption half of specseed. Once started it runs on a
daemon thread (so the main thread stays free and the loop can be stopped without
blocking) and, while RUNNING:

1. processes operator commands from the CONTROL post (STATUS/START/PAUSE/STOP) -
   done every tick, even while PAUSED, so the operator can always resume/stop;
2. on the poll interval, syncs the remote (source of truth) into the local mirror
   and turns the diff into queued tasks (``sync_to_db``);
3. drains one queued task per iteration, dispatching it.

Each task is dispatched on a **worker thread** so a long agent run can be governed
by a wall-clock backstop (default 6h) and cooperatively cancelled - without
wedging the loop. Cancellation is signalled through the
``cancellation`` registry; ``sync_to_db`` uses the same registry to abort an
in-progress task when its entry is closed/deleted.

States: RUNNING (sync+drain), PAUSED (loop alive, control only), STOPPED (loop
exits). STOP is a hard stop: it cancels the in-flight task and joins.

Only Python stdlib is used.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from specseed_target_src.scheduling.sync_to_db import sync_to_db
from specseed_target_src.tracking.resolve_remote import (
    load_remote_state,
    resolve_local,
    resolve_remote,
)
from specseed_target_src.executing import cancellation
from specseed_target_src.executing import dashboards as dashboards_mod
from specseed_target_src.executing import platform_log
from specseed_target_src.executing.agent_runner import (
    AgentRunner,
    DEFAULT_AGENT_TIMEOUT_S,
)
from specseed_target_src.executing.context import ExecutionContext
from specseed_target_src.executing.control import ControlChannel, render_status
from specseed_target_src.executing.dispatch import HandlerOutcome, dispatch
from specseed_target_src.executing.permissions import Permissions


RUNNING = "running"
PAUSED = "paused"
STOPPED = "stopped"

DEFAULT_POLL_INTERVAL = 45


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Scheduler:
    """Owns the background loop and the worker-thread agent runs."""

    def __init__(
        self,
        *,
        db: Any,
        runner: AgentRunner,
        config: Optional[dict[str, Any]] = None,
        storage: Optional[str | Path] = None,
        repo_root: Optional[str | Path] = None,
        remote: Any = None,
        local: Any = None,
        permissions: Optional[Permissions] = None,
        control_channel: Optional[ControlChannel] = None,
        poll_interval: Optional[float] = None,
        agent_timeout_s: float = DEFAULT_AGENT_TIMEOUT_S,
        tick: float = 0.5,
        remote_factory: Optional[Callable[[], Any]] = None,
        local_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.db = db
        self.runner = runner
        self.config = config or {}
        self.storage = Path(storage) if storage else None
        if self.storage is not None:
            platform_log.configure(self.storage)
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        remote_state = load_remote_state(self.storage) if self.storage is not None else {}
        self.permissions = permissions or Permissions(self.config, remote_state)
        self.agent_timeout_s = agent_timeout_s
        self.tick = tick
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else float(self.config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL))
        )

        self._remote = remote
        self._local = local
        self._remote_factory = remote_factory or (lambda: resolve_remote(self.storage))
        self._local_factory = local_factory or (lambda: resolve_local())
        self._control = control_channel

        self._state = PAUSED
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()

        # status bookkeeping
        self._started_at: Optional[str] = None
        self._last_poll_at: Optional[str] = None
        self._last_sync: Optional[dict[str, Any]] = None
        self._current_task_id: Optional[int] = None
        self._next_poll_at = 0.0  # monotonic; 0 forces an immediate first sync

        dashboards_cfg = self.config.get("dashboards")
        self._dashboards_enabled = (
            bool(dashboards_cfg.get("auto_refresh", True))
            if isinstance(dashboards_cfg, dict)
            else True
        )
        self._last_work_sig: Optional[str] = None

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Start the background loop (non-blocking). Idempotent while alive."""
        if self._thread is not None and self._thread.is_alive():
            platform_log.log_event("scheduler_start_skipped", reason="already_alive")
            return
        self._stop.clear()
        self._next_poll_at = 0.0
        with self._state_lock:
            self._state = RUNNING
        self._started_at = _now_iso()
        platform_log.log_event(
            "scheduler_start",
            repo_root=str(self.repo_root),
            storage=str(self.storage) if self.storage else None,
            poll_interval=self.poll_interval,
            agent_timeout_s=self.agent_timeout_s,
        )
        self._thread = threading.Thread(target=self._loop, name="specseed-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        """Hard stop: cancel the in-flight task and join the loop thread."""
        platform_log.log_event("scheduler_stop_requested", timeout=timeout)
        self.request_stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            platform_log.log_event("scheduler_stop_joined", alive=thread.is_alive())

    def request_stop(self) -> None:
        with self._state_lock:
            self._state = STOPPED
        self._stop.set()
        platform_log.log_event("scheduler_request_stop", current_task_id=self._current_task_id)
        if self._current_task_id is not None:
            cancellation.cancel(self._current_task_id)

    def pause(self) -> None:
        with self._state_lock:
            if self._state != STOPPED:
                self._state = PAUSED
                platform_log.log_event("scheduler_pause")

    def resume(self) -> None:
        with self._state_lock:
            if self._state != STOPPED:
                self._state = RUNNING
                platform_log.log_event("scheduler_resume")
        self._next_poll_at = 0.0  # sync promptly on resume

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------ #
    # status
    # ------------------------------------------------------------------ #
    def status(self) -> dict[str, Any]:
        pending = self.db.pending_count()
        active = self.db.active_count()
        return {
            "state": self.state,
            "alive": self.is_running(),
            "pending": pending,
            "in_progress": max(active - pending, 0),
            "current_task_id": self._current_task_id,
            "started_at": self._started_at,
            "last_poll_at": self._last_poll_at,
            "poll_interval_seconds": self.poll_interval,
            "last_sync": self._last_sync,
        }

    # ------------------------------------------------------------------ #
    # the loop
    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        platform_log.log_event("scheduler_loop_start")
        while not self._stop.is_set():
            self._process_control()
            if self._stop.is_set():
                break

            did_work = False
            if self.state == RUNNING:
                if time.monotonic() >= self._next_poll_at:
                    self._sync()
                    self._next_poll_at = time.monotonic() + self.poll_interval
                task = self.db.claim_next()
                if task is not None:
                    self._process_task(task)
                    did_work = True

            if not did_work and not self._stop.is_set():
                self._stop.wait(timeout=self._idle_sleep())
        platform_log.log_event("scheduler_loop_exit", state=self.state)

    def _idle_sleep(self) -> float:
        if self.state != RUNNING:
            return self.tick
        remaining = self._next_poll_at - time.monotonic()
        if remaining <= 0:
            return self.tick
        return min(self.tick, remaining)

    def run_once(self) -> dict[str, Any]:
        """Do one control + sync + full drain pass synchronously (no thread).

        Handy for ``run.py --once`` and for tests. Returns the sync summary.
        """
        self._process_control()
        self._sync()
        drained = 0
        while not self._stop.is_set():
            task = self.db.claim_next()
            if task is None:
                break
            platform_log.log_event(
                "task_claimed",
                task_id=task.get("task_id"),
                action=task.get("action"),
                post_id=task.get("post_id"),
            )
            self._process_task(task)
            drained += 1
        summary = dict(self._last_sync or {})
        summary["drained"] = drained
        platform_log.log_event("scheduler_run_once_summary", summary=summary)
        return summary

    # ------------------------------------------------------------------ #
    # steps
    # ------------------------------------------------------------------ #
    def _ensure_trackers(self) -> None:
        if self._remote is None:
            platform_log.log_event("tracker_remote_resolve_start")
            self._remote = self._remote_factory()
            platform_log.log_event("tracker_remote_resolve_complete", type=type(self._remote).__name__)
        if self._local is None:
            platform_log.log_event("tracker_local_resolve_start")
            self._local = self._local_factory()
            platform_log.log_event("tracker_local_resolve_complete", type=type(self._local).__name__)

    def _ensure_control(self) -> Optional[ControlChannel]:
        if self._control is None:
            try:
                self._ensure_trackers()
            except Exception:
                platform_log.log_event("control_init_failed")
                return None
            self._control = ControlChannel(
                self._remote,
                self.config,
                self.permissions,
                bot_author=getattr(self._remote, "author", None),
            )
        return self._control

    def _process_control(self) -> None:
        channel = self._ensure_control()
        if channel is None:
            return
        try:
            commands = channel.poll()
        except Exception as exc:
            platform_log.log_event("control_poll_error", error=repr(exc))
            return
        for command in commands:
            verb = command.verb
            platform_log.log_event("control_command", verb=verb, author=command.author)
            if verb == "pause":
                self.pause()
            elif verb == "start":
                self.resume()
            elif verb == "stop":
                self.request_stop()
            elif verb == "status":
                try:
                    posted = channel.post_status(render_status(self.status()))
                    platform_log.log_event("control_status_posted", posted=posted)
                except Exception as exc:
                    platform_log.log_event("control_status_post_error", error=repr(exc))
                    pass

    def _sync(self) -> None:
        try:
            self._ensure_trackers()
            platform_log.log_event("sync_start")
            summary = sync_to_db(self._local, self._remote, db=self.db)
        except Exception as exc:  # never let a bad poll kill the loop
            summary = {"ok": False, "error": repr(exc)}
            platform_log.log_event("sync_error", error=repr(exc))
        self._last_sync = summary
        self._last_poll_at = _now_iso()
        platform_log.log_event("sync_complete", summary=summary)
        self._refresh_dashboards()

    def _refresh_dashboards(self) -> None:
        """Re-render ROADMAP / Current sprint when the work tree changed.

        Cheap signature gate first so the per-post body reads only happen after a
        real change. Never lets a dashboard hiccup disturb the loop.
        """
        if not self._dashboards_enabled or self._remote is None:
            return
        try:
            signature = dashboards_mod.work_signature(self._remote)
            if signature is None or signature == self._last_work_sig:
                return
            dashboards_mod.refresh_dashboards(self._remote)
            self._last_work_sig = signature
        except Exception as exc:  # dashboards are best-effort
            platform_log.log_event("dashboard_refresh_error", error=repr(exc))

    def _make_context(self, cancel: threading.Event) -> ExecutionContext:
        self._ensure_trackers()
        return ExecutionContext(
            db=self.db,
            local=self._local,
            remote=self._remote,
            config=self.config,
            permissions=self.permissions,
            runner=self.runner,
            repo_root=self.repo_root,
            storage=self.storage or self.repo_root,
            cancel=cancel,
            agent_timeout_s=self.agent_timeout_s,
        )

    def _process_task(self, task: dict[str, Any]) -> None:
        task_id = int(task["task_id"])
        cancel = cancellation.register(task_id)
        self._current_task_id = task_id
        platform_log.log_event(
            "task_start",
            task_id=task_id,
            action=task.get("action"),
            post_id=task.get("post_id"),
            attempts=task.get("attempts"),
        )
        try:
            outcome = self._run_in_worker(task, cancel)
        except Exception as exc:  # pragma: no cover - defensive
            outcome = HandlerOutcome(success=False, error=f"handler crashed: {exc!r}")
            platform_log.log_event("task_handler_crash", task_id=task_id, error=repr(exc))
        finally:
            self._current_task_id = None
            cancellation.clear(task_id)

        if outcome is None:
            self.db.requeue(task_id)
            platform_log.log_event("task_requeued", task_id=task_id, reason="worker_no_outcome")
            return
        if outcome.requeue:
            self.db.requeue(task_id)
            platform_log.log_event(
                "task_requeued",
                task_id=task_id,
                success=outcome.success,
                error=outcome.error,
                detail=outcome.detail,
            )
        else:
            self.db.complete(task_id, outcome.success, outcome.error)
            platform_log.log_event(
                "task_complete",
                task_id=task_id,
                success=outcome.success,
                error=outcome.error,
                detail=outcome.detail,
            )

    def _run_in_worker(self, task: dict[str, Any], cancel: threading.Event) -> Optional[HandlerOutcome]:
        """Run dispatch on its own thread, governed by stop + the 6h backstop."""
        holder: dict[str, HandlerOutcome] = {}

        def target() -> None:
            ctx = self._make_context(cancel)
            holder["outcome"] = dispatch(ctx, task)

        worker = threading.Thread(target=target, name=f"specseed-task-{task['task_id']}", daemon=True)
        worker.start()
        platform_log.log_event(
            "task_worker_start",
            task_id=task.get("task_id"),
            worker_name=worker.name,
            timeout_s=self.agent_timeout_s,
        )
        deadline = time.monotonic() + self.agent_timeout_s if self.agent_timeout_s else None
        logged_stop_cancel = False
        logged_timeout_cancel = False
        while worker.is_alive():
            if self._stop.is_set():
                cancel.set()
                if not logged_stop_cancel:
                    platform_log.log_event("task_worker_cancelled_by_stop", task_id=task.get("task_id"))
                    logged_stop_cancel = True
            if deadline is not None and time.monotonic() >= deadline:
                cancel.set()
                if not logged_timeout_cancel:
                    platform_log.log_event("task_worker_cancelled_by_timeout", task_id=task.get("task_id"))
                    logged_timeout_cancel = True
            worker.join(timeout=self.tick)
        platform_log.log_event("task_worker_exit", task_id=task.get("task_id"), has_outcome="outcome" in holder)
        return holder.get("outcome")
