"""
remote_sync.py — the local<->remote mirror engine (see references/remote.md).

Local `.specseed/` is ground truth. This pushes the work layer onto github/gitlab
issues and pulls back the ONLY two honored remote inputs: brand-new issues (bugs)
and (handled by remote_control.py) CONTROL commands.

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

PM = lambda root: rc.find_root(root) / ".specseed" / "project_management"
SPEC = lambda root: rc.find_root(root) / ".specseed" / "spec"


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


def _write_md(path, fm, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {json.dumps(v) if isinstance(v, (list, dict, bool)) else v}")
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
| `adapt <text>` | run `/specseed adapt <text>` |
| `plan-next` | run `/specseed plan-next` |
| `approvals` | list pending HITL approval gates |
| `approve <ID> [opt]` | approve a parked gate (e.g. `approve FEAT-0101 A`) |
| `reject <ID> <note>` | reject a parked gate with a reason |

When an issue needs your OK, the agent posts a `🔔 Needs your approval` comment on that
issue and waits — reply here with `approve`/`reject`.

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
def ensure_labels(remote, dry, log):
    for name in list(rc.LABEL_COLORS):
        if dry:
            log(f"[dry] label {name}")
        else:
            remote.ensure_label(name)


def init(root, cfg, remote, dry=False, log=print):
    ensure_labels(remote, dry, log)
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
    cfg = rc.load_config(root)
    if cfg is None:
        print("no remote.json — run onboarding first (mirror is off)", file=sys.stderr)
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
        sync_push(root, cfg, remote, dry=args.dry_run, log=log)
        push_dashboards(root, cfg, remote, dry=args.dry_run, log=log)

    if not args.dry_run:
        rc.save_config(cfg, root)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
