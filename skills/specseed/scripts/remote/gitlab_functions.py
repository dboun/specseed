"""
gitlab_functions.py — thin stdlib-only wrapper over the GitLab REST API (v4).

Sibling of github_functions.py, same shape (LIBRARY + auto-generated CLI), but
mapped onto GitLab's vocabulary. Works against gitlab.com AND self-hosted
instances (the API root is derived from the repo URL's host).

Dual use:
  * LIBRARY  — `import gitlab_functions as gl; gl.create_gitlab_issue(...)`
               Other Python code should IMPORT and call these directly — do not
               shell out to the CLI from Python.
  * CLI      — `python gitlab_functions.py <function> [--arg value ...]`
               For agents / the shell; auto-generated from the signatures.

Near-1:1 projection of the API primitives we need (issues, notes/comments,
labels, assignees, merge requests, branches, files) — NOT workflow logic.

Terminology note (GitHub -> GitLab), since specseed reasons in GitHub-ish terms:
  pull request -> merge request   |   comment -> note   |   "number" -> iid
  close/reopen via state_event     |   list state is "opened" (not "open")
  assignees are user IDs (these functions accept usernames and resolve them)

--------------------------------------------------------------------------------
Token scopes required (project access token / PAT)
--------------------------------------------------------------------------------
  api             : REQUIRED. GitLab has no per-feature scopes — issues, merge
                    requests, notes, labels, assignees AND repository files all
                    sit behind `api` (read+write). This one scope == GitHub's
                    Issues:RW + Pull requests:RW + Contents:RW combined.
  write_repository: only if you also do raw `git clone/push` over HTTPS (these
                    functions use the Files API, so `api` alone suffices).
  Project role    : Developer (create/manage issues+MRs, push, merge unprotected
                    branches). Maintainer only for protected-branch merges.

--------------------------------------------------------------------------------
Environment
--------------------------------------------------------------------------------
  GITLAB_PAT      : the token. REQUIRED.
  GITLAB_REPO     : default target project. Accepts a full URL or clone form on
                    ANY host (self-hosted supported), or a bare namespace path:
                      https://git.example.com/group/sub/proj(.git)
                      git@git.example.com:group/sub/proj.git
                      group/sub/proj            (host then taken from GITLAB_URL
                                                 or defaults to gitlab.com)
  GITLAB_URL      : optional base URL override, e.g. https://git.example.com
                    (otherwise the host is taken from GITLAB_REPO).

Read from the process environment first; a `.env` found by searching the working
directory upward fills in only unset vars (real environment always wins).

CLI conventions & exit codes mirror github_functions.py.
Exit: 0 OK; 1 GitLab/API error (JSON on stderr); 2 bad CLI usage.
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

API_SUFFIX = "/api/v4"
USER_AGENT = "specseed-gitlab-functions"
TOKEN_ENV = "GITLAB_PAT"
REPO_ENV = "GITLAB_REPO"
URL_ENV = "GITLAB_URL"

_dotenv_loaded = False


class GitLabError(Exception):
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
        raise GitLabError(0, f"{TOKEN_ENV} not set (env or .env)")
    return tok


def _split_repo(raw):
    """(scheme, host, path) from any GitLab repo reference. host may be None."""
    s = raw.strip()
    scheme, host = "https", None
    if s.startswith("git@"):                       # git@host:group/proj.git
        host, _, path = s[4:].partition(":")
    else:
        m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://(.+)$", s)
        if m:
            scheme = m.group(1)
            host, _, path = m.group(2).partition("/")
        else:
            path = s                               # bare namespace/project
    if path.endswith(".git"):
        path = path[:-4]
    return scheme, host, path.strip("/")


def _resolve(repo=None):
    """-> (api_root, url_encoded_project_path). Honors self-hosted hosts."""
    if not repo:
        _load_dotenv()
        repo = os.environ.get(REPO_ENV)
    if not repo:
        raise GitLabError(0, f"project not given; pass repo= or set {REPO_ENV}")
    scheme, host, path = _split_repo(repo)
    base = os.environ.get(URL_ENV)
    if base:
        api_root = base.rstrip("/") + API_SUFFIX
    elif host:
        api_root = f"{scheme}://{host}{API_SUFFIX}"
    else:
        api_root = "https://gitlab.com" + API_SUFFIX
    if not path or "/" not in path:
        raise GitLabError(0, f"could not resolve 'group/project' from {repo!r}")
    return api_root, urllib.parse.quote(path, safe="")


def _raw_request(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "PRIVATE-TOKEN": _token(),
        "Accept": "application/json",
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
        msg = None
        if isinstance(parsed, dict):
            msg = parsed.get("message") or parsed.get("error")
        raise GitLabError(e.code, _stringify(msg) or e.reason or "HTTP error",
                          parsed) from None
    except urllib.error.URLError as e:
        raise GitLabError(0, f"connection error: {e.reason}") from None


def _stringify(msg):
    if msg is None or isinstance(msg, str):
        return msg
    return json.dumps(msg)


def _request(api_root, method, path, params=None, body=None):
    url = api_root + path
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


def _paginate(api_root, path, params=None):
    p = {k: v for k, v in (params or {}).items() if v is not None}
    p.setdefault("per_page", 100)
    url = api_root + path + ("&" if "?" in path else "?") + urllib.parse.urlencode(p)
    items = []
    while url:
        _, headers, parsed = _raw_request("GET", url)
        if isinstance(parsed, list):
            items.extend(parsed)
        elif parsed is not None:
            items.append(parsed)
        url = _next_link(headers.get("Link"))
    return items


def _csv(values):
    return ",".join(str(v) for v in values) if values else None


# --------------------------------------------------------------------------- #
# meta / auth / users
# --------------------------------------------------------------------------- #
def get_authenticated_user():
    """Identity behind GITLAB_PAT (cheap token/connectivity check)."""
    api, _ = _resolve()
    return _request(api, "GET", "/user")


def get_gitlab_version():
    """Instance version (GitLab has no rate-limit endpoint; this is the ping)."""
    api, _ = _resolve()
    return _request(api, "GET", "/version")


def get_project(repo: str = None):
    """Project metadata (includes default_branch)."""
    api, proj = _resolve(repo)
    return _request(api, "GET", f"/projects/{proj}")


def get_user_id(username: str, repo: str = None):
    """Resolve a username to its numeric user id (GitLab assigns by id)."""
    api, _ = _resolve(repo)
    hits = _request(api, "GET", "/users", params={"username": username})
    if not hits:
        raise GitLabError(404, f"no user named {username!r}")
    return hits[0]["id"]


def _assignee_ids(usernames, repo):
    return [get_user_id(u, repo=repo) for u in (usernames or [])]


# --------------------------------------------------------------------------- #
# issues  (identified by iid, the per-project number)
# --------------------------------------------------------------------------- #
def create_gitlab_issue(title: str, description: str = None, labels: list = None,
                        assignee_usernames: list = None, repo: str = None):
    api, proj = _resolve(repo)
    payload: dict = {"title": title}
    if description is not None:
        payload["description"] = description
    if labels:
        payload["labels"] = _csv(labels)
    if assignee_usernames:
        payload["assignee_ids"] = _assignee_ids(assignee_usernames, repo)
    return _request(api, "POST", f"/projects/{proj}/issues", body=payload)


def get_gitlab_issue(iid: int, repo: str = None):
    api, proj = _resolve(repo)
    return _request(api, "GET", f"/projects/{proj}/issues/{iid}")


def list_gitlab_issues(state: str = "opened", labels: str = None,
                       assignee_username: str = None, search: str = None,
                       repo: str = None):
    """All issues (auto-paginated). state in opened|closed|all.
    `labels` is a comma-separated string filter."""
    api, proj = _resolve(repo)
    return _paginate(api, f"/projects/{proj}/issues", {
        "state": state, "labels": labels,
        "assignee_username": assignee_username, "search": search,
    })


def update_gitlab_issue(iid: int, title: str = None, description: str = None,
                        labels: list = None, add_labels: list = None,
                        remove_labels: list = None, state_event: str = None,
                        assignee_usernames: list = None, repo: str = None):
    """Patch any subset. state_event in close|reopen. `labels` replaces the set;
    add_labels/remove_labels are incremental."""
    api, proj = _resolve(repo)
    payload: dict = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if labels is not None:
        payload["labels"] = _csv(labels)
    if add_labels:
        payload["add_labels"] = _csv(add_labels)
    if remove_labels:
        payload["remove_labels"] = _csv(remove_labels)
    if state_event is not None:
        payload["state_event"] = state_event
    if assignee_usernames is not None:
        payload["assignee_ids"] = _assignee_ids(assignee_usernames, repo)
    return _request(api, "PUT", f"/projects/{proj}/issues/{iid}", body=payload)


def close_gitlab_issue(iid: int, repo: str = None):
    return update_gitlab_issue(iid, state_event="close", repo=repo)


def reopen_gitlab_issue(iid: int, repo: str = None):
    return update_gitlab_issue(iid, state_event="reopen", repo=repo)


# --------------------------------------------------------------------------- #
# labels
# --------------------------------------------------------------------------- #
def list_labels(repo: str = None):
    api, proj = _resolve(repo)
    return _paginate(api, f"/projects/{proj}/labels")


def create_label(name: str, color: str = None, description: str = None, repo: str = None):
    """color is a hex like '#6f42c1' (a leading # is added if missing) or a CSS name."""
    api, proj = _resolve(repo)
    payload: dict = {"name": name, "color": _hexcolor(color) if color else "#808080"}
    if description is not None:
        payload["description"] = description
    return _request(api, "POST", f"/projects/{proj}/labels", body=payload)


def _hexcolor(c):
    return c if c.startswith("#") or not re.fullmatch(r"[0-9a-fA-F]{6}", c) else "#" + c


def delete_label(name: str, repo: str = None):
    api, proj = _resolve(repo)
    return _request(api, "DELETE",
                    f"/projects/{proj}/labels/{urllib.parse.quote(name, safe='')}")


def add_labels_to_gitlab_issue(iid: int, labels: list, repo: str = None):
    return update_gitlab_issue(iid, add_labels=labels, repo=repo)


def remove_label_from_gitlab_issue(iid: int, label: str, repo: str = None):
    return update_gitlab_issue(iid, remove_labels=[label], repo=repo)


def set_labels_on_gitlab_issue(iid: int, labels: list, repo: str = None):
    """Replace the full label set."""
    return update_gitlab_issue(iid, labels=labels, repo=repo)


# --------------------------------------------------------------------------- #
# assignees  (GitLab sets the whole assignee_ids list; add/remove read-modify-write)
# --------------------------------------------------------------------------- #
def add_assignees_to_gitlab_issue(iid: int, assignee_usernames: list, repo: str = None):
    api, proj = _resolve(repo)
    cur = {a["id"] for a in (get_gitlab_issue(iid, repo=repo).get("assignees") or [])}
    cur |= set(_assignee_ids(assignee_usernames, repo))
    return _request(api, "PUT", f"/projects/{proj}/issues/{iid}",
                    body={"assignee_ids": sorted(cur)})


def remove_assignees_from_gitlab_issue(iid: int, assignee_usernames: list, repo: str = None):
    api, proj = _resolve(repo)
    cur = {a["id"] for a in (get_gitlab_issue(iid, repo=repo).get("assignees") or [])}
    cur -= set(_assignee_ids(assignee_usernames, repo))
    return _request(api, "PUT", f"/projects/{proj}/issues/{iid}",
                    body={"assignee_ids": sorted(cur) or [0]})  # [0] clears in CE


# --------------------------------------------------------------------------- #
# notes (comments) — issues
# --------------------------------------------------------------------------- #
def add_comment_to_gitlab_issue(iid: int, body: str, repo: str = None):
    api, proj = _resolve(repo)
    return _request(api, "POST", f"/projects/{proj}/issues/{iid}/notes",
                    body={"body": body})


def list_gitlab_issue_comments(iid: int, repo: str = None):
    api, proj = _resolve(repo)
    return _paginate(api, f"/projects/{proj}/issues/{iid}/notes")


def update_gitlab_issue_comment(iid: int, note_id: int, body: str, repo: str = None):
    """GitLab note updates need BOTH the issue iid and the note id."""
    api, proj = _resolve(repo)
    return _request(api, "PUT", f"/projects/{proj}/issues/{iid}/notes/{note_id}",
                    body={"body": body})


def delete_gitlab_issue_comment(iid: int, note_id: int, repo: str = None):
    api, proj = _resolve(repo)
    return _request(api, "DELETE", f"/projects/{proj}/issues/{iid}/notes/{note_id}")


def list_project_events(action: str = None, after: str = None,
                        target_type: str = None, repo: str = None):
    """Recent project activity — the inbound-feedback poll (no webhook needed).
    action e.g. 'commented'; after is an ISO date 'YYYY-MM-DD'. Comment events
    carry the note body, so this answers 'did anyone comment since X'."""
    api, proj = _resolve(repo)
    return _paginate(api, f"/projects/{proj}/events",
                     {"action": action, "after": after, "target_type": target_type})


# --------------------------------------------------------------------------- #
# merge requests  (GitHub pull requests)
# --------------------------------------------------------------------------- #
def create_merge_request(title: str, source_branch: str, target_branch: str,
                         description: str = None, draft: bool = False, repo: str = None):
    api, proj = _resolve(repo)
    payload: dict = {"title": ("Draft: " + title) if draft else title,
                     "source_branch": source_branch, "target_branch": target_branch}
    if description is not None:
        payload["description"] = description
    return _request(api, "POST", f"/projects/{proj}/merge_requests", body=payload)


def get_merge_request(iid: int, repo: str = None):
    api, proj = _resolve(repo)
    return _request(api, "GET", f"/projects/{proj}/merge_requests/{iid}")


def list_merge_requests(state: str = "opened", source_branch: str = None,
                        target_branch: str = None, repo: str = None):
    """state in opened|closed|merged|all (auto-paginated)."""
    api, proj = _resolve(repo)
    return _paginate(api, f"/projects/{proj}/merge_requests", {
        "state": state, "source_branch": source_branch, "target_branch": target_branch,
    })


def update_merge_request(iid: int, title: str = None, description: str = None,
                         state_event: str = None, target_branch: str = None,
                         repo: str = None):
    """state_event in close|reopen."""
    api, proj = _resolve(repo)
    payload: dict = {}
    for k, v in (("title", title), ("description", description),
                 ("state_event", state_event), ("target_branch", target_branch)):
        if v is not None:
            payload[k] = v
    return _request(api, "PUT", f"/projects/{proj}/merge_requests/{iid}", body=payload)


def merge_merge_request(iid: int, squash: bool = False, merge_commit_message: str = None,
                        squash_commit_message: str = None, repo: str = None):
    api, proj = _resolve(repo)
    payload: dict = {"squash": squash}
    if merge_commit_message is not None:
        payload["merge_commit_message"] = merge_commit_message
    if squash_commit_message is not None:
        payload["squash_commit_message"] = squash_commit_message
    return _request(api, "PUT", f"/projects/{proj}/merge_requests/{iid}/merge", body=payload)


def add_comment_to_merge_request(iid: int, body: str, repo: str = None):
    api, proj = _resolve(repo)
    return _request(api, "POST", f"/projects/{proj}/merge_requests/{iid}/notes",
                    body={"body": body})


def list_merge_request_notes(iid: int, repo: str = None):
    api, proj = _resolve(repo)
    return _paginate(api, f"/projects/{proj}/merge_requests/{iid}/notes")


# --------------------------------------------------------------------------- #
# branches  (first-class in GitLab — used to back merge requests)
# --------------------------------------------------------------------------- #
def list_branches(repo: str = None):
    api, proj = _resolve(repo)
    return _paginate(api, f"/projects/{proj}/repository/branches")


def create_branch(branch: str, ref: str, repo: str = None):
    """Create `branch` from `ref` (an existing branch/tag/sha)."""
    api, proj = _resolve(repo)
    return _request(api, "POST", f"/projects/{proj}/repository/branches",
                    body={"branch": branch, "ref": ref})


def delete_branch(branch: str, repo: str = None):
    api, proj = _resolve(repo)
    return _request(api, "DELETE",
                    f"/projects/{proj}/repository/branches/"
                    f"{urllib.parse.quote(branch, safe='')}")


# --------------------------------------------------------------------------- #
# repository files
# --------------------------------------------------------------------------- #
def _default_branch(repo):
    return get_project(repo).get("default_branch") or "main"


def get_file_contents(path: str, ref: str = None, repo: str = None):
    """File at `path` on `ref` (default branch if omitted). Adds `decoded_content`
    (utf-8) when decodable."""
    api, proj = _resolve(repo)
    ref = ref or _default_branch(repo)
    qpath = urllib.parse.quote(path, safe="")
    result = _request(api, "GET", f"/projects/{proj}/repository/files/{qpath}",
                      params={"ref": ref})
    if isinstance(result, dict) and result.get("encoding") == "base64":
        try:
            result["decoded_content"] = base64.b64decode(result["content"]).decode("utf-8")
        except Exception:
            pass
    return result


def create_or_update_file(path: str, content: str, commit_message: str,
                          branch: str = None, repo: str = None):
    """Write `content` (plain text) to `path`. POSTs if the file is new, PUTs if
    it exists (GitLab splits create vs update)."""
    api, proj = _resolve(repo)
    branch = branch or _default_branch(repo)
    qpath = urllib.parse.quote(path, safe="")
    exists = True
    try:
        get_file_contents(path, ref=branch, repo=repo)
    except GitLabError as e:
        if e.status == 404:
            exists = False
        else:
            raise
    method = "PUT" if exists else "POST"
    return _request(api, method, f"/projects/{proj}/repository/files/{qpath}",
                    body={"branch": branch, "content": content,
                          "commit_message": commit_message})


def delete_file(path: str, commit_message: str, branch: str = None, repo: str = None):
    api, proj = _resolve(repo)
    branch = branch or _default_branch(repo)
    qpath = urllib.parse.quote(path, safe="")
    return _request(api, "DELETE", f"/projects/{proj}/repository/files/{qpath}",
                    body={"branch": branch, "commit_message": commit_message})


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
COMMANDS = {fn.__name__: fn for fn in (
    get_authenticated_user, get_gitlab_version, get_project, get_user_id,
    create_gitlab_issue, get_gitlab_issue, list_gitlab_issues,
    update_gitlab_issue, close_gitlab_issue, reopen_gitlab_issue,
    list_labels, create_label, delete_label,
    add_labels_to_gitlab_issue, remove_label_from_gitlab_issue, set_labels_on_gitlab_issue,
    add_assignees_to_gitlab_issue, remove_assignees_from_gitlab_issue,
    add_comment_to_gitlab_issue, list_gitlab_issue_comments,
    update_gitlab_issue_comment, delete_gitlab_issue_comment, list_project_events,
    create_merge_request, get_merge_request, list_merge_requests,
    update_merge_request, merge_merge_request,
    add_comment_to_merge_request, list_merge_request_notes,
    list_branches, create_branch, delete_branch,
    get_file_contents, create_or_update_file, delete_file,
)}


def _sig_str(fn):
    parts = []
    for p in inspect.signature(fn).parameters.values():
        parts.append(p.name if p.default is inspect.Parameter.empty
                     else f"[{p.name}={p.default!r}]")
    return " ".join(parts)


def _print_commands():
    print("Functions (python gitlab_functions.py <name> [--arg value ...]):\n")
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
                flags[key] = "true"
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
    except GitLabError as e:
        print(json.dumps({"error": e.message, "status": e.status,
                          "response": e.response}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result if result is not None else {"ok": True}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
