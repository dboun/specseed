"""
remote_sync.py — the local<->remote mirror engine (see references/remote.md).

Local `.specseed/` is ground truth. This pushes the work layer onto github/gitlab
issues and pulls back only the honored remote inputs: brand-new issues (bugs) and
allowlisted comments handled by remote_control.py (CONTROL commands, work-issue gate
verbs, and work-issue inbox notes).

Commands:
  init                 create the 4 dashboards (pin 3), seed labels, write config,
                       push current work. Idempotent / self-healing.
  reconcile            one full pass: pull new issues -> drift+heal -> push.
  push                 local -> remote only (no pull).
  dashboards           re-render ROADMAP / TIMELINE / SPRINT issues only.

Flags: --dry-run (print intended mutations, change nothing).

Provider-agnostic via remote_config.Remote. Stdlib only.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import remote_config as rc

# the CR entity lives in core/; reuse its pure I/O (folders = truth)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import change_requests as crmod  # noqa: E402

PM = lambda root: rc.find_root(root) / ".specseed" / "project_management"
SPEC = lambda root: rc.find_root(root) / ".specseed" / "spec"

# --------------------------------------------------------------------------- #
# change-request (CR) intake + relay constants
# --------------------------------------------------------------------------- #
CR_LABEL = "change-request"           # the intake label a CR issue carries
# Bot comments on a CR issue are tagged with this (invisible) marker so the
# comment-ingest never feeds the agent's own replies back as a user turn — the PAT
# owner is usually allowlisted, so an author check alone would not catch them.
CR_BOT_MARKER = "<!-- specseed:cr -->"
DEFAULT_IGNORE_LABELS = {
    "draft", "ignore", "specseed:ignore", "changes-requested",
    "needs-more-info", "needs-triage",
}


# --------------------------------------------------------------------------- #
# local read helpers
# --------------------------------------------------------------------------- #
def _load_json(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def split_md(text):
    """(frontmatter_dict, body_str) from a `--- ... ---` md file."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        try:
            fm[k.strip()] = json.loads(v)
        except Exception:
            fm[k.strip()] = v
    return fm, m.group(2)


def _entity_file(root, tier, eid):
    return PM(root) / f"{tier}s" / eid / f"{eid}.md"


def _body_of(root, tier, eid):
    p = _entity_file(root, tier, eid)
    if not p.exists():
        return ""
    _, body = split_md(p.read_text(encoding="utf-8"))
    return body.strip()


def read_entities(root):
    """Unified {id: entity} across epics/tickets/issues. Folders are truth; the
    assembled JSONs supply derived/runtime fields."""
    ents = {}
    tickets = _load_json(PM(root) / "tickets.json")
    issues = _load_json(PM(root) / "issues.json")

    # epics: folders only (no epics.json)
    epics_dir = PM(root) / "epics"
    if epics_dir.is_dir():
        for d in sorted(epics_dir.iterdir()):
            f = d / f"{d.name}.md"
            if not f.exists():
                continue
            fm, _ = split_md(f.read_text(encoding="utf-8"))
            ents[d.name] = {
                "id": d.name, "tier": "epic", "title": fm.get("title", d.name),
                "status": fm.get("status", "todo"), "tickets": fm.get("tickets", []),
            }
    for tid, t in tickets.items():
        ents[tid] = {
            "id": tid, "tier": "ticket", "title": t.get("title", tid),
            "status": t.get("status", "todo"), "epic": t.get("epic"),
            "issues": t.get("issues", []), "depends_on": t.get("depends_on", []),
            "sprint": t.get("sprint"),
        }
    for iid, i in issues.items():
        ents[iid] = {
            "id": iid, "tier": "issue", "title": i.get("title", iid),
            "status": i.get("status", "todo"), "ticket": i.get("ticket"),
            "claimed_by": i.get("claimed_by"),
        }
    return ents


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _num(cfg, eid):
    v = cfg["map"].get(eid)
    return v["n"] if isinstance(v, dict) else v


def _iid(cfg, num):
    """Reverse of `_num`: remote issue number -> local entity id (None if unmapped).
    Built from the same issue-map; lets the repo-wide comment channel route a comment
    landing on a WORK issue back to its local id."""
    if num is None:
        return None
    for eid, v in (cfg.get("map") or {}).items():
        n = v["n"] if isinstance(v, dict) else v
        if n == num:
            return eid
    return None


def _ref(cfg, eid, ents):
    n = _num(cfg, eid)
    title = ents.get(eid, {}).get("title", "")
    label = f"{eid} {title}".strip()
    return f"{label} (#{n})" if n else label


def render_labels(e):
    out = [f"tier:{e['tier']}", f"status:{e['status']}"]
    if e.get("sprint"):
        out.append(f"sprint:{e['sprint']}")
    return out


def render_title(e):
    return f"[{e['id']}] {e['title']}"


def render_body(root, cfg, e, ents):
    prose = _body_of(root, e["tier"], e["id"])
    lines = [prose, "", "---"]
    if e["tier"] == "epic" and e.get("tickets"):
        lines.append("- **Tickets:** " + " · ".join(_ref(cfg, t, ents) for t in e["tickets"]))
    if e["tier"] == "ticket":
        if e.get("epic"):
            lines.append("- **Epic:** " + _ref(cfg, e["epic"], ents))
        if e.get("issues"):
            lines.append("- **Issues:** " + " · ".join(_ref(cfg, i, ents) for i in e["issues"]))
        if e.get("depends_on"):
            lines.append("- **Depends on:** " + " · ".join(_ref(cfg, d, ents) for d in e["depends_on"]))
        if e.get("sprint"):
            lines.append(f"- **Sprint:** {e['sprint']}")
    if e["tier"] == "issue" and e.get("ticket"):
        lines.append("- **Ticket:** " + _ref(cfg, e["ticket"], ents))
    lines.append(f"- specseed-id: {e['id']}")
    return "\n".join(lines).strip()


def _sig(title, state, labels, body):
    h = hashlib.sha1()
    h.update("".join([title, state, ",".join(sorted(labels)), body]).encode())
    return h.hexdigest()


def _desired_state(e):
    return "closed" if e["status"] in rc.TERMINAL else "open"


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #
REVERT_MSG = ("⚠️ Reverted a manual edit. This issue mirrors local "
              "`{id}` (specseed is the source of truth). Change it via the pinned "
              "**CONTROL** issue (`adapt …`) or locally — not by editing here.")


def _set_map(cfg, eid, n, sig):
    cfg["map"][eid] = {"n": n, "sig": sig}


def sync_push(root, cfg, remote, dry=False, log=print):
    ents = read_entities(root)

    # pass 1: ensure every entity has a github issue (so links resolve in pass 2)
    for eid, e in ents.items():
        if _num(cfg, eid) is None:
            if dry:
                log(f"[dry] create {eid}")
                _set_map(cfg, eid, f"NEW:{eid}", None)
                continue
            created = remote.create_issue(render_title(e), body=f"specseed-id: {eid}",
                                          labels=[f"tier:{e['tier']}"])
            _set_map(cfg, eid, created["number"], None)
            log(f"create {eid} -> #{created['number']}")

    # pass 2: render full body + labels + state; apply drift/heal logic
    for eid, e in ents.items():
        n = _num(cfg, eid)
        if isinstance(n, str):  # dry-run placeholder
            continue
        title, labels = render_title(e), render_labels(e)
        body, state = render_body(root, cfg, e, ents), _desired_state(e)
        desired_sig = _sig(title, state, labels, body)
        stored = cfg["map"][eid].get("sig") if isinstance(cfg["map"][eid], dict) else None

        cur = remote.get_issue(n)
        if cur is None:  # mapped issue deleted on remote -> deprecate locally, drop map
            log(f"{eid}: remote #{n} gone -> mark deprecated locally")
            if not dry:
                _mark_local_status(root, e, "deprecated")
                cfg["map"].pop(eid, None)
            continue
        remote_sig = _sig(cur["title"], cur["state"], cur["labels"], cur["body"])

        if dry:
            if desired_sig != stored or remote_sig != stored:
                log(f"[dry] update {eid} (#{n})")
            continue

        if desired_sig != stored:                 # local changed -> push, no comment
            _apply(remote, n, cur, title, body, labels, state, e)
            _set_map(cfg, eid, n, desired_sig)
        elif remote_sig != stored:                 # manual remote edit -> revert + comment
            _apply(remote, n, cur, title, body, labels, state, e)
            remote.comment(n, REVERT_MSG.format(id=eid))
            _set_map(cfg, eid, n, desired_sig)
            log(f"{eid}: reverted manual edit on #{n}")
        # else: in sync, nothing to do
    return cfg


def _apply(remote, n, cur, title, body, labels, state, e):
    assignees = [e["claimed_by"]] if e.get("claimed_by") else None
    remote.update_issue(n, title=title, body=body, assignees=assignees)
    if sorted(cur["labels"]) != sorted(labels):
        remote.set_labels(n, labels)
    if cur["state"] != state:
        if state == "closed":
            remote.close_issue(n, planned=(e["status"] == "done"))
        else:
            remote.reopen_issue(n)


def _mark_local_status(root, e, status):
    """Write status into the entity's folder frontmatter (best-effort)."""
    p = _entity_file(root, e["tier"], e["id"])
    if not p.exists():
        return
    txt = p.read_text(encoding="utf-8")
    new = re.sub(r"(?m)^status:.*$", f"status: {status}", txt, count=1)
    p.write_text(new, encoding="utf-8")


# --------------------------------------------------------------------------- #
# pull (ingest brand-new issues = the one allowed remote-origin action)
# --------------------------------------------------------------------------- #
def _known_numbers(cfg):
    nums = {v["n"] if isinstance(v, dict) else v for v in cfg["map"].values()}
    nums |= {v for v in cfg["permanent"].values() if v}
    return nums


def _next_id(root, prefix):
    mx = 0
    d = PM(root) / ("tickets" if prefix == "PROJ" else "issues")
    if d.is_dir():
        for f in d.iterdir():
            m = re.match(rf"{prefix}-(\d+)$", f.name)
            if m:
                mx = max(mx, int(m.group(1)))
    return f"{prefix}-{mx + 1:04d}"


def is_cr_issue(iss):
    """A brand-new issue is a CHANGE REQUEST (not a bug) iff it carries the
    `change-request` intake label (decision #3). Pure — `iss` is a normalized issue
    dict (labels already a list of strings)."""
    return CR_LABEL in (iss.get("labels") or [])


def ignored_by_label(iss, cfg=None):
    """True when an unknown remote issue carries a draft/ignore label. Applied before
    bug/feature/CR intake so phone drafts stay remote-only until the label is removed."""
    labels = set(iss.get("labels") or [])
    ignore_source = cfg.get("ignore_labels") if cfg and "ignore_labels" in cfg \
        else DEFAULT_IGNORE_LABELS
    ignore = set(ignore_source or [])
    return bool(labels & ignore)


def sync_pull(root, cfg, remote, dry=False, log=print):
    known = _known_numbers(cfg)
    cursor = cfg.get("pull_cursor")
    ingested = []
    for iss in remote.list_open_issues():
        n = iss["number"]
        if n in known:
            continue
        if "specseed-id:" in (iss["body"] or ""):   # ours, map lost -> re-link, don't ingest
            sid = re.search(r"specseed-id:\s*(\S+)", iss["body"]).group(1)
            if not dry:
                _set_map(cfg, sid, n, None)
            log(f"re-linked #{n} -> {sid}")
            continue
        created = (iss.get("raw") or {}).get("created_at", "")
        if cursor and created and created <= cursor:
            continue
        if ignored_by_label(iss, cfg):
            log(f"skip #{n}: ignored by label")
            continue
        if is_cr_issue(iss):                          # CR, NOT a bug — feed adapt, not work
            cr_id = _ingest_cr_issue(root, cfg, remote, iss, dry, log)
            if cr_id:
                ingested.append((n, cr_id))
            continue
        ticket_id, issue_id = _next_id(root, "PROJ"), _next_id(root, "BUG")
        log(f"ingest #{n} -> {ticket_id} / {issue_id} (draft)")
        if dry:
            continue
        _write_bug(root, ticket_id, issue_id, iss)
        _set_map(cfg, ticket_id, n, None)            # the github issue mirrors the ticket
        remote.comment(n, f"Ingested as **{ticket_id}** (draft `{issue_id}`). The agent "
                          f"will refine + slot it into the plan. Track it via the pinned "
                          f"ROADMAP / CONTROL issues.")
        ingested.append((n, ticket_id))
    if ingested and not dry:
        cfg["pull_cursor"] = rc.now_iso()
    return ingested


def _ingest_cr_issue(root, cfg, remote, iss, dry, log):
    """A new `change-request`-labeled issue → a local CR-NNNN (ground truth) mapped to
    this issue. NO ticket/issue is created — an approved CR drives `adapt`. Returns the
    new CR id (None on dry-run)."""
    n = iss["number"]
    title = re.sub(r"^\[[^\]]+\]\s*", "", iss["title"] or "Untitled")
    body = (iss.get("body") or "").strip()
    log(f"ingest #{n} -> change request (label `{CR_LABEL}`)")
    if dry:
        return None
    cr_id = crmod.create_cr(root, title, body, remote_issue=n)
    crmod.advance_cursor(root, cr_id, rc.now_iso())   # ignore comments predating intake
    _set_map(cfg, cr_id, n, None)                      # so reconcile never re-ingests it
    remote.comment(n, _bot(
        f"Filed as **{cr_id}**. Sprint work pauses while we work this. Reply here with "
        f"answers; comment **I approve** to regenerate the spec, or **reject** to drop it."))
    return cr_id


def _write_md(path, fm, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {json.dumps(v) if v is None or isinstance(v, (list, dict, bool)) else v}")
    lines += ["---", "", body.strip(), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_bug(root, ticket_id, issue_id, iss):
    title = re.sub(r"^\[[^\]]+\]\s*", "", iss["title"] or "Untitled")
    body = (iss.get("body") or "").strip()
    _write_md(PM(root) / "tickets" / ticket_id / f"{ticket_id}.md", {
        "id": ticket_id, "title": title, "epic": None, "type": "bug",
        "priority": "medium", "status": "todo", "approval_required": False,
        "depends_on": [], "satisfies_reqs": [], "issues": [issue_id], "sprint": None,
    }, f"## Description\nFrom remote issue. NEEDS TRIAGE.\n\n{body}\n\n"
       f"## Acceptance criteria\n- TODO (agent to define)")
    _write_md(PM(root) / "issues" / issue_id / f"{issue_id}.md", {
        "id": issue_id, "title": title, "ticket": ticket_id, "type": "bug",
        "component": "unknown", "effort_hours": 1, "depends_on": [], "status": "todo",
        "review_required": False, "approval_required": False, "claimed_at": None,
        "claimed_by": None, "artifacts": {"touches": [], "tests": [], "migrations": []},
        "notes": "ingested from remote; triage needed",
    }, f"## Acceptance criteria\n- TODO (agent to define from the report)\n\n## Notes\n{body}")


# --------------------------------------------------------------------------- #
# change requests: comment relay (input) + reply (output) + state reflection
# --------------------------------------------------------------------------- #
def _bot(body):
    """Tag a comment as bot-authored so comment-ingest skips it."""
    return f"{CR_BOT_MARKER}\n\n{body}"


def is_bot_comment(body):
    return (body or "").lstrip().startswith(CR_BOT_MARKER)


def cr_status_label(status):
    """CR status → the issue label that lets a phone filter the inbox. respec_complete
    is still 'in progress' to a human, so it shares cr:open."""
    return {"open": "cr:open", "respec_complete": "cr:open",
            "done": "cr:done", "rejected": "cr:rejected"}.get(status, "cr:open")


def pick_cr_comments(comments, cursor, allowlist):
    """Pure: from one CR issue's comments (normalized {author, body, created_at}),
    return (new_texts, new_cursor):
      - chronological, strictly after `cursor`
      - authored by an allowlisted user (empty allowlist = accept any author here; the
        caller resolves owner-only)
      - skipping our own bot comments
    `new_cursor` is the high-water mark over ALL comments seen (so the cursor advances
    past bot/own comments too, never re-reading them)."""
    picked, newest = [], cursor
    for c in sorted(comments, key=lambda x: x.get("created_at") or ""):
        ts = c.get("created_at") or ""
        newest = max(newest or "", ts)
        if cursor and ts and ts <= cursor:
            continue
        if is_bot_comment(c.get("body") or ""):
            continue
        if allowlist and c.get("author") not in allowlist:
            continue
        body = (c.get("body") or "").strip()
        if body:
            picked.append(body)
    return picked, newest


def _resolve_allowlist(cfg, remote):
    """The CONTROL allowlist, or [owner] when empty (owner-only). [] only if owner
    lookup fails — then pick_cr_comments accepts any author (degraded, logged upstream)."""
    allow = cfg.get("allowlist") or []
    if allow:
        return allow
    try:
        who = remote.whoami()
        owner = who.get("login") or who.get("username")
        return [owner] if owner else []
    except Exception:
        return []


def sync_cr_comments(root, cfg, remote, dry=False, log=print):
    """INPUT side of the relay: for each live CR, pull new allowlisted comments on its
    issue, stash them for the runner's relay turn, flip `turn: agent`, advance the
    per-CR `comment_cursor`. Network read; the decision is in pick_cr_comments (tested).
    Returns the list of CR ids that got a new comment this pass."""
    allow = _resolve_allowlist(cfg, remote)
    touched = []
    for slim in crmod.list_crs(root):
        if slim.get("status") in ("done", "rejected"):
            continue
        n = slim.get("remote_issue")
        if not n:
            continue
        cursor = slim.get("comment_cursor")
        try:
            comments = [c for c in remote.comments_since(cursor)
                        if c.get("issue_number") == n]
        except Exception as e:
            log(f"CR {slim['id']}: comment poll failed: {e}")
            continue
        texts, newest = pick_cr_comments(comments, cursor, allow)
        if newest and newest != cursor and not dry:
            crmod.advance_cursor(root, slim["id"], newest)
        if not texts:
            continue
        log(f"CR {slim['id']}: {len(texts)} new comment(s) on #{n}")
        if dry:
            continue
        crmod.append_pending_comment(root, slim["id"], "\n\n".join(texts))
        crmod.set_turn(root, slim["id"], "agent")
        touched.append(slim["id"])
    return touched


def post_cr_reply(root, cfg, remote, cr_id, body, log=print):
    """OUTPUT side: post the conductor's reply as a (bot-tagged) comment on the CR's
    own issue. No-op when the CR has no remote issue (local-only CR)."""
    try:
        cr = crmod.load_cr(root, cr_id)
    except Exception as e:
        log(f"CR {cr_id}: reply skipped, load failed: {e}")
        return False
    n = cr.get("remote_issue")
    if not n or not (body or "").strip():
        return False
    try:
        remote.comment(n, _bot(body.strip()))
        return True
    except Exception as e:
        log(f"CR {cr_id}: reply post failed: {e}")
        return False


def reflect_cr_state(root, cfg, remote, dry=False, log=print):
    """Mirror each CR's status onto its issue (label + open/closed), and heal an issue a
    human closed while the CR is still live. Cheap, best-effort — never blocks the loop."""
    for slim in crmod.list_crs(root):
        n = slim.get("remote_issue")
        if not n:
            continue
        status = slim.get("status")
        want_closed = status in ("done", "rejected")
        if dry:
            log(f"[dry] CR {slim['id']} -> {cr_status_label(status)}"
                f"{' (close)' if want_closed else ''}")
            continue
        cur = remote.get_issue(n)
        if cur is None:
            continue
        want_labels = sorted({CR_LABEL, cr_status_label(status)})
        if sorted(cur.get("labels") or []) != want_labels:
            try:
                remote.set_labels(n, want_labels)
            except Exception as e:
                log(f"CR {slim['id']}: label set failed: {e}")
        state = cur.get("state")
        if want_closed and state == "open":
            try:
                remote.close_issue(n, planned=(status == "done"))
            except Exception as e:
                log(f"CR {slim['id']}: close failed: {e}")
        elif not want_closed and state == "closed":      # heal a hand-closed live CR
            try:
                remote.reopen_issue(n)
                remote.comment(n, _bot("This CR is still in progress. Reply here to "
                                       "continue, or comment **reject** to drop it."))
                log(f"CR {slim['id']}: reopened (#{n}) — still live")
            except Exception as e:
                log(f"CR {slim['id']}: reopen failed: {e}")


def reconcile_crs(root, cfg, remote, dry=False, log=print):
    """One CR reconcile pass: ingest new comments (input) then reflect status (output).
    Called from the runner's reconcile block BEFORE cr_step, so a freshly-ingested
    comment flips `turn: agent` in time for this pass's relay."""
    sync_cr_comments(root, cfg, remote, dry=dry, log=log)
    reflect_cr_state(root, cfg, remote, dry=dry, log=log)


# --------------------------------------------------------------------------- #
# dashboards
# --------------------------------------------------------------------------- #
CONTROL_TOPPOST = """\
# CONTROL — command channel

Comment one of these verbs (allowlisted users only). The agent replies here.

| verb | does |
|------|------|
| `status` | runner state, active sprint, in-flight issue, ready count |
| `sync` | force a remote↔local sync now |
| `pause` | finish current issue, then idle |
| `resume` | resume picking up work |
| `kill` | stop the running agent immediately |
| `claim-next` | claim + run the next ready issue |
| `adapt <text>` | update the spec per `<text>` (runs adapt mode) |
| `plan-next` | spec + break down the next roadmap slice |
| `approvals` | list pending HITL approval gates (each with its `APR-NNNN`) |
| `approve <APR-NNNN> [opt]` | approve a parked gate |
| `reject <APR-NNNN> <note>` | reject a parked gate with a reason |
| `hold <APR-NNNN>` | defer a gate (parks it `blocked`) |
| `crs` | list open spec-change requests + their state |

**Spec changes:** open a NEW issue labeled `change-request` to file one. The agent files
it as `CR-NNNN`, pauses sprint work, and converses on THAT issue's thread — answer there,
comment **I approve** to regenerate the spec, or **reject** to drop it.

When an issue needs your OK, the agent posts a `🔔 Needs your approval` comment on that
issue (with its `APR-NNNN`). Resolve it EITHER here (`approve APR-NNNN`) OR right on that
issue — comment `approve` / `reject <note>` / `hold` there; the `APR-NNNN` is optional
when the issue has a single open gate.

**Talk to an issue:** any OTHER comment on a work issue (not a verb) is a free-form
instruction or question — "add more comments", "why did you do X?", "don't do it that
way". The agent reads the issue + the real code and replies on that issue. Boundaries: a
spec/scope change → it asks you to file a `change-request`; brand-new work → `add_work`.

Local control (no phone): `echo pause > .specseed/memory/runner.ctl` (or `run` /
`stop`). Stop = graceful (finishes current, then exits). This issue is permanent —
don't close it.
"""


def _read_text(p):
    return p.read_text(encoding="utf-8") if p.exists() else "_not generated yet_"


def render_sprint_dashboard(root):
    sprints = _load_json(PM(root) / "sprints.json")
    tickets = _load_json(PM(root) / "tickets.json")
    active = [s for s in sprints.values() if s.get("status") == "in_progress"]
    if not active:
        return "# Current sprint\n\n_No sprint in progress._"
    out = []
    for s in active:
        out.append(f"# Sprint {s.get('id')} — {s.get('title', '')}")
        out.append(f"_status: {s.get('status')} · {s.get('starts','?')} → {s.get('ends','?')}_\n")
        for tid in s.get("tickets", []):
            t = tickets.get(tid, {})
            out.append(f"- {tid} {t.get('title','')} — {t.get('status','?')} "
                       f"({t.get('issues_done',0)}/{t.get('issues_total',0)})")
    return "\n".join(out)


def push_dashboards(root, cfg, remote, dry=False, log=print):
    pm = PM(root)
    content = {
        "roadmap": _read_text(pm / "ROADMAP.md"),
        "timeline": _read_text(pm / "TIMELINE.md"),
        "sprint": render_sprint_dashboard(root),
    }
    titles = {"roadmap": "📍 ROADMAP", "timeline": "🗓️ TIMELINE", "sprint": "🏃 Current sprint"}
    for key, body in content.items():
        n = cfg["permanent"].get(key)
        if dry:
            log(f"[dry] dashboard {key} -> #{n}")
            continue
        if n:
            remote.update_issue(n, body=body)
        log(f"dashboard {key} updated (#{n})")


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
def ensure_labels(remote, dry, log, extra_labels=None):
    names = list(rc.LABEL_COLORS)
    for name in extra_labels or []:
        if name not in names:
            names.append(name)
    for name in names:
        if dry:
            log(f"[dry] label {name}")
        else:
            remote.ensure_label(name)


def init(root, cfg, remote, dry=False, log=print):
    ensure_labels(remote, dry, log, cfg.get("ignore_labels"))
    cfg["labels_seeded"] = True
    specs = {
        "roadmap": ("📍 ROADMAP", _read_text(PM(root) / "ROADMAP.md")),
        "timeline": ("🗓️ TIMELINE", _read_text(PM(root) / "TIMELINE.md")),
        "control": ("⌨️ CONTROL", CONTROL_TOPPOST),
        "sprint": ("🏃 Current sprint", render_sprint_dashboard(root)),
    }
    for key, (title, body) in specs.items():
        if cfg["permanent"].get(key):
            continue
        if dry:
            log(f"[dry] create permanent {key}")
            continue
        created = remote.create_issue(title, body=body)
        cfg["permanent"][key] = created["number"]
        log(f"permanent {key} -> #{created['number']}")
    # pin the 3 (sprint not pinned; GitHub caps pins at 3)
    for key in ("roadmap", "timeline", "control"):
        n = cfg["permanent"].get(key)
        if n and not dry:
            try:
                remote.pin(n)
            except Exception as e:
                log(f"pin {key} skipped: {e}")
    if not dry:
        cfg["pull_cursor"] = rc.now_iso()
        cfg["cli_cursor"] = rc.now_iso()
        cfg["cli_cursor_ids"] = []
    sync_push(root, cfg, remote, dry=dry, log=log)
    if not dry:
        cfg["initialized"] = True
    return cfg


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv):
    ap = argparse.ArgumentParser(description="local<->remote mirror engine")
    ap.add_argument("command", choices=["init", "reconcile", "push", "dashboards"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = rc.find_root()
    cfg, enabled, _ = rc.load_runtime(root)
    if not enabled:
        print("mirror not enabled (backend.enabled=false in config.json)", file=sys.stderr)
        return 1
    remote = rc.Remote(cfg)
    log = print

    if args.command == "init":
        init(root, cfg, remote, dry=args.dry_run, log=log)
    elif args.command == "push":
        sync_push(root, cfg, remote, dry=args.dry_run, log=log)
        push_dashboards(root, cfg, remote, dry=args.dry_run, log=log)
    elif args.command == "dashboards":
        push_dashboards(root, cfg, remote, dry=args.dry_run, log=log)
    elif args.command == "reconcile":
        sync_pull(root, cfg, remote, dry=args.dry_run, log=log)
        reconcile_crs(root, cfg, remote, dry=args.dry_run, log=log)
        sync_push(root, cfg, remote, dry=args.dry_run, log=log)
        push_dashboards(root, cfg, remote, dry=args.dry_run, log=log)

    if not args.dry_run:
        rc.save_state(cfg, root)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
