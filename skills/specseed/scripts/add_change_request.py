"""
add_change_request.py — file a spec CHANGE REQUEST locally, out of band.

A change request (`CR-NNNN`) is NOT a work item: it does not satisfy a requirement, it
MUTATES the spec and regenerates work via `adapt`. This is the local twin of filing a
`change-request`-labelled issue on the remote (Phase 4) — it scaffolds the CR folder so
the runner picks it up, pauses sprint work, and drives the change-request conductor.

There is NO assemble/render chain here (a CR is not work — nothing to assemble). The
runner reconcile + respec mode do the rest.

Usage (flags optional — you're prompted for any required field you omit):
  python .specseed/scripts/add_change_request.py \
      --title "Switch auth to OAuth" \
      --desc "Drop password login; users sign in with Google/GitHub."

Stdlib only. Human-run (not part of the agent loop).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))
import change_requests as crmod  # noqa: E402

PRIORITIES = ("urgent",)  # phase 1: only urgent is meaningful; flag kept for forward-compat


def prompt(label, default=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"{label}{suffix}: ").strip()
        if not val and default is not None:
            return default
        if not val:
            print("  (required)")
            continue
        return val


def main(argv=None):
    p = argparse.ArgumentParser(description="file a spec change request (CR-NNNN)")
    p.add_argument("--title")
    p.add_argument("--desc", default=None, help="the request body (what should change)")
    p.add_argument("--priority", default="urgent",
                   help="phase 1: only 'urgent' is meaningful (kept for forward-compat)")
    args = p.parse_args(argv)

    try:
        root = crmod.find_root()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    title = args.title or prompt("Title")
    desc = args.desc if args.desc is not None else prompt("Request (what should change)", default=title)

    cr_id = crmod.create_cr(root, title, desc, priority=args.priority)
    path = crmod.cr_dir(root) / cr_id / "cr.md"
    print(f"Created {cr_id} ({args.priority}) → {path}")
    print("The runner will pick it up, pause sprint work, and start a conversation on "
          "its issue. Respond there (or re-run the conductor) when it asks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
