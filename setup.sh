#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$ROOT_DIR/config"
INSTALL_DIR="/opt/altoids"
VENV_DIR="$INSTALL_DIR/.venv"

echo "Installing Altoids dependencies"
sudo apt-get update
sudo apt-get install -y \
  git \
  network-manager \
  python3-pip \
  python3-venv \
  tmux \
  bluez \
  python3-gi \
  python3-gi-cairo

sudo mkdir -p "$INSTALL_DIR"
sudo cp -R "$ROOT_DIR"/. "$INSTALL_DIR"/
sudo python3 -m venv "$VENV_DIR"
sudo "$VENV_DIR/bin/pip" install --upgrade pip
sudo "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

sudo mkdir -p /var/lib/altoids/tmux
sudo cp "$CONFIG_DIR/tmux.conf" /etc/tmux.conf
sudo cp "$CONFIG_DIR/altoids.service" /etc/systemd/system/altoids.service

sudo systemctl daemon-reload
sudo systemctl enable altoids.service

echo "Setup complete. Review overlayfs and raspi-config steps manually on the Pi."
