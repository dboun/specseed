"""configure.py — pure config-builder helpers + the non-interactive CLI paths.

No agent calls, no tokens, no input() in the assertions (one interactive path is
exercised by monkeypatching builtins.input with a scripted answer list)."""

import builtins
import importlib.util
import json
from pathlib import Path

import pytest

from conftest import SCRIPTS

import config as cfgmod


def _load_configure():
    spec = importlib.util.spec_from_file_location("configure", SCRIPTS / "configure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


configure = _load_configure()


def _read(root):
    return json.loads((root / ".specseed" / "memory" / "config.json").read_text())


# --------------------------------------------------------------------------- #
# coerce
# --------------------------------------------------------------------------- #
def test_coerce_types():
    assert configure.coerce("true") is True
    assert configure.coerce("False") is False
    assert configure.coerce("null") is None
    assert configure.coerce("45") == 45
    assert configure.coerce("1.5") == 1.5
    assert configure.coerce('["a","b"]') == ["a", "b"]
    assert configure.coerce("hard") == "hard"


# --------------------------------------------------------------------------- #
# apply_set
# --------------------------------------------------------------------------- #
def test_apply_set_nested():
    cfg = {"git": {"push": "user"}}
    configure.apply_set(cfg, "git.push", "auto")
    assert cfg["git"]["push"] == "auto"


def test_apply_set_creates_path():
    cfg = {}
    configure.apply_set(cfg, "hitl.categories.network", "block")
    assert cfg["hitl"]["categories"]["network"] == "block"


def test_apply_set_rejects_scalar_intermediate():
    cfg = {"git": "notadict"}
    with pytest.raises(ValueError):
        configure.apply_set(cfg, "git.push", "auto")


# --------------------------------------------------------------------------- #
# --defaults
# --------------------------------------------------------------------------- #
def test_defaults_writes_valid_config(tmp_path):
    rc = configure.main(["--defaults", "--root", str(tmp_path)])
    assert rc == 0
    cfg = _read(tmp_path)
    assert cfgmod.validate(cfg) == []
    assert cfg["backend"]["enabled"] is False


# --------------------------------------------------------------------------- #
# --set
# --------------------------------------------------------------------------- #
def test_set_overrides_and_validates(tmp_path):
    rc = configure.main(["--set", "git.push=auto",
                         "--set", "hitl.categories.network=block",
                         "--set", "review.auto_approve.min_confidence=80",
                         "--root", str(tmp_path)])
    assert rc == 0
    cfg = _read(tmp_path)
    assert cfg["git"]["push"] == "auto"
    assert cfg["hitl"]["categories"]["network"] == "block"
    assert cfg["review"]["auto_approve"]["min_confidence"] == 80
    assert cfgmod.validate(cfg) == []


def test_set_preserves_existing_untouched_keys(tmp_path):
    configure.main(["--set", "git.push=auto", "--root", str(tmp_path)])
    configure.main(["--set", "qa.mode=all", "--root", str(tmp_path)])
    cfg = _read(tmp_path)
    assert cfg["git"]["push"] == "auto"   # survived the second run
    assert cfg["qa"]["mode"] == "all"


def test_invalid_set_writes_nothing(tmp_path):
    rc = configure.main(["--set", "git.push=bogus", "--root", str(tmp_path)])
    assert rc == 2
    assert not (tmp_path / ".specseed" / "memory" / "config.json").exists()


# --------------------------------------------------------------------------- #
# mirror → remote.json
# --------------------------------------------------------------------------- #
def test_mirror_writes_remote_state(tmp_path):
    rc = configure.main(["--set", "backend.enabled=true",
                         "--set", "backend.provider=github",
                         "--root", str(tmp_path)])
    assert rc == 0
    rp = tmp_path / ".specseed" / "memory" / "remote.json"
    assert rp.exists()
    state = json.loads(rp.read_text())
    assert "allowlist" in state and "initialized" in state


def test_local_only_writes_no_remote(tmp_path):
    configure.main(["--defaults", "--root", str(tmp_path)])
    assert not (tmp_path / ".specseed" / "memory" / "remote.json").exists()


# --------------------------------------------------------------------------- #
# --show
# --------------------------------------------------------------------------- #
def test_show_prints_json_writes_nothing(tmp_path, capsys):
    rc = configure.main(["--show", "--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "backend" in parsed
    assert not (tmp_path / ".specseed" / "memory" / "config.json").exists()


# --------------------------------------------------------------------------- #
# one interactive path (scripted input)
# --------------------------------------------------------------------------- #
def test_interactive_local_defaults(tmp_path, monkeypatch):
    # Enter-through everything: local-only, default git, default gates, default
    # review/qa/agents/cr, skip advanced, then "write".
    answers = iter([""] * 40)
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    rc = configure.main(["--root", str(tmp_path)])
    assert rc == 0
    cfg = _read(tmp_path)
    assert cfgmod.validate(cfg) == []
    assert cfg["backend"]["enabled"] is False


def test_interactive_codex_only(tmp_path, monkeypatch):
    # Codex-only must NOT leave Claude defaults anywhere, and effort must be a bare
    # string. Force the model cache empty so the path is machine-independent.
    monkeypatch.setattr(cfgmod, "list_codex_models", lambda *a, **k: [])
    monkeypatch.setattr(cfgmod, "codex_reasoning_levels", lambda *a, **k: [])
    answers = iter(["",          # local only? Y
                    "",          # git keep default? Y
                    "",          # gates accept? Y
                    "",          # review enable? Y
                    "", "",      # scope, min_confidence
                    "",          # qa
                    "n",         # use Claude? -> no
                    "y",         # use Codex? -> yes
                    "",          # one codex model for everything? Y
                    "gpt-5.5",   # codex slug
                    "high",      # effort
                    "",          # cr? (fresh default Y)
                    "",          # advanced? N
                    "",          # write? Y
                    ])
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    rc = configure.main(["--root", str(tmp_path)])
    assert rc == 0
    cfg = _read(tmp_path)
    agents = cfg["runner"]["agents"]
    for fn in ("implement", "review", "qa", "respec"):
        for diff in ("easy", "hard"):
            spec = agents[fn][diff][0]
            assert spec["provider"] == "codex"
            assert spec["model"] == "gpt-5.5"
            assert spec["effort"] == "high"          # bare string, not a dict
    assert cfgmod.validate(cfg) == []


def test_interactive_cr_defaults_on_for_fresh(tmp_path, monkeypatch):
    answers = iter([""] * 40)
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    configure.main(["--root", str(tmp_path)])
    assert _read(tmp_path)["cr"]["enabled"] is True


def test_interactive_toggle_push(tmp_path, monkeypatch):
    # local-only (Enter), git: don't-keep-default(n) → touch git(Enter=Y) →
    # branch(Enter=dev) → push auto? y → automerge Enter(Y) → pr Enter(N),
    # then Enter through the rest, then write(Enter=Y).
    answers = iter(["",          # local only? Y
                    "n",         # keep default git? -> no
                    "",          # let agent touch git? Y
                    "",          # integration branch -> dev
                    "y",         # push automatically? yes
                    "",          # auto-merge? Y
                    "",          # PR? N
                    ] + [""] * 30)
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    rc = configure.main(["--root", str(tmp_path)])
    assert rc == 0
    assert _read(tmp_path)["git"]["push"] == "auto"
