#!/usr/bin/env bash
set -euo pipefail

DEVICE_NAME_SUBSTRING="${ALTOIDS_KEYBOARD_NAME:-M4}"
SCAN_SECONDS="${ALTOIDS_BT_SCAN_SECONDS:-12}"
DEVICE_MAC=""
LAST_SCAN_OUTPUT=""
DISCOVERED_DEVICES=()

usage() {
  cat <<'EOF'
Usage: scripts/pair_keyboard.sh [--name SUBSTRING] [--mac MAC] [--scan-seconds N]

Pairs, trusts, and connects a Bluetooth keyboard through bluetoothctl.

Defaults:
  --name M4
  --scan-seconds 12
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --name)
      DEVICE_NAME_SUBSTRING="$2"
      shift 2
      ;;
    --mac)
      DEVICE_MAC="$2"
      shift 2
      ;;
    --scan-seconds)
      SCAN_SECONDS="$2"
      shift 2
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

if ! command -v bluetoothctl >/dev/null 2>&1; then
  echo "bluetoothctl is required but not installed." >&2
  exit 1
fi

if ! [[ "$SCAN_SECONDS" =~ ^[0-9]+$ ]] || [ "$SCAN_SECONDS" -le 0 ]; then
  echo "--scan-seconds must be a positive integer." >&2
  exit 1
fi

run_bt() {
  bluetoothctl --agent KeyboardOnly "$@"
}

try_bt() {
  local output status
  output="$(bluetoothctl --agent KeyboardOnly "$@" 2>&1)" || status=$?
  status="${status:-0}"
  if [ "$status" -ne 0 ]; then
    printf '%s\n' "$output" >&2
    return "$status"
  fi
  if [ -n "$output" ]; then
    printf '%s\n' "$output"
  fi
}

find_device_mac() {
  local exact_mac=""
  local partial_mac=""
  local line mac name lowered target
  target="$(printf '%s' "$DEVICE_NAME_SUBSTRING" | tr '[:upper:]' '[:lower:]')"
  while IFS= read -r line; do
    case "$line" in
      Device\ *)
        mac="$(printf '%s\n' "$line" | awk '{print $2}')"
        name="${line#Device $mac }"
        lowered="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
        if [ "$lowered" = "$target" ]; then
          exact_mac="$mac"
          break
        fi
        if [ -z "$partial_mac" ] && [[ "$lowered" == *"$target"* ]]; then
          partial_mac="$mac"
        fi
        ;;
    esac
  done < <(run_bt devices)

  if [ -n "$exact_mac" ]; then
    printf '%s\n' "$exact_mac"
  else
    printf '%s\n' "$partial_mac"
  fi
}

scan_for_device_mac() {
  local exact_mac=""
  local partial_mac=""
  local line mac name lowered target cleaned
  target="$(printf '%s' "$DEVICE_NAME_SUBSTRING" | tr '[:upper:]' '[:lower:]')"
  LAST_SCAN_OUTPUT="$(bluetoothctl --agent KeyboardOnly --timeout "$SCAN_SECONDS" scan on 2>&1 || true)"
  while IFS= read -r line; do
    cleaned="$(printf '%s' "$line" | perl -pe 's/\e\[[0-9;]*m//g')"
    case "$cleaned" in
      *Device\ *)
        mac="$(printf '%s\n' "$cleaned" | sed -n 's/.*Device \([0-9A-F:][0-9A-F:]*\) .*/\1/p')"
        [ -n "$mac" ] || continue
        name="${cleaned##*${mac} }"
        DISCOVERED_DEVICES+=("${mac}|${name}")
        lowered="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
        if [ "$lowered" = "$target" ]; then
          exact_mac="$mac"
          break
        fi
        if [ -z "$partial_mac" ] && [[ "$lowered" == *"$target"* ]]; then
          partial_mac="$mac"
        fi
        ;;
    esac
  done <<< "$LAST_SCAN_OUTPUT"

  if [ -n "$exact_mac" ]; then
    printf '%s\n' "$exact_mac"
  else
    printf '%s\n' "$partial_mac"
  fi
}

print_discovered_devices() {
  local seen=() entry
  for entry in "${DISCOVERED_DEVICES[@]}"; do
    case " ${seen[*]} " in
      *" ${entry} "*)
        continue
        ;;
    esac
    seen+=("$entry")
    printf '  %s\n' "$entry"
  done
}

prompt_for_discovered_device() {
  local seen=() entry unique=() index choice mac name
  for entry in "${DISCOVERED_DEVICES[@]}"; do
    case " ${seen[*]} " in
      *" ${entry} "*)
        continue
        ;;
    esac
    seen+=("$entry")
    unique+=("$entry")
  done

  [ "${#unique[@]}" -gt 0 ] || return 1
  [ -t 0 ] && [ -t 1 ] || return 1

  echo 'Discovered devices from the last scan:'
  index=1
  for entry in "${unique[@]}"; do
    mac="${entry%%|*}"
    name="${entry#*|}"
    printf '  %d) %s  %s\n' "$index" "$mac" "$name"
    index=$((index + 1))
  done

  read -r -p "Select a device number or press Enter to cancel: " choice
  if [ -z "$choice" ]; then
    return 1
  fi
  if ! [[ "$choice" =~ ^[0-9]+$ ]] || [ "$choice" -lt 1 ] || [ "$choice" -gt "${#unique[@]}" ]; then
    echo "Invalid selection: $choice" >&2
    return 1
  fi

  DEVICE_MAC="${unique[$((choice - 1))]%%|*}"
  return 0
}

echo "Prepare the keyboard before pairing:"
echo "  1. Power it on."
echo "  2. Put it in pairing mode with Fn+Q until the backlight blinks."
echo "  3. Set Android mode with Fn+W."
echo ""

try_bt power on >/dev/null 2>&1 || true

if [ -z "$DEVICE_MAC" ]; then
  echo "Scanning for Bluetooth devices for ${SCAN_SECONDS}s..."
  DEVICE_MAC="$(scan_for_device_mac)"
  run_bt scan off >/dev/null || true
  if [ -z "$DEVICE_MAC" ]; then
    DEVICE_MAC="$(find_device_mac)"
  fi
fi

if [ -z "$DEVICE_MAC" ]; then
  echo "Could not find a Bluetooth device matching \"$DEVICE_NAME_SUBSTRING\"." >&2
  if [ "${#DISCOVERED_DEVICES[@]}" -gt 0 ]; then
    echo "Devices seen during the last scan:" >&2
    print_discovered_devices >&2
    if prompt_for_discovered_device; then
      echo "Using selected device: $DEVICE_MAC"
    fi
  fi
fi

if [ -z "$DEVICE_MAC" ]; then
  echo "Run again with --mac <MAC> if the keyboard is visible under a different name." >&2
  exit 1
fi

echo "Using device: $DEVICE_MAC"
echo "Pairing..."
run_bt pair "$DEVICE_MAC"

echo "Trusting..."
run_bt trust "$DEVICE_MAC" >/dev/null

echo "Connecting..."
run_bt connect "$DEVICE_MAC" >/dev/null || true

if ! run_bt info "$DEVICE_MAC" | grep -q "Connected: yes"; then
  echo "Keyboard is paired and trusted, but not currently connected." >&2
  echo "Press a key on the keyboard to wake it, then run: bluetoothctl connect $DEVICE_MAC" >&2
  exit 1
fi

echo "Keyboard paired and connected: $DEVICE_MAC"
