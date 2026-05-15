#!/usr/bin/env bash

set -euo pipefail

AGGRESSIVE=0
ASSUME_YES=0

usage() {
  cat <<'EOF'
Usage: cleanup_disk.sh [--aggressive] [--yes]

Conservative cleanup:
  - remount / read-write
  - apt clean
  - remove npm and nvm caches
  - remove stale Codex plugin backup tmp dir
  - remove downloaded Claude binary
  - truncate Codex TUI log

Aggressive cleanup (--aggressive):
  - also remove Codex history databases:
    ~/.codex/logs_2.sqlite
    ~/.codex/logs_2.sqlite-wal
    ~/.codex/logs_2.sqlite-shm

Notes:
  - This script uses sudo and may prompt for your password.
  - Close Codex before using --aggressive.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --aggressive)
      AGGRESSIVE=1
      shift
      ;;
    --yes)
      ASSUME_YES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

HOME_DIR="${HOME:-/home/kayna}"
CODEX_DIR="${HOME_DIR}/.codex"

safe_remove() {
  local path="$1"
  if [[ -e "$path" ]]; then
    echo "Removing $path"
    rm -rf -- "$path"
  else
    echo "Skipping missing path: $path"
  fi
}

safe_truncate() {
  local path="$1"
  if [[ -f "$path" ]]; then
    echo "Truncating $path"
    : > "$path"
  else
    echo "Skipping missing file: $path"
  fi
}

confirm() {
  local prompt="$1"
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    return 0
  fi

  read -r -p "$prompt [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

echo "Disk usage before cleanup:"
df -h /
echo

echo "Checking sudo access"
sudo -v

echo "Remounting / read-write"
sudo mount -o remount,rw /

echo "Cleaning apt cache"
sudo apt clean

safe_remove "${HOME_DIR}/.npm/_cacache"
safe_remove "${HOME_DIR}/.nvm/.cache"
safe_remove "${CODEX_DIR}/.tmp/plugins-backup-ZBEVsf"
safe_remove "${HOME_DIR}/.claude/downloads/claude-2.1.138-linux-arm64"
safe_truncate "${CODEX_DIR}/log/codex-tui.log"

if [[ "$AGGRESSIVE" -eq 1 ]]; then
  if confirm "Delete Codex history databases too? Make sure Codex is closed."; then
    safe_remove "${CODEX_DIR}/logs_2.sqlite"
    safe_remove "${CODEX_DIR}/logs_2.sqlite-wal"
    safe_remove "${CODEX_DIR}/logs_2.sqlite-shm"
  else
    echo "Skipping aggressive Codex history cleanup"
  fi
fi

echo
echo "Disk usage after cleanup:"
df -h /
