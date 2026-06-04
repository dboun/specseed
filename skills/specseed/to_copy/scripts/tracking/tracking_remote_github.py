"""
tracking_remote_github.py - GitHub implementation of the TrackingBase contract.

Stdlib-only adapter over the GitHub REST API. It uses GITHUB_PAT and
GITHUB_REPO by default; both may be passed explicitly to TrackingRemoteGitHub.
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

from tracking_base import (
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


API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
TOKEN_ENV = "GITHUB_PAT"
REPO_ENV = "GITHUB_REPO"
USER_AGENT = "specseed-remote-github"

GITHUB_REACTION_BY_REMOTE = {
    REACTION_EYES: "eyes",
    REACTION_HEART: "heart",
    REACTION_THUMBS_UP: "+1",
    REACTION_THUMBS_DOWN: "-1",
}
REMOTE_REACTION_BY_GITHUB = {value: key for key, value in GITHUB_REACTION_BY_REMOTE.items()}

_dotenv_loaded = False


class GitHubRemoteError(Exception):
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


def _normalize_repo(raw: str) -> str:
    repo = raw.strip()
    if repo.startswith("git@"):
        repo = repo.split(":", 1)[-1]
    had_scheme = bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", repo))
    repo = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", repo)
    first = repo.split("/", 1)[0]
    if "/" in repo and (
        had_scheme
        or first == "github.com"
        or first.endswith(".github.com")
        or first.startswith("git@")
    ):
        repo = repo.split("/", 1)[1]
    repo = repo.rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    parts = [part for part in repo.split("/") if part]
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else repo


class TrackingRemoteGitHub(TrackingBase):
    """GitHub Issues-backed implementation of the provider-neutral interface."""

    def __init__(self, repo: str | None = None, token: str | None = None) -> None:
        _load_dotenv()
        self.repo = _normalize_repo(repo or os.environ.get(REPO_ENV, ""))
        self.token = token or os.environ.get(TOKEN_ENV)
        if not self.repo or "/" not in self.repo:
            raise GitHubRemoteError(0, f"pass repo= or set {REPO_ENV}")
        if not self.token:
            raise GitHubRemoteError(0, f"pass token= or set {TOKEN_ENV}")

    def list_entries(
        self,
        is_open: Optional[bool] = None,
        labels: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        updated_since: Optional[str] = None,
    ) -> TrackingResult:
        try:
            state = "all" if is_open is None else ("open" if is_open else "closed")
            items = self._paginate(
                f"/repos/{self.repo}/issues",
                {
                    "state": state,
                    "labels": ",".join(labels) if labels else None,
                    "assignee": assignee,
                    "since": updated_since,
                },
            )
            entries = [
                self._summary_from_issue(item)
                for item in items
                if "pull_request" not in item
            ]
            return TrackingResult(ok=True, data=entries)
        except Exception as exc:
            return self._error(exc)

    def get_entry(self, entry_id: int | str) -> TrackingResult:
        try:
            issue = self._request("GET", f"/repos/{self.repo}/issues/{entry_id}")
            summary = self._summary_from_issue(issue)
            comments = [
                self._comment_from_issue_comment(comment)
                for comment in self._paginate(
                    f"/repos/{self.repo}/issues/{entry_id}/comments"
                )
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
                    body=issue.get("body"),
                    comments=comments,
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def is_entry_open(self, entry_id: int | str) -> TrackingResult:
        try:
            issue = self._request("GET", f"/repos/{self.repo}/issues/{entry_id}")
            return TrackingResult(
                ok=True,
                data=TrackingEntryOpenState(id=issue["number"], is_open=issue["state"] == "open"),
            )
        except Exception as exc:
            return self._error(exc)

    def set_entry_open(self, entry_id: int | str) -> TrackingResult:
        return self._set_entry_state(entry_id, True)

    def set_entry_closed(self, entry_id: int | str) -> TrackingResult:
        return self._set_entry_state(entry_id, False)

    def pin_entry(self, entry_id: int | str) -> TrackingResult:
        try:
            issue = self._request("GET", f"/repos/{self.repo}/issues/{entry_id}")
            try:
                self._graphql(
                    "mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{number}}}",
                    {"id": issue["node_id"]},
                )
            except GitHubRemoteError as exc:
                if "already pinned" not in exc.message.lower():
                    raise
            return TrackingResult(ok=True, data=TrackingPinState(id=issue["number"], pinned=True))
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
                payload["body"] = body
            if labels:
                payload["labels"] = labels
            if assignees:
                payload["assignees"] = assignees
            issue = self._request("POST", f"/repos/{self.repo}/issues", body=payload)
            return TrackingResult(ok=True, data=TrackingEntryId(id=issue["number"]))
        except Exception as exc:
            return self._error(exc)

    def add_entry_comment(self, entry_id: int | str, body: str) -> TrackingResult:
        if not body:
            return TrackingResult(ok=False, error="comment body is required")
        try:
            comment = self._request(
                "POST",
                f"/repos/{self.repo}/issues/{entry_id}/comments",
                body={"body": body},
            )
            return TrackingResult(ok=True, data=TrackingCommentId(id=comment["id"]))
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
                f"/repos/{self.repo}/issues/comments/{comment_id}/reactions",
                body={"content": GITHUB_REACTION_BY_REMOTE[reaction]},
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
            issue = self._request("GET", f"/repos/{self.repo}/issues/{entry_id}")
            return TrackingResult(
                ok=True,
                data=TrackingLabelSet(
                    entry_id=issue["number"],
                    labels=[self._label_from_github(label) for label in issue.get("labels", [])],
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def add_entry_label(self, entry_id: int | str, label: str) -> TrackingResult:
        try:
            self.ensure_label(label)
            labels = self._request(
                "POST",
                f"/repos/{self.repo}/issues/{entry_id}/labels",
                body={"labels": [label]},
            )
            return TrackingResult(
                ok=True,
                data=TrackingLabelSet(
                    entry_id=entry_id,
                    labels=[self._label_from_github(item) for item in labels],
                ),
            )
        except Exception as exc:
            return self._error(exc)

    def list_labels(self) -> TrackingResult:
        try:
            labels = [self._label_from_github(item) for item in self._paginate(f"/repos/{self.repo}/labels")]
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
            payload = {"name": name, "color": (color or "ededed").lstrip("#")}
            if description is not None:
                payload["description"] = description
            label = self._request(
                "POST",
                f"/repos/{self.repo}/labels",
                body=payload,
            )
            return TrackingResult(ok=True, data=self._label_from_github(label))
        except GitHubRemoteError as exc:
            if exc.status == 422:
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
            label = self._request(
                "GET",
                f"/repos/{self.repo}/labels/{urllib.parse.quote(name, safe='')}",
            )
            return TrackingResult(ok=True, data=self._label_from_github(label))
        except GitHubRemoteError as exc:
            if exc.status == 404:
                return self.create_label(name, color=color, description=description)
            return self._error(exc)
        except Exception as exc:
            return self._error(exc)

    def sync_from_remote(self, remote: object) -> TrackingResult:
        return TrackingResult(ok=False, error="sync_from_remote is not implemented for GitHub")

    def _set_entry_state(self, entry_id: int | str, is_open: bool) -> TrackingResult:
        try:
            issue = self._request(
                "PATCH",
                f"/repos/{self.repo}/issues/{entry_id}",
                body={"state": "open" if is_open else "closed"},
            )
            return TrackingResult(
                ok=True,
                data=TrackingEntryOpenState(id=issue["number"], is_open=issue["state"] == "open"),
            )
        except Exception as exc:
            return self._error(exc)

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        body: object = None,
    ) -> object:
        url = path if path.startswith("http") else API_ROOT + path
        query = {key: value for key, value in (params or {}).items() if value is not None}
        if query:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
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
            message = parsed.get("message") if isinstance(parsed, dict) else None
            raise GitHubRemoteError(exc.code, message or exc.reason or "HTTP error", parsed) from None
        except urllib.error.URLError as exc:
            raise GitHubRemoteError(0, f"connection error: {exc.reason}") from None

    def _request_with_headers(
        self, method: str, url: str, body: object = None
    ) -> tuple[dict[str, str], object]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
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
            message = parsed.get("message") if isinstance(parsed, dict) else None
            raise GitHubRemoteError(exc.code, message or exc.reason or "HTTP error", parsed) from None
        except urllib.error.URLError as exc:
            raise GitHubRemoteError(0, f"connection error: {exc.reason}") from None

    def _paginate(
        self, path: str, params: dict[str, object] | None = None
    ) -> list[object]:
        query = {key: value for key, value in (params or {}).items() if value is not None}
        query.setdefault("per_page", 100)
        url = API_ROOT + path + ("&" if "?" in path else "?") + urllib.parse.urlencode(query)
        items: list[object] = []
        while url:
            headers, parsed = self._request_with_headers("GET", url)
            if isinstance(parsed, list):
                items.extend(parsed)
            elif parsed is not None:
                items.append(parsed)
            url = self._next_link(headers.get("Link"))
        return items

    def _graphql(self, query: str, variables: dict[str, object] | None = None) -> object:
        parsed = self._request(
            "POST",
            "/graphql",
            body={"query": query, "variables": variables or {}},
        )
        if isinstance(parsed, dict) and parsed.get("errors"):
            message = "; ".join(error.get("message", "?") for error in parsed["errors"])
            raise GitHubRemoteError(0, f"graphql: {message}", parsed)
        return parsed

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
            id=issue.get("number"),
            title=str(issue.get("title") or ""),
            labels=[self._label_from_github(label) for label in issue.get("labels", [])],
            is_open=issue.get("state") == "open",
            url=issue.get("html_url"),
            author=self._login(issue.get("user")),
            assignees=[self._login(user) for user in issue.get("assignees", []) if self._login(user)],
            created_at=issue.get("created_at"),
            updated_at=issue.get("updated_at"),
        )

    def _comment_from_issue_comment(self, comment: dict[str, object]) -> TrackingEntryComment:
        return TrackingEntryComment(
            id=comment.get("id"),
            body=str(comment.get("body") or ""),
            author=self._login(comment.get("user")),
            created_at=comment.get("created_at"),
            updated_at=comment.get("updated_at"),
            reactions=self._reactions_for_comment(comment.get("id")),
        )

    def _reactions_for_comment(self, comment_id: int | str) -> list[TrackingReaction]:
        reactions = self._paginate(f"/repos/{self.repo}/issues/comments/{comment_id}/reactions")
        grouped: dict[str, list[str]] = {}
        for reaction in reactions:
            kind = REMOTE_REACTION_BY_GITHUB.get(reaction.get("content"))
            if not kind:
                continue
            user = self._login(reaction.get("user"))
            grouped.setdefault(kind, [])
            if user:
                grouped[kind].append(user)
        return [
            TrackingReaction(kind=kind, count=len(users), users=users)
            for kind, users in sorted(grouped.items())
        ]

    @staticmethod
    def _label_from_github(label: dict[str, object]) -> TrackingLabel:
        return TrackingLabel(
            name=str(label.get("name") or ""),
            color=label.get("color"),
            description=label.get("description"),
        )

    @staticmethod
    def _login(user: object) -> str | None:
        return user.get("login") if isinstance(user, dict) else None

    def _error(self, exc: Exception) -> TrackingResult:
        if isinstance(exc, GitHubRemoteError):
            return TrackingResult(ok=False, error=exc.message)
        return TrackingResult(ok=False, error=str(exc))


__all__ = ["TrackingRemoteGitHub", "GitHubRemoteError"]
