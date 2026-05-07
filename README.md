# Altoids Cyberdeck

Control software for an Altoids tin cyberdeck built around a Raspberry Pi Zero 2W and PiSugar Whisplay. The UI is meant to feel closer to a tiny instrument or gadget than a raw Linux console: playful, minimal, and persistent.

This repository currently contains the first working scaffold of that system:

- a multi-screen UI rendered with `Pillow`
- a home dashboard with an animated mascot and rotating status messages
- a tmux-backed terminal screen
- a system screen with device stats and Wi‑Fi controls
- TinScope, a keyboard-reachable network field agent with persisted reports
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

Global keyboard help is available from any screen with `F1`, `Ctrl+H`, `Ctrl+/`, or `CMD+H`. The overlay opens to the current screen's help page unless that screen already has a remembered help page. Use `1`..`6` to jump directly to a help page, `Left` / `Right`, `Up` / `Down`, `Tab`, or `Space` to switch pages, and `Esc`, `Enter`, `F1`, `Ctrl+H`, `Ctrl+/`, or `H` to close it.

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
- `Up` / `Down`: scroll the captured terminal view
- `Ctrl+Home` / `Ctrl+End`: jump to the oldest captured lines or back to live output
- `Ctrl` + letter chords such as `Ctrl+C` are forwarded into tmux
- `F1`, `Ctrl+H`, `Ctrl+/`, or `CMD+H`: open keyboard shortcut help
- `CMD+A` / `CMD+S`: previous / next tmux window
- `CMD+1`..`CMD+9`: jump to tmux windows 1 through 9
- `CMD+0`: jump to tmux window 10
- `CMD+D` / `CMD+F`: create / close tmux window
- `CMD+Q` / `CMD+W` / `CMD+E`: jump to home / terminal / system
- `CMD+Z` / `CMD+X`: previous / next app screen

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

- `A`: previous system subpage
- `B`: next system subpage
- `X`: return home
- `Y`: enter Wi‑Fi setup
- `long Y`: open terminal

Wi‑Fi setup scans nearby networks and shows the selected network from that scan. Inside setup, `A` / `B` picks a network, `X` rescans, and `Y` joins the selected network. Keyboard users can press `CMD+C` from the system screen to enter setup, then use `Up` / `Down`, `R`, `Enter`, and `Esc`.

If the selected Wi‑Fi network is secured and no working password is cached, the system screen prompts for a password from the keyboard. `Enter` submits, `Backspace` edits, and `Esc` cancels.

On the system screen, `CMD+C` enters Wi‑Fi setup.

Wi‑Fi management is implemented in [altoids/wifi.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/wifi.py:1) and currently depends on `nmcli`, which means the Pi should use NetworkManager.

### TinScope

Implemented in [altoids/ui/tinscope.py](/home/kayna/altoids-cyberdeck/altoids/ui/tinscope.py:1).

TinScope is a compact network field agent for the deck UI. It runs a Network Field Kit mission, keeps the main screen focused on state and approvals, and stores detailed network memory under `.runtime/tinscope/`.

The full flow is keyboard reachable: `Enter` starts, approves, or opens the selected inbox item; `Space` shows context; `Esc` denies or backs out; arrows select and scroll. Detailed results open in a `cdx`-style inspection overlay instead of being packed into the tiny display.

See [docs/tinscope.md](/home/kayna/altoids-cyberdeck/docs/tinscope.md:1) for controls, persistence, reports, and inspection behavior.

## Configuration

Application config lives in [config/altoids.toml](/Users/kaynaoliveira/Documents/GitHub/altoids/config/altoids.toml:1) and is loaded by [altoids/config.py](/Users/kaynaoliveira/Documents/GitHub/altoids/altoids/config.py:78).

Current config sections:

- `[display]`: backend, logical dimensions, rotation, FPS, backlight brightness, driver path, transfer quantization
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
transfer_quantization = "rgb332"

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

## `cdx`

`cdx` is a separate deck-oriented Codex client. It talks to `codex app-server` over stdio, owns an explicit `threadId`, renders a feed-first live session view, and handles approvals through the app-server protocol instead of rollout-file heuristics.

See [docs/cdx.md](/home/kayna/altoids-cyberdeck/docs/cdx.md:1) for:

- how to launch and use `cdx`
- keyboard shortcuts and startup flow
- the approval workflow
- the feed icon and layout design system
- deployment notes for `runtime-sync` and `make update`

## Setup

The intended deployment path is [setup.sh](/Users/kaynaoliveira/Documents/GitHub/altoids/setup.sh:1).

It currently:

- installs Python and tmux dependencies
- uses distro `python3-gi` and `python3-gi-cairo` packages instead of building `PyGObject` from `pip`
- installs `alsa-utils` for WM8960 mixer and playback control
- installs `i2c-tools`, `dkms`, `libasound2-plugins`, `unzip`, and `raspi-config` for PiSugar's WM8960 installer
- installs `python3-spidev` and `python3-libgpiod` for the Whisplay LCD driver
- installs `network-manager`
- clones the PiSugar Whisplay driver into `/opt/altoids/vendor/Whisplay`
- runs PiSugar's `Driver/install_wm8960_drive.sh` so the WM8960 overlay, modules, mixer service, and ALSA state are installed during setup
- copies the bundled `tmux.conf`
- installs the `altoids.service` systemd unit
- enables the service

Related config files:

- [config/altoids.service](/Users/kaynaoliveira/Documents/GitHub/altoids/config/altoids.service:1)
- [config/tmux.conf](/Users/kaynaoliveira/Documents/GitHub/altoids/config/tmux.conf:1)

PiSugar's installer edits `/boot/firmware/config.txt`, updates `/etc/modules`, installs `wm8960-soundcard.service`, and recommends a reboot after setup so ALSA re-enumerates the `wm8960soundcard` device cleanly.

## Safe Reload Flow

The service now runs behind a stable supervisor entrypoint instead of launching `python -m altoids` directly. Releases are staged into versioned directories under `/opt/altoids/releases`, then promoted only after the new build passes a self-test and survives a short health window.

The intended operator flow from the tmux shell is through the repo `Makefile`:

- `make stage`: copy the current checkout into a new staged release
- `make reload`: ask the supervisor to switch to that staged release
- `make update`: refresh the stable tmux config, re-source it into the running tmux server, then run a local self-test, stage the current checkout, reload it, and print status
- `make rollback`: switch back to the previous known-good release when you have an operator path to trigger it
- `make status`: show the active, previous, and staged release plus the last reload result
- `make tmux-sync`: install `config/tmux.conf` to `/opt/altoids/runtime/tmux.conf`, point `/etc/tmux.conf` and `~/.tmux.conf` at it, then re-source it into the running tmux server
- `make tmux-apply`: re-source `/opt/altoids/runtime/tmux.conf` into the running tmux server
- `make tmux-install-user`: point `~/.tmux.conf` at `/opt/altoids/runtime/tmux.conf`
- `make tmux-install-system`: point `/etc/tmux.conf` at `/opt/altoids/runtime/tmux.conf`

The important behavior is that reloads are now explicit, staged operations rather than "quick save / quick load" shortcuts. That keeps the process focused on preparing a candidate build, validating it, and only then asking the supervisor to cut over.

Tmux is intentionally handled through one stable path outside the staged release directories. That avoids the old problem where tmux was reading one file while the repo was changing another. The repo copy stays at `config/tmux.conf`, while the live path is `/opt/altoids/runtime/tmux.conf`.

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
