"""config.py — schema validation, back-compat accessors, render-claude."""

import json

import config


def test_default_config_valid():
    assert config.validate(config.default_config()) == []
    assert config.default_config()["backend"]["entity_templates"]["enabled"] is False
    assert "draft" in config.default_config()["backend"]["ignore_labels"]


def test_old_config_without_optional_blocks_still_valid():
    """Back-compat: a config.json without the optional review/qa blocks still
    validates (readers fill defaults)."""
    old = config.default_config()
    del old["review"]
    del old["qa"]
    assert config.validate(old) == []


def test_old_backend_without_entity_templates_still_valid():
    old = config.default_config()
    del old["backend"]["entity_templates"]
    assert config.validate(old) == []
    assert config.backend_config(old)["entity_templates"]["enabled"] is False
    assert config.entity_templates_config(old)["enabled"] is False


def test_old_backend_without_ignore_labels_still_valid():
    old = config.default_config()
    del old["backend"]["ignore_labels"]
    assert config.validate(old) == []
    assert "changes-requested" in config.backend_config(old)["ignore_labels"]


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


def test_agent_chain_and_main_defaults():
    cfg = config.default_config()
    # default matrix: implement/hard = opus/high, implement/easy = sonnet/medium
    assert config.agent_main(cfg, "implement", "hard")["model"] == "opus"
    assert config.agent_main(cfg, "implement", "easy")["model"] == "sonnet"
    # missing difficulty buckets to hard (conservative)
    assert config.agent_main(cfg, "implement", None)["model"] == "opus"
    assert config.agent_main(cfg, "qa", "hard")["effort"] == "high"


def test_agent_chain_fills_from_defaults_when_block_absent():
    # empty/partial config still yields a usable chain
    assert config.agent_chain({}, "review", "hard")[0]["provider"] == "claude"
    partial = {"runner": {"agents": {"implement": {}}}}
    assert config.agent_chain(partial, "implement", "easy")[0]["model"] == "sonnet"


def test_agent_chain_returns_configured_fallback_list():
    cfg = config.default_config()
    chain = [{"provider": "codex", "config_dir": None, "model": "gpt-5.5", "effort": "high"},
             {"provider": "claude", "config_dir": None, "model": "opus", "effort": "high"}]
    cfg["runner"]["agents"]["implement"]["hard"] = chain
    assert config.validate(cfg) == []
    assert [s["provider"] for s in config.agent_chain(cfg, "implement", "hard")] == \
        ["codex", "claude"]


def test_validate_catches_bad_values():
    bad = config.default_config()
    bad["review"]["scope"] = "weird"
    bad["qa"]["mode"] = "nope"
    bad["runner"]["agents"]["implement"]["easy"][0]["provider"] = "gpt"
    bad["backend"]["entity_templates"]["enabled"] = "yes"
    bad["backend"]["ignore_labels"] = ["ok", ""]
    errs = config.validate(bad)
    assert any("review.scope" in e for e in errs)
    assert any("qa.mode" in e for e in errs)
    assert any("provider must be one of" in e for e in errs)
    assert any("backend.entity_templates.enabled" in e for e in errs)
    assert any("backend.ignore_labels" in e for e in errs)


def test_validate_catches_agents_structure_errors():
    bad = config.default_config()
    bad["runner"]["agents"]["qa"]["hard"] = []          # empty chain
    del bad["runner"]["agents"]["review"]               # missing function
    bad["runner"]["agents"]["implement"]["hard"][0]["model"] = ""  # empty model
    errs = config.validate(bad)
    assert any("['qa']['hard'] must be a non-empty list" in e for e in errs)
    assert any("missing function 'review'" in e for e in errs)
    assert any("['implement']['hard'][0].model" in e for e in errs)


def test_list_codex_models_reads_cache(tmp_path):
    cache = {"models": [
        {"slug": "gpt-5.5", "visibility": "list",
         "supported_reasoning_levels": ["low", "medium", "high"]},
        {"slug": "hidden-x", "visibility": "hidden"},
        {"slug": "gpt-5.5", "visibility": "list"},        # dup → de-duped
    ]}
    (tmp_path / "models_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    assert config.list_codex_models(str(tmp_path)) == ["gpt-5.5"]
    assert config.codex_reasoning_levels("gpt-5.5", str(tmp_path)) == ["low", "medium", "high"]
    # missing cache dir → empty, no raise
    assert config.list_codex_models(str(tmp_path / "nope")) == []
    assert config.list_models("claude") == ["opus", "sonnet", "haiku"]


def test_codex_reasoning_levels_object_form(tmp_path):
    # newer caches store levels as {effort, description} objects, not bare strings
    cache = {"models": [
        {"slug": "gpt-5.5", "visibility": "list", "supported_reasoning_levels": [
            {"effort": "low", "description": "fast"},
            {"effort": "high", "description": "deep"},
            {"effort": "xhigh", "description": "deeper"},
        ]},
    ]}
    (tmp_path / "models_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    assert config.codex_reasoning_levels("gpt-5.5", str(tmp_path)) == ["low", "high", "xhigh"]


def test_validate_min_confidence_range():
    bad = config.default_config()
    bad["review"]["auto_approve"]["min_confidence"] = 150
    assert any("min_confidence" in e for e in config.validate(bad))


def test_render_claude_includes_completion_gates():
    out = config.render_claude(config.default_config())
    assert "Completion gates" in out
    assert "Code review is ON" in out          # default scope=hard ⇒ on
    assert "type: qa" in out


def test_render_claude_documents_handoff_kind():
    out = config.render_claude(config.default_config())
    assert "handoff" in out                       # in the Kind enum + the guidance para
    assert "**Handoff:**" in out                   # sidecar pointer field
    assert "**Verify:**" in out                    # optional re-check field
    assert "handoff/" in out                       # the sidecar dir convention


def test_render_claude_approval_template_has_blank_id():
    out = config.render_claude(config.default_config())
    assert "- **Id:**" in out                      # the APR-NNNN slot in the template
    assert "stamps a global APR-NNNN" in out        # the leave-blank guidance


def test_render_claude_review_off():
    cfg = config.default_config()
    cfg["review"]["enabled"] = False
    out = config.render_claude(cfg)
    assert "Code review is OFF" in out


# --------------------------------------------------------------------------- #
# respec agent function (CR conductor)
# --------------------------------------------------------------------------- #
def test_respec_function_parses_and_validates():
    cfg = config.default_config()
    assert "respec" in cfg["runner"]["agents"]
    assert config.validate(cfg) == []
    # default respec chain = opus/high in BOTH buckets (no easy/hard split)
    assert config.agent_main(cfg, "respec", "hard")["model"] == "opus"
    assert config.agent_main(cfg, "respec", "hard")["effort"] == "high"
    assert config.agent_main(cfg, "respec", "easy")["model"] == "opus"


def test_respec_absent_loads_and_gets_default_chain():
    """Back-compat: an agents matrix with no `respec` key still validates, and
    agent_chain fills the default opus/high chain (runner reads the hard bucket)."""
    old = config.default_config()
    del old["runner"]["agents"]["respec"]
    assert config.validate(old) == []
    chain = config.agent_chain(old, "respec", "hard")
    assert chain[0]["provider"] == "claude"
    assert chain[0]["model"] == "opus"


def test_respec_bad_spec_rejected():
    bad = config.default_config()
    bad["runner"]["agents"]["respec"]["hard"][0]["provider"] = "gpt"
    errs = config.validate(bad)
    assert any("['respec']['hard'][0].provider must be one of" in e for e in errs)


def test_respec_unknown_function_still_rejected():
    bad = config.default_config()
    bad["runner"]["agents"]["bogus"] = {"easy": [], "hard": []}
    errs = config.validate(bad)
    assert any("unknown function 'bogus'" in e for e in errs)


# --------------------------------------------------------------------------- #
# cr block
# --------------------------------------------------------------------------- #
def test_cr_block_defaults_and_valid():
    cfg = config.default_config()
    assert cfg["cr"] == {"enabled": False, "label": "change-request",
                         "branch_prefix": "cr/"}
    assert config.validate(cfg) == []


def test_cr_config_fills_defaults_when_absent():
    """Back-compat: a config.json with no `cr` block loads + defaults apply."""
    old = config.default_config()
    del old["cr"]
    assert config.validate(old) == []
    cc = config.cr_config(old)
    assert cc["enabled"] is False
    assert cc["label"] == "change-request"
    assert cc["branch_prefix"] == "cr/"


def test_cr_config_merges_partial_override():
    cc = config.cr_config({"cr": {"enabled": True, "label": "spec-change"}})
    assert cc["enabled"] is True
    assert cc["label"] == "spec-change"
    assert cc["branch_prefix"] == "cr/"          # default preserved


def test_validate_rejects_bad_cr_values():
    bad = config.default_config()
    bad["cr"]["enabled"] = "yes"                  # not a bool
    bad["cr"]["label"] = ""                       # empty
    bad["cr"]["branch_prefix"] = "cr"             # no trailing /
    errs = config.validate(bad)
    assert any("cr.enabled must be a boolean" in e for e in errs)
    assert any("cr.label must be a non-empty string" in e for e in errs)
    assert any("cr.branch_prefix must be a non-empty string ending with '/'" in e
               for e in errs)


def test_render_claude_cr_line_only_when_enabled():
    off = config.render_claude(config.default_config())
    assert "Spec-change requests" not in off
    cfg = config.default_config()
    cfg["cr"]["enabled"] = True
    on = config.render_claude(cfg)
    assert "Spec-change requests" in on
    assert "CR-NNNN" in on
