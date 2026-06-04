"""agent_runner.py - run a coding agent as a stoppable subprocess.

A run is driven from the *agent worker thread* the scheduler spawns for it, so the
scheduler loop never blocks. The run loop polls two stop conditions every
``poll_interval`` seconds:

* a cooperative ``cancel`` Event (set when the entry the task is about gets closed,
  or on a hard STOP), and
* a wall-clock ``timeout_s`` deadline (default 6h) so a wedged agent cannot run
  forever.

On either trip the child is ``terminate()``d, given ``grace`` seconds, then
``kill()``ed. Stdout is drained on a daemon thread so a large stream cannot
deadlock the OS pipe while we poll. The prompt is delivered on stdin (headless
``claude -p`` reads the task there). No API key, no SDK: it shells out to the
already-authenticated CLI, mirroring how the old runner worked.

Only Python stdlib is used.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from specseed_target_src.executing import platform_log


# Hard cap on a single agent run. The scheduler also enforces this at the thread
# level as a backstop, but the runner owns the primary deadline.
DEFAULT_AGENT_TIMEOUT_S = 6 * 60 * 60


@dataclass
class AgentResult:
    """Outcome of one agent run."""

    ok: bool
    returncode: Optional[int] = None
    stdout: str = ""
    killed: bool = False        # stopped via the cancel Event
    timed_out: bool = False     # stopped by the wall-clock deadline
    error: Optional[str] = None
    duration_s: float = 0.0


class AgentRunner:
    """Interface: run a prompt to completion and report the outcome."""

    def run(
        self,
        prompt: str,
        *,
        cwd: str | Path,
        cancel: Optional[threading.Event] = None,
        timeout_s: float = DEFAULT_AGENT_TIMEOUT_S,
    ) -> AgentResult:
        raise NotImplementedError


class SubprocessAgentRunner(AgentRunner):
    """Run an external CLI, prompt on stdin, stoppable by cancel + deadline."""

    def __init__(self, *, poll_interval: float = 0.25, grace: float = 10.0) -> None:
        self.poll_interval = poll_interval
        self.grace = grace

    def build_command(self, prompt: str, cwd: str | Path) -> list[str]:
        raise NotImplementedError

    def run(
        self,
        prompt: str,
        *,
        cwd: str | Path,
        cancel: Optional[threading.Event] = None,
        timeout_s: float = DEFAULT_AGENT_TIMEOUT_S,
    ) -> AgentResult:
        argv = self.build_command(prompt, cwd)
        start = time.monotonic()
        deadline = start + timeout_s if timeout_s and timeout_s > 0 else None
        platform_log.log_event(
            "agent_subprocess_start",
            runner=type(self).__name__,
            cwd=str(cwd),
            binary=argv[0] if argv else None,
            argv=argv,
            timeout_s=timeout_s,
            prompt_chars=len(prompt),
        )
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except (OSError, ValueError) as exc:
            platform_log.log_event(
                "agent_subprocess_launch_failed",
                runner=type(self).__name__,
                cwd=str(cwd),
                error=repr(exc),
            )
            return AgentResult(ok=False, error=f"failed to launch agent: {exc}",
                               duration_s=time.monotonic() - start)

        chunks: list[str] = []
        drained = threading.Event()

        def _drain() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    chunks.append(line)
            except (OSError, ValueError):
                pass
            finally:
                drained.set()

        drainer = threading.Thread(target=_drain, name="agent-stdout", daemon=True)
        drainer.start()

        # Deliver the prompt on stdin, then close so the agent sees EOF.
        try:
            if proc.stdin is not None:
                proc.stdin.write(prompt)
                proc.stdin.close()
        except (BrokenPipeError, ValueError, OSError):
            pass

        killed = False
        timed_out = False
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                killed = True
                platform_log.log_event(
                    "agent_subprocess_cancel_requested",
                    runner=type(self).__name__,
                    pid=proc.pid,
                )
                self._stop(proc)
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                platform_log.log_event(
                    "agent_subprocess_timeout",
                    runner=type(self).__name__,
                    pid=proc.pid,
                    timeout_s=timeout_s,
                )
                self._stop(proc)
                break
            time.sleep(self.poll_interval)

        returncode = proc.wait()
        drained.wait(timeout=self.grace)
        stdout = "".join(chunks)
        duration = time.monotonic() - start

        if killed:
            error = "agent run cancelled"
        elif timed_out:
            error = f"agent run timed out after {timeout_s:.0f}s"
        elif returncode != 0:
            error = f"agent exited with code {returncode}"
        else:
            error = None
        ok = (returncode == 0) and not killed and not timed_out
        platform_log.log_event(
            "agent_subprocess_complete",
            runner=type(self).__name__,
            pid=proc.pid,
            ok=ok,
            returncode=returncode,
            killed=killed,
            timed_out=timed_out,
            duration_s=duration,
            error=error,
            stdout_chars=len(stdout),
        )
        return AgentResult(
            ok=ok,
            returncode=returncode,
            stdout=stdout,
            killed=killed,
            timed_out=timed_out,
            error=error,
            duration_s=duration,
        )

    def _stop(self, proc: "subprocess.Popen[Any]") -> None:
        try:
            proc.terminate()
        except (OSError, ValueError):
            return
        try:
            proc.wait(timeout=self.grace)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except (OSError, ValueError):
                pass


class ClaudeAgentRunner(SubprocessAgentRunner):
    """Run the authenticated ``claude`` CLI headless, prompt on stdin."""

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-8",
        max_turns: int = 200,
        allowed_tools: str = "Read,Edit,Bash",
        permission_mode: str = "acceptEdits",
        binary: str = "claude",
        poll_interval: float = 0.5,
        grace: float = 10.0,
    ) -> None:
        super().__init__(poll_interval=poll_interval, grace=grace)
        self.model = model
        self.max_turns = max_turns
        self.allowed_tools = allowed_tools
        self.permission_mode = permission_mode
        self.binary = binary

    def build_command(self, prompt: str, cwd: str | Path) -> list[str]:
        return [
            self.binary, "-p",
            "--model", self.model,
            "--permission-mode", self.permission_mode,
            "--allowedTools", self.allowed_tools,
            "--max-turns", str(self.max_turns),
        ]


class CodexAgentRunner(SubprocessAgentRunner):
    """Run the authenticated ``codex exec`` CLI headless, prompt on stdin.

    Mirrors :class:`ClaudeAgentRunner`: shells out to an already-authenticated
    CLI, prompt delivered on stdin (trailing ``-``), stoppable by the cancel
    Event + wall-clock deadline. ``max_turns``/``allowed_tools`` are Claude-only
    and have no codex equivalent, so they are intentionally absent.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-5.4-mini",
        effort: str = "medium",
        sandbox: str = "workspace-write",
        binary: str = "codex",
        poll_interval: float = 0.5,
        grace: float = 10.0,
    ) -> None:
        super().__init__(poll_interval=poll_interval, grace=grace)
        self.model = model
        self.effort = effort
        self.sandbox = sandbox
        self.binary = binary

    def build_command(self, prompt: str, cwd: str | Path) -> list[str]:
        return [
            self.binary, "exec",
            "--model", self.model,
            "-c", f'model_reasoning_effort="{self.effort}"',
            "--sandbox", self.sandbox,
            "-c", 'approval_policy="never"',
            "-",
        ]


def build_runner(config: Optional[dict[str, Any]] = None) -> AgentRunner:
    """Build the agent runner the config selects (``config["runner"]``).

    ``runner.provider`` picks the CLI (``claude`` default, or ``codex``);
    ``runner.model`` / ``runner.effort`` tune it. Missing config => the Claude
    default, matching prior hardcoded behaviour.
    """
    runner_cfg = (config or {}).get("runner") or {}
    provider = str(runner_cfg.get("provider") or "claude").lower()
    model = runner_cfg.get("model")
    if provider == "codex":
        kwargs: dict[str, Any] = {}
        if model:
            kwargs["model"] = model
        if runner_cfg.get("effort"):
            kwargs["effort"] = runner_cfg["effort"]
        if runner_cfg.get("sandbox"):
            kwargs["sandbox"] = runner_cfg["sandbox"]
        return CodexAgentRunner(**kwargs)
    if provider in ("claude", "claude-code"):
        kwargs = {}
        if model:
            kwargs["model"] = model
        return ClaudeAgentRunner(**kwargs)
    raise ValueError(f"unsupported runner provider: {provider!r}")


class FakeAgentRunner(AgentRunner):
    """Test double: records calls, returns a canned result, no subprocess.

    Pass ``result`` for a fixed AgentResult, or ``side_effect(call)->AgentResult``
    for per-call behavior (e.g. to honor ``cancel``).
    """

    def __init__(self, result: Optional[AgentResult] = None, side_effect: Any = None) -> None:
        self.result = result if result is not None else AgentResult(ok=True, returncode=0)
        self.side_effect = side_effect
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        prompt: str,
        *,
        cwd: str | Path,
        cancel: Optional[threading.Event] = None,
        timeout_s: float = DEFAULT_AGENT_TIMEOUT_S,
    ) -> AgentResult:
        call = {"prompt": prompt, "cwd": str(cwd), "timeout_s": timeout_s, "cancel": cancel}
        self.calls.append(call)
        if self.side_effect is not None:
            return self.side_effect(call)
        return self.result
