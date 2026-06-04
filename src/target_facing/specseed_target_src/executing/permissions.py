"""permissions.py - programmatic permission gating.

Every action the system can take against git or the remote is gated by an explicit
switch the human set in ``configuration.json`` (see ``configuring/configure.py``).
Permissions are judged HERE, in code, never by an agent.

Shape of ``configuration.json`` permissions::

    "backend": {"enabled": bool, "provider": "github"|"gitlab"|null},
    "permissions": {
        "git":    {"enabled": true, "merge_to_dev": false, "merge_to_main": false},
        "remote": {"post_issues": false, "post_dashboards": false, "post_control": false,
                   "push_branches": false, "push_main": false, "make_prs": false},
    }

When ``backend.enabled`` is false the system runs against the networkless
``TrackingRemoteLocal`` stand-in: "remote" writes have no external effect, so they
are treated as allowed. The per-switch gates only bite once a real provider
(github/gitlab) is wired up.

Only Python stdlib is used.
"""

from __future__ import annotations

from typing import Any

from specseed_target_src.state_machines.base import approver_usernames


class Permissions:
    """Read-only view over ``configuration.json`` permission switches."""

    def __init__(self, config: dict[str, Any] | None) -> None:
        self._config: dict[str, Any] = config or {}
        self._perms: dict[str, Any] = self._config.get("permissions") or {}
        self._git: dict[str, Any] = self._perms.get("git") or {}
        self._remote: dict[str, Any] = self._perms.get("remote") or {}

    # -- backend --------------------------------------------------------- #
    def remote_enabled(self) -> bool:
        """Whether a real remote provider (github/gitlab) is configured."""
        backend = self._config.get("backend") or {}
        return bool(backend.get("enabled"))

    # -- git ------------------------------------------------------------- #
    def git_enabled(self) -> bool:
        # Default True: local git is on unless explicitly disabled.
        return bool(self._git.get("enabled", True))

    def can_merge_to_dev(self) -> bool:
        return self.git_enabled() and bool(self._git.get("merge_to_dev"))

    def can_merge_to_main(self) -> bool:
        return self.git_enabled() and bool(self._git.get("merge_to_main"))

    # -- remote ---------------------------------------------------------- #
    def _remote_switch(self, key: str) -> bool:
        # Local stand-in: no external effect, so remote writes are allowed.
        if not self.remote_enabled():
            return True
        return bool(self._remote.get(key))

    def can_post_issues(self) -> bool:
        return self._remote_switch("post_issues")

    def can_post_dashboards(self) -> bool:
        # Dashboards need the issue channel too.
        return self.can_post_issues() and self._remote_switch("post_dashboards")

    def can_post_control(self) -> bool:
        return self.can_post_issues() and self._remote_switch("post_control")

    def can_push_branches(self) -> bool:
        return self._remote_switch("push_branches")

    def can_push_main(self) -> bool:
        return self._remote_switch("push_main")

    def can_make_prs(self) -> bool:
        return self._remote_switch("make_prs")

    # -- derived gates --------------------------------------------------- #
    def can_run_spec_change(self) -> bool:
        """A spec-change ``apply.py`` mutates remote posts; gate it on post_issues.

        With the local stand-in (backend disabled) this is always allowed.
        """
        return self._remote_switch("post_issues")

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
