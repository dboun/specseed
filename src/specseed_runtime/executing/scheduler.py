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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from specseed_runtime.scheduling.sync_to_db import sync_to_db
from specseed_runtime.tracking.resolve_remote import (
    load_remote_state,
    resolve_local,
    resolve_remote,
)
from specseed_runtime.executing import cancellation
from specseed_runtime.executing import dashboards as dashboards_mod
from specseed_runtime.executing import platform_log
from specseed_runtime.executing import recovery
from specseed_runtime.executing import runner_control
from specseed_runtime.executing.agent_runner import (
    AgentRunner,
    DEFAULT_AGENT_TIMEOUT_S,
    build_runner_chains,
)
from specseed_runtime.executing.context import ExecutionContext
from specseed_runtime.executing.control import ControlChannel, render_status
from specseed_runtime.executing.dispatch import HandlerOutcome, dispatch
from specseed_runtime.executing.permissions import Permissions


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
        control_file: Optional[str | Path] = None,
        status_file: Optional[str | Path] = None,
        heartbeat_interval: float = 5.0,
        config_loader: Optional[Callable[[], dict[str, Any]]] = None,
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
        # Set -> resume() re-reads config (and rebuilds what hangs off it). None
        # (tests, injected doubles) -> resume never swaps anything out.
        self._config_loader = config_loader
        self._poll_interval_override = poll_interval is not None
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else float(self.config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL))
        )

        # Out-of-band operator control: a separate CLI/web process writes the
        # desired state into control_file; we stamp our heartbeat into status_file.
        self._control_file = Path(control_file) if control_file else None
        self._status_file = Path(status_file) if status_file else None
        self.heartbeat_interval = heartbeat_interval
        self._last_desired: Optional[str] = None
        self._last_heartbeat = 0.0

        self._remote = remote
        self._local = local
        self._remote_factory = remote_factory or (lambda: resolve_remote(self.storage))
        self._local_factory = local_factory or (lambda: resolve_local(storage=self.storage))
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

        self._last_work_sig: Optional[str] = None

        # Provider-quota circuit breaker. When a task reports every runner spec was
        # quota-blocked, we park sync+claim until this monotonic deadline (the loop
        # stays alive for heartbeat/control). 0 = closed.
        self._quota_paused_until = 0.0

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
            if self._state == STOPPED:
                return
        # Config edits are gated on a paused/stopped runner, so resume is the
        # pick-up point: re-read config + remote wiring before running again.
        self.reload_config()
        with self._state_lock:
            if self._state != STOPPED:
                self._state = RUNNING
                platform_log.log_event("scheduler_resume")
        self._next_poll_at = 0.0  # sync promptly on resume

    def reload_config(self) -> None:
        """Re-read config and rebuild everything derived from it.

        No-op without a ``config_loader`` (tests, injected doubles). Rebuilds
        permissions, runner chains, poll interval (unless CLI-overridden);
        trackers re-resolve lazily so remote.json edits land.
        A bad config file logs and keeps the old state - never kills the loop.
        """
        if self._config_loader is None:
            return
        try:
            config = self._config_loader() or {}
        except Exception as exc:
            platform_log.log_event("config_reload_error", error=repr(exc))
            return
        self.config = config
        remote_state = load_remote_state(self.storage) if self.storage is not None else {}
        self.permissions = Permissions(self.config, remote_state)
        self.runner = build_runner_chains(self.config)
        if not self._poll_interval_override:
            self.poll_interval = float(self.config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL))
        self._remote = None
        self._local = None
        if self._control is not None:
            # Update the channel IN PLACE: recreating it would reset the comment
            # cursor and replay every old CONTROL command.
            try:
                self._ensure_trackers()
            except Exception as exc:
                platform_log.log_event("config_reload_tracker_error", error=repr(exc))
            else:
                self._control.tracker = self._remote
                self._control.config = self.config
                self._control.permissions = self.permissions
                self._control.bot_author = getattr(self._remote, "author", None)
        platform_log.log_event(
            "config_reloaded",
            storage=str(self.storage) if self.storage else None,
            poll_interval=self.poll_interval,
        )

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
            self._reconcile_control_file()
            self._heartbeat()
            self._process_control()
            if self._stop.is_set():
                break

            did_work = False
            if self.state == RUNNING and not self._quota_circuit_open():
                if time.monotonic() >= self._next_poll_at:
                    self._sync()
                    self._next_poll_at = time.monotonic() + self.poll_interval
                task = self.db.claim_next()
                if task is not None:
                    self._process_task(task)
                    did_work = True

            if not did_work and not self._stop.is_set():
                self._stop.wait(timeout=self._idle_sleep())
        self._heartbeat(force=True, final=True)
        platform_log.log_event("scheduler_loop_exit", state=self.state)

    def _reconcile_control_file(self) -> None:
        """Apply the operator's desired state written by the CLI/web service."""
        if self._control_file is None:
            return
        try:
            desired = runner_control.read_desired(self._control_file.parent)
        except Exception:  # control plane must never crash the loop
            return
        if desired is None or desired == self._last_desired:
            return
        self._last_desired = desired
        platform_log.log_event("control_file_desired", desired=desired)
        if desired == runner_control.PAUSED:
            self.pause()
        elif desired == runner_control.RUNNING:
            self.resume()
        elif desired == runner_control.STOPPED:
            self.request_stop()

    def _heartbeat(self, *, force: bool = False, final: bool = False) -> None:
        """Stamp the runner status file so other processes can observe us."""
        if self._status_file is None:
            return
        now = time.monotonic()
        if not force and (now - self._last_heartbeat) < self.heartbeat_interval:
            return
        self._last_heartbeat = now
        try:
            status = self.status()
            status["repo_root"] = str(self.repo_root)
            if final:
                status["state"] = STOPPED
                status["alive"] = False
            runner_control.write_runner_status(self._status_file.parent, status)
        except Exception:  # heartbeat is best-effort
            pass

    def _quota_circuit_open(self) -> bool:
        """True while a provider-quota park is in effect (sync+claim suspended)."""
        if self._quota_paused_until <= 0.0:
            return False
        if time.monotonic() >= self._quota_paused_until:
            self._quota_paused_until = 0.0
            platform_log.log_event("quota_circuit_closed")
            self._next_poll_at = 0.0  # sync promptly once the quota clears
            return False
        return True

    def _open_quota_circuit(self, task_id: int, outcome: "HandlerOutcome") -> None:
        """Requeue a quota-blocked task for its reset time and park the loop.

        ``outcome.quota_until`` is an ISO reset hint if the provider stated one;
        otherwise we park a default window. Requeue (not complete) means recovery
        never runs - no error post, no resolver (which shares the quota)."""
        park_s, not_before = self._quota_park(outcome.quota_until)
        try:
            self.db.requeue(task_id, not_before=not_before)
        except TypeError:  # a db double without not_before support
            self.db.requeue(task_id)
        self._quota_paused_until = time.monotonic() + park_s
        platform_log.log_event(
            "quota_circuit_open",
            task_id=task_id,
            park_seconds=park_s,
            not_before=not_before,
            quota_until=outcome.quota_until,
        )

    def _requeue_with_delay(self, task_id: int, delay_s: Optional[float]) -> None:
        """Requeue a task, optionally not before ``delay_s`` from now (the
        dependency gate uses this to re-check a held issue next poll, not at once)."""
        not_before = None
        if delay_s and delay_s > 0:
            nxt = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=delay_s)
            not_before = nxt.isoformat().replace("+00:00", "Z")
        try:
            self.db.requeue(task_id, not_before=not_before) if not_before else self.db.requeue(task_id)
        except TypeError:  # a db double without not_before support
            self.db.requeue(task_id)

    def _quota_park(self, quota_until: Optional[str]) -> tuple[float, str]:
        """Return (park_seconds, not_before_iso) from an optional ISO reset hint."""
        from specseed_runtime.executing.quota import DEFAULT_PARK_S, MIN_PARK_S

        now = datetime.now(timezone.utc)
        if quota_until:
            try:
                reset = datetime.fromisoformat(quota_until)
                if reset.tzinfo is None:
                    reset = reset.replace(tzinfo=timezone.utc)
                secs = max(MIN_PARK_S, (reset - now).total_seconds())
                return secs, reset.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            except ValueError:
                pass
        nxt = now.replace(microsecond=0) + timedelta(seconds=DEFAULT_PARK_S)
        return float(DEFAULT_PARK_S), nxt.isoformat().replace("+00:00", "Z")

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
            summary = sync_to_db(self._local, self._remote, db=self.db, config=self.config)
        except Exception as exc:  # never let a bad poll kill the loop
            summary = {"ok": False, "error": repr(exc)}
            platform_log.log_event("sync_error", error=repr(exc))
        self._last_sync = summary
        self._last_poll_at = _now_iso()
        platform_log.log_event("sync_complete", summary=summary)
        self._refresh_dashboards()

    def _refresh_dashboards(self) -> None:
        """Re-render ROADMAP / CURRENT SPRINT when the work tree changed.

        Cheap signature gate first so the per-post body reads only happen after a
        real change. Never lets a dashboard hiccup disturb the loop. Dashboards are
        always kept in sync - a stale ROADMAP/CURRENT SPRINT is worse than useless.
        """
        if self._remote is None:
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
            if getattr(outcome, "quota", False):
                self._open_quota_circuit(task_id, outcome)
            else:
                self._requeue_with_delay(task_id, getattr(outcome, "requeue_after_s", None))
            platform_log.log_event(
                "task_requeued",
                task_id=task_id,
                success=outcome.success,
                error=outcome.error,
                detail=outcome.detail,
                quota=getattr(outcome, "quota", False),
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
            self._recover(task, outcome)

    def _recover(self, task: dict[str, Any], outcome: HandlerOutcome) -> None:
        """Post-completion recovery: schedule retries / error post / agent.

        Best-effort by design - recovery must never take the loop down.
        """
        try:
            remote = None
            try:
                self._ensure_trackers()
                remote = self._remote
            except Exception:
                pass  # retry scheduling still works without a reachable remote
            if outcome.success:
                if int(task.get("attempts") or 0) > 1:
                    recovery.on_recovered(
                        db=self.db, config=self.config, remote=remote, task=task
                    )
                return
            recovery.on_failure(
                db=self.db,
                config=self.config,
                remote=remote,
                task=task,
                outcome=outcome,
            )
        except Exception as exc:
            platform_log.log_event(
                "recovery_error", task_id=task.get("task_id"), error=repr(exc)
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
            # The loop thread sits here for the whole run (minutes-hours): keep
            # the heartbeat fresh or the runner reads as dead while merely busy
            # (and an operator "restart" then kills the live agent).
            self._heartbeat()
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
