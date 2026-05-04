#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$ROOT_DIR/config"
INSTALL_DIR="/opt/altoids"
VENV_DIR="$INSTALL_DIR/.venv"
RUNTIME_DIR="$INSTALL_DIR/runtime"
RUNTIME_BIN_DIR="$RUNTIME_DIR/bin"

echo "Installing Altoids dependencies"
sudo apt-get update
sudo apt-get install -y \
  git \
  network-manager \
  alsa-utils \
  python3-pip \
  python3-venv \
  python3-spidev \
  python3-libgpiod \
  python3-numpy \
  tmux \
  bluez \
  python3-gi \
  python3-gi-cairo

sudo mkdir -p "$INSTALL_DIR" "$RUNTIME_BIN_DIR" "$INSTALL_DIR/releases" /var/lib/altoids/runtime
if [ ! -f "$INSTALL_DIR/vendor/Whisplay/Driver/WhisPlay.py" ]; then
  sudo mkdir -p "$INSTALL_DIR/vendor"
  sudo git clone --depth 1 https://github.com/PiSugar/Whisplay.git "$INSTALL_DIR/vendor/Whisplay"
fi
sudo python3 -m venv --system-site-packages "$VENV_DIR"
sudo "$VENV_DIR/bin/pip" install --upgrade pip
sudo "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"

sudo mkdir -p /var/lib/altoids/tmux
sudo cp "$CONFIG_DIR/tmux.conf" /etc/tmux.conf
sudo install -m 755 "$CONFIG_DIR/altoids-runtime.py" "$RUNTIME_BIN_DIR/altoids-runtime"
sudo install -m 755 "$CONFIG_DIR/altoids-supervisor" "$RUNTIME_BIN_DIR/altoids-supervisor"
sudo install -m 755 "$CONFIG_DIR/altoidsctl" "$RUNTIME_BIN_DIR/altoidsctl"
sudo install -m 755 "$CONFIG_DIR/qs" /usr/local/bin/qs
sudo install -m 755 "$CONFIG_DIR/reload" /usr/local/bin/reload
sudo install -m 755 "$CONFIG_DIR/qload" /usr/local/bin/qload
sudo install -m 755 "$CONFIG_DIR/deck-status" /usr/local/bin/deck-status
if [ ! -L "$INSTALL_DIR/current" ]; then
  sudo "$RUNTIME_BIN_DIR/altoidsctl" bootstrap --source "$ROOT_DIR" --release-id bootstrap
fi
sudo cp "$CONFIG_DIR/altoids.service" /etc/systemd/system/altoids.service

sudo systemctl daemon-reload
sudo systemctl enable altoids.service

echo "Setup complete. Review overlayfs and raspi-config steps manually on the Pi."
echo "Commands installed: qs, reload, qload, deck-status"
echo "For Whisplay audio, run PiSugar's WM8960 installer on full Raspberry Pi OS after setup."
