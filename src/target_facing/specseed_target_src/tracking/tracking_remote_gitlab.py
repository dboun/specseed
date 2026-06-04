"""
tracking_remote_gitlab.py - GitLab implementation of the TrackingBase contract.

Stdlib-only adapter over the GitLab v4 API. It uses GITLAB_PAT and GITLAB_REPO
by default; GITLAB_URL can point at a self-hosted GitLab instance.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from src.target_facing.specseed_target_src.tracking.tracking_base import (
    REACTION_EYES,
    REACTION_HEART,
    REACTION_THUMBS_DOWN,
    REACTION_THUMBS_UP,
    TrackingBase,
    TrackingCommentId,
    TrackingEntryComment,
    TrackingEntryDetails,
    TrackingEntryId,
    TrackingEntryOpenState,
    TrackingEntrySummary,
    TrackingLabel,
    TrackingLabelList,
    TrackingLabelSet,
    TrackingPinState,
    TrackingReaction,
    TrackingReactionResult,
    TrackingResult,
)
from src.target_facing.specseed_target_src.tracking.pull_request import (
    TrackingPullRequestDetails,
    TrackingPullRequestId,
    TrackingPullRequestOpenState,
    TrackingPullRequestSummary,
)


API_SUFFIX = "/api/v4"
TOKEN_ENV = "GITLAB_PAT"
REPO_ENV = "GITLAB_REPO"
URL_ENV = "GITLAB_URL"
USER_AGENT = "specseed-remote-gitlab"

GITLAB_REACTION_BY_REMOTE = {
    REACTION_EYES: "eyes",
    REACTION_HEART: "heart",
    REACTION_THUMBS_UP: "thumbsup",
    REACTION_THUMBS_DOWN: "thumbsdown",
}
REMOTE_REACTION_BY_GITLAB = {value: key for key, value in GITLAB_REACTION_BY_REMOTE.items()}

_dotenv_loaded = False


class GitLabRemoteError(Exception):
    def __init__(self, status: int, message: str, response: object = None) -> None:
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.response = response


def _load_dotenv() -> None:
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    for parent in (Path.cwd().resolve(), *Path.cwd().resolve().parents):
        env_path = parent / ".env"
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return


def _split_repo(raw: str) -> tuple[str, str | None, str]:
    repo = raw.strip()
    scheme, host = "https", None
    if repo.startswith("git@"):
        host, _, path = repo[4:].partition(":")
    else:
        match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://(.+)$", repo)
        if match:
            scheme = match.group(1)
            host, _, path = match.group(2).partition("/")
        else:
            path = repo
    if path.endswith(".git"):
        path = path[:-4]
    return scheme, host, path.strip("/")


def _stringify_message(message: object) -> str | None:
    if message is None or isinstance(message, str):
        return message
    return json.dumps(message, separators=(",", ":"))


class TrackingRemoteGitLab(TrackingBase):
    """GitLab Issues-backed implementation of the provider-neutral interface."""

    def __init__(self, repo: str | None = None, token: str | None = None, base_url: str | None = None) -> None:
        _load_dotenv()
        repo_ref = repo or os.environ.get(REPO_ENV)
        if not repo_ref:
            raise GitLabRemoteError(0, f"pass repo= or set {REPO_ENV}")
        scheme, host, path = _split_repo(repo_ref)
        base = base_url or os.environ.get(URL_ENV)
        if base:
            self.api_root = base.rstrip("/") + API_SUFFIX
        elif host:
            self.api_root = f"{scheme}://{host}{API_SUFFIX}"
        else:
            self.api_root = "https://gitlab.com" + API_SUFFIX
        if not path or "/" not in path:
            raise GitLabRemoteError(0, f"could not resolve group/project from {repo_ref!r}")
        self.project = urllib.parse.quote(path, safe="")
        self.token = token or os.environ.get(TOKEN_ENV)
        if not self.token:
            raise GitLabRemoteError(0, f"pass token= or set {TOKEN_ENV}")

    def list_entries(
        self,
        is_open: Optional[bool] = None,
        labels: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> TrackingResult:
        try:
            state = "all" if is_open is None else ("opened" if is_open else "closed")
            issues = self._paginate(
                f"/projects/{self.project}/issues",
                {
                    "state": state,
                    "labels": ",".join(labels) if labels else None,
                    "assignee_username": assignee,
                    "updated_after": updated_since,
                },
            )
            entries = [self._summary_from_issue(issue) for issue in issues]
            return TrackingResult(ok=True, data=entries)
        except Exception as exc:
            return self._error(exc)

    def get_entry(self, entry_id: int | str) -> TrackingResult:
        try:
            issue = self._request("GET", f"/projects/{self.project}/issues/{entry_id}")
            summary = self._summary_from_issue(issue)
            comments = [
                self._comment_from_note(entry_id, note)
                for note in self._paginate(f"/projects/{self.project}/issues/{entry_id}/notes")
                if not note.get("system")
            ]
            return TrackingResult(
                ok=True,
                data=TrackingEntryDetails(
                    id=summary.id,
                    title=summary.title,
                    labels=summary.labels,
                    is_open=summary.is_open,
                    url=summary.url,
                    author=summary.author,
                    assignees=summary.assignees,
                    created_at=summary.created_at,
                    updated_at=summary.updated_at,
                    body=issue.get("description"),
                    comments=comments,
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def is_entry_open(self, entry_id: int | str) -> TrackingResult:
        try:
            issue = self._request("GET", f"/projects/{self.project}/issues/{entry_id}")
            return TrackingResult(
                ok=True,
                data=TrackingEntryOpenState(id=issue["iid"], is_open=issue["state"] == "opened"),
            )
        except Exception as exc:
            return self._error(exc)

    def set_entry_open(self, entry_id: int | str) -> TrackingResult:
        return self._set_entry_state(entry_id, True)

    def set_entry_closed(self, entry_id: int | str) -> TrackingResult:
        return self._set_entry_state(entry_id, False)

    def pin_entry(self, entry_id: int | str) -> TrackingResult:
        return TrackingResult(
            ok=True,
            data=TrackingPinState(id=entry_id, pinned=False),
        )

    def delete_entry(self, entry_id: int | str) -> TrackingResult:
        # GitLab supports issue deletion (DELETE) for Owners; it 204s on success.
        try:
            self._request("DELETE", f"/projects/{self.project}/issues/{entry_id}")
            return TrackingResult(ok=True, data=TrackingEntryId(id=entry_id))
        except Exception as exc:
            return self._error(exc)

    def edit_entry(
        self,
        entry_id: int | str,
        title: Optional[str] = None,
        body: Optional[str] = None,
    ) -> TrackingResult:
        if title is not None and not title.strip():
            return TrackingResult(ok=False, error="entry title cannot be blank")
        if title is None and body is None:
            return TrackingResult(ok=False, error="nothing to edit (title and body both None)")
        try:
            payload: dict[str, object] = {}
            if title is not None:
                payload["title"] = title
            if body is not None:
                payload["description"] = body
            issue = self._request("PUT", f"/projects/{self.project}/issues/{entry_id}", body=payload)
            return TrackingResult(ok=True, data=TrackingEntryId(id=issue["iid"]))
        except Exception as exc:
            return self._error(exc)

    def add_entry(
        self,
        title: str,
        body: Optional[str] = None,
        labels: Optional[list[str]] = None,
        assignees: Optional[list[str]] = None,
    ) -> TrackingResult:
        if not title.strip():
            return TrackingResult(ok=False, error="entry title is required")
        try:
            payload: dict[str, object] = {"title": title}
            if body is not None:
                payload["description"] = body
            if labels:
                payload["labels"] = ",".join(labels)
            if assignees:
                payload["assignee_ids"] = self._assignee_ids(assignees)
            issue = self._request("POST", f"/projects/{self.project}/issues", body=payload)
            return TrackingResult(ok=True, data=TrackingEntryId(id=issue["iid"]))
        except Exception as exc:
            return self._error(exc)

    def add_entry_comment(self, entry_id: int | str, body: str) -> TrackingResult:
        if not body:
            return TrackingResult(ok=False, error="comment body is required")
        try:
            note = self._request(
                "POST",
                f"/projects/{self.project}/issues/{entry_id}/notes",
                body={"body": body},
            )
            return TrackingResult(ok=True, data=TrackingCommentId(id=note["id"]))
        except Exception as exc:
            return self._error(exc)

    def add_entry_comment_reaction(
        self, entry_id: int | str, comment_id: int | str, reaction: str
    ) -> TrackingResult:
        if not self.is_supported_reaction(reaction):
            return TrackingResult(ok=False, error=f"unsupported reaction: {reaction}")
        try:
            self._request(
                "POST",
                f"/projects/{self.project}/issues/{entry_id}/notes/{comment_id}/award_emoji",
                params={"name": GITLAB_REACTION_BY_REMOTE[reaction]},
            )
            return TrackingResult(
                ok=True,
                data=TrackingReactionResult(
                    entry_id=entry_id,
                    comment_id=comment_id,
                    reaction=reaction,
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def get_entry_labels(self, entry_id: int | str) -> TrackingResult:
        try:
            issue = self._request("GET", f"/projects/{self.project}/issues/{entry_id}")
            return TrackingResult(
                ok=True,
                data=TrackingLabelSet(
                    entry_id=issue["iid"],
                    labels=[TrackingLabel(name=str(label)) for label in issue.get("labels", [])],
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def add_entry_label(self, entry_id: int | str, label: str) -> TrackingResult:
        try:
            self.ensure_label(label)
            issue = self._request(
                "PUT",
                f"/projects/{self.project}/issues/{entry_id}",
                body={"add_labels": label},
            )
            return TrackingResult(
                ok=True,
                data=TrackingLabelSet(
                    entry_id=issue["iid"],
                    labels=[TrackingLabel(name=str(item)) for item in issue.get("labels", [])],
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def remove_entry_label(self, entry_id: int | str, label: str) -> TrackingResult:
        try:
            issue = self._request(
                "PUT",
                f"/projects/{self.project}/issues/{entry_id}",
                body={"remove_labels": label},
            )
            return TrackingResult(
                ok=True,
                data=TrackingLabelSet(
                    entry_id=issue["iid"],
                    labels=[TrackingLabel(name=str(item)) for item in issue.get("labels", [])],
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def list_labels(self) -> TrackingResult:
        try:
            labels = [
                self._label_from_gitlab(item)
                for item in self._paginate(f"/projects/{self.project}/labels")
            ]
            return TrackingResult(ok=True, data=TrackingLabelList(labels=labels))
        except Exception as exc:
            return self._error(exc)

    def create_label(
        self,
        name: str,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> TrackingResult:
        try:
            payload = {"name": name, "color": self._gitlab_color(color)}
            if description is not None:
                payload["description"] = description
            label = self._request(
                "POST",
                f"/projects/{self.project}/labels",
                body=payload,
            )
            return TrackingResult(ok=True, data=self._label_from_gitlab(label))
        except GitLabRemoteError as exc:
            if exc.status in (400, 409):
                return self.ensure_label(name, color=color, description=description)
            return self._error(exc)
        except Exception as exc:
            return self._error(exc)

    def ensure_label(
        self,
        name: str,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> TrackingResult:
        try:
            labels = self._paginate(f"/projects/{self.project}/labels", {"search": name})
            for label in labels:
                if label.get("name") == name:
                    return TrackingResult(ok=True, data=self._label_from_gitlab(label))
            return self.create_label(name, color=color, description=description)
        except Exception as exc:
            return self._error(exc)

    def sync_from_remote(self, remote: object) -> TrackingResult:
        return TrackingResult(ok=False, error="sync_from_remote is not implemented for GitLab")

    def list_pull_requests(
        self,
        is_open: Optional[bool] = None,
        labels: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> TrackingResult:
        try:
            state = "all" if is_open is None else ("opened" if is_open else "closed")
            merge_requests = self._paginate(
                f"/projects/{self.project}/merge_requests",
                {
                    "state": state,
                    "labels": ",".join(labels) if labels else None,
                    "assignee_username": assignee,
                    "updated_after": updated_since,
                },
            )
            return TrackingResult(
                ok=True,
                data=[self._summary_from_merge_request(mr) for mr in merge_requests],
            )
        except Exception as exc:
            return self._error(exc)

    def get_pull_request(self, pull_request_id: int | str) -> TrackingResult:
        try:
            mr = self._request(
                "GET",
                f"/projects/{self.project}/merge_requests/{pull_request_id}",
            )
            summary = self._summary_from_merge_request(mr)
            comments = [
                self._comment_from_merge_request_note(pull_request_id, note)
                for note in self._paginate(
                    f"/projects/{self.project}/merge_requests/{pull_request_id}/notes"
                )
                if not note.get("system")
            ]
            return TrackingResult(
                ok=True,
                data=TrackingPullRequestDetails(
                    id=summary.id,
                    title=summary.title,
                    source_branch=summary.source_branch,
                    target_branch=summary.target_branch,
                    labels=summary.labels,
                    is_open=summary.is_open,
                    is_merged=summary.is_merged,
                    url=summary.url,
                    author=summary.author,
                    assignees=summary.assignees,
                    created_at=summary.created_at,
                    updated_at=summary.updated_at,
                    body=mr.get("description"),
                    comments=comments,
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def add_pull_request(
        self,
        title: str,
        source_branch: str,
        target_branch: str,
        body: Optional[str] = None,
        labels: Optional[list[str]] = None,
        assignees: Optional[list[str]] = None,
    ) -> TrackingResult:
        if not title.strip():
            return TrackingResult(ok=False, error="pull request title is required")
        try:
            payload: dict[str, object] = {
                "title": title,
                "source_branch": source_branch,
                "target_branch": target_branch,
            }
            if body is not None:
                payload["description"] = body
            if labels:
                payload["labels"] = ",".join(labels)
            if assignees:
                payload["assignee_ids"] = self._assignee_ids(assignees)
            mr = self._request(
                "POST",
                f"/projects/{self.project}/merge_requests",
                body=payload,
            )
            return TrackingResult(ok=True, data=TrackingPullRequestId(id=mr["iid"]))
        except Exception as exc:
            return self._error(exc)

    def set_pull_request_closed(self, pull_request_id: int | str) -> TrackingResult:
        try:
            mr = self._request(
                "PUT",
                f"/projects/{self.project}/merge_requests/{pull_request_id}",
                body={"state_event": "close"},
            )
            return TrackingResult(
                ok=True,
                data=TrackingPullRequestOpenState(
                    id=mr["iid"],
                    is_open=mr.get("state") == "opened",
                    is_merged=mr.get("state") == "merged",
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def _set_entry_state(self, entry_id: int | str, is_open: bool) -> TrackingResult:
        try:
            issue = self._request(
                "PUT",
                f"/projects/{self.project}/issues/{entry_id}",
                body={"state_event": "reopen" if is_open else "close"},
            )
            return TrackingResult(
                ok=True,
                data=TrackingEntryOpenState(id=issue["iid"], is_open=issue["state"] == "opened"),
            )
        except Exception as exc:
            return self._error(exc)

    def _assignee_ids(self, usernames: list[str]) -> list[int]:
        ids = []
        for username in usernames:
            hits = self._request("GET", "/users", params={"username": username})
            if not hits:
                raise GitLabRemoteError(404, f"no user named {username!r}")
            ids.append(hits[0]["id"])
        return ids

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        body: object = None,
    ) -> object:
        url = path if path.startswith("http") else self.api_root + path
        query = {key: value for key, value in (params or {}).items() if value is not None}
        if query:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "PRIVATE-TOKEN": self.token,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else None
            except Exception:
                parsed = None
            message = None
            if isinstance(parsed, dict):
                message = parsed.get("message") or parsed.get("error")
            raise GitLabRemoteError(
                exc.code,
                _stringify_message(message) or exc.reason or "HTTP error",
                parsed,
            ) from None
        except urllib.error.URLError as exc:
            raise GitLabRemoteError(0, f"connection error: {exc.reason}") from None

    def _request_with_headers(self, method: str, url: str) -> tuple[dict[str, str], object]:
        headers = {
            "PRIVATE-TOKEN": self.token,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                raw = response.read()
                parsed = json.loads(raw.decode("utf-8")) if raw else None
                return dict(response.headers), parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else None
            except Exception:
                parsed = None
            message = None
            if isinstance(parsed, dict):
                message = parsed.get("message") or parsed.get("error")
            raise GitLabRemoteError(
                exc.code,
                _stringify_message(message) or exc.reason or "HTTP error",
                parsed,
            ) from None
        except urllib.error.URLError as exc:
            raise GitLabRemoteError(0, f"connection error: {exc.reason}") from None

    def _paginate(
        self, path: str, params: dict[str, object] | None = None
    ) -> list[object]:
        query = {key: value for key, value in (params or {}).items() if value is not None}
        query.setdefault("per_page", 100)
        url = self.api_root + path + ("&" if "?" in path else "?") + urllib.parse.urlencode(query)
        items: list[object] = []
        while url:
            headers, parsed = self._request_with_headers("GET", url)
            if isinstance(parsed, list):
                items.extend(parsed)
            elif parsed is not None:
                items.append(parsed)
            url = self._next_link(headers.get("Link"))
        return items

    @staticmethod
    def _next_link(link_header: str | None) -> str | None:
        if not link_header:
            return None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                start, end = part.find("<"), part.find(">")
                if start != -1 and end != -1:
                    return part[start + 1:end]
        return None

    def _summary_from_issue(self, issue: dict[str, object]) -> TrackingEntrySummary:
        return TrackingEntrySummary(
            id=issue.get("iid"),
            title=str(issue.get("title") or ""),
            labels=[TrackingLabel(name=str(label)) for label in issue.get("labels", [])],
            is_open=issue.get("state") == "opened",
            url=issue.get("web_url"),
            author=self._username(issue.get("author")),
            assignees=[
                self._username(user)
                for user in issue.get("assignees", [])
                if self._username(user)
            ],
            created_at=issue.get("created_at"),
            updated_at=issue.get("updated_at"),
        )

    def _summary_from_merge_request(self, mr: dict[str, object]) -> TrackingPullRequestSummary:
        return TrackingPullRequestSummary(
            id=mr.get("iid"),
            title=str(mr.get("title") or ""),
            source_branch=str(mr.get("source_branch") or ""),
            target_branch=str(mr.get("target_branch") or ""),
            labels=[TrackingLabel(name=str(label)) for label in mr.get("labels", [])],
            is_open=mr.get("state") == "opened",
            is_merged=mr.get("state") == "merged" or bool(mr.get("merged_at")),
            url=mr.get("web_url"),
            author=self._username(mr.get("author")),
            assignees=[
                self._username(user)
                for user in mr.get("assignees", [])
                if self._username(user)
            ],
            created_at=mr.get("created_at"),
            updated_at=mr.get("updated_at"),
        )

    def _comment_from_note(self, entry_id: int | str, note: dict[str, object]) -> TrackingEntryComment:
        return TrackingEntryComment(
            id=note.get("id"),
            body=str(note.get("body") or ""),
            author=self._username(note.get("author")),
            created_at=note.get("created_at"),
            updated_at=note.get("updated_at"),
            reactions=self._reactions_for_comment(entry_id, note.get("id")),
        )

    def _comment_from_merge_request_note(
        self, pull_request_id: int | str, note: dict[str, object]
    ) -> TrackingEntryComment:
        return TrackingEntryComment(
            id=note.get("id"),
            body=str(note.get("body") or ""),
            pull_request_id=pull_request_id,
            author=self._username(note.get("author")),
            created_at=note.get("created_at"),
            updated_at=note.get("updated_at"),
            reactions=self._reactions_for_merge_request_comment(pull_request_id, note.get("id")),
        )

    def _reactions_for_comment(self, entry_id: int | str, note_id: int | str) -> list[TrackingReaction]:
        reactions = self._paginate(
            f"/projects/{self.project}/issues/{entry_id}/notes/{note_id}/award_emoji"
        )
        return self._group_reactions(reactions)

    def _reactions_for_merge_request_comment(
        self, pull_request_id: int | str, note_id: int | str
    ) -> list[TrackingReaction]:
        reactions = self._paginate(
            f"/projects/{self.project}/merge_requests/{pull_request_id}/notes/{note_id}/award_emoji"
        )
        return self._group_reactions(reactions)

    def _group_reactions(self, reactions: list[object]) -> list[TrackingReaction]:
        grouped: dict[str, list[str]] = {}
        for reaction in reactions:
            kind = REMOTE_REACTION_BY_GITLAB.get(reaction.get("name"))
            if not kind:
                continue
            user = self._username(reaction.get("user"))
            grouped.setdefault(kind, [])
            if user:
                grouped[kind].append(user)
        return [
            TrackingReaction(kind=kind, count=len(users), users=users)
            for kind, users in sorted(grouped.items())
        ]

    @staticmethod
    def _label_from_gitlab(label: dict[str, object]) -> TrackingLabel:
        return TrackingLabel(
            name=str(label.get("name") or ""),
            color=label.get("color"),
            description=label.get("description"),
        )

    @staticmethod
    def _gitlab_color(color: str | None) -> str:
        if not color:
            return "#808080"
        return color if color.startswith("#") or not re.fullmatch(r"[0-9a-fA-F]{6}", color) else "#" + color

    @staticmethod
    def _username(user: object) -> str | None:
        return user.get("username") if isinstance(user, dict) else None

    def _error(self, exc: Exception) -> TrackingResult:
        if isinstance(exc, GitLabRemoteError):
            return TrackingResult(ok=False, error=exc.message)
        return TrackingResult(ok=False, error=str(exc))


__all__ = ["TrackingRemoteGitLab", "GitLabRemoteError"]
