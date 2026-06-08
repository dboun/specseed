# QA Environments

Human QA environments live in `qa/<name>/`. Create one only when user asks.

Keep each environment updated when setup, config, runner, tracker, or UI behavior changes. Each environment has a `prepare.py` that creates a timestamped repo under root `.playground/`. Setup must not invoke agents or spend tokens.

Default QA runner config: Codex with `gpt-5.4-mini` and low effort for every runner function, unless user asks otherwise. Default tracker: local UI backed by runtime `remote_local` behavior, not GitHub/GitLab.
