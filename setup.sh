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

# --- Enable SPI, I2C, and Bluetooth ---
echo "Enabling SPI and I2C interfaces"
sudo raspi-config nonint do_spi 0 2>/dev/null || true
sudo raspi-config nonint do_i2c 0 2>/dev/null || true
echo "Enabling Bluetooth auto-power-on"
sudo rfkill unblock bluetooth 2>/dev/null || true
sudo sed -i 's/^#AutoEnable=true/AutoEnable=true/' /etc/bluetooth/main.conf 2>/dev/null || true

# --- Install system packages ---
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

# --- Create directory structure ---
sudo install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$INSTALL_DIR" "$RUNTIME_DIR" "$INSTALL_DIR/releases"
sudo install -d -m 755 "$RUNTIME_BIN_DIR"

# --- Clone Whisplay vendor driver (for Whisplay hardware only) ---
WHISPLAY_DIR="$INSTALL_DIR/vendor/Whisplay"
if [ ! -d "$WHISPLAY_DIR" ]; then
  sudo mkdir -p "$INSTALL_DIR/vendor"
  sudo git clone --depth 1 https://github.com/PiSugar/Whisplay.git "$WHISPLAY_DIR"
fi

# --- Install WM8960 audio driver if present (Whisplay hardware only) ---
# New repo layout: install_driver.sh at root
# Old repo layout: Driver/install_wm8960_drive.sh
WM8960_INSTALLER=""
if [ -f "$WHISPLAY_DIR/install_driver.sh" ]; then
  WM8960_INSTALLER="$WHISPLAY_DIR/install_driver.sh"
elif [ -f "$WHISPLAY_DIR/Driver/install_wm8960_drive.sh" ]; then
  WM8960_INSTALLER="$WHISPLAY_DIR/Driver/install_wm8960_drive.sh"
fi
if [ -n "$WM8960_INSTALLER" ]; then
  echo "Installing Whisplay WM8960 audio driver"
  (
    cd "$(dirname "$WM8960_INSTALLER")"
    printf 'y\n' | sudo bash "$(basename "$WM8960_INSTALLER")"
  )
fi

# --- Create Python virtual environment and install packages ---
sudo python3 -m venv --system-site-packages "$VENV_DIR"
sudo "$VENV_DIR/bin/pip" install --upgrade pip
sudo "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"

# --- Copy config file if missing ---
if [ ! -f "$CONFIG_DIR/altoids.toml" ]; then
  cp "$CONFIG_DIR/altoids.example.toml" "$CONFIG_DIR/altoids.toml"
  echo "Created config/altoids.toml from example template"
fi

# --- Install runtime files ---
sudo mkdir -p /var/lib/altoids/tmux
sudo install -m 644 "$CONFIG_DIR/tmux.conf" "$TMUX_RUNTIME_CONF"
sudo ln -sfn "$TMUX_RUNTIME_CONF" /etc/tmux.conf
ln -sfn "$TMUX_RUNTIME_CONF" "$HOME/.tmux.conf"
sudo install -m 755 "$CONFIG_DIR/altoids-runtime.py" "$RUNTIME_BIN_DIR/altoids-runtime"
sudo install -m 755 "$CONFIG_DIR/altoids-supervisor" "$RUNTIME_BIN_DIR/altoids-supervisor"
sudo install -m 755 "$CONFIG_DIR/altoidsctl" "$RUNTIME_BIN_DIR/altoidsctl"
sudo install -m 755 "$CONFIG_DIR/cdx" "$RUNTIME_BIN_DIR/cdx"

# --- Bootstrap initial release if needed ---
if [ ! -L "$INSTALL_DIR/current" ]; then
  sudo "$RUNTIME_BIN_DIR/altoidsctl" bootstrap --source "$ROOT_DIR" --release-id bootstrap
fi

# --- Fix ownership (must run AFTER bootstrap so new files are included) ---
sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"

# --- Install and enable systemd service ---
sudo cp "$CONFIG_DIR/altoids.service" /etc/systemd/system/altoids.service
sudo systemctl daemon-reload
sudo systemctl enable altoids.service

echo ""
echo "Setup complete."
echo ""
echo "Next steps:"
echo "  1. Reboot the Pi (required for SPI/I2C and audio driver changes):"
echo "       sudo reboot"
echo "  2. After reboot, the altoids service starts automatically"
echo "  3. Pair the EXknight M4 keyboard (see README for full steps):"
echo "       bluetoothctl scan on        # start scanning"
echo "       bluetoothctl pair <MAC>     # pair with M4"
echo "       bluetoothctl trust <MAC>    # auto-reconnect on wake"
echo "  4. Use 'make status' to check the service"
echo ""
echo "Available make targets: self-test, stage, reload, update, status, tmux-sync"
