#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="specseed"
SOURCE_DIR="${SCRIPT_DIR}/skills/${SKILL_NAME}"

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "Missing source skill directory: ${SOURCE_DIR}" >&2
  exit 1
fi

install_skill() {
  local label="$1"
  local parent_dir="$2"
  local target_dir="${parent_dir}/${SKILL_NAME}"
  local tmp_dir

  if [[ ! -d "${parent_dir}" ]]; then
    echo "Skipped ${label}: ${parent_dir} does not exist"
    return 0
  fi

  tmp_dir="$(mktemp -d "${parent_dir}/.${SKILL_NAME}.XXXXXX")"
  cp -R "${SOURCE_DIR}/." "${tmp_dir}/"
  rm -rf "${target_dir}"
  mv "${tmp_dir}" "${target_dir}"
  echo "Installed ${label}: ${target_dir}"
}

claude_parent="${HOME}/.claude/skills"
if [[ -n "${CLAUDE_CONFIG_DIR:-}" && -d "${CLAUDE_CONFIG_DIR}/skills" ]]; then
  claude_parent="${CLAUDE_CONFIG_DIR}/skills"
fi

codex_parent="${HOME}/.agents/skills"
if [[ -n "${CODEX_HOME:-}" && -d "${CODEX_HOME}/skills" ]]; then
  codex_parent="${CODEX_HOME}/skills"
fi

install_skill "Claude" "${claude_parent}"
install_skill "Codex" "${codex_parent}"
