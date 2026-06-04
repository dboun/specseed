"""
Tkinter UI for the local specseed tracking database.

Run from the repository root:

    python3.13 src/target_facing/specseed_target_src/tracking/tracking_local_ui.py

Optional:

    python3.13 src/target_facing/specseed_target_src/tracking/tracking_local_ui.py --db /path/to/tracking_local.db
    python3.13 src/target_facing/specseed_target_src/tracking/tracking_local_ui.py --author your-name

This UI uses TrackingLocal's public methods for reads and writes. It does not
write sqlite directly.
"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Optional


def _add_repo_root_to_path() -> None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "target_facing").exists():
            sys.path.insert(0, str(parent))
            return


_add_repo_root_to_path()

from src.target_facing.specseed_target_src.tracking.supported_values import SUPPORTED_REACTIONS
from src.target_facing.specseed_target_src.tracking.tracking_local import (
    DEFAULT_DB_PATH,
    TrackingLocal,
)


REACTION_CHOICES = tuple(sorted(SUPPORTED_REACTIONS))


class TrackingLocalUI(tk.Tk):
    """Small GitHub-issues-style desktop UI backed by a tracking provider."""

    tracker_cls = TrackingLocal
    default_db_path = DEFAULT_DB_PATH
    window_title = "Specseed Local Tracking"

    def __init__(self, db_path: str | Path | None = None, author: str = "local") -> None:
        super().__init__()
        self.db_path = Path(db_path or self.default_db_path)
        self.tracker = self.tracker_cls(db_path=self.db_path, author=author)
        self.selected_issue_id: Optional[int | str] = None
        self.selected_pr_id: Optional[int | str] = None
        self.issue_comment_ids: list[int | str] = []
        self.pr_comment_ids: list[int | str] = []

        self.title(self.window_title)
        self.geometry("1180x760")
        self.minsize(980, 640)

        self._configure_style()
        self._build()
        self.refresh_all()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f6f8fa")
        style.configure("Panel.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("TLabel", background="#f6f8fa", foreground="#24292f")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#24292f")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#57606a")
        style.configure("TButton", padding=(10, 5))
        style.configure("Treeview", rowheight=26, fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("TkDefaultFont", 10, "bold"))

    def _build(self) -> None:
        header = ttk.Frame(self, padding=(12, 10))
        header.pack(fill=tk.X)
        ttk.Label(header, text=self.window_title, font=("TkDefaultFont", 15, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(header, text=f"  {self.db_path}", foreground="#57606a").pack(side=tk.LEFT)
        ttk.Button(header, text="Refresh", command=self.refresh_all).pack(side=tk.RIGHT)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self.issue_tab = ttk.Frame(self.tabs)
        self.pr_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.issue_tab, text="Issues")
        self.tabs.add(self.pr_tab, text="Pull Requests")

        self._build_issues_tab()
        self._build_prs_tab()

    def _build_issues_tab(self) -> None:
        toolbar = ttk.Frame(self.issue_tab, padding=(0, 8))
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="State").pack(side=tk.LEFT)
        self.issue_filter = tk.StringVar(value="open")
        state = ttk.Combobox(
            toolbar,
            textvariable=self.issue_filter,
            values=("open", "closed", "all"),
            state="readonly",
            width=9,
        )
        state.pack(side=tk.LEFT, padx=(6, 12))
        state.bind("<<ComboboxSelected>>", lambda _event: self.refresh_issues())
        ttk.Button(toolbar, text="New Issue", command=self.new_issue).pack(side=tk.LEFT)

        split = ttk.PanedWindow(self.issue_tab, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(split)
        right = ttk.Frame(split, style="Panel.TFrame", padding=12)
        split.add(left, weight=2)
        split.add(right, weight=3)

        self.issue_tree = ttk.Treeview(
            left,
            columns=("id", "state", "title", "labels", "updated"),
            show="headings",
            selectmode="browse",
        )
        self.issue_tree.heading("id", text="#")
        self.issue_tree.heading("state", text="State")
        self.issue_tree.heading("title", text="Title")
        self.issue_tree.heading("labels", text="Labels")
        self.issue_tree.heading("updated", text="Updated")
        self.issue_tree.column("id", width=60, anchor=tk.E)
        self.issue_tree.column("state", width=80)
        self.issue_tree.column("title", width=280)
        self.issue_tree.column("labels", width=170)
        self.issue_tree.column("updated", width=180)
        self.issue_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        issue_scroll = ttk.Scrollbar(left, command=self.issue_tree.yview)
        issue_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.issue_tree.configure(yscrollcommand=issue_scroll.set)
        self.issue_tree.bind("<<TreeviewSelect>>", lambda _event: self.load_selected_issue())

        self.issue_title = tk.StringVar()
        ttk.Label(right, text="Title", style="Panel.TLabel").pack(anchor=tk.W)
        ttk.Entry(right, textvariable=self.issue_title).pack(fill=tk.X, pady=(2, 10))
        ttk.Label(right, text="Body", style="Panel.TLabel").pack(anchor=tk.W)
        self.issue_body = tk.Text(right, height=8, wrap=tk.WORD, relief=tk.SOLID, bd=1)
        self.issue_body.pack(fill=tk.X, pady=(2, 10))

        issue_actions = ttk.Frame(right, style="Panel.TFrame")
        issue_actions.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(issue_actions, text="Save", command=self.save_issue).pack(side=tk.LEFT)
        ttk.Button(issue_actions, text="Close / Reopen", command=self.toggle_issue).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(issue_actions, text="Delete", command=self.delete_issue).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(issue_actions, text="Add Label", command=self.add_issue_label).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(issue_actions, text="Remove Label", command=self.remove_issue_label).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        self.issue_meta = ttk.Label(right, text="", style="Muted.TLabel", wraplength=560)
        self.issue_meta.pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(right, text="Comments", style="Panel.TLabel").pack(anchor=tk.W)
        self.issue_comments = tk.Listbox(right, height=8, relief=tk.SOLID, bd=1)
        self.issue_comments.pack(fill=tk.BOTH, expand=True, pady=(2, 8))

        comment_bar = ttk.Frame(right, style="Panel.TFrame")
        comment_bar.pack(fill=tk.X)
        self.issue_comment_text = tk.Text(comment_bar, height=3, wrap=tk.WORD, relief=tk.SOLID, bd=1)
        self.issue_comment_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(comment_bar, text="Comment", command=self.add_issue_comment).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        self.issue_reaction = tk.StringVar(value=REACTION_CHOICES[0])
        ttk.Combobox(
            comment_bar,
            textvariable=self.issue_reaction,
            values=REACTION_CHOICES,
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(comment_bar, text="React", command=self.react_to_issue_comment).pack(
            side=tk.LEFT, padx=(8, 0)
        )

    def _build_prs_tab(self) -> None:
        toolbar = ttk.Frame(self.pr_tab, padding=(0, 8))
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="State").pack(side=tk.LEFT)
        self.pr_filter = tk.StringVar(value="open")
        state = ttk.Combobox(
            toolbar,
            textvariable=self.pr_filter,
            values=("open", "closed", "all"),
            state="readonly",
            width=9,
        )
        state.pack(side=tk.LEFT, padx=(6, 12))
        state.bind("<<ComboboxSelected>>", lambda _event: self.refresh_prs())
        ttk.Button(toolbar, text="New Pull Request", command=self.new_pull_request).pack(side=tk.LEFT)

        split = ttk.PanedWindow(self.pr_tab, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(split)
        right = ttk.Frame(split, style="Panel.TFrame", padding=12)
        split.add(left, weight=2)
        split.add(right, weight=3)

        self.pr_tree = ttk.Treeview(
            left,
            columns=("id", "state", "title", "branches", "labels", "updated"),
            show="headings",
            selectmode="browse",
        )
        for column, text in (
            ("id", "#"),
            ("state", "State"),
            ("title", "Title"),
            ("branches", "Branches"),
            ("labels", "Labels"),
            ("updated", "Updated"),
        ):
            self.pr_tree.heading(column, text=text)
        self.pr_tree.column("id", width=60, anchor=tk.E)
        self.pr_tree.column("state", width=80)
        self.pr_tree.column("title", width=250)
        self.pr_tree.column("branches", width=180)
        self.pr_tree.column("labels", width=140)
        self.pr_tree.column("updated", width=180)
        self.pr_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pr_scroll = ttk.Scrollbar(left, command=self.pr_tree.yview)
        pr_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.pr_tree.configure(yscrollcommand=pr_scroll.set)
        self.pr_tree.bind("<<TreeviewSelect>>", lambda _event: self.load_selected_pr())

        self.pr_title = tk.StringVar()
        ttk.Label(right, text="Title", style="Panel.TLabel").pack(anchor=tk.W)
        ttk.Entry(right, textvariable=self.pr_title, state="readonly").pack(fill=tk.X, pady=(2, 10))
        ttk.Label(right, text="Body", style="Panel.TLabel").pack(anchor=tk.W)
        self.pr_body = tk.Text(right, height=8, wrap=tk.WORD, relief=tk.SOLID, bd=1)
        self.pr_body.pack(fill=tk.X, pady=(2, 10))
        self.pr_body.configure(state=tk.DISABLED)

        pr_actions = ttk.Frame(right, style="Panel.TFrame")
        pr_actions.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(pr_actions, text="Close", command=self.close_pull_request).pack(side=tk.LEFT)

        self.pr_meta = ttk.Label(right, text="", style="Muted.TLabel", wraplength=560)
        self.pr_meta.pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(right, text="Comments", style="Panel.TLabel").pack(anchor=tk.W)
        self.pr_comments = tk.Listbox(right, height=8, relief=tk.SOLID, bd=1)
        self.pr_comments.pack(fill=tk.BOTH, expand=True, pady=(2, 8))

        comment_bar = ttk.Frame(right, style="Panel.TFrame")
        comment_bar.pack(fill=tk.X)
        self.pr_comment_text = tk.Text(comment_bar, height=3, wrap=tk.WORD, relief=tk.SOLID, bd=1)
        self.pr_comment_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(comment_bar, text="Comment", command=self.add_pr_comment).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        self.pr_reaction = tk.StringVar(value=REACTION_CHOICES[0])
        ttk.Combobox(
            comment_bar,
            textvariable=self.pr_reaction,
            values=REACTION_CHOICES,
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(comment_bar, text="React", command=self.react_to_pr_comment).pack(
            side=tk.LEFT, padx=(8, 0)
        )

    def refresh_all(self) -> None:
        self.refresh_issues()
        self.refresh_prs()

    def refresh_issues(self) -> None:
        selected = self.selected_issue_id
        for item in self.issue_tree.get_children():
            self.issue_tree.delete(item)
        result = self.tracker.list_entries(is_open=self._state_filter(self.issue_filter.get()))
        if not self._ok(result):
            return
        for entry in result.data:
            labels = ", ".join(label.name for label in entry.labels)
            state = "open" if entry.is_open else "closed"
            self.issue_tree.insert(
                "",
                tk.END,
                iid=str(entry.id),
                values=(entry.id, state, entry.title, labels, entry.updated_at or ""),
            )
        if selected and str(selected) in self.issue_tree.get_children():
            self.issue_tree.selection_set(str(selected))
            self.issue_tree.focus(str(selected))

    def refresh_prs(self) -> None:
        selected = self.selected_pr_id
        for item in self.pr_tree.get_children():
            self.pr_tree.delete(item)
        result = self.tracker.list_pull_requests(is_open=self._state_filter(self.pr_filter.get()))
        if not self._ok(result):
            return
        for pr in result.data:
            labels = ", ".join(label.name for label in pr.labels)
            state = "open" if pr.is_open else "closed"
            branches = f"{pr.source_branch} -> {pr.target_branch}"
            self.pr_tree.insert(
                "",
                tk.END,
                iid=str(pr.id),
                values=(pr.id, state, pr.title, branches, labels, pr.updated_at or ""),
            )
        if selected and str(selected) in self.pr_tree.get_children():
            self.pr_tree.selection_set(str(selected))
            self.pr_tree.focus(str(selected))

    def load_selected_issue(self) -> None:
        selection = self.issue_tree.selection()
        if not selection:
            return
        self.selected_issue_id = selection[0]
        result = self.tracker.get_entry(self.selected_issue_id)
        if not self._ok(result):
            return
        entry = result.data
        self.issue_title.set(entry.title)
        self.issue_body.delete("1.0", tk.END)
        self.issue_body.insert("1.0", entry.body or "")
        labels = ", ".join(label.name for label in entry.labels) or "none"
        assignees = ", ".join(entry.assignees) or "none"
        state = "open" if entry.is_open else "closed"
        self.issue_meta.configure(
            text=(
                f"#{entry.id} {state} by {entry.author or 'unknown'} | "
                f"labels: {labels} | assignees: {assignees} | updated: {entry.updated_at or 'unknown'}"
            )
        )
        self.issue_comments.delete(0, tk.END)
        self.issue_comment_ids = []
        for comment in entry.comments:
            self.issue_comment_ids.append(comment.id)
            reactions = self._reaction_summary(comment.reactions)
            self.issue_comments.insert(
                tk.END,
                f"#{comment.id} {comment.author or 'unknown'}: {comment.body} {reactions}".strip(),
            )

    def load_selected_pr(self) -> None:
        selection = self.pr_tree.selection()
        if not selection:
            return
        self.selected_pr_id = selection[0]
        result = self.tracker.get_pull_request(self.selected_pr_id)
        if not self._ok(result):
            return
        pr = result.data
        self.pr_title.set(pr.title)
        self.pr_body.configure(state=tk.NORMAL)
        self.pr_body.delete("1.0", tk.END)
        self.pr_body.insert("1.0", pr.body or "")
        self.pr_body.configure(state=tk.DISABLED)
        labels = ", ".join(label.name for label in pr.labels) or "none"
        assignees = ", ".join(pr.assignees) or "none"
        state = "open" if pr.is_open else "closed"
        self.pr_meta.configure(
            text=(
                f"#{pr.id} {state} {pr.source_branch} -> {pr.target_branch} | "
                f"labels: {labels} | assignees: {assignees} | updated: {pr.updated_at or 'unknown'}"
            )
        )
        self.pr_comments.delete(0, tk.END)
        self.pr_comment_ids = []
        for comment in pr.comments:
            self.pr_comment_ids.append(comment.id)
            reactions = self._reaction_summary(comment.reactions)
            self.pr_comments.insert(
                tk.END,
                f"#{comment.id} {comment.author or 'unknown'}: {comment.body} {reactions}".strip(),
            )

    def new_issue(self) -> None:
        dialog = IssueDialog(self, "New Issue")
        if not dialog.values:
            return
        result = self.tracker.add_entry(
            title=dialog.values["title"],
            body=dialog.values["body"],
            labels=self._csv(dialog.values["labels"]),
            assignees=self._csv(dialog.values["assignees"]),
        )
        if self._ok(result):
            self.selected_issue_id = result.data.id
            self.refresh_issues()
            self.load_issue_by_id(result.data.id)

    def save_issue(self) -> None:
        if not self.selected_issue_id:
            return
        result = self.tracker.edit_entry(
            self.selected_issue_id,
            title=self.issue_title.get(),
            body=self.issue_body.get("1.0", tk.END).rstrip(),
        )
        if self._ok(result):
            self.refresh_issues()
            self.load_issue_by_id(self.selected_issue_id)

    def toggle_issue(self) -> None:
        if not self.selected_issue_id:
            return
        result = self.tracker.is_entry_open(self.selected_issue_id)
        if not self._ok(result):
            return
        if result.data.is_open:
            mutation = self.tracker.set_entry_closed(self.selected_issue_id)
        else:
            mutation = self.tracker.set_entry_open(self.selected_issue_id)
        if self._ok(mutation):
            self.refresh_issues()
            self.load_issue_by_id(self.selected_issue_id)

    def delete_issue(self) -> None:
        if not self.selected_issue_id:
            return
        if not messagebox.askyesno("Delete issue", f"Delete issue #{self.selected_issue_id}?"):
            return
        result = self.tracker.delete_entry(self.selected_issue_id)
        if self._ok(result):
            self.selected_issue_id = None
            self.issue_title.set("")
            self.issue_body.delete("1.0", tk.END)
            self.issue_meta.configure(text="")
            self.issue_comments.delete(0, tk.END)
            self.refresh_issues()

    def add_issue_label(self) -> None:
        if not self.selected_issue_id:
            return
        label = simpledialog.askstring("Add label", "Label name:", parent=self)
        if not label:
            return
        result = self.tracker.add_entry_label(self.selected_issue_id, label.strip())
        if self._ok(result):
            self.refresh_issues()
            self.load_issue_by_id(self.selected_issue_id)

    def remove_issue_label(self) -> None:
        if not self.selected_issue_id:
            return
        label = simpledialog.askstring("Remove label", "Label name:", parent=self)
        if not label:
            return
        result = self.tracker.remove_entry_label(self.selected_issue_id, label.strip())
        if self._ok(result):
            self.refresh_issues()
            self.load_issue_by_id(self.selected_issue_id)

    def add_issue_comment(self) -> None:
        if not self.selected_issue_id:
            return
        body = self.issue_comment_text.get("1.0", tk.END).strip()
        result = self.tracker.add_entry_comment(self.selected_issue_id, body)
        if self._ok(result):
            self.issue_comment_text.delete("1.0", tk.END)
            self.refresh_issues()
            self.load_issue_by_id(self.selected_issue_id)

    def react_to_issue_comment(self) -> None:
        if not self.selected_issue_id:
            return
        index = self._selected_listbox_index(self.issue_comments)
        if index is None:
            messagebox.showinfo("Reaction", "Select a comment first.")
            return
        result = self.tracker.add_entry_comment_reaction(
            self.selected_issue_id,
            self.issue_comment_ids[index],
            self.issue_reaction.get(),
        )
        if self._ok(result):
            self.load_issue_by_id(self.selected_issue_id)

    def new_pull_request(self) -> None:
        dialog = PullRequestDialog(self, "New Pull Request")
        if not dialog.values:
            return
        result = self.tracker.add_pull_request(
            title=dialog.values["title"],
            body=dialog.values["body"],
            source_branch=dialog.values["source_branch"],
            target_branch=dialog.values["target_branch"],
            labels=self._csv(dialog.values["labels"]),
            assignees=self._csv(dialog.values["assignees"]),
        )
        if self._ok(result):
            self.selected_pr_id = result.data.id
            self.refresh_prs()
            self.load_pr_by_id(result.data.id)

    def close_pull_request(self) -> None:
        if not self.selected_pr_id:
            return
        result = self.tracker.set_pull_request_closed(self.selected_pr_id)
        if self._ok(result):
            self.refresh_prs()
            self.load_pr_by_id(self.selected_pr_id)

    def add_pr_comment(self) -> None:
        if not self.selected_pr_id:
            return
        body = self.pr_comment_text.get("1.0", tk.END).strip()
        result = self.tracker.add_pull_request_comment(self.selected_pr_id, body)
        if self._ok(result):
            self.pr_comment_text.delete("1.0", tk.END)
            self.refresh_prs()
            self.load_pr_by_id(self.selected_pr_id)

    def react_to_pr_comment(self) -> None:
        if not self.selected_pr_id:
            return
        index = self._selected_listbox_index(self.pr_comments)
        if index is None:
            messagebox.showinfo("Reaction", "Select a comment first.")
            return
        result = self.tracker.add_pull_request_comment_reaction(
            self.selected_pr_id,
            self.pr_comment_ids[index],
            self.pr_reaction.get(),
        )
        if self._ok(result):
            self.load_pr_by_id(self.selected_pr_id)

    def load_issue_by_id(self, entry_id: int | str) -> None:
        if str(entry_id) in self.issue_tree.get_children():
            self.issue_tree.selection_set(str(entry_id))
            self.issue_tree.focus(str(entry_id))
            self.issue_tree.see(str(entry_id))
        self.selected_issue_id = entry_id
        self.load_selected_issue()

    def load_pr_by_id(self, pr_id: int | str) -> None:
        if str(pr_id) in self.pr_tree.get_children():
            self.pr_tree.selection_set(str(pr_id))
            self.pr_tree.focus(str(pr_id))
            self.pr_tree.see(str(pr_id))
        self.selected_pr_id = pr_id
        self.load_selected_pr()

    def _ok(self, result: Any) -> bool:
        if result.ok:
            return True
        messagebox.showerror("Tracking error", result.error or "Unknown tracking error")
        return False

    @staticmethod
    def _state_filter(value: str) -> Optional[bool]:
        if value == "open":
            return True
        if value == "closed":
            return False
        return None

    @staticmethod
    def _csv(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]

    @staticmethod
    def _selected_listbox_index(listbox: tk.Listbox) -> Optional[int]:
        selection = listbox.curselection()
        if not selection:
            return None
        return int(selection[0])

    @staticmethod
    def _reaction_summary(reactions: list[Any]) -> str:
        if not reactions:
            return ""
        return " ".join(f"{reaction.kind}:{reaction.count}" for reaction in reactions)


class FormDialog(simpledialog.Dialog):
    fields: tuple[tuple[str, str, bool], ...] = ()

    def __init__(self, parent: tk.Widget, title: str | None = None) -> None:
        self.values: dict[str, str] | None = None
        super().__init__(parent, title)

    def body(self, master: tk.Widget) -> tk.Widget:
        self.inputs: dict[str, tk.Widget] = {}
        for row, (name, label, multiline) in enumerate(self.fields):
            ttk.Label(master, text=label).grid(row=row, column=0, sticky=tk.NW, padx=6, pady=5)
            if multiline:
                widget = tk.Text(master, width=58, height=8, wrap=tk.WORD)
                widget.grid(row=row, column=1, sticky=tk.EW, padx=6, pady=5)
            else:
                widget = ttk.Entry(master, width=58)
                widget.grid(row=row, column=1, sticky=tk.EW, padx=6, pady=5)
            self.inputs[name] = widget
        master.columnconfigure(1, weight=1)
        return next(iter(self.inputs.values()))

    def validate(self) -> bool:
        self.values = {}
        for name, _label, multiline in self.fields:
            widget = self.inputs[name]
            if multiline:
                value = widget.get("1.0", tk.END).rstrip()
            else:
                value = widget.get().strip()
            self.values[name] = value
        if not self.values.get("title"):
            messagebox.showerror("Missing title", "Title is required.", parent=self)
            return False
        return True


class IssueDialog(FormDialog):
    fields = (
        ("title", "Title", False),
        ("body", "Body", True),
        ("labels", "Labels, comma-separated", False),
        ("assignees", "Assignees, comma-separated", False),
    )


class PullRequestDialog(FormDialog):
    fields = (
        ("title", "Title", False),
        ("body", "Body", True),
        ("source_branch", "Source branch", False),
        ("target_branch", "Target branch", False),
        ("labels", "Labels, comma-separated", False),
        ("assignees", "Assignees, comma-separated", False),
    )

    def validate(self) -> bool:
        if not super().validate():
            return False
        if not self.values.get("source_branch") or not self.values.get("target_branch"):
            messagebox.showerror(
                "Missing branches",
                "Source branch and target branch are required.",
                parent=self,
            )
            return False
        return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the specseed local tracking Tk UI.")
    parser.add_argument("--db", default=None, help="Path to the sqlite tracking database.")
    parser.add_argument("--author", default="local", help="Author name for new writes.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = TrackingLocalUI(db_path=args.db, author=args.author)
    app.mainloop()


if __name__ == "__main__":
    main()
