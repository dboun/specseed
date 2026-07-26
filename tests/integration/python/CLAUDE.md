# Integration Python Tests

- Opt-in only: run `python3 -m pytest tests/integration/python/`.
- Always mark tests with `pytest.mark.integration`.
- No agents. No spawned LLMs. No token use.
- No real GitHub/GitLab. Use `TrackingRemoteLocal` as remote source of truth.
- Tests may use sqlite DBs and fake data under `tmp_path`.
- Treat flow as real: remote -> `TrackingLocal.sync_from_remote` -> `sync_to_db` -> `Database`.
- Prefer multi-part state/change tests over one-method unit checks.
- Scary paths: close/delete teardown, supersession, reactions, comments, labels, reopen.
- If integration test exposes runtime bug, leave test failing and report it.
- Keep generated DB files under temp dirs; cleanup should be automatic.
- Do not edit source from this directory.
