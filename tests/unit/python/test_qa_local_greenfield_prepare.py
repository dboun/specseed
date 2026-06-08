"""QA local_greenfield prepare script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_prepare():
    path = Path(__file__).resolve().parents[3] / "qa" / "local_greenfield" / "prepare.py"
    spec = importlib.util.spec_from_file_location("qa_local_greenfield_prepare", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_codex_config_sets_every_runner_function_to_gpt_5_4_mini() -> None:
    prepare = _load_prepare()
    cfg = prepare.codex_config()
    runner = cfg["runner"]

    assert set(runner) == set(prepare.RUNNER_FUNCTIONS)
    for chain in runner.values():
        assert chain == [
            {
                "provider": "codex",
                "provider_data_dir": "~/.codex",
                "model": "gpt-5.4-mini",
                "effort": "medium",
            }
        ]


def test_config_is_json_serializable() -> None:
    prepare = _load_prepare()
    assert json.loads(json.dumps(prepare.codex_config())) == prepare.codex_config()


def test_main_initializes_target_git_before_configure(tmp_path, monkeypatch) -> None:
    prepare = _load_prepare()
    calls = []

    monkeypatch.setattr(prepare, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(prepare, "run", lambda cmd, *, cwd: calls.append((cmd, cwd)))

    assert prepare.main() == 0

    assert calls[0][0] == ["git", "init", "-q"]
    assert calls[0][1].name == "repo"
    assert calls[1][0][1] == "configure"
    assert calls[2][0][1] == "add"
    assert not list((tmp_path / ".playground").glob("*/configuration.codex.json"))
