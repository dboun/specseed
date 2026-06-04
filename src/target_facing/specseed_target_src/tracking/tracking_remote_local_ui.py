"""
Tkinter UI for the remote-local specseed tracking database.

Run from the repository root:

    python3.13 src/target_facing/specseed_target_src/tracking/tracking_remote_local_ui.py

Optional:

    python src/target_facing/specseed_target_src/tracking/tracking_remote_local_ui.py --db /path/to/tracking_remote_local.db
    python3.13 src/target_facing/specseed_target_src/tracking/tracking_remote_local_ui.py --author your-name

This UI inherits the local Tkinter UI and swaps in TrackingRemoteLocal's default
database/provider. It does not write sqlite directly.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _add_repo_root_to_path() -> None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "target_facing").exists():
            sys.path.insert(0, str(parent))
            return


_add_repo_root_to_path()

from src.target_facing.specseed_target_src.tracking.tracking_local_ui import (
    TrackingLocalUI,
    parse_args,
)
from src.target_facing.specseed_target_src.tracking.tracking_remote_local import (
    DEFAULT_DB_PATH,
    TrackingRemoteLocal,
)


class TrackingRemoteLocalUI(TrackingLocalUI):
    tracker_cls = TrackingRemoteLocal
    default_db_path = DEFAULT_DB_PATH
    window_title = "Specseed Remote-Local Tracking"


def main() -> None:
    args = parse_args()
    app = TrackingRemoteLocalUI(db_path=args.db, author=args.author)
    app.mainloop()


if __name__ == "__main__":
    main()
