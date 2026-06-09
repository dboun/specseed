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

import json
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from specseed_runtime.executing import agent_report
from specseed_runtime.executing import platform_log
from specseed_runtime.executing import quota as quota_mod


# Hard cap on a single agent run. The scheduler also enforces this at the thread
# level as a backstop, but the runner owns the primary deadline.
DEFAULT_AGENT_TIMEOUT_S = 6 * 60 * 60

# The agent jobs a runner chain is configured per. Each maps to one dispatch
# intent except merge_conflicts, which is surfaced but not dispatched yet (the
# runtime never drives git, so nothing triggers it — conflict handling lives in
# the implement prompt's git policy instead). resolve_platform_errors is the
# failure-recovery investigator (executing/recovery.py).
RUNNER_FUNCTIONS = (
    "spec",
    "implementation",
    "review",
    "merge_conflicts",
    "resolve_platform_errors",
)

# provider -> the env var its CLI reads for its config/home dir, and the default.
PROVIDER_CONFIG_ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
PROVIDER_DEFAULT_HOME = {"claude": "~/.claude", "codex": "~/.codex"}

# Claude model presets - aliases that always point at the latest version of each
# tier. Anything else a user types is a "custom" model string passed verbatim.
CLAUDE_MODELS = ("opus", "sonnet", "haiku")


def list_codex_model_slugs(cache_path: Path | None = None) -> list[str]:
    """Codex model slugs the local CLI knows about, read from its models cache.
    Empty list if the cache is missing/unreadable - caller falls back to custom."""
    path = cache_path or (Path.home() / ".codex" / "models_cache.json")
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    slugs: list[str] = []
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        slug = str(model.get("slug") or "").strip()
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    return slugs


def model_presets(provider: str) -> list[str]:
    """Preset model options offered for ``provider`` (claude tags, codex slugs).
    Codex reads from the local cache, so the list is machine-dependent."""
    if str(provider or "").lower() == "codex":
        return list_codex_model_slugs()
    return list(CLAUDE_MODELS)


def default_model(provider: str) -> str:
    """Default model when a spec switches to ``provider``: opus for claude, the
    first cached slug for codex (blank if none cached -> user types a custom one)."""
    if str(provider or "").lower() == "codex":
        slugs = list_codex_model_slugs()
        return slugs[0] if slugs else ""
    return "opus"

# Tail of agent stdout kept on failure (log + task error). Tail, not head: CLIs
# print the error last.
STDOUT_TAIL_CHARS = 2000


def stdout_tail(text: str, limit: int = STDOUT_TAIL_CHARS) -> str:
    """Last ``limit`` chars of ``text``, stripped; marks truncation."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "...[truncated]" + text[-limit:]


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# -- live feed: claude stream-json -> readable lines ----------------------- #
# `claude -p --output-format stream-json` writes one self-contained JSON event
# per line. We turn each into a short, human-readable line for the 'Agent output'
# popup: thinking, assistant text, tool calls (with a one-field input summary),
# and tool results. Display-only and defensive - any unexpected shape is skipped,
# never raised (the run must not depend on this).

_STREAM_LINE_MAX = 200


def _condense(text: str, limit: int = _STREAM_LINE_MAX) -> str:
    """Collapse whitespace to a single line, truncated with an ellipsis."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _tool_input_summary(inp: object) -> str:
    """One representative field of a tool's input (path/command/pattern/...)."""
    if not isinstance(inp, dict):
        return ""
    for key in ("file_path", "path", "command", "pattern", "query", "url", "prompt", "description"):
        val = inp.get(key)
        if val:
            return _condense(val, 160)
    try:
        return _condense(json.dumps(inp, separators=(",", ":")), 160)
    except (TypeError, ValueError):
        return ""


def _result_first_line(content: object) -> str:
    """First text line of a tool_result's content (string or block list)."""
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                text = block["text"]
                break
    text = text.strip()
    return text.splitlines()[0] if text else ""


def _format_assistant_blocks(content: object) -> list[str]:
    out: list[str] = []
    if not isinstance(content, list):
        return out
    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type")
        if bt == "text":
            txt = (block.get("text") or "").strip()
            if txt:
                out.append("● " + _condense(txt))
        elif bt == "thinking":
            txt = (block.get("thinking") or "").strip()
            if txt:
                out.append("● Thinking: " + _condense(txt))
        elif bt == "tool_use":
            name = block.get("name") or "tool"
            summary = _tool_input_summary(block.get("input"))
            out.append("● {0}({1})".format(name, summary) if summary else "● " + str(name))
    return out


def format_claude_stream_line(raw: str) -> str:
    """One stream-json line -> newline-terminated display text (or "")."""
    line = (raw or "").strip()
    if not line:
        return ""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        # Not JSON (a stray warning/log line): show it verbatim so nothing is lost.
        return raw if raw.endswith("\n") else raw + "\n"
    if not isinstance(obj, dict):
        return ""
    kind = obj.get("type")
    if kind == "assistant":
        shown = _format_assistant_blocks((obj.get("message") or {}).get("content"))
    elif kind == "user":
        content = (obj.get("message") or {}).get("content")
        shown = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    first = _result_first_line(block.get("content"))
                    if first:
                        shown.append("  └ " + _condense(first))
    else:
        shown = []  # system/result/etc: housekeeping, nothing to show
    return "".join(s + "\n" for s in shown)


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
    # Structured result the agent wrote to $SPECSEED_RESULT_FILE (parsed per
    # intent), or None + report_error when missing/invalid. rc==0 is no longer a
    # sufficient success signal; the caller judges from this.
    report: Optional[dict] = None
    report_error: Optional[str] = None
    # Set by RunnerChains when EVERY spec in the chain failed with a provider
    # quota signal: a global condition, not a task-local failure.
    quota_exhausted: bool = False
    quota_reset_hint: Optional[str] = None


class AgentRunner:
    """Interface: run a prompt to completion and report the outcome.

    ``on_start(pid, binary)`` fires once when a child process spawns, so the
    caller can ledger it (``executing/inflight``) for orphan reclaim.
    """

    def run(
        self,
        prompt: str,
        *,
        cwd: str | Path,
        cancel: Optional[threading.Event] = None,
        timeout_s: float = DEFAULT_AGENT_TIMEOUT_S,
        on_start: Optional[Callable[[int, str], None]] = None,
        intent: Optional[str] = None,
        live_log: Optional[str | Path] = None,
    ) -> AgentResult:
        raise NotImplementedError


class SubprocessAgentRunner(AgentRunner):
    """Run an external CLI, prompt on stdin, stoppable by cancel + deadline."""

    def __init__(
        self,
        *,
        poll_interval: float = 0.25,
        grace: float = 10.0,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.poll_interval = poll_interval
        self.grace = grace
        # Extra env vars layered over the parent process env for the child (e.g.
        # CLAUDE_CONFIG_DIR / CODEX_HOME from a spec's provider_data_dir).
        self.env_overrides: dict[str, str] = dict(env or {})

    def build_command(self, prompt: str, cwd: str | Path) -> list[str]:
        raise NotImplementedError

    def format_stream_line(self, raw: str) -> str:
        """Turn one raw stdout line into display text for the live feed.

        Base behaviour is passthrough - codex exec already streams readable
        reasoning + actions. Providers whose stdout is machine-encoded (claude
        ``--output-format stream-json``) override this to humanise it. Returns
        the (newline-terminated) text to append, or ``""`` to drop the line."""
        return raw

    def _child_env(self, result_file: Optional[str] = None) -> Optional[dict[str, str]]:
        env = {**os.environ, **self.env_overrides}
        # The agent runs in the target (cwd=repo_root) but may execute python that
        # imports the engine (e.g. enqueue a spec-change run). The engine is not in
        # the target, so put its src/ on PYTHONPATH (this file: executing/ ->
        # specseed_runtime/ -> src/).
        engine_src = str(Path(__file__).resolve().parents[2])
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = engine_src if not existing else os.pathsep.join([engine_src, existing])
        # Where the agent must write its structured JSON result (agent_report).
        if result_file:
            env[agent_report.RESULT_FILE_ENV] = result_file
        return env

    def run(
        self,
        prompt: str,
        *,
        cwd: str | Path,
        cancel: Optional[threading.Event] = None,
        timeout_s: float = DEFAULT_AGENT_TIMEOUT_S,
        on_start: Optional[Callable[[int, str], None]] = None,
        intent: Optional[str] = None,
        live_log: Optional[str | Path] = None,
    ) -> AgentResult:
        argv = self.build_command(prompt, cwd)
        # Per-run result file the agent writes its JSON outcome to. Pre-created
        # empty so a vanished file is unambiguous; cleaned up after parsing.
        result_fd, result_path = tempfile.mkstemp(prefix="specseed-result-", suffix=".json")
        os.close(result_fd)
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
            env_overrides=sorted(self.env_overrides) or None,
        )
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # line-buffered: lines reach the live feed as they arrive
                env=self._child_env(result_path),
            )
        except (OSError, ValueError) as exc:
            _unlink(result_path)
            platform_log.log_event(
                "agent_subprocess_launch_failed",
                runner=type(self).__name__,
                cwd=str(cwd),
                error=repr(exc),
            )
            return AgentResult(ok=False, error=f"failed to launch agent: {exc}",
                               duration_s=time.monotonic() - start)

        if on_start is not None:
            try:
                on_start(proc.pid, argv[0] if argv else "")
            except Exception:  # the ledger must never break a run
                pass

        chunks: list[str] = []
        drained = threading.Event()
        # Optional live feed: humanise each stdout line and append it to a file the
        # UI tails ('Agent output' popup). Best-effort and ephemeral - a write error
        # never disturbs the run, and the raw stdout still flows into `chunks`.
        live = None
        if live_log is not None:
            try:
                Path(live_log).parent.mkdir(parents=True, exist_ok=True)
                live = open(live_log, "w", encoding="utf-8")
            except OSError:
                live = None

        def _drain() -> None:
            try:
                assert proc.stdout is not None
                # readline (not `for line in proc.stdout`) so each line lands the
                # moment the child flushes it - the file iterator's read-ahead would
                # otherwise hold the live feed back until a big buffer fills.
                for line in iter(proc.stdout.readline, ""):
                    chunks.append(line)
                    if live is not None:
                        try:
                            shown = self.format_stream_line(line)
                            if shown:
                                live.write(shown)
                                live.flush()
                        except (OSError, ValueError):
                            pass
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
        if live is not None:
            try:
                live.close()
            except OSError:
                pass
        stdout = "".join(chunks)
        duration = time.monotonic() - start

        # Read the structured result the agent wrote, then drop the temp file.
        report = None
        report_error = None
        if intent is not None:
            report, report_error = agent_report.parse_result_file(result_path, intent)
        _unlink(result_path)

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
            stdout_tail=stdout_tail(stdout) if error else None,
            has_report=report is not None,
            report_error=report_error,
        )
        return AgentResult(
            ok=ok,
            returncode=returncode,
            stdout=stdout,
            killed=killed,
            timed_out=timed_out,
            error=error,
            duration_s=duration,
            report=report,
            report_error=report_error,
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
        config_dir: Optional[str] = None,
        poll_interval: float = 0.5,
        grace: float = 10.0,
    ) -> None:
        env = _config_dir_env("claude", config_dir)
        super().__init__(poll_interval=poll_interval, grace=grace, env=env)
        self.model = model
        self.max_turns = max_turns
        self.allowed_tools = allowed_tools
        self.permission_mode = permission_mode
        self.binary = binary
        self.config_dir = config_dir

    def build_command(self, prompt: str, cwd: str | Path) -> list[str]:
        # stream-json (requires --verbose) emits one JSON event per line as the
        # agent thinks/acts, so the live feed shows the work in flight instead of
        # just a final dump. The structured result still arrives via
        # $SPECSEED_RESULT_FILE - stdout format is display-only.
        return [
            self.binary, "-p",
            "--model", self.model,
            "--permission-mode", self.permission_mode,
            "--allowedTools", self.allowed_tools,
            "--max-turns", str(self.max_turns),
            "--verbose",
            "--output-format", "stream-json",
        ]

    def format_stream_line(self, raw: str) -> str:
        return format_claude_stream_line(raw)


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
        config_dir: Optional[str] = None,
        poll_interval: float = 0.5,
        grace: float = 10.0,
    ) -> None:
        env = _config_dir_env("codex", config_dir)
        super().__init__(poll_interval=poll_interval, grace=grace, env=env)
        self.model = model
        self.effort = effort
        self.sandbox = sandbox
        self.binary = binary
        self.config_dir = config_dir

    def build_command(self, prompt: str, cwd: str | Path) -> list[str]:
        # --skip-git-repo-check: a target need not be a git repo (TrackingRemoteLocal
        # stand-in, fresh dirs). Without it codex exec refuses to start and exits 1
        # instantly with "Not inside a trusted directory". claude has no such gate.
        return [
            self.binary, "exec",
            "--model", self.model,
            "-c", f'model_reasoning_effort="{self.effort}"',
            "--sandbox", self.sandbox,
            "-c", 'approval_policy="never"',
            "--skip-git-repo-check",
            "-",
        ]


def _config_dir_env(provider: str, config_dir: Optional[str]) -> dict[str, str]:
    """``{ENV_VAR: expanded_dir}`` for a provider's config/home, or ``{}``.

    ``config_dir`` (a spec's ``provider_data_dir``) is ``~``-expanded. ``None``/blank
    means inherit the parent env (no override).
    """
    if not config_dir:
        return {}
    var = PROVIDER_CONFIG_ENV.get(provider)
    if not var:
        return {}
    return {var: str(Path(str(config_dir)).expanduser())}


def default_runner_spec() -> dict[str, Any]:
    """The single Claude spec every function defaults to."""
    return {
        "provider": "claude",
        "provider_data_dir": PROVIDER_DEFAULT_HOME["claude"],
        "model": "opus",
        "effort": "high",
    }


def default_runner_chains() -> dict[str, list[dict[str, Any]]]:
    """One default Claude spec per function."""
    return {fn: [default_runner_spec()] for fn in RUNNER_FUNCTIONS}


def runner_from_spec(spec: dict[str, Any]) -> AgentRunner:
    """Build one :class:`AgentRunner` from a ``{provider, provider_data_dir, model,
    effort}`` spec. ``provider_data_dir`` wires the CLI's config-dir env var."""
    spec = spec or {}
    provider = str(spec.get("provider") or "claude").lower()
    model = spec.get("model")
    data_dir = spec.get("provider_data_dir")
    if provider == "codex":
        kwargs: dict[str, Any] = {}
        if model:
            kwargs["model"] = model
        if spec.get("effort"):
            kwargs["effort"] = spec["effort"]
        if spec.get("sandbox"):
            kwargs["sandbox"] = spec["sandbox"]
        if data_dir:
            kwargs["config_dir"] = data_dir
        return CodexAgentRunner(**kwargs)
    if provider in ("claude", "claude-code"):
        kwargs = {}
        if model:
            kwargs["model"] = model
        if data_dir:
            kwargs["config_dir"] = data_dir
        return ClaudeAgentRunner(**kwargs)
    raise ValueError(f"unsupported runner provider: {provider!r}")


class RunnerChains:
    """A per-function ordered fallback chain of agent runners.

    ``dispatch`` asks for a function (spec/implementation/review); the chain tries
    its first spec, and on a plain failure (nonzero exit / launch error, NOT a
    cancel) falls through to the next spec. A cancel returns immediately — a
    deliberate stop is not a failure to retry.
    """

    def __init__(self, chains: dict[str, list[AgentRunner]]) -> None:
        self.chains = chains

    @classmethod
    def single(cls, runner: AgentRunner) -> "RunnerChains":
        """Wrap one runner as the whole chain for every function (test/back-compat)."""
        return cls({fn: [runner] for fn in RUNNER_FUNCTIONS})

    def chain_for(self, function: str) -> list[AgentRunner]:
        chain = self.chains.get(function)
        if chain:
            return chain
        # Fall back to implementation, then any configured chain — never empty.
        return self.chains.get("implementation") or next(
            (c for c in self.chains.values() if c), []
        )

    def run(
        self,
        prompt: str,
        *,
        function: str,
        cwd: str | Path,
        cancel: Optional[threading.Event] = None,
        timeout_s: float = DEFAULT_AGENT_TIMEOUT_S,
        on_start: Optional[Callable[[int, str], None]] = None,
        intent: Optional[str] = None,
        live_log: Optional[str | Path] = None,
    ) -> AgentResult:
        chain = self.chain_for(function)
        last: Optional[AgentResult] = None
        quota_hits: list[Any] = []
        for i, runner in enumerate(chain):
            if i:
                platform_log.log_event(
                    "agent_chain_fallback", function=function, spec_index=i
                )
            result = runner.run(
                prompt, cwd=cwd, cancel=cancel, timeout_s=timeout_s,
                on_start=on_start, intent=intent, live_log=live_log,
            )
            last = result
            if getattr(result, "killed", False):
                return result  # deliberate stop — do not try fallbacks
            if getattr(result, "ok", False):
                return result
            # A failure: was it a provider quota? Sniff error + stdout. We try the
            # WHOLE chain first (a fallback may be a different provider/account);
            # only if every spec is quota-blocked do we open the circuit.
            sig = quota_mod.quota_signal(
                "{0}\n{1}".format(getattr(result, "error", "") or "", getattr(result, "stdout", "") or "")
            )
            quota_hits.append(sig)
        if last is None:
            return AgentResult(ok=False, error=f"no runner configured for {function!r}")
        if quota_hits and all(h is not None for h in quota_hits):
            longest = max(quota_hits, key=lambda h: h.park_seconds)
            last.quota_exhausted = True
            last.quota_reset_hint = (
                longest.reset_at.isoformat() if longest.reset_at is not None else None
            )
            platform_log.log_event(
                "agent_chain_quota_exhausted",
                function=function,
                specs=len(chain),
                park_seconds=longest.park_seconds,
                reset_hint=last.quota_reset_hint,
            )
            return last
        platform_log.log_event(
            "agent_chain_exhausted", function=function, specs=len(chain)
        )
        return last


def build_runner_chains(config: Optional[dict[str, Any]] = None) -> RunnerChains:
    """Build a :class:`RunnerChains` from ``config["runner"]``.

    ``runner`` maps each function to an ordered list of specs. A missing/empty
    function rides the configured ``implementation`` chain (a target that set up
    codex must not get a surprise default-claude run for a newly added
    function); only a config with no usable chains at all falls back to the
    default Claude spec.
    """
    runner_cfg = (config or {}).get("runner") or {}
    chains: dict[str, list[AgentRunner]] = {}
    for fn in RUNNER_FUNCTIONS:
        specs = runner_cfg.get(fn)
        if not isinstance(specs, list) or not specs:
            specs = runner_cfg.get("implementation")
        if not isinstance(specs, list) or not specs:
            specs = [default_runner_spec()]
        chains[fn] = [runner_from_spec(s) for s in specs]
    return RunnerChains(chains)


def build_runner(config: Optional[dict[str, Any]] = None) -> AgentRunner:
    """Back-compat: the primary implementation runner (first spec of the chain)."""
    return build_runner_chains(config).chain_for("implementation")[0]


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
        on_start: Optional[Callable[[int, str], None]] = None,
        intent: Optional[str] = None,
        live_log: Optional[str | Path] = None,
    ) -> AgentResult:
        call = {"prompt": prompt, "cwd": str(cwd), "timeout_s": timeout_s, "cancel": cancel,
                "on_start": on_start, "intent": intent, "live_log": live_log}
        self.calls.append(call)
        if self.side_effect is not None:
            return self.side_effect(call)
        return self.result
