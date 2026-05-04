# Altoids Cyberdeck

Control software for an Altoids tin cyberdeck built around a Raspberry Pi Zero 2W and PiSugar Whisplay. The UI is meant to feel closer to a tiny instrument or gadget than a raw Linux console: playful, minimal, and persistent.

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
- 280x240 logical canvas rotated onto the Whisplay's 240x280 panel
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

The current implementation uses a `Display` abstraction in [altoids/display.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/display.py:1). On Pi hardware it now prefers the PiSugar `WhisPlayBoard` driver and rotates the framebuffer 90 degrees clockwise before pushing it to the physical `240x280` LCD, which matches a panel mounted 90 degrees counter-clockwise in the enclosure. When the hardware driver is missing, it falls back to saving the latest rendered frame to `artifacts/last-frame.png`. For remote development without the physical display, a lightweight browser-based viewer is also available.

When Whisplay hardware is active, the app also enables Whisplay-only accent features:

- generated WM8960 speaker cues for boot, wake, screen changes, Wi‑Fi success/failure, and errors
- RGB LED pulses for those same major events
- standby-aware power saving that shuts off the LCD backlight, clears the RGB LED, and suppresses accent audio while sleeping

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

Global keyboard help is available from any screen with `F1`, `Ctrl+H`, `Ctrl+/`, or `Meta`, `H`. The overlay is paged; use `Left` / `Right`, `Up` / `Down`, `Tab`, or `Space` to switch pages, and `Esc`, `Enter`, `F1`, `Ctrl+H`, `Ctrl+/`, or `H` to close it.

### Terminal

Implemented in [altoids/ui/term.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/ui/term.py:9) with tmux integration in [altoids/terminal.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/terminal.py:16).

The terminal view currently:

- ensures a tmux session exists
- captures pane contents from tmux
- preserves basic ANSI colors from tmux output
- renders the session inside a dedicated cyberdeck-style frame with pane metadata
- compacts standard `user@host:path$` prompts for narrow display readability
- supports scroll offset and tmux window management
- starts new tmux panes through a cyberdeck-specific shell rc profile for a shorter prompt and pane titles

Current controls:

- `A`: scroll up
- `long A`: create a new tmux window
- `B`: scroll down
- `long B`: close the active tmux window
- `X`: next tmux window
- `long X`: previous tmux window
- `Y`: send Enter
- `long Y`: return home

Keyboard behavior on the terminal screen:

- plain text is typed into tmux
- `Ctrl+Up` / `Ctrl+Down`: scroll the captured terminal view
- `Ctrl+PageUp` / `Ctrl+PageDown`: page the terminal scrollback
- `Ctrl+Home` / `Ctrl+End`: jump to the oldest captured lines or back to live output
- `Ctrl` + letter chords such as `Ctrl+C` are forwarded into tmux
- `F1`, `Ctrl+H`, `Ctrl+/`, or `Meta`, `H`: open keyboard shortcut help
- `Meta`, `A` / `S`: previous / next tmux window
- `Meta`, `1`..`9`: jump to tmux windows 1 through 9
- `Meta`, `0`: jump to tmux window 10
- `Meta`, `D` / `F`: create / close tmux window
- `Meta`, `Q` / `W` / `E`: jump to home / terminal / system
- `Meta`, `Z` / `X`: previous / next app screen

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

If the selected Wi‑Fi network is secured and no working password is cached, the system screen now prompts for a password from the keyboard. `Enter` submits, `Backspace` edits, and `Esc` cancels.

On the system screen, `Meta`, `J` / `K` mirrors the `A` / `B` Wi‑Fi selection buttons, and `Meta`, `R` / `C` mirrors scan / connect.

Wi‑Fi management is implemented in [altoids/wifi.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/wifi.py:1) and currently depends on `nmcli`, which means the Pi should use NetworkManager.

## Configuration

Application config lives in [config/altoids.toml](/Users/kaynaoliveira/Documents/GitHub/altoids/config/altoids.toml:1) and is loaded by [altoids/config.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/config.py:78).

Current config sections:

- `[display]`: backend, logical dimensions, rotation, FPS, backlight brightness, driver path
- `[audio]`: Whisplay-only speaker cue settings and volume/mute defaults
- `[led]`: Whisplay-only RGB LED pulse settings
- `[sleep]`: idle timeout
- `[ui]`: font path, font size, animation timings
- `[terminal]`: tmux session name, history depth, terminal geometry
- `[system]`: warning threshold for temperature
- `[wifi]`: scan cache duration
- `[wifi.passwords]`: optional SSID-to-password defaults for secured Wi‑Fi networks

Example:

```toml
[display]
backend = "whisplay"
width = 280
height = 240
rotation = 270
driver_path = "vendor/Whisplay/Driver"

[wifi]
scan_cache_seconds = 15.0

[wifi.passwords]
"MySSID" = "supersecret"
```

The terminal config also supports a dedicated rcfile for deck sessions:

```toml
[terminal]
shell_rc_path = "config/cyberdeck-shell.sh"
```

Secured Wi‑Fi connections can now be joined interactively from the system screen without predefining passwords in config. Password entries from config remain supported as optional defaults.

## Setup

The intended deployment path is [setup.sh](/Users/kaynaoliveira/Documents/GitHub/altoids/setup.sh:1).

It currently:

- installs Python and tmux dependencies
- uses distro `python3-gi` and `python3-gi-cairo` packages instead of building `PyGObject` from `pip`
- installs `alsa-utils` for WM8960 mixer and playback control
- installs `python3-spidev` and `python3-libgpiod` for the Whisplay LCD driver
- installs `network-manager`
- clones the PiSugar Whisplay driver into `/opt/altoids/vendor/Whisplay`
- copies the bundled `tmux.conf`
- installs the `altoids.service` systemd unit
- enables the service

Related config files:

- [config/altoids.service](/Users/kaynaoliveira/Documents/GitHub/altoids/config/altoids.service:1)
- [config/tmux.conf](/Users/kaynaoliveira/Documents/GitHub/altoids/config/tmux.conf:1)

Whisplay audio is still a separate vendor step. PiSugar's current docs say the WM8960 installer targets full Raspberry Pi OS rather than Lite, so run their audio installer after `setup.sh` if you need the speaker/mic path.

## Safe Reload Flow

The service now runs behind a stable supervisor entrypoint instead of launching `python -m altoids` directly. Releases are staged into versioned directories under `/opt/altoids/releases`, then promoted only after the new build passes a self-test and survives a short health window.

The intended operator loop from the tmux shell is:

- `qs`: copy the current checkout into a new staged release
- `reload`: ask the supervisor to switch to that staged release
- `qload`: roll back to the previous known-good release
- `deck-status`: show the active, previous, and staged release plus the last reload result

The app now also supports `python -m altoids --self-test`, which initializes the UI stack, renders one frame, and exits non-zero on startup failure. The supervisor uses this with a JSON health file under `/run/altoids/health.json` to decide whether a candidate release is safe to keep running.

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
- direct window jumps on `Alt+1` through `Alt+0`
- prefix-free previous / next window on `Shift+Left` / `Shift+Right` and `Alt+Left` / `Alt+Right`
- prefix-free pane movement on `Alt+h`, `Alt+j`, `Alt+k`, `Alt+l`
- confirmation before prefix-based pane or window kills

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

Run the browser-based viewer with:

```bash
python3 -m altoids --web-viewer --web-host 0.0.0.0 --web-port 8765
```

Then open the Pi from your laptop:

```text
http://<pi-ip>:8765/
```

Web viewer features:

- live frame refresh in the browser
- clickable `A`, `B`, `X`, `Y` buttons
- clickable long-press variants for `A`, `B`, `X`, `Y`
- browser keyboard forwarding for letters, Enter, Backspace, Tab, Escape, arrows, and the host `Meta` key
- browser-side `F1` help toggle is available where the host browser forwards that key

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
