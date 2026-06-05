#!/usr/bin/env bash
#
# install.sh - install/uninstall the specseed engine and/or skill.
#
# The engine is NEVER copied into a target repo. It installs once, system-wide,
# under ~/.specseed and runs against targets: `specseed run --target <repo>`.
#
#   platform install (default): copy engine -> ~/.specseed, add src to PATH.
#   skill install:              copy skills/specseed -> claude + codex skills dirs.
#
# Modes:
#   (no flag)                       platform only
#   --only-skill                    skill only
#   --both-platform-and-skill       platform + skill
#   --uninstall                     remove platform + PATH line + skill copies
#
# Skill targets:
#   codex  -> ALWAYS ~/.agents/skills        (created whether or not it exists)
#   claude -> ~/.claude/skills               (default)
#          -> --custom-claude-config-dirs a,b   (comma-separated skills dirs instead)
#
# PATH line: one marked line appended to the shell rc; uninstall strips only it.
#
set -euo pipefail

INSTALL_DIR="$HOME/.specseed"
PLATFORM_SRC="$INSTALL_DIR/src"
PATH_MARKER="# specseed (managed by install.sh)"
PATH_LINE="export PATH=\"\$HOME/.specseed/src:\$PATH\"  $PATH_MARKER"
CODEX_SKILLS_DIR="$HOME/.agents/skills"
DEFAULT_CLAUDE_SKILLS_DIR="$HOME/.claude/skills"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/skills/specseed"

# --- args ---------------------------------------------------------------------
MODE="platform"           # platform | skill | both | uninstall
CUSTOM_CLAUDE_DIRS=""

usage() {
  cat <<'EOF'
install.sh - install/uninstall the specseed engine and/or skill.

Usage: install.sh [mode] [--custom-claude-config-dirs a,b]

Modes (default = platform):
  (no flag)                    platform only: engine -> ~/.specseed, src on PATH
  --only-skill                 skill only: skills/specseed -> claude + codex dirs
  --both-platform-and-skill    platform + skill
  --uninstall                  remove ~/.specseed, the PATH line, and skill copies

Skill targets:
  codex  -> ALWAYS ~/.agents/skills (created whether or not it exists)
  claude -> ~/.claude/skills, or --custom-claude-config-dirs <comma,sep,skills,dirs>
EOF
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall)                 MODE="uninstall" ;;
    --only-skill)                MODE="skill" ;;
    --both-platform-and-skill|--both-plaform-and-skill) MODE="both" ;;
    --custom-claude-config-dirs) CUSTOM_CLAUDE_DIRS="${2:-}"; shift ;;
    --custom-claude-config-dirs=*) CUSTOM_CLAUDE_DIRS="${1#*=}" ;;
    -h|--help)                   usage 0 ;;
    *) echo "install.sh: unknown arg: $1" >&2; usage 1 ;;
  esac
  shift
done

# --- os gate ------------------------------------------------------------------
case "$(uname -s)" in
  Darwin|Linux) : ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    echo "install.sh: Windows not supported yet (TODO)." >&2; exit 2 ;;
  *) echo "install.sh: unsupported OS: $(uname -s) (TODO)." >&2; exit 2 ;;
esac

# --- helpers ------------------------------------------------------------------

# expand a leading ~ to $HOME (eval-free).
expand_tilde() {
  case "$1" in
    "~") printf '%s' "$HOME" ;;
    "~/"*) printf '%s' "$HOME/${1#\~/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# copy skills/specseed -> <dir>/specseed (dir created if missing).
install_skill_into() {
  local dir; dir="$(expand_tilde "$1")"
  mkdir -p "$dir"
  rm -rf "$dir/specseed"
  cp -R "$SKILL_SRC" "$dir/specseed"
  echo "  skill -> $dir/specseed"
}

remove_skill_from() {
  local dir; dir="$(expand_tilde "$1")"
  if [ -d "$dir/specseed" ]; then
    rm -rf "$dir/specseed"
    echo "  removed $dir/specseed"
  fi
}

# claude skills dirs: custom list (comma-separated) or the single default.
claude_skill_dirs() {
  if [ -n "$CUSTOM_CLAUDE_DIRS" ]; then
    printf '%s\n' "$CUSTOM_CLAUDE_DIRS" | tr ',' '\n' | sed '/^[[:space:]]*$/d'
  else
    printf '%s\n' "$DEFAULT_CLAUDE_SKILLS_DIR"
  fi
}

# rc file to APPEND to: pick by current shell, fall back to an existing one.
target_rc() {
  case "${SHELL:-}" in
    *zsh)  printf '%s' "$HOME/.zshrc"; return ;;
    *bash) printf '%s' "$HOME/.bashrc"; return ;;
  esac
  for f in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
    [ -f "$f" ] && { printf '%s' "$f"; return; }
  done
  printf '%s' "$HOME/.profile"
}

# every rc file the marker line might live in (for clean removal).
rc_candidates() {
  printf '%s\n' "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile" "$HOME/.bash_profile"
}

add_path_line() {
  local rc; rc="$(target_rc)"
  if [ -f "$rc" ] && grep -qF "$PATH_MARKER" "$rc"; then
    echo "  PATH line already in $rc"
    return
  fi
  [ -f "$rc" ] || touch "$rc"
  printf '\n%s\n' "$PATH_LINE" >> "$rc"
  echo "  PATH line -> $rc"
}

remove_path_line() {
  local rc tmp
  rc_candidates | while IFS= read -r rc; do
    [ -f "$rc" ] || continue
    if grep -qF "$PATH_MARKER" "$rc"; then
      tmp="$(mktemp)"
      grep -vF "$PATH_MARKER" "$rc" > "$tmp"
      cat "$tmp" > "$rc"
      rm -f "$tmp"
      echo "  removed PATH line from $rc"
    fi
  done
}

# --- actions ------------------------------------------------------------------

install_platform() {
  echo "platform install -> $INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  for item in src skills version.txt; do
    if [ ! -e "$SCRIPT_DIR/$item" ]; then
      echo "install.sh: missing $SCRIPT_DIR/$item - run from the engine repo." >&2
      exit 1
    fi
    rm -rf "${INSTALL_DIR:?}/$item"
    cp -R "$SCRIPT_DIR/$item" "$INSTALL_DIR/$item"
  done
  chmod +x "$PLATFORM_SRC/specseed" 2>/dev/null || true
  add_path_line
  echo "done. open a new shell, then: specseed configure --target <repo>"
}

install_skill() {
  echo "skill install"
  install_skill_into "$CODEX_SKILLS_DIR"          # codex: always
  claude_skill_dirs | while IFS= read -r d; do    # claude: default or custom
    [ -n "$d" ] && install_skill_into "$d"
  done
}

uninstall() {
  echo "uninstall"
  if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "  removed $INSTALL_DIR"
  fi
  remove_path_line
  remove_skill_from "$CODEX_SKILLS_DIR"
  remove_skill_from "$DEFAULT_CLAUDE_SKILLS_DIR"
  claude_skill_dirs | while IFS= read -r d; do
    [ -n "$d" ] && remove_skill_from "$d"
  done
  echo "done."
}

# --- dispatch -----------------------------------------------------------------
case "$MODE" in
  platform)  install_platform ;;
  skill)     install_skill ;;
  both)      install_platform; install_skill ;;
  uninstall) uninstall ;;
esac
