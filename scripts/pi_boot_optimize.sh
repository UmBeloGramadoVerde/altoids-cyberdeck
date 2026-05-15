#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/etc/systemd/system/altoids-boot-backup"
SYSTEMD_DIR="/etc/systemd/system"
TARGET_DIR="/usr/lib/systemd/system"

usage() {
  cat <<'USAGE'
Usage:
  sudo scripts/pi_boot_optimize.sh --apply
  sudo scripts/pi_boot_optimize.sh --rollback

This script avoids systemctl. It edits systemd symlinks directly and keeps
backups under /etc/systemd/system/altoids-boot-backup.
USAGE
}

require_root() {
  if [[ "$(id -u)" != "0" ]]; then
    echo "Run this from a visible terminal with sudo so password prompts are visible." >&2
    exit 1
  fi
}

backup_path_for() {
  local path="$1"
  printf '%s/%s' "$BACKUP_DIR" "${path#/etc/systemd/system/}"
}

move_link_to_backup() {
  local path="$1"
  [[ -L "$path" ]] || return 0
  local backup
  backup="$(backup_path_for "$path")"
  mkdir -p "$(dirname "$backup")"
  if [[ ! -e "$backup" ]]; then
    mv "$path" "$backup"
  else
    rm "$path"
  fi
  echo "disabled $(realpath -m --relative-to="$SYSTEMD_DIR" "$path")"
}

restore_backup_link() {
  local backup="$1"
  [[ -L "$backup" ]] || return 0
  local path="$SYSTEMD_DIR/${backup#$BACKUP_DIR/}"
  mkdir -p "$(dirname "$path")"
  if [[ ! -e "$path" ]]; then
    mv "$backup" "$path"
    echo "restored $(realpath -m --relative-to="$SYSTEMD_DIR" "$path")"
  fi
}

apply_changes() {
  require_root
  mkdir -p "$BACKUP_DIR"

  if [[ -L "$SYSTEMD_DIR/default.target" && ! -e "$BACKUP_DIR/default.target" ]]; then
    mv "$SYSTEMD_DIR/default.target" "$BACKUP_DIR/default.target"
  fi
  ln -sfn "$TARGET_DIR/multi-user.target" "$SYSTEMD_DIR/default.target"
  echo "default target -> multi-user.target"

  move_link_to_backup "$SYSTEMD_DIR/graphical.target.wants/accounts-daemon.service"
  move_link_to_backup "$SYSTEMD_DIR/graphical.target.wants/udisks2.service"
  move_link_to_backup "$SYSTEMD_DIR/display-manager.service"

  move_link_to_backup "$SYSTEMD_DIR/multi-user.target.wants/cups.path"
  move_link_to_backup "$SYSTEMD_DIR/sockets.target.wants/cups.socket"
  move_link_to_backup "$SYSTEMD_DIR/printer.target.wants/cups.service"

  move_link_to_backup "$SYSTEMD_DIR/multi-user.target.wants/avahi-daemon.service"
  move_link_to_backup "$SYSTEMD_DIR/sockets.target.wants/avahi-daemon.socket"
  move_link_to_backup "$SYSTEMD_DIR/dbus-org.freedesktop.Avahi.service"

  move_link_to_backup "$SYSTEMD_DIR/multi-user.target.wants/nfs-client.target"
  move_link_to_backup "$SYSTEMD_DIR/remote-fs.target.wants/nfs-client.target"
  move_link_to_backup "$SYSTEMD_DIR/nfs-client.target.wants/nfs-blkmap.service"
  move_link_to_backup "$SYSTEMD_DIR/multi-user.target.wants/rpcbind.service"
  move_link_to_backup "$SYSTEMD_DIR/sockets.target.wants/rpcbind.socket"

  move_link_to_backup "$SYSTEMD_DIR/network-online.target.wants/NetworkManager-wait-online.service"

  move_link_to_backup "$SYSTEMD_DIR/cloud-init.target.wants/cloud-init-main.service"
  move_link_to_backup "$SYSTEMD_DIR/cloud-init.target.wants/cloud-init-local.service"
  move_link_to_backup "$SYSTEMD_DIR/cloud-init.target.wants/cloud-init-network.service"
  move_link_to_backup "$SYSTEMD_DIR/cloud-init.target.wants/cloud-config.service"
  move_link_to_backup "$SYSTEMD_DIR/cloud-init.target.wants/cloud-final.service"
  move_link_to_backup "$SYSTEMD_DIR/cloud-config.target.wants/cloud-init-hotplugd.socket"

  echo "Done. Reboot to measure boot impact."
}

rollback_changes() {
  require_root
  if [[ -L "$BACKUP_DIR/default.target" ]]; then
    rm -f "$SYSTEMD_DIR/default.target"
    mv "$BACKUP_DIR/default.target" "$SYSTEMD_DIR/default.target"
    echo "restored default.target"
  fi
  if [[ -d "$BACKUP_DIR" ]]; then
    while IFS= read -r backup; do
      restore_backup_link "$backup"
    done < <(find "$BACKUP_DIR" -type l | sort)
  fi
  echo "Rollback complete. Reboot to verify."
}

case "${1:-}" in
  --apply)
    apply_changes
    ;;
  --rollback)
    rollback_changes
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
