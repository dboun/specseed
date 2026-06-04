"""permissions.py - programmatic permission gating.

Every action the system can take against git or the remote is gated by an explicit
switch the human set in ``configuration.json`` (see ``configuring/configure.py``).
Permissions are judged HERE, in code, never by an agent.

Shape of ``configuration.json`` permissions::

    "permissions": {
        "git":      {"enabled": true, "merge_to_dev_branch": false},
        "remote":   {"post_control": false, "push_branches": false,
                     "push_dev_branch": false, "make_prs": false},
        "platform": {"auto_implement_issue": true,
                     "auto_proceed_to_next_sprint_if_available": false},
        "agents":   {"<action-class>": "block"|"surface"|"auto"|"require_human_approval", ...},
    }

The local-vs-remote choice (``enabled`` + ``provider``) lives in ``remote.json``, not
here, so it is passed in as ``remote_state``. When the remote is disabled the system
runs against the networkless ``TrackingRemoteLocal`` stand-in: "remote" writes have no
external effect, so the per-switch gates are treated as allowed. The switches only bite
once a real provider (github/gitlab) is wired up.

Posting issues/tickets/epics is NOT a switch: the work tracker lives on the remote and
there is no opt-out, so issue posts (and the spec-change ``apply.py`` that mutates them)
are always permitted.

Only Python stdlib is used.
"""

from __future__ import annotations

from typing import Any

from specseed_target_src.state_machines.base import approver_usernames


# Action-class gate taxonomy honoured by the implementation agent. Each class maps to a
# level the agent obeys before taking such an action. Ported from old_specseed.
AGENT_CATEGORIES: dict[str, str] = {
    "container":        "docker/podman build, run, push, pull",
    "heavy_compute":    "GPU / training / running experiment scripts / long jobs",
    "network":          "outbound non-localhost calls (downloads, external APIs)",
    "deps":             "add/remove a dependency, or major-version bump",
    "data_destructive": "delete data, drop/rewrite schema, destructive migration",
    "external_publish": "deploy, submission, upload — anything leaving the repo",
    "outside_repo":     "writes outside the repo root",
    "secrets":          "reading/writing credentials or secret material",
}
AGENT_LEVELS = ("block", "surface", "auto", "require_human_approval")
DEFAULT_AGENT_GATES: dict[str, str] = {
    "container":        "block",
    "heavy_compute":    "block",
    "network":          "surface",
    "deps":             "block",
    "data_destructive": "block",
    "external_publish": "block",
    "outside_repo":     "block",
    "secrets":          "surface",
}


class Permissions:
    """Read-only view over ``configuration.json`` permission switches."""

    def __init__(
        self,
        config: dict[str, Any] | None,
        remote_state: dict[str, Any] | None = None,
    ) -> None:
        self._config: dict[str, Any] = config or {}
        self._remote_state: dict[str, Any] = remote_state or {}
        self._perms: dict[str, Any] = self._config.get("permissions") or {}
        self._git: dict[str, Any] = self._perms.get("git") or {}
        self._remote: dict[str, Any] = self._perms.get("remote") or {}
        self._platform: dict[str, Any] = self._perms.get("platform") or {}
        self._agents: dict[str, Any] = self._perms.get("agents") or {}

    # -- backend (from remote.json) -------------------------------------- #
    def remote_enabled(self) -> bool:
        """Whether a real remote provider (github/gitlab) is configured."""
        return bool(self._remote_state.get("enabled"))

    # -- git ------------------------------------------------------------- #
    def git_enabled(self) -> bool:
        # Default True: local git is on unless explicitly disabled.
        return bool(self._git.get("enabled", True))

    def can_merge_to_dev_branch(self) -> bool:
        return self.git_enabled() and bool(self._git.get("merge_to_dev_branch"))

    # -- remote ---------------------------------------------------------- #
    def _remote_switch(self, key: str) -> bool:
        # Local stand-in: no external effect, so remote writes are allowed.
        if not self.remote_enabled():
            return True
        return bool(self._remote.get(key))

    def can_post_issues(self) -> bool:
        # Issues/tickets/epics live on the remote; there is no opt-out.
        return True

    def can_post_control(self) -> bool:
        return self._remote_switch("post_control")

    def can_push_branches(self) -> bool:
        return self._remote_switch("push_branches")

    def can_push_dev_branch(self) -> bool:
        return self._remote_switch("push_dev_branch")

    def can_make_prs(self) -> bool:
        return self._remote_switch("make_prs")

    # -- derived gates --------------------------------------------------- #
    def can_run_spec_change(self) -> bool:
        """A spec-change ``apply.py`` mutates remote posts. Always permitted: the
        work tracker lives on the remote and there is no opt-out."""
        return True

    # -- platform autos -------------------------------------------------- #
    def auto_implement_issue(self) -> bool:
        """Whether a ready issue may be implemented without per-issue approval.

        Default True. When False, an issue parks ``awaiting_approval`` until an
        approver signs off, then the implementation agent runs.
        """
        return bool(self._platform.get("auto_implement_issue", True))

    def auto_proceed_to_next_sprint(self) -> bool:
        """Whether the runner may roll into the next sprint without approval."""
        return bool(self._platform.get("auto_proceed_to_next_sprint_if_available", False))

    # -- agents action-class gates -------------------------------------- #
    def agent_gate(self, category: str) -> str:
        """Level (block/surface/auto/require_human_approval) for an action class."""
        level = self._agents.get(category)
        if level in AGENT_LEVELS:
            return str(level)
        return DEFAULT_AGENT_GATES.get(category, "block")

    def agents_policy(self) -> dict[str, str]:
        """Resolved level for every known action class (config over defaults)."""
        return {cat: self.agent_gate(cat) for cat in AGENT_CATEGORIES}

    # -- approvals / generic -------------------------------------------- #
    def approver_usernames(self) -> set[str]:
        return approver_usernames(self._config)

    def allowed(self, dotted_key: str) -> bool:
        """Generic dotted/slash lookup under config and config.permissions."""
        parts = [p for p in dotted_key.replace("/", ".").split(".") if p]
        if not parts:
            return False
        for root in (self._config, self._perms):
            value: Any = root
            ok = True
            for part in parts:
                if not isinstance(value, dict):
                    ok = False
                    break
                value = value.get(part)
            if ok:
                return bool(value)
        return False
