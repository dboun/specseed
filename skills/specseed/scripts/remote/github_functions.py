"""
github_functions.py — thin stdlib-only wrapper over the GitHub REST API.

Dual use:
  * LIBRARY  — `import github_functions as gh; gh.create_github_issue(...)`
               Other Python code (specseed scripts) should IMPORT and call these
               directly — do not shell out to the CLI from Python.
  * CLI      — `python github_functions.py <function> [--arg value ...]`
               For agents / the shell. Auto-generated from the function
               signatures below, so every library function is callable by name.

This is deliberately a near-1:1 projection of the API primitives we might need
(GitHub Issues, comments, labels, assignees, pull requests, file contents) —
NOT higher-level workflow logic. Compose these in other code.

Terminology: a GitHub Issue is called a "github_issue" throughout, to avoid
collision with specseed's own "issue" tier.

--------------------------------------------------------------------------------
PAT permissions required (fine-grained personal access token, repo-scoped)
--------------------------------------------------------------------------------
  Metadata        : Read     (forced on every fine-grained PAT; repo/user/rate-limit reads)
  Issues          : Read+Write   (github_issues, comments, labels, assignees)
  Pull requests   : Read+Write   (the *_pull_request functions)
  Contents        : Read+Write   (get_file_contents / create_or_update_file / delete_file)

Inbound feedback (e.g. a user's comment) is read by polling list_repo_issue_comments
with a `since` timestamp — no webhook permission needed.

--------------------------------------------------------------------------------
Environment
--------------------------------------------------------------------------------
  GITHUB_PAT      : the token. REQUIRED.
  GITHUB_REPO     : default target repo. Optional if every call passes repo=.
                    Accepts "owner/name" or any GitHub URL/clone form, e.g.
                    https://github.com/owner/name(.git), github.com/owner/name,
                    git@github.com:owner/name.git.

Both are read from the process environment first. A `.env` file is a convenience,
not a requirement: if one is found by searching the working directory upward, it
fills in only the vars not already set (real environment always wins).

--------------------------------------------------------------------------------
CLI conventions
--------------------------------------------------------------------------------
  python github_functions.py                       # list available functions
  python github_functions.py <function> --help     # show its parameters
  python github_functions.py get_github_issue 7    # required args positionally
  python github_functions.py create_github_issue --title "Hi" --labels '["bug"]'

  - Args map to parameters by name (--name value or --name=value); required
    params may instead be given positionally in signature order.
  - Values are coerced by the parameter's type annotation: list/dict params are
    parsed as JSON, int as int, bool as 1/true/yes/on; everything else is a
    raw string (so titles/bodies are never mangled).
  - Output: JSON to stdout. Empty-body responses (e.g. deletes) print {"ok": true}.

Exit: 0 OK; 1 GitHub/API error (message as JSON on stderr); 2 bad CLI usage.
"""

import base64
import inspect
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "specseed-github-functions"
TOKEN_ENV = "GITHUB_PAT"
REPO_ENV = "GITHUB_REPO"

_dotenv_loaded = False


class GitHubError(Exception):
    """Raised on any non-success API response or transport failure."""

    def __init__(self, status, message, response=None):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.response = response


# --------------------------------------------------------------------------- #
# config / transport
# --------------------------------------------------------------------------- #
def _load_dotenv():
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    start = Path.cwd().resolve()
    for parent in (start, *start.parents):
        f = parent / ".env"
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def _token():
    _load_dotenv()
    tok = os.environ.get(TOKEN_ENV)
    if not tok:
        raise GitHubError(0, f"{TOKEN_ENV} not set (env or .env)")
    return tok


def _normalize_repo(raw):
    """Reduce any GitHub repo reference to 'owner/name'.

    Accepts owner/name, https://github.com/owner/name(.git), github.com/owner/name,
    git@github.com:owner/name.git, ssh://..., trailing slashes, etc."""
    s = raw.strip()
    if s.startswith("git@"):                       # git@github.com:owner/name.git
        s = s.split(":", 1)[-1]
    had_scheme = bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s))
    s = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", s)   # strip scheme
    first = s.split("/", 1)[0]
    if "/" in s and (had_scheme or first == "github.com" or first.endswith(".github.com")
                     or first.startswith("git@")):
        s = s.split("/", 1)[1]                     # drop the host segment
    s = s.rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    parts = [p for p in s.split("/") if p]
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else s


def _repo(repo=None):
    if not repo:
        _load_dotenv()
        repo = os.environ.get(REPO_ENV)
    norm = _normalize_repo(repo) if repo else None
    if not norm or "/" not in norm:
        raise GitHubError(0, f"could not resolve 'owner/name' from {repo!r}; "
                             f"pass repo= or set {REPO_ENV}")
    return norm


def _raw_request(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else None
            return resp.status, dict(resp.headers), parsed
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            parsed = None
        msg = parsed.get("message") if isinstance(parsed, dict) else None
        raise GitHubError(e.code, msg or e.reason or "HTTP error", parsed) from None
    except urllib.error.URLError as e:
        raise GitHubError(0, f"connection error: {e.reason}") from None


def _request(method, path, params=None, body=None):
    url = path if path.startswith("http") else API_ROOT + path
    if params:
        q = {k: v for k, v in params.items() if v is not None}
        if q:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(q)
    _, _, parsed = _raw_request(method, url, body)
    return parsed


def _next_link(link_header):
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            s, e = part.find("<"), part.find(">")
            if s != -1 and e != -1:
                return part[s + 1:e]
    return None


def _paginate(path, params=None):
    p = {k: v for k, v in (params or {}).items() if v is not None}
    p.setdefault("per_page", 100)
    url = API_ROOT + path + ("&" if "?" in path else "?") + urllib.parse.urlencode(p)
    items = []
    while url:
        _, headers, parsed = _raw_request("GET", url)
        if isinstance(parsed, list):
            items.extend(parsed)
        elif parsed is not None:
            items.append(parsed)
        url = _next_link(headers.get("Link"))
    return items


# --------------------------------------------------------------------------- #
# meta / auth
# --------------------------------------------------------------------------- #
def get_authenticated_user():
    """Identity behind GITHUB_PAT (cheap token/connectivity check)."""
    return _request("GET", "/user")


def get_rate_limit():
    """Current rate-limit budget."""
    return _request("GET", "/rate_limit")


def get_repo(repo: str = None):
    """Repository metadata."""
    return _request("GET", f"/repos/{_repo(repo)}")


# --------------------------------------------------------------------------- #
# github issues
# --------------------------------------------------------------------------- #
def create_github_issue(title: str, body: str = None, labels: list = None,
                        assignees: list = None, repo: str = None):
    payload: dict = {"title": title}
    if body is not None:
        payload["body"] = body
    if labels:
        payload["labels"] = labels
    if assignees:
        payload["assignees"] = assignees
    return _request("POST", f"/repos/{_repo(repo)}/issues", body=payload)


def get_github_issue(number: int, repo: str = None):
    return _request("GET", f"/repos/{_repo(repo)}/issues/{number}")


def list_github_issues(state: str = "open", labels: str = None, assignee: str = None,
                       creator: str = None, since: str = None,
                       include_pull_requests: bool = False, repo: str = None):
    """All github_issues (auto-paginated). `state` in open|closed|all.

    `labels` is a comma-separated string filter. PRs are excluded by default
    (the /issues endpoint returns them too)."""
    items = _paginate(f"/repos/{_repo(repo)}/issues", {
        "state": state, "labels": labels, "assignee": assignee,
        "creator": creator, "since": since,
    })
    if not include_pull_requests:
        items = [i for i in items if "pull_request" not in i]
    return items


def update_github_issue(number: int, title: str = None, body: str = None,
                        state: str = None, labels: list = None,
                        assignees: list = None, repo: str = None):
    """Patch any subset of fields. `state` in open|closed."""
    payload: dict = {}
    for k, v in (("title", title), ("body", body), ("state", state),
                 ("labels", labels), ("assignees", assignees)):
        if v is not None:
            payload[k] = v
    return _request("PATCH", f"/repos/{_repo(repo)}/issues/{number}", body=payload)


def close_github_issue(number: int, state_reason: str = "completed", repo: str = None):
    """Close. state_reason in completed|not_planned."""
    return _request("PATCH", f"/repos/{_repo(repo)}/issues/{number}",
                    body={"state": "closed", "state_reason": state_reason})


def reopen_github_issue(number: int, repo: str = None):
    return _request("PATCH", f"/repos/{_repo(repo)}/issues/{number}",
                    body={"state": "open"})


# --------------------------------------------------------------------------- #
# pinning  (GraphQL only — the REST API has no pin endpoint)
# --------------------------------------------------------------------------- #
def _graphql(query, variables=None):
    _, _, parsed = _raw_request("POST", f"{API_ROOT}/graphql",
                                {"query": query, "variables": variables or {}})
    if isinstance(parsed, dict) and parsed.get("errors"):
        msg = "; ".join(e.get("message", "?") for e in parsed["errors"])
        raise GitHubError(0, f"graphql: {msg}", parsed)
    return parsed


def pin_github_issue(number: int, repo: str = None):
    """Pin an issue to the repo (max 3 pinned per repo). GraphQL `pinIssue`.

    Idempotent: re-pinning an already-pinned issue is a no-op error we swallow."""
    node_id = get_github_issue(number, repo=repo)["node_id"]
    try:
        return _graphql(
            "mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{number}}}",
            {"id": node_id})
    except GitHubError as e:
        if "already pinned" in (e.message or "").lower():
            return {"ok": True, "already_pinned": True}
        raise


def unpin_github_issue(number: int, repo: str = None):
    node_id = get_github_issue(number, repo=repo)["node_id"]
    return _graphql(
        "mutation($id:ID!){unpinIssue(input:{issueId:$id}){issue{number}}}",
        {"id": node_id})


# --------------------------------------------------------------------------- #
# comments  (PRs are github_issues too, so these work on PR numbers as well)
# --------------------------------------------------------------------------- #
def add_comment_to_github_issue(number: int, body: str, repo: str = None):
    return _request("POST", f"/repos/{_repo(repo)}/issues/{number}/comments",
                    body={"body": body})


def list_github_issue_comments(number: int, repo: str = None):
    return _paginate(f"/repos/{_repo(repo)}/issues/{number}/comments")


def list_repo_issue_comments(since: str = None, repo: str = None):
    """Every issue/PR comment in the repo (auto-paginated). `since` is an ISO-8601
    timestamp — the polling primitive for 'did anyone comment since last check'."""
    return _paginate(f"/repos/{_repo(repo)}/issues/comments", {"since": since})


def update_github_issue_comment(comment_id: int, body: str, repo: str = None):
    return _request("PATCH", f"/repos/{_repo(repo)}/issues/comments/{comment_id}",
                    body={"body": body})


def delete_github_issue_comment(comment_id: int, repo: str = None):
    return _request("DELETE", f"/repos/{_repo(repo)}/issues/comments/{comment_id}")


# --------------------------------------------------------------------------- #
# labels
# --------------------------------------------------------------------------- #
def list_labels(repo: str = None):
    return _paginate(f"/repos/{_repo(repo)}/labels")


def create_label(name: str, color: str = None, description: str = None, repo: str = None):
    """color is a 6-char hex (leading # tolerated)."""
    payload: dict = {"name": name}
    if color is not None:
        payload["color"] = color.lstrip("#")
    if description is not None:
        payload["description"] = description
    return _request("POST", f"/repos/{_repo(repo)}/labels", body=payload)


def delete_label(name: str, repo: str = None):
    return _request("DELETE", f"/repos/{_repo(repo)}/labels/{urllib.parse.quote(name, safe='')}")


def add_labels_to_github_issue(number: int, labels: list, repo: str = None):
    return _request("POST", f"/repos/{_repo(repo)}/issues/{number}/labels",
                    body={"labels": labels})


def remove_label_from_github_issue(number: int, label: str, repo: str = None):
    return _request("DELETE", f"/repos/{_repo(repo)}/issues/{number}/labels/"
                              f"{urllib.parse.quote(label, safe='')}")


def set_labels_on_github_issue(number: int, labels: list, repo: str = None):
    """Replace the full label set."""
    return _request("PUT", f"/repos/{_repo(repo)}/issues/{number}/labels",
                    body={"labels": labels})


# --------------------------------------------------------------------------- #
# assignees
# --------------------------------------------------------------------------- #
def add_assignees_to_github_issue(number: int, assignees: list, repo: str = None):
    return _request("POST", f"/repos/{_repo(repo)}/issues/{number}/assignees",
                    body={"assignees": assignees})


def remove_assignees_from_github_issue(number: int, assignees: list, repo: str = None):
    return _request("DELETE", f"/repos/{_repo(repo)}/issues/{number}/assignees",
                    body={"assignees": assignees})


# --------------------------------------------------------------------------- #
# pull requests
# --------------------------------------------------------------------------- #
def create_pull_request(title: str, head: str, base: str, body: str = None,
                        draft: bool = False, repo: str = None):
    """head = source branch (or owner:branch for forks); base = target branch."""
    payload: dict = {"title": title, "head": head, "base": base, "draft": draft}
    if body is not None:
        payload["body"] = body
    return _request("POST", f"/repos/{_repo(repo)}/pulls", body=payload)


def get_pull_request(number: int, repo: str = None):
    return _request("GET", f"/repos/{_repo(repo)}/pulls/{number}")


def list_pull_requests(state: str = "open", base: str = None, head: str = None,
                       repo: str = None):
    """state in open|closed|all (auto-paginated)."""
    return _paginate(f"/repos/{_repo(repo)}/pulls",
                     {"state": state, "base": base, "head": head})


def update_pull_request(number: int, title: str = None, body: str = None,
                        state: str = None, base: str = None, repo: str = None):
    payload: dict = {}
    for k, v in (("title", title), ("body", body), ("state", state), ("base", base)):
        if v is not None:
            payload[k] = v
    return _request("PATCH", f"/repos/{_repo(repo)}/pulls/{number}", body=payload)


def merge_pull_request(number: int, commit_title: str = None, commit_message: str = None,
                       merge_method: str = "merge", repo: str = None):
    """merge_method in merge|squash|rebase."""
    payload: dict = {"merge_method": merge_method}
    if commit_title is not None:
        payload["commit_title"] = commit_title
    if commit_message is not None:
        payload["commit_message"] = commit_message
    return _request("PUT", f"/repos/{_repo(repo)}/pulls/{number}/merge", body=payload)


# --------------------------------------------------------------------------- #
# contents (files)
# --------------------------------------------------------------------------- #
def get_file_contents(path: str, ref: str = None, repo: str = None):
    """File or directory at `path`. For a file, `decoded_content` (utf-8 text) is
    added when decodable; a directory returns a list of entries."""
    qpath = urllib.parse.quote(path, safe="/")
    result = _request("GET", f"/repos/{_repo(repo)}/contents/{qpath}", params={"ref": ref})
    if isinstance(result, dict) and result.get("encoding") == "base64":
        try:
            result["decoded_content"] = base64.b64decode(result["content"]).decode("utf-8")
        except Exception:
            pass
    return result


def create_or_update_file(path: str, content: str, message: str, branch: str = None,
                          sha: str = None, repo: str = None):
    """Write `content` (plain text) to `path` with commit `message`. On update,
    the blob sha is required; if not given it is fetched automatically."""
    repo = _repo(repo)
    if sha is None:
        try:
            existing = get_file_contents(path, ref=branch, repo=repo)
            if isinstance(existing, dict):
                sha = existing.get("sha")
        except GitHubError as e:
            if e.status != 404:
                raise
    payload: dict = {"message": message,
               "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
    if branch:
        payload["branch"] = branch
    if sha:
        payload["sha"] = sha
    qpath = urllib.parse.quote(path, safe="/")
    return _request("PUT", f"/repos/{repo}/contents/{qpath}", body=payload)


def delete_file(path: str, message: str, sha: str = None, branch: str = None, repo: str = None):
    """Delete `path`. Blob sha required; fetched automatically if omitted."""
    repo = _repo(repo)
    if sha is None:
        existing = get_file_contents(path, ref=branch, repo=repo)
        if isinstance(existing, dict):
            sha = existing.get("sha")
    payload: dict = {"message": message, "sha": sha}
    if branch:
        payload["branch"] = branch
    qpath = urllib.parse.quote(path, safe="/")
    return _request("DELETE", f"/repos/{repo}/contents/{qpath}", body=payload)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
# Ordered registry of shell-exposed functions (name -> callable).
COMMANDS = {fn.__name__: fn for fn in (
    get_authenticated_user, get_rate_limit, get_repo,
    create_github_issue, get_github_issue, list_github_issues,
    update_github_issue, close_github_issue, reopen_github_issue,
    pin_github_issue, unpin_github_issue,
    add_comment_to_github_issue, list_github_issue_comments, list_repo_issue_comments,
    update_github_issue_comment, delete_github_issue_comment,
    list_labels, create_label, delete_label,
    add_labels_to_github_issue, remove_label_from_github_issue, set_labels_on_github_issue,
    add_assignees_to_github_issue, remove_assignees_from_github_issue,
    create_pull_request, get_pull_request, list_pull_requests,
    update_pull_request, merge_pull_request,
    get_file_contents, create_or_update_file, delete_file,
)}


def _sig_str(fn):
    parts = []
    for p in inspect.signature(fn).parameters.values():
        parts.append(p.name if p.default is inspect.Parameter.empty
                     else f"[{p.name}={p.default!r}]")
    return " ".join(parts)


def _print_commands():
    print("Functions (python github_functions.py <name> [--arg value ...]):\n")
    for name, fn in COMMANDS.items():
        print(f"  {name} {_sig_str(fn)}")


def _coerce(value, annotation):
    if annotation in (list, dict):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            if annotation is list:
                # bare scalar (e.g. `--labels draft`) → single-element list
                return [value]
            raise
    if annotation is int:
        return int(value)
    if annotation is bool:
        return str(value).lower() in ("1", "true", "yes", "on")
    return value


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_commands()
        return 0
    cmd, rest = argv[0], argv[1:]
    fn = COMMANDS.get(cmd)
    if fn is None:
        print(f"ERROR: unknown function {cmd!r}\n", file=sys.stderr)
        _print_commands()
        return 2

    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if rest and rest[0] in ("-h", "--help"):
        print(f"{cmd} {_sig_str(fn)}")
        return 0

    positionals, flags = [], {}
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("--"):
            key = tok[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                flags[k] = v
            elif i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                flags[key] = rest[i + 1]
                i += 1
            else:
                flags[key] = "true"  # bare flag => boolean true
        else:
            positionals.append(tok)
        i += 1

    by_name = {p.name: p for p in params}
    kwargs = {}
    for idx, val in enumerate(positionals):
        if idx >= len(params):
            print(f"ERROR: too many positional args for {cmd}", file=sys.stderr)
            return 2
        p = params[idx]
        kwargs[p.name] = _coerce(val, p.annotation)
    for k, v in flags.items():
        if k not in by_name:
            print(f"ERROR: {cmd} has no parameter --{k}", file=sys.stderr)
            return 2
        kwargs[k] = _coerce(v, by_name[k].annotation)

    missing = [p.name for p in params
               if p.default is inspect.Parameter.empty and p.name not in kwargs]
    if missing:
        print(f"ERROR: {cmd} missing required: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        result = fn(**kwargs)
    except GitHubError as e:
        print(json.dumps({"error": e.message, "status": e.status,
                          "response": e.response}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result if result is not None else {"ok": True}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
