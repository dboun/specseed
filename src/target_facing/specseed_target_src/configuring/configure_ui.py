#!/usr/bin/env python3.13
"""
configure_ui.py — Tkinter setup UI for specseed, run INSIDE a target repo.

Run from the repository root:

    python3.13 src/target_facing/specseed_target_src/configuring/configure_ui.py

Optional:

    python3.13 src/target_facing/specseed_target_src/configuring/configure_ui.py --storage PATH

The Save button stays fixed at the top. The options form scrolls below it.
Choices hide or reveal dependent options, and saving reuses configure.py's
storage, gitignore, config, remote, and token writing helpers.
"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


def _add_repo_root_to_path() -> None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "target_facing").exists():
            sys.path.insert(0, str(parent / "src" / "target_facing"))
            return
        if (parent / "specseed_target_src").exists():
            sys.path.insert(0, str(parent))
            return


_add_repo_root_to_path()

from specseed_target_src.configuring import configure


class ConfigureUI(tk.Tk):
    def __init__(self, storage: Path, explicit_storage: bool = False) -> None:
        super().__init__()
        self.initial_storage = Path(storage)
        self.explicit_storage = explicit_storage
        self.repo_root = configure.repo_root_from_cwd()
        self.cfg = configure.load_config(self.initial_storage)
        self.remote = configure.load_remote_state(self.initial_storage)

        self.title("Specseed Configure")
        self.geometry("920x760")
        self.minsize(760, 560)

        self._build_vars()
        self._configure_style()
        self._build()
        self._sync_visibility()
        self._update_storage_preview()

    def _build_vars(self) -> None:
        runner = self.cfg.get("runner") or {}
        approvals = self.cfg["approvals"]
        permissions = self.cfg["permissions"]
        git = permissions["git"]
        remote_perms = permissions["remote"]
        platform = permissions.get("platform") or {}
        agents = permissions.get("agents") or {}

        self.specseed_dir = tk.StringVar(value=self.cfg.get("specseed_dir") or configure.DEFAULT_SPECSEED_DIR)
        self.dev_branch = tk.StringVar(value=self._dev_branch_default())
        self.ignore_specseed = tk.BooleanVar(value=True)
        self.local_only = tk.BooleanVar(value=not self.remote.get("enabled"))
        self.provider = tk.StringVar(value=self.remote.get("provider") or "github")
        self.repo = tk.StringVar(value=self.remote.get("repo") or "")
        self.token = tk.StringVar(value="")
        self.git_enabled = tk.BooleanVar(value=git.get("enabled", True))
        self.merge_to_dev_branch = tk.BooleanVar(value=git.get("merge_to_dev_branch", False))
        self.post_control = tk.BooleanVar(value=remote_perms.get("post_control", False))
        self.push_branches = tk.BooleanVar(value=remote_perms.get("push_branches", False))
        self.push_dev_branch = tk.BooleanVar(value=remote_perms.get("push_dev_branch", False))
        self.make_prs = tk.BooleanVar(value=remote_perms.get("make_prs", False))
        self.auto_implement_issue = tk.BooleanVar(value=platform.get("auto_implement_issue", True))
        self.auto_next_sprint = tk.BooleanVar(
            value=platform.get("auto_proceed_to_next_sprint_if_available", False)
        )
        self.agent_gates = {
            cat: tk.StringVar(value=agents.get(cat) or configure.DEFAULT_AGENT_GATES.get(cat, "block"))
            for cat in configure.AGENT_CATEGORIES
        }
        self.approvers = tk.StringVar(value=", ".join(approvals.get("approver_usernames", [])))
        # Per-function fallback chains: function -> list of spec-var dicts.
        self.runner_chains = self._runner_chains_from_cfg(runner)
        self.poll_interval = tk.StringVar(
            value=str(self.cfg.get("poll_interval_seconds", configure.DEFAULT_POLL_INTERVAL))
        )
        self.storage_preview = tk.StringVar(value="")
        self.status = tk.StringVar(value="")

        for variable in (
            self.specseed_dir,
            self.local_only,
            self.provider,
            self.git_enabled,
        ):
            variable.trace_add("write", lambda *_args: self._sync_visibility())
        self.specseed_dir.trace_add("write", lambda *_args: self._update_storage_preview())

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f6f8fa")
        style.configure("Toolbar.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background="#ffffff", foreground="#24292f")
        style.configure("TLabel", background="#f6f8fa", foreground="#24292f")
        style.configure("Card.TLabel", background="#ffffff", foreground="#24292f")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#57606a")
        style.configure("Danger.TLabel", background="#ffffff", foreground="#cf222e")
        style.configure("TCheckbutton", background="#ffffff")
        style.configure("TRadiobutton", background="#ffffff")
        style.configure("TButton", padding=(12, 6))

    def _build(self) -> None:
        toolbar = ttk.Frame(self, style="Toolbar.TFrame", padding=(12, 10))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Save", command=self.save).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Reload", command=self.reload).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(toolbar, textvariable=self.status, foreground="#57606a", background="#ffffff").pack(
            side=tk.LEFT, padx=(14, 0)
        )
        ttk.Label(toolbar, textvariable=self.storage_preview, foreground="#57606a", background="#ffffff").pack(
            side=tk.RIGHT
        )

        body_outer = ttk.Frame(self)
        body_outer.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(body_outer, borderwidth=0, highlightthickness=0, background="#f6f8fa")
        scrollbar = ttk.Scrollbar(body_outer, orient=tk.VERTICAL, command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, padding=14)
        self.body_id = self.canvas.create_window((0, 0), window=self.body, anchor=tk.NW)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)

        self._build_repo_section()
        self._build_backend_section()
        self._build_git_section()
        self._build_remote_permissions_section()
        self._build_platform_section()
        self._build_agents_section()
        self._build_approvals_section()
        self._build_runner_section()

    def _build_repo_section(self) -> None:
        frame = self._card("Repository")
        self._row(frame, 0, "Target repo", ttk.Label(frame, text=str(self.repo_root), style="Muted.TLabel"))
        self._row(frame, 1, "Specseed dir", ttk.Entry(frame, textvariable=self.specseed_dir, width=44))
        self._row(frame, 2, "Dev branch", ttk.Entry(frame, textvariable=self.dev_branch, width=44))
        self._row(
            frame,
            3,
            "",
            ttk.Checkbutton(
                frame,
                text="Append the specseed directory to the repo .gitignore on save",
                variable=self.ignore_specseed,
            ),
        )

    def _build_backend_section(self) -> None:
        self.backend_frame = self._card("Backend")
        ttk.Checkbutton(
            self.backend_frame,
            text="Track work locally only, without GitHub/GitLab",
            variable=self.local_only,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(8, 4))

        self.remote_backend_frame = ttk.Frame(self.backend_frame, style="TFrame")
        self.remote_backend_frame.grid(row=1, column=0, columnspan=2, sticky=tk.EW, padx=0, pady=0)
        self.remote_backend_frame.columnconfigure(1, weight=1)
        provider_box = ttk.Frame(self.remote_backend_frame)
        ttk.Radiobutton(provider_box, text="GitHub", variable=self.provider, value="github").pack(side=tk.LEFT)
        ttk.Radiobutton(provider_box, text="GitLab", variable=self.provider, value="gitlab").pack(
            side=tk.LEFT, padx=(12, 0)
        )
        self._row(self.remote_backend_frame, 0, "Provider", provider_box)
        self._row(
            self.remote_backend_frame,
            1,
            "Repo",
            ttk.Entry(self.remote_backend_frame, textvariable=self.repo, width=56),
        )
        self._row(
            self.remote_backend_frame,
            2,
            "Access token",
            ttk.Entry(self.remote_backend_frame, textvariable=self.token, show="*", width=56),
        )
        self.token_help = ttk.Label(
            self.remote_backend_frame,
            text="",
            style="Muted.TLabel",
            justify=tk.LEFT,
            wraplength=620,
        )
        self.token_help.grid(row=3, column=1, sticky=tk.EW, padx=10, pady=(0, 10))

    def _build_git_section(self) -> None:
        self.git_frame = self._card("Local Git")
        ttk.Checkbutton(
            self.git_frame,
            text="Let the agent use local git",
            variable=self.git_enabled,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(8, 4))
        self.git_merge_frame = ttk.Frame(self.git_frame)
        self.git_merge_frame.grid(row=1, column=0, columnspan=2, sticky=tk.EW, padx=10, pady=(0, 8))
        ttk.Label(
            self.git_merge_frame,
            text="Creating local branches is always allowed once git is on.",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(0, 5))
        ttk.Checkbutton(
            self.git_merge_frame,
            text="Allow merging into the dev branch",
            variable=self.merge_to_dev_branch,
        ).pack(anchor=tk.W)

    def _build_remote_permissions_section(self) -> None:
        self.remote_permissions_frame = self._card("Remote Actions")
        ttk.Label(
            self.remote_permissions_frame,
            text="Posting issues/tickets/epics is always allowed — the tracker lives on the remote.",
            style="Muted.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(8, 4))
        ttk.Checkbutton(
            self.remote_permissions_frame,
            text="Post the CONTROL channel",
            variable=self.post_control,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=10, pady=2)
        ttk.Checkbutton(
            self.remote_permissions_frame,
            text="Push branches to the remote",
            variable=self.push_branches,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=10, pady=2)
        ttk.Checkbutton(
            self.remote_permissions_frame,
            text="Push to the dev branch on the remote",
            variable=self.push_dev_branch,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, padx=10, pady=2)
        ttk.Checkbutton(
            self.remote_permissions_frame,
            text="Open pull/merge requests",
            variable=self.make_prs,
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(2, 10))

    def _build_platform_section(self) -> None:
        frame = self._card("Platform Autos")
        self.platform_frame = frame
        ttk.Checkbutton(
            frame,
            text="Auto-implement ready issues (off = each needs human approval first)",
            variable=self.auto_implement_issue,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(8, 2))
        ttk.Checkbutton(
            frame,
            text="Auto-proceed to the next sprint when available (off = human approves)",
            variable=self.auto_next_sprint,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(2, 10))

    def _build_agents_section(self) -> None:
        frame = self._card("Agent Action Gates")
        ttk.Label(
            frame,
            text=("Level for each action class the implementation agent may hit mid-work: "
                  "block = never · surface = do + announce · auto = do silently · "
                  "require_human_approval = only after a human approves."),
            style="Muted.TLabel",
            wraplength=620,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(8, 6))
        for i, (cat, desc) in enumerate(configure.AGENT_CATEGORIES.items(), start=1):
            self._row(
                frame,
                i,
                cat,
                ttk.Combobox(
                    frame,
                    textvariable=self.agent_gates[cat],
                    values=list(configure.AGENT_LEVELS),
                    state="readonly",
                    width=24,
                ),
            )

    def _build_approvals_section(self) -> None:
        frame = self._card("Approvals")
        self.approvals_frame = frame
        self._row(
            frame,
            0,
            "Approvers",
            ttk.Entry(frame, textvariable=self.approvers, width=56),
        )
        ttk.Label(
            frame,
            text="Comma or space separated usernames allowed to approve HITL gates remotely.",
            style="Muted.TLabel",
            wraplength=620,
        ).grid(row=1, column=1, sticky=tk.EW, padx=10, pady=(0, 10))

    def _build_runner_section(self) -> None:
        frame = self._card("Runner")
        ttk.Label(
            frame,
            text=(
                "Each function runs an ordered fallback chain: the first agent is primary, "
                "the rest are tried on failure. merge_conflicts is surfaced for later use."
            ),
            style="Muted.TLabel",
            wraplength=620,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.EW, padx=10, pady=(4, 8))
        # One container per function; rows are (re)drawn dynamically by _draw_chain.
        self.runner_function_frames = {}
        row = 1
        for fn in configure.RUNNER_FUNCTIONS:
            fn_frame = ttk.LabelFrame(frame, text=fn, padding=(6, 4))
            fn_frame.grid(row=row, column=0, columnspan=2, sticky=tk.EW, padx=10, pady=6)
            fn_frame.columnconfigure(0, weight=1)
            self.runner_function_frames[fn] = fn_frame
            self._draw_chain(fn)
            row += 1
        self._row(
            frame,
            row,
            "Poll interval",
            ttk.Entry(frame, textvariable=self.poll_interval, width=14),
            suffix="seconds",
        )

    def _draw_chain(self, fn: str) -> None:
        """(Re)draw every spec row for one function plus its Add button."""
        container = self.runner_function_frames[fn]
        for child in container.winfo_children():
            child.destroy()
        specs = self.runner_chains[fn]
        for i, v in enumerate(specs):
            self._draw_spec_row(container, fn, i, v, removable=len(specs) > 1)
        ttk.Button(
            container,
            text="+ Add fallback",
            command=lambda f=fn: self._add_runner_spec(f),
        ).grid(row=len(specs), column=0, sticky=tk.W, padx=4, pady=(4, 2))

    def _draw_spec_row(self, container, fn: str, i: int, v: dict, *, removable: bool) -> None:
        row = ttk.Frame(container)
        row.grid(row=i, column=0, sticky=tk.EW, pady=2)
        label = "primary" if i == 0 else f"fallback #{i}"
        ttk.Label(row, text=label, width=11).pack(side=tk.LEFT)
        ttk.Combobox(
            row, textvariable=v["provider"], values=list(configure.RUNNER_PROVIDERS),
            state="readonly", width=8,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Entry(row, textvariable=v["model"], width=16).pack(side=tk.LEFT, padx=2)
        ttk.Combobox(
            row, textvariable=v["effort"], values=["low", "medium", "high"],
            state="readonly", width=8,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Entry(row, textvariable=v["data_dir"], width=18).pack(side=tk.LEFT, padx=2)
        if removable:
            ttk.Button(
                row, text="✕", width=3,
                command=lambda f=fn, idx=i: self._remove_runner_spec(f, idx),
            ).pack(side=tk.LEFT, padx=2)

    def _add_runner_spec(self, fn: str) -> None:
        self.runner_chains[fn].append(self._spec_vars(None))
        self._draw_chain(fn)

    def _remove_runner_spec(self, fn: str, idx: int) -> None:
        if len(self.runner_chains[fn]) > 1:
            del self.runner_chains[fn][idx]
            self._draw_chain(fn)

    def _card(self, title: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(self.body, text=title, style="Card.TLabelframe", padding=(0, 4))
        frame.pack(fill=tk.X, pady=(0, 12))
        frame.columnconfigure(1, weight=1)
        return frame

    def _row(
        self,
        frame: ttk.Frame,
        row: int,
        label: str,
        widget: tk.Widget,
        suffix: str | None = None,
    ) -> None:
        ttk.Label(frame, text=label, style="Card.TLabel").grid(
            row=row, column=0, sticky=tk.W, padx=10, pady=8
        )
        widget.grid(row=row, column=1, sticky=tk.EW, padx=10, pady=8)
        if suffix:
            ttk.Label(frame, text=suffix, style="Card.TLabel").grid(
                row=row, column=2, sticky=tk.W, padx=(0, 10), pady=8
            )

    def _sync_visibility(self) -> None:
        if self.local_only.get():
            self.remote_backend_frame.grid_remove()
            self.remote_permissions_frame.pack_forget()
        else:
            self.remote_backend_frame.grid()
            if not self.remote_permissions_frame.winfo_ismapped():
                self.remote_permissions_frame.pack(fill=tk.X, pady=(0, 12), before=self.platform_frame)

        if self.git_enabled.get():
            self.git_merge_frame.grid()
        else:
            self.git_merge_frame.grid_remove()

        if self.provider.get() == "github":
            self.token_help.configure(
                text=(
                    "GitHub token: fine-grained PAT for this repo with Metadata read, "
                    "Issues read/write, Pull requests read/write, Contents read/write."
                )
            )
        else:
            self.token_help.configure(
                text=(
                    "GitLab token: personal/project access token with api scope and Developer role. "
                    "Use Maintainer or relax branch protection if remote main pushes are allowed."
                )
            )

    def _on_body_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.body_id, width=event.width)

    def _on_mousewheel(self, event: tk.Event) -> None:
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _save_storage(self) -> Path:
        if self.explicit_storage:
            return self.initial_storage
        specseed_rel = self._specseed_rel()
        return configure.storage_for_specseed_dir(self.repo_root / specseed_rel)

    def _specseed_rel(self) -> Path:
        raw = self.specseed_dir.get().strip() or configure.DEFAULT_SPECSEED_DIR
        path = Path(raw)
        if path.is_absolute():
            try:
                path = path.resolve().relative_to(self.repo_root)
            except ValueError as exc:
                raise ValueError("Specseed directory must be inside the target repo.") from exc
        value = path.as_posix().strip("/")
        return Path(value or configure.DEFAULT_SPECSEED_DIR)

    def _update_storage_preview(self) -> None:
        try:
            storage = self._save_storage()
        except ValueError:
            self.storage_preview.set("storage: invalid specseed path")
            return
        self.storage_preview.set(f"storage: {storage}")

    def _dev_branch_default(self) -> str:
        # Stored value wins; absent/plain-default falls back to git autodetection.
        stored = self.cfg.get("dev_branch")
        if stored and stored != configure.DEFAULT_DEV_BRANCH:
            return stored
        return configure.detect_default_branch(self.repo_root)

    def _spec_vars(self, spec: dict | None) -> dict:
        """tk vars for one agent spec, defaulting from ``spec``."""
        spec = spec or configure.default_runner_spec()
        provider = spec.get("provider") or "claude"
        return {
            "provider": tk.StringVar(value=provider),
            "model": tk.StringVar(value=spec.get("model") or "opus"),
            "effort": tk.StringVar(value=spec.get("effort") or "high"),
            "data_dir": tk.StringVar(
                value=spec.get("provider_data_dir")
                or configure.PROVIDER_DEFAULT_HOME.get(provider, "~/.claude")
            ),
        }

    def _spec_from_vars(self, v: dict) -> dict:
        provider = v["provider"].get().strip().lower() or "claude"
        return {
            "provider": provider,
            "provider_data_dir": v["data_dir"].get().strip()
            or configure.PROVIDER_DEFAULT_HOME.get(provider, "~/.claude"),
            "model": v["model"].get().strip() or "opus",
            "effort": v["effort"].get().strip().lower() or "high",
        }

    def _runner_chains_from_cfg(self, runner: dict | None) -> dict:
        coerced = configure._coerce_runner(runner) or configure.default_runner_chains()
        chains = {}
        for fn in configure.RUNNER_FUNCTIONS:
            specs = coerced.get(fn) or [configure.default_runner_spec()]
            chains[fn] = [self._spec_vars(s) for s in specs]
        return chains

    def reload(self) -> None:
        storage = self._save_storage()
        self.cfg = configure.load_config(storage)
        self.remote = configure.load_remote_state(storage)
        self.initial_storage = storage
        self._load_values_into_vars()
        self._sync_visibility()
        self._update_storage_preview()
        messagebox.showinfo("Reload", f"Reloaded values from {storage}")

    def _load_values_into_vars(self) -> None:
        runner = self.cfg.get("runner") or {}
        approvals = self.cfg["approvals"]
        permissions = self.cfg["permissions"]
        git = permissions["git"]
        remote_perms = permissions["remote"]
        platform = permissions.get("platform") or {}
        agents = permissions.get("agents") or {}
        self.specseed_dir.set(self.cfg.get("specseed_dir") or configure.DEFAULT_SPECSEED_DIR)
        self.dev_branch.set(self._dev_branch_default())
        self.local_only.set(not self.remote.get("enabled"))
        self.provider.set(self.remote.get("provider") or "github")
        self.repo.set(self.remote.get("repo") or "")
        self.token.set("")
        self.git_enabled.set(git.get("enabled", True))
        self.merge_to_dev_branch.set(git.get("merge_to_dev_branch", False))
        self.post_control.set(remote_perms.get("post_control", False))
        self.push_branches.set(remote_perms.get("push_branches", False))
        self.push_dev_branch.set(remote_perms.get("push_dev_branch", False))
        self.make_prs.set(remote_perms.get("make_prs", False))
        self.auto_implement_issue.set(platform.get("auto_implement_issue", True))
        self.auto_next_sprint.set(platform.get("auto_proceed_to_next_sprint_if_available", False))
        for cat, var in self.agent_gates.items():
            var.set(agents.get(cat) or configure.DEFAULT_AGENT_GATES.get(cat, "block"))
        self.approvers.set(", ".join(approvals.get("approver_usernames", [])))
        self.runner_chains = self._runner_chains_from_cfg(runner)
        if hasattr(self, "runner_function_frames"):
            for fn in configure.RUNNER_FUNCTIONS:
                self._draw_chain(fn)
        self.poll_interval.set(
            str(self.cfg.get("poll_interval_seconds", configure.DEFAULT_POLL_INTERVAL))
        )

    def save(self) -> None:
        try:
            storage = self._save_storage()
            specseed_rel = self._specseed_rel()
            poll_interval = int(self.poll_interval.get().strip())
            if poll_interval <= 0:
                raise ValueError("Poll interval must be greater than zero.")
            runner_chains = {}
            for fn in configure.RUNNER_FUNCTIONS:
                specs = [self._spec_from_vars(v) for v in self.runner_chains[fn]]
                for s in specs:
                    if s["provider"] not in configure.RUNNER_PROVIDERS:
                        raise ValueError(f"{fn}: provider must be claude or codex.")
                    if s["effort"] not in ("low", "medium", "high"):
                        raise ValueError(f"{fn}: reasoning effort must be low, medium, or high.")
                runner_chains[fn] = specs or [configure.default_runner_spec()]
        except ValueError as exc:
            messagebox.showerror("Cannot save", str(exc))
            return

        cfg = configure.default_config()
        cfg["specseed_dir"] = specseed_rel.as_posix()
        cfg["dev_branch"] = self.dev_branch.get().strip() or configure.DEFAULT_DEV_BRANCH
        cfg["poll_interval_seconds"] = poll_interval
        cfg["runner"] = runner_chains
        cfg["approvals"]["approver_usernames"] = self._split_names(self.approvers.get())
        local_only = self.local_only.get()
        git_on = self.git_enabled.get()
        cfg["permissions"]["git"]["enabled"] = git_on
        cfg["permissions"]["git"]["merge_to_dev_branch"] = self.merge_to_dev_branch.get() if git_on else False
        cfg["permissions"]["remote"]["post_control"] = self.post_control.get() if not local_only else False
        cfg["permissions"]["remote"]["push_branches"] = self.push_branches.get() if not local_only else False
        cfg["permissions"]["remote"]["push_dev_branch"] = self.push_dev_branch.get() if not local_only else False
        cfg["permissions"]["remote"]["make_prs"] = self.make_prs.get() if not local_only else False
        cfg["permissions"]["platform"]["auto_implement_issue"] = self.auto_implement_issue.get()
        cfg["permissions"]["platform"]["auto_proceed_to_next_sprint_if_available"] = self.auto_next_sprint.get()
        for cat, var in self.agent_gates.items():
            level = var.get().strip() or configure.DEFAULT_AGENT_GATES.get(cat, "block")
            cfg["permissions"]["agents"][cat] = level

        remote = configure.load_remote_state(storage)
        remote["enabled"] = not local_only
        remote["provider"] = None if local_only else self.provider.get()
        remote["repo"] = None if local_only else (self.repo.get().strip() or None)
        token = self.token.get().strip() or configure.load_token(storage)

        try:
            written = configure.write_config_files(
                storage,
                cfg,
                remote,
                token=token,
                repo_root=self.repo_root,
                specseed_rel=specseed_rel,
                ignore_specseed=self.ignore_specseed.get(),
            )
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))
            return

        self.initial_storage = storage
        self.cfg = cfg
        self.remote = remote
        self.token.set("")
        self._update_storage_preview()
        summary = "\n".join(str(path) for path in written.values())
        self.status.set("Saved.")
        messagebox.showinfo("Saved", f"Wrote:\n{summary}")

    @staticmethod
    def _split_names(value: str) -> list[str]:
        return [part.strip() for part in value.replace(",", " ").split() if part.strip()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tkinter setup UI for specseed.")
    parser.add_argument("--storage", default=None, help="override the storage dir")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    storage = Path(args.storage) if args.storage else configure.default_storage_dir()
    app = ConfigureUI(storage=storage, explicit_storage=args.storage is not None)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
