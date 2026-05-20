#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$ROOT_DIR/config"
INSTALL_DIR="/opt/altoids"
VENV_DIR="$INSTALL_DIR/.venv"
RUNTIME_DIR="$INSTALL_DIR/runtime"
RUNTIME_BIN_DIR="$RUNTIME_DIR/bin"
TMUX_RUNTIME_CONF="$RUNTIME_DIR/tmux.conf"
SERVICE_USER="$(awk -F= '/^User=/{print $2}' "$CONFIG_DIR/altoids.service" | tail -n 1)"
SERVICE_GROUP="${SERVICE_GROUP:-$SERVICE_USER}"

if [ -z "$SERVICE_USER" ]; then
  echo "Could not determine service user from $CONFIG_DIR/altoids.service" >&2
  exit 1
fi

echo "Installing Altoids dependencies"
sudo apt-get update
sudo apt-get install -y \
  git \
  network-manager \
  alsa-utils \
  i2c-tools \
  dkms \
  libasound2-plugins \
  unzip \
  raspi-config \
  python3-pip \
  python3-venv \
  python3-spidev \
  python3-libgpiod \
  python3-numpy \
  tmux \
  bluez \
  python3-gi \
  python3-gi-cairo

sudo install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$INSTALL_DIR" "$RUNTIME_DIR" "$INSTALL_DIR/releases"
sudo install -d -m 755 "$RUNTIME_BIN_DIR"
if [ ! -f "$INSTALL_DIR/vendor/Whisplay/Driver/WhisPlay.py" ]; then
  sudo mkdir -p "$INSTALL_DIR/vendor"
  sudo git clone --depth 1 https://github.com/PiSugar/Whisplay.git "$INSTALL_DIR/vendor/Whisplay"
fi
if [ -f "$INSTALL_DIR/vendor/Whisplay/Driver/install_wm8960_drive.sh" ]; then
  echo "Installing Whisplay WM8960 audio driver"
  (
    cd "$INSTALL_DIR/vendor/Whisplay/Driver"
    printf 'y\n' | sudo bash ./install_wm8960_drive.sh
  )
fi
sudo python3 -m venv --system-site-packages "$VENV_DIR"
sudo "$VENV_DIR/bin/pip" install --upgrade pip
sudo "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"

sudo mkdir -p /var/lib/altoids/tmux
sudo install -m 644 "$CONFIG_DIR/tmux.conf" "$TMUX_RUNTIME_CONF"
sudo ln -sfn "$TMUX_RUNTIME_CONF" /etc/tmux.conf
ln -sfn "$TMUX_RUNTIME_CONF" "$HOME/.tmux.conf"
sudo install -m 755 "$CONFIG_DIR/altoids-runtime.py" "$RUNTIME_BIN_DIR/altoids-runtime"
sudo install -m 755 "$CONFIG_DIR/altoids-supervisor" "$RUNTIME_BIN_DIR/altoids-supervisor"
sudo install -m 755 "$CONFIG_DIR/altoidsctl" "$RUNTIME_BIN_DIR/altoidsctl"
sudo install -m 755 "$CONFIG_DIR/cdx" "$RUNTIME_BIN_DIR/cdx"
sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"
if [ ! -L "$INSTALL_DIR/current" ]; then
  sudo "$RUNTIME_BIN_DIR/altoidsctl" bootstrap --source "$ROOT_DIR" --release-id bootstrap
fi
sudo cp "$CONFIG_DIR/altoids.service" /etc/systemd/system/altoids.service

sudo systemctl daemon-reload
sudo systemctl enable altoids.service

echo "Setup complete. Review overlayfs and raspi-config steps manually on the Pi."
echo "Make targets: make self-test, make stage, make reload, make update, make status, make tmux-sync"
echo "If Whisplay WM8960 audio was just installed, reboot the Pi before launching Altoids."
