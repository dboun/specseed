# E2E Python Tests

- Opt-in only, slow: run `python3 -m pytest tests/e2e/python -m e2e`. Never in the default suite.
- Always mark with `pytest.mark.e2e`.
- User-perspective only: drive `src/specseed` CLI subprocesses + `TrackingRemoteLocal`
  (the remote_local user seam). Never import/poke runtime internals, DBs, or queue
  files directly from the test.
- No real agents, no tokens. Agent runs go through a scripted CLI stand-in put first
  on PATH (see `greenfield/data/fake_agent/claude`); it follows the real skill
  contract (plan.json + apply.py + enqueue).
- No real GitHub/GitLab. Remote stays `TrackingRemoteLocal`.
- One scenario = one subdir: `test_<scenario>.py` + `data/` with everything it needs.
  Target repos are copied from `data/` into `tmp_path`; nothing runs inside the
  engine repo tree.
- On wait timeouts, dump the listener stdout + `storage/platform.log` +
  `storage/fake_agent.log` tails into the failure message.
- If an e2e run exposes a runtime bug, fix the runtime (with unit tests) rather than
  bending the scenario around it.
