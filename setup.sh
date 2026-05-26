#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$ROOT_DIR/config"
INSTALL_DIR="/opt/altoids"
VENV_DIR="$INSTALL_DIR/.venv"
RUNTIME_DIR="$INSTALL_DIR/runtime"
RUNTIME_BIN_DIR="$RUNTIME_DIR/bin"
RUNTIME_STATE_DIR="$RUNTIME_DIR/state"
TMUX_RUNTIME_CONF="$RUNTIME_DIR/tmux.conf"
SERVICE_USER="$(awk -F= '/^User=/{print $2}' "$CONFIG_DIR/altoids.service" | tail -n 1)"
SERVICE_GROUP="${SERVICE_GROUP:-$SERVICE_USER}"
DISPLAY_TARGET="${ALTOIDS_DISPLAY:-}"
PAIR_KEYBOARD_CHOICE="${ALTOIDS_PAIR_KEYBOARD:-}"

if [ -z "$SERVICE_USER" ]; then
  echo "Could not determine service user from $CONFIG_DIR/altoids.service" >&2
  exit 1
fi

if [ -z "$DISPLAY_TARGET" ]; then
  if [ -t 0 ] && [ -t 1 ]; then
    echo "Select display hardware:"
    echo "  1) Pimoroni Display HAT Mini"
    echo "  2) PiSugar Whisplay"
    read -r -p "Choice [1/2] (default 1): " display_choice
    case "$display_choice" in
      "" | 1)
        DISPLAY_TARGET="displayhatmini"
        ;;
      2)
        DISPLAY_TARGET="whisplay"
        ;;
      *)
        echo "Invalid display choice: $display_choice" >&2
        exit 1
        ;;
    esac
  else
    DISPLAY_TARGET="displayhatmini"
    echo "No TTY detected; defaulting display hardware to $DISPLAY_TARGET"
    echo "Set ALTOIDS_DISPLAY=whisplay to override."
  fi
fi

case "$DISPLAY_TARGET" in
  displayhatmini | whisplay)
    ;;
  *)
    echo "Unsupported ALTOIDS_DISPLAY value: $DISPLAY_TARGET" >&2
    echo "Expected one of: displayhatmini, whisplay" >&2
    exit 1
    ;;
esac

echo "Display target: $DISPLAY_TARGET"

if [ -z "$PAIR_KEYBOARD_CHOICE" ]; then
  if [ -t 0 ] && [ -t 1 ]; then
    read -r -p "Pair the EXknight M4 keyboard during setup? [y/N]: " pair_keyboard_reply
    case "$pair_keyboard_reply" in
      y | Y | yes | YES)
        PAIR_KEYBOARD_CHOICE="yes"
        ;;
      *)
        PAIR_KEYBOARD_CHOICE="no"
        ;;
    esac
  else
    PAIR_KEYBOARD_CHOICE="no"
  fi
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
sudo install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$INSTALL_DIR" "$RUNTIME_DIR" "$RUNTIME_STATE_DIR" "$INSTALL_DIR/releases"
sudo install -d -m 755 "$RUNTIME_BIN_DIR"

# --- Clone Whisplay vendor driver and install WM8960 audio stack (Whisplay only) ---
if [ "$DISPLAY_TARGET" = "whisplay" ]; then
  WHISPLAY_DIR="$INSTALL_DIR/vendor/Whisplay"
  if [ ! -d "$WHISPLAY_DIR" ]; then
    sudo mkdir -p "$INSTALL_DIR/vendor"
    sudo git clone --depth 1 https://github.com/PiSugar/Whisplay.git "$WHISPLAY_DIR"
  fi

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
fi

# --- Create Python virtual environment and install packages ---
sudo python3 -m venv "$VENV_DIR"
sudo "$VENV_DIR/bin/pip" install --upgrade pip
sudo "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"

# --- Copy config file if missing ---
if [ ! -f "$CONFIG_DIR/altoids.toml" ]; then
  cp "$CONFIG_DIR/altoids.example.toml" "$CONFIG_DIR/altoids.toml"
  echo "Created config/altoids.toml from example template"
fi

python3 - "$CONFIG_DIR/altoids.toml" "$DISPLAY_TARGET" <<'PY'
from pathlib import Path
import re
import sys

config_path = Path(sys.argv[1])
display_target = sys.argv[2]
text = config_path.read_text()

profiles = {
    "displayhatmini": {
        "width": "320",
        "height": "240",
        "rotation": "0",
        "transfer_quantization": '"rgb565"',
    },
    "whisplay": {
        "width": "280",
        "height": "240",
        "rotation": "270",
        "transfer_quantization": '"rgb332"',
    },
}

for key, value in profiles[display_target].items():
    text, count = re.subn(rf"(?m)^({re.escape(key)}\s*=\s*).*$", rf"\g<1>{value}", text, count=1)
    if count != 1:
        raise SystemExit(f"Could not update {key} in {config_path}")

config_path.write_text(text)
PY

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

if [ "$PAIR_KEYBOARD_CHOICE" = "yes" ]; then
  echo ""
  echo "Starting keyboard pairing helper"
  bash "$ROOT_DIR/scripts/pair_keyboard.sh"
fi

echo ""
echo "Setup complete."
echo ""
echo "Next steps:"
echo "  1. Reboot the Pi (required for SPI/I2C and audio driver changes):"
echo "       sudo reboot"
echo "  2. After reboot, the altoids service starts automatically"
echo "  3. Pair the EXknight M4 keyboard (see README for full steps):"
echo "       make pair-keyboard"
echo "  4. Use 'make status' to check the service"
echo ""
echo "Available make targets: pair-keyboard, self-test, stage, reload, update, status, tmux-sync"
