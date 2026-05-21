# Altoids Cyberdeck

Control software for a Raspberry Pi Zero 2W cyberdeck with a tiny LCD display.

## Quick Reference

- **Language**: Python 3.7+
- **Entry point**: `python3 -m altoids` (or `altoids/app.py:main`)
- **Config**: `config/altoids.toml` (created from `config/altoids.example.toml` on first setup)
- **Tests**: `python3 -m pytest tests/` (20 tests, run in <1s, no hardware needed)
- **Service**: `altoids.service` runs via systemd as user `kayna`

## Project Layout

```
altoids/          # Main Python package
  app.py          # App loop, screen framework, main()
  display.py      # Display backend abstraction (Whisplay / Display HAT Mini / mock)
  config.py       # Config dataclasses, loads config/altoids.toml
  ui/             # Screen implementations (home, term, system, emulation)
config/           # Deployment configs (systemd, tmux, toml)
scripts/          # Benchmarks and utilities
tests/            # Pytest test suite
fonts/            # Bitmap font files
roms/             # CHIP-8 game ROMs
```

## Display Hardware

The display auto-detects between two backends (set `backend = "auto"` in config):

- **Pimoroni Display HAT Mini** (320x240): uses `displayhatmini` package, requires PIL Image buffer passed to constructor, `display()` takes no args, needs 180-degree rotation applied before display
- **PiSugar Whisplay** (240x280): uses vendor driver at `vendor/Whisplay/runtime/whisplay.py`, uses `draw_image()` with RGB565 bytes, needs rotation from config (default 270)
- **Mock**: fallback when no hardware present, saves frames to `artifacts/last-frame.png`

Auto-detection tries `displayhatmini` first, then `whisplay`. This order matters: both drivers share the same SPI bus and both init successfully regardless of which physical board is connected. The first one to init wins.

## Keyboard

Recommended: EXknight M4 (Bluetooth 5.0, pairs as "M4"). Input handled by `altoids/input_keyboard.py` via evdev.

Pairing after setup+reboot:
```bash
bluetoothctl scan on          # find "M4" and note MAC
bluetoothctl pair <MAC>       # pair
bluetoothctl trust <MAC>      # auto-reconnect on wake
```

Set to Android mode (`Fn + W`) for Linux. The `Cmd`/`Windows` key is the command-mode trigger. See `docs/keyboard-exknight-m4.md` and `docs/keyboard-integration-spec.md` for full details.

## Deployment on a Raspberry Pi

### Fresh install steps

1. Flash Raspberry Pi OS to SD card (Debian Trixie/Bookworm, 64-bit)
2. Configure WiFi and SSH during imaging (or via raspi-config after boot)
3. Set up passwordless sudo: `sudo bash -c 'echo "kayna ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/kayna'`
4. Clone: `git clone https://github.com/UmBeloGramadoVerde/altoids-cyberdeck.git ~/altoids`
5. Run: `cd ~/altoids && bash setup.sh`
6. Reboot: `sudo reboot` (required for SPI/I2C activation)
7. Service starts automatically after reboot
8. Pair keyboard: `bluetoothctl scan on`, then `pair` and `trust` the M4 MAC

### Deploying code changes

From the repo checkout on the Pi:

```bash
make update    # self-test, stage, reload, show status
make status    # check active release
make rollback  # revert to previous release
```

### Remote development

```bash
python3 -m altoids --web-viewer --web-host 0.0.0.0 --web-port 8765
# Open http://<pi-ip>:8765/ in browser
```

## Key Patterns

- **Config loading**: `config.py:load_config()` reads `config/altoids.toml`, falls back to dataclass defaults
- **Screen system**: screens implement `Screen` base class with `render()`, `update()`, `on_button()`, `on_keyboard_event()`
- **App buffer**: app renders to a 280x240 PIL Image, display backend handles resizing/rotation for actual hardware
- **Service runs as user `kayna`**: hardcoded in `config/altoids.service`, change before running setup.sh if needed

## Testing

Tests run without hardware and mock all Pi-specific dependencies:

```bash
python3 -m pytest tests/ -v
```

## Important Files for Common Changes

- Display issues: `altoids/display.py`
- Adding screens: `altoids/ui/` + register in `altoids/app.py:AltoidsApp.__init__`
- Config options: `altoids/config.py` (dataclasses) + `config/altoids.example.toml`
- System setup: `setup.sh`
- Service management: `config/altoids.service`, `config/altoids-supervisor`, `config/altoids-runtime.py`
