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
            sys.path.insert(0, str(parent))
            return


_add_repo_root_to_path()

from src.target_facing.specseed_target_src.configuring import configure


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
        backend = self.cfg["backend"]
        runner = self.cfg.get("runner") or {}
        approvals = self.cfg["approvals"]
        permissions = self.cfg["permissions"]
        git = permissions["git"]
        remote_perms = permissions["remote"]

        self.specseed_dir = tk.StringVar(value=self.cfg.get("specseed_dir") or configure.DEFAULT_SPECSEED_DIR)
        self.ignore_specseed = tk.BooleanVar(value=True)
        self.local_only = tk.BooleanVar(value=not backend.get("enabled"))
        self.provider = tk.StringVar(value=backend.get("provider") or self.remote.get("provider") or "github")
        self.repo = tk.StringVar(value=self.remote.get("repo") or "")
        self.token = tk.StringVar(value="")
        self.git_enabled = tk.BooleanVar(value=git.get("enabled", True))
        self.merge_to_dev = tk.BooleanVar(value=git.get("merge_to_dev", False))
        self.merge_to_main = tk.BooleanVar(value=git.get("merge_to_main", False))
        self.post_issues = tk.BooleanVar(value=remote_perms.get("post_issues", False))
        self.post_dashboards = tk.BooleanVar(value=remote_perms.get("post_dashboards", False))
        self.post_control = tk.BooleanVar(value=remote_perms.get("post_control", False))
        self.push_branches = tk.BooleanVar(value=remote_perms.get("push_branches", False))
        self.push_main = tk.BooleanVar(value=remote_perms.get("push_main", False))
        self.make_prs = tk.BooleanVar(value=remote_perms.get("make_prs", False))
        self.approvers = tk.StringVar(value=", ".join(approvals.get("approver_usernames", [])))
        self.runner_provider = tk.StringVar(value=runner.get("provider") or "claude")
        self.runner_model = tk.StringVar(value=runner.get("model") or self._default_runner_model())
        self.runner_effort = tk.StringVar(value=runner.get("effort") or "medium")
        self.runner_model_was_default = self.runner_model.get() == self._default_runner_model()
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
            self.post_issues,
            self.runner_provider,
        ):
            variable.trace_add("write", lambda *_args: self._sync_visibility())
        self.runner_provider.trace_add("write", lambda *_args: self._on_runner_provider_changed())
        self.runner_model.trace_add("write", lambda *_args: self._on_runner_model_changed())
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
        self._build_approvals_section()
        self._build_runner_section()

    def _build_repo_section(self) -> None:
        frame = self._card("Repository")
        self._row(frame, 0, "Target repo", ttk.Label(frame, text=str(self.repo_root), style="Muted.TLabel"))
        self._row(frame, 1, "Specseed dir", ttk.Entry(frame, textvariable=self.specseed_dir, width=44))
        self._row(
            frame,
            2,
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
            text="Allow merging into the dev integration branch",
            variable=self.merge_to_dev,
        ).pack(anchor=tk.W)
        ttk.Checkbutton(
            self.git_merge_frame,
            text="Allow merging into main/master",
            variable=self.merge_to_main,
        ).pack(anchor=tk.W)

    def _build_remote_permissions_section(self) -> None:
        self.remote_permissions_frame = self._card("Remote Actions")
        ttk.Checkbutton(
            self.remote_permissions_frame,
            text="Post issues/tickets/epics, labels, and comments",
            variable=self.post_issues,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(8, 4))
        self.post_children_frame = ttk.Frame(self.remote_permissions_frame)
        self.post_children_frame.grid(row=1, column=0, columnspan=2, sticky=tk.EW, padx=28, pady=(0, 6))
        ttk.Checkbutton(
            self.post_children_frame,
            text="Also post dashboards: ROADMAP, TIMELINE, current branch",
            variable=self.post_dashboards,
        ).pack(anchor=tk.W)
        ttk.Checkbutton(
            self.post_children_frame,
            text="Also post the CONTROL channel",
            variable=self.post_control,
        ).pack(anchor=tk.W)
        ttk.Label(
            self.post_children_frame,
            text="Replying to spec-change posts is always allowed while posting is on.",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))
        ttk.Checkbutton(
            self.remote_permissions_frame,
            text="Push branches to the remote",
            variable=self.push_branches,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=10, pady=2)
        ttk.Checkbutton(
            self.remote_permissions_frame,
            text="Push to main/master on the remote",
            variable=self.push_main,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, padx=10, pady=2)
        ttk.Checkbutton(
            self.remote_permissions_frame,
            text="Open pull/merge requests",
            variable=self.make_prs,
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(2, 10))

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
        provider_box = ttk.Frame(frame)
        ttk.Radiobutton(provider_box, text="Claude", variable=self.runner_provider, value="claude").pack(
            side=tk.LEFT
        )
        ttk.Radiobutton(provider_box, text="Codex", variable=self.runner_provider, value="codex").pack(
            side=tk.LEFT, padx=(12, 0)
        )
        self._row(frame, 0, "Agent CLI", provider_box)
        self._row(
            frame,
            1,
            "Model",
            ttk.Entry(frame, textvariable=self.runner_model, width=44),
        )
        self.effort_frame = ttk.Frame(frame)
        for label, value in (("Low", "low"), ("Medium", "medium"), ("High", "high")):
            ttk.Radiobutton(self.effort_frame, text=label, variable=self.runner_effort, value=value).pack(
                side=tk.LEFT, padx=(0 if value == "low" else 12, 0)
            )
        self._row(frame, 2, "Reasoning effort", self.effort_frame)
        ttk.Label(
            frame,
            text="Reasoning effort is used by the Codex runner.",
            style="Muted.TLabel",
            wraplength=620,
        ).grid(row=3, column=1, sticky=tk.EW, padx=10, pady=(0, 10))
        self._row(
            frame,
            4,
            "Poll interval",
            ttk.Entry(frame, textvariable=self.poll_interval, width=14),
            suffix="seconds",
        )

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
                self.remote_permissions_frame.pack(fill=tk.X, pady=(0, 12), before=self.approvals_frame)

        if self.git_enabled.get():
            self.git_merge_frame.grid()
        else:
            self.git_merge_frame.grid_remove()

        if self.post_issues.get() and not self.local_only.get():
            self.post_children_frame.grid()
        else:
            self.post_children_frame.grid_remove()

        if self.runner_provider.get() == "codex":
            self.effort_frame.grid()
        else:
            self.effort_frame.grid_remove()

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

    def _default_runner_model(self) -> str:
        provider = self.runner_provider.get() if hasattr(self, "runner_provider") else "claude"
        return "gpt-5.4-mini" if provider == "codex" else "claude-opus-4-8"

    def _on_runner_provider_changed(self) -> None:
        if self.runner_model_was_default or not self.runner_model.get().strip():
            self.runner_model.set(self._default_runner_model())
            self.runner_model_was_default = True

    def _on_runner_model_changed(self) -> None:
        self.runner_model_was_default = self.runner_model.get().strip() == self._default_runner_model()

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
        backend = self.cfg["backend"]
        runner = self.cfg.get("runner") or {}
        approvals = self.cfg["approvals"]
        permissions = self.cfg["permissions"]
        git = permissions["git"]
        remote_perms = permissions["remote"]
        self.specseed_dir.set(self.cfg.get("specseed_dir") or configure.DEFAULT_SPECSEED_DIR)
        self.local_only.set(not backend.get("enabled"))
        self.provider.set(backend.get("provider") or self.remote.get("provider") or "github")
        self.repo.set(self.remote.get("repo") or "")
        self.token.set("")
        self.git_enabled.set(git.get("enabled", True))
        self.merge_to_dev.set(git.get("merge_to_dev", False))
        self.merge_to_main.set(git.get("merge_to_main", False))
        self.post_issues.set(remote_perms.get("post_issues", False))
        self.post_dashboards.set(remote_perms.get("post_dashboards", False))
        self.post_control.set(remote_perms.get("post_control", False))
        self.push_branches.set(remote_perms.get("push_branches", False))
        self.push_main.set(remote_perms.get("push_main", False))
        self.make_prs.set(remote_perms.get("make_prs", False))
        self.approvers.set(", ".join(approvals.get("approver_usernames", [])))
        self.runner_provider.set(runner.get("provider") or "claude")
        self.runner_model.set(runner.get("model") or self._default_runner_model())
        self.runner_model_was_default = self.runner_model.get() == self._default_runner_model()
        self.runner_effort.set(runner.get("effort") or "medium")
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
            runner_provider = self.runner_provider.get().strip().lower()
            if runner_provider not in ("claude", "codex"):
                raise ValueError("Agent CLI must be claude or codex.")
            runner_model = self.runner_model.get().strip() or None
            runner_effort = self.runner_effort.get().strip().lower() or "medium"
            if runner_effort not in ("low", "medium", "high"):
                raise ValueError("Reasoning effort must be low, medium, or high.")
        except ValueError as exc:
            messagebox.showerror("Cannot save", str(exc))
            return

        cfg = configure.default_config()
        cfg["specseed_dir"] = specseed_rel.as_posix()
        cfg["poll_interval_seconds"] = poll_interval
        cfg["runner"]["provider"] = runner_provider
        cfg["runner"]["model"] = runner_model
        cfg["runner"]["effort"] = runner_effort
        cfg["backend"]["enabled"] = not self.local_only.get()
        cfg["backend"]["provider"] = None if self.local_only.get() else self.provider.get()
        cfg["approvals"]["approver_usernames"] = self._split_names(self.approvers.get())
        cfg["permissions"]["git"]["enabled"] = self.git_enabled.get()
        cfg["permissions"]["git"]["merge_to_dev"] = self.merge_to_dev.get() if self.git_enabled.get() else False
        cfg["permissions"]["git"]["merge_to_main"] = self.merge_to_main.get() if self.git_enabled.get() else False
        cfg["permissions"]["remote"]["post_issues"] = self.post_issues.get() if not self.local_only.get() else False
        cfg["permissions"]["remote"]["post_dashboards"] = (
            self.post_dashboards.get() if cfg["permissions"]["remote"]["post_issues"] else False
        )
        cfg["permissions"]["remote"]["post_control"] = (
            self.post_control.get() if cfg["permissions"]["remote"]["post_issues"] else False
        )
        cfg["permissions"]["remote"]["push_branches"] = self.push_branches.get() if not self.local_only.get() else False
        cfg["permissions"]["remote"]["push_main"] = self.push_main.get() if not self.local_only.get() else False
        cfg["permissions"]["remote"]["make_prs"] = self.make_prs.get() if not self.local_only.get() else False

        remote = configure.load_remote_state(storage)
        remote["provider"] = None if self.local_only.get() else self.provider.get()
        remote["repo"] = None if self.local_only.get() else (self.repo.get().strip() or None)
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
