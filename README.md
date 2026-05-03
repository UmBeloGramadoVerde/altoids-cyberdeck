# Altoids Cyberdeck

Control software for an Altoids tin cyberdeck built around a Raspberry Pi Zero 2W and Pimoroni Display HAT Mini. The UI is meant to feel closer to a tiny instrument or gadget than a raw Linux console: playful, minimal, and persistent.

This repository currently contains the first working scaffold of that system:

- a multi-screen UI rendered with `Pillow`
- a home dashboard with an animated mascot and rotating status messages
- a tmux-backed terminal screen
- a system screen with device stats and Wi‑Fi controls
- a sleep manager for backlight timeout
- deployment/config scaffolding for `systemd`, `tmux-resurrect`, and `tmux-continuum`

## Design Intent

The original product direction is:

- Teenage Engineering meets Flipper Zero
- 320x240 display with a crisp bitmap-style UI
- restricted five-color palette
- 53x20 terminal grid
- persistent shells across reboot and power loss
- a default screen with personality instead of dropping straight into a terminal

Current palette constants live in [altoids/colors.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/colors.py:1).

## Current Structure

```text
altoids/
├── altoids/
│   ├── app.py
│   ├── bluetooth.py
│   ├── colors.py
│   ├── config.py
│   ├── display.py
│   ├── input_buttons.py
│   ├── input_keyboard.py
│   ├── messages.py
│   ├── renderer.py
│   ├── sleep.py
│   ├── sprites.py
│   ├── terminal.py
│   ├── wifi.py
│   └── ui/
│       ├── base.py
│       ├── home.py
│       ├── system.py
│       ├── term.py
│       └── widgets.py
├── config/
│   ├── altoids.service
│   ├── altoids.toml
│   └── tmux.conf
├── requirements.txt
└── setup.sh
```

## Runtime Overview

The main entry point is [altoids/app.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/app.py:29).

The app loop does four things:

1. Polls input sources.
2. Updates the active screen.
3. Renders the active screen plus the bottom button bar.
4. Pushes the frame to the display backend.

The current implementation uses a `Display` abstraction in [altoids/display.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/display.py:1). On Pi hardware it should use `displayhatmini`. When that module is missing, it falls back to saving the latest rendered frame to `artifacts/last-frame.png`.
You can also run a desktop simulator window for development without the physical display.

## Screens

### Home

Implemented in [altoids/ui/home.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/ui/home.py:14).

Shows:

- animated placeholder mascot from `assets/mascot.png` when present, otherwise generated fallback frames
- current time
- uptime
- rotating status message
- Bluetooth connection indicator
- terminal window count
- CPU temperature

Current controls:

- `A`: previous message
- `B`: next message
- `X`: open terminal
- `Y`: open system screen

### Terminal

Implemented in [altoids/ui/term.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/ui/term.py:9) with tmux integration in [altoids/terminal.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/terminal.py:16).

The terminal view currently:

- ensures a tmux session exists
- captures pane contents from tmux
- renders plain text terminal output into the UI
- supports scroll offset and tmux window switching

Current controls:

- `A`: scroll up
- `B`: scroll down
- `X`: next tmux window
- `long X`: previous tmux window
- `Y`: send Enter
- `long Y`: return home

Note: the plan called for `pyte`-based ANSI rendering, but the current code strips ANSI sequences and renders plain text via [altoids/renderer.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/renderer.py:1). That is a deliberate simplification for the first pass.

### System

Implemented in [altoids/ui/system.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/ui/system.py:11).

Shows:

- CPU usage
- memory usage
- temperature
- disk usage
- IP address
- Bluetooth status
- tmux window count
- Wi‑Fi connection state
- a selected Wi‑Fi network from the latest scan cache

Current controls:

- `A`: previous Wi‑Fi network
- `B`: next Wi‑Fi network
- `X`: scan Wi‑Fi networks
- `long X`: return home
- `Y`: connect to selected Wi‑Fi network
- `long Y`: open terminal

Wi‑Fi management is implemented in [altoids/wifi.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/wifi.py:1) and currently depends on `nmcli`, which means the Pi should use NetworkManager.

## Configuration

Application config lives in [config/altoids.toml](/Users/kaynaoliveira/Documents/GitHub/altoids/config/altoids.toml:1) and is loaded by [altoids/config.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/config.py:78).

Current config sections:

- `[display]`: display dimensions, FPS, backlight brightness
- `[sleep]`: idle timeout
- `[ui]`: font path, font size, animation timings
- `[terminal]`: tmux session name, history depth, terminal geometry
- `[system]`: warning threshold for temperature
- `[wifi]`: scan cache duration
- `[wifi.passwords]`: SSID-to-password mapping for secured Wi‑Fi networks

Example:

```toml
[wifi]
scan_cache_seconds = 15.0

[wifi.passwords]
"MySSID" = "supersecret"
```

Secured Wi‑Fi connections currently require the password to exist in config. Open networks can connect without an entry.

## Setup

The intended deployment path is [setup.sh](/Users/kaynaoliveira/Documents/GitHub/altoids/setup.sh:1).

It currently:

- installs Python and tmux dependencies
- installs `network-manager`
- copies the bundled `tmux.conf`
- installs the `altoids.service` systemd unit
- enables the service

Related config files:

- [config/altoids.service](/Users/kaynaoliveira/Documents/GitHub/altoids/config/altoids.service:1)
- [config/tmux.conf](/Users/kaynaoliveira/Documents/GitHub/altoids/config/tmux.conf:1)

The original plan also called for overlayfs and a writable tmux state area for better power-loss tolerance. Those operational steps are not yet fully automated in this repository.

## Persistence Model

Terminal persistence is based on:

- `tmux`
- `tmux-resurrect`
- `tmux-continuum`

The bundled tmux config enables:

- automatic restore
- pane content capture
- aggressive five-minute save intervals

This is intended to preserve shell layout, working directories, and scrollback across reboot. It does not preserve the in-memory state of interactive processes.

## Hardware and OS Assumptions

This codebase is currently written around these assumptions:

- Raspberry Pi Zero 2W
- Pimoroni Display HAT Mini
- Linux environment with `tmux`
- NetworkManager with `nmcli`
- Python environment with `Pillow` and `psutil`

Some modules are still scaffolds rather than full hardware integrations:

- [altoids/input_buttons.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/input_buttons.py:1)
- [altoids/input_keyboard.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/input_keyboard.py:1)
- [altoids/bluetooth.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/bluetooth.py:1)

They are structured so the app can run without Pi-specific hardware bindings during development.

## Development Notes

Run the app with:

```bash
python3 -m altoids
```

Run the desktop simulator with:

```bash
python3 -m altoids --simulator
```

Simulator controls:

- `1` `2` `3` `4` = buttons `A` `B` `X` `Y`
- `Alt+1` `Alt+2` `Alt+3` `Alt+4` = long-press `A` `B` `X` `Y`
- host `Cmd`/`Windows` key = cyberdeck command mode
- printable keys and common navigation keys are forwarded as keyboard input

For a short render smoke test:

```bash
python3 -m altoids --frames 1
```

If hardware display support is unavailable, the most recent frame is written to:

```text
artifacts/last-frame.png
```

## Status Against The Plan

Implemented now:

- project skeleton
- app loop and screen framework
- home screen
- terminal screen
- system screen
- sleep manager
- tmux config and systemd service scaffolding
- Wi‑Fi status/scan/connect support

Still incomplete or simplified relative to the original plan:

- real GPIO button handling with debounce and long-press timing
- Bluetooth D-Bus monitoring
- evdev keyboard forwarding into tmux
- true ANSI terminal emulation with `pyte`
- polished pixel art assets
- splash screen
- overlayfs automation and writable tmux state setup
- final Pi-specific deployment verification

## Verification

The code has been syntax-checked with:

```bash
python3 -m py_compile altoids/*.py altoids/ui/*.py
```

A full runtime test still depends on local installation of runtime packages like `Pillow`, and Pi-specific behavior still needs to be verified on target hardware.
