#!/usr/bin/env bash
set -euo pipefail

show_help() {
  echo "Usage: $(basename "$0") [OPTIONS]"
  echo ""
  echo "Install the specseed skill for Claude and Codex."
  echo ""
  echo "Options:"
  echo "  --claude-dir DIR   Specify a custom config directory for Claude (overrides CLAUDE_CONFIG_DIR)"
  echo "  --codex-dir DIR    Specify a custom config directory for Codex (overrides CODEX_HOME)"
  echo "  -h, --help         Show this help message and exit"
  echo ""
  echo "Environment variables CLAUDE_CONFIG_DIR and CODEX_HOME are also respected if flags are not provided."
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --claude-dir)
      export CLAUDE_CONFIG_DIR="$2"
      shift 2
      ;;
    --codex-dir)
      export CODEX_HOME="$2"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      show_help >&2
      exit 1
      ;;
  esac
done

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
if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
  claude_parent="${CLAUDE_CONFIG_DIR}/skills"
  mkdir -p "${claude_parent}"
fi

codex_parent="${HOME}/.agents/skills"
if [[ -n "${CODEX_HOME:-}" ]]; then
  codex_parent="${CODEX_HOME}/skills"
  mkdir -p "${codex_parent}"
fi

install_skill "Claude" "${claude_parent}"
install_skill "Codex" "${codex_parent}"
