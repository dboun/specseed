"""config.py — schema validation, back-compat accessors, render-claude."""

import config


def test_default_config_valid():
    assert config.validate(config.default_config()) == []


def test_old_config_without_new_blocks_still_valid():
    """Back-compat: a config.json written before review/qa/runner.models existed
    must still validate (the blocks are optional; readers fill defaults)."""
    old = config.default_config()
    del old["review"]
    del old["qa"]
    old["runner"].pop("models", None)
    assert config.validate(old) == []


def test_review_config_fills_defaults_when_absent():
    rc = config.review_config({})
    assert rc["scope"] == "hard"
    assert rc["auto_approve"]["min_confidence"] == 90
    assert rc["auto_approve"]["difficulty"] == ["easy"]


def test_review_config_merges_partial_override():
    rc = config.review_config({"review": {"scope": "both",
                                           "auto_approve": {"min_confidence": 75}}})
    assert rc["scope"] == "both"
    assert rc["auto_approve"]["min_confidence"] == 75
    # difficulty default preserved through the partial merge
    assert rc["auto_approve"]["difficulty"] == ["easy"]


def test_qa_config_defaults():
    qc = config.qa_config({})
    assert qc["mode"] == "suggest"
    assert qc["enabled"] is True


def test_runner_model_role_fallback():
    cfg = {"runner": {"model": "opus", "models": {"review": "sonnet"}}}
    assert config.runner_model(cfg, "implement") == "opus"   # falls back
    assert config.runner_model(cfg, "review") == "sonnet"    # override
    assert config.runner_model(cfg, "merge") == "opus"       # falls back
    assert config.runner_model({}, "review") == "opus"       # ultimate default


def test_validate_catches_bad_values():
    bad = config.default_config()
    bad["review"]["scope"] = "weird"
    bad["qa"]["mode"] = "nope"
    bad["runner"]["models"] = {"unknown_role": "x"}
    errs = config.validate(bad)
    assert any("review.scope" in e for e in errs)
    assert any("qa.mode" in e for e in errs)
    assert any("unknown role" in e for e in errs)


def test_validate_min_confidence_range():
    bad = config.default_config()
    bad["review"]["auto_approve"]["min_confidence"] = 150
    assert any("min_confidence" in e for e in config.validate(bad))


def test_render_claude_includes_completion_gates():
    out = config.render_claude(config.default_config())
    assert "Completion gates" in out
    assert "Code review is ON" in out          # default scope=hard ⇒ on
    assert "type: qa" in out


def test_render_claude_review_off():
    cfg = config.default_config()
    cfg["review"]["enabled"] = False
    out = config.render_claude(cfg)
    assert "Code review is OFF" in out
