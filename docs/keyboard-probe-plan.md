# Keyboard Probe Plan

This plan exists to characterize the actual Linux-visible behavior of the EXknight M4 over Bluetooth on the Raspberry Pi before the display arrives and before we commit to a production keyboard abstraction.

The probe does **not** rely on the cyberdeck UI or the physical display. It is designed to be run over SSH and produce a raw event report from `evdev`.

## Goal

We want to answer these questions with real data:

- Which evdev device node the keyboard uses on Raspberry Pi OS
- What the keyboard reports for every visible key we care about
- Whether the Windows/Cmd key arrives cleanly as a usable meta trigger
- Whether `Fn` combos emit the documented navigation keys on Linux
- Whether OS-mode changes alter modifier behavior
- Whether “special” vendor claims like `Alt+C`, `Alt+S`, and `Ctrl+Space` have any Linux-visible effect

## Run Order

Use this order unless you hit a failure:

1. Pair the keyboard and switch it to Android mode with `Fn + W`
2. Run the guided probe once in Android mode
3. If modifiers or the Windows/Cmd key look wrong, switch to Windows mode with `Fn + E`
4. Run the guided probe again in Windows mode

Avoid running `Fn + Q` during the middle of a probe because that is documented as a pairing-mode action and can disrupt the session.

## Script

Run:

```bash
python3 scripts/keyboard_probe.py
```

The script will:

- list candidate evdev keyboard devices
- prefer a device whose name contains `M4`
- guide you case by case
- write both JSON and Markdown reports into `artifacts/keyboard-probe/`

## Key Sequences I Want Tested

These are the sequences I want to see in the report.

### Core identity / modifiers

- Windows/Cmd key
- Tab
- Backspace
- Enter
- Space
- Shift
- Ctrl
- Alt

### Full alphanumeric sweep

- `A` through `Z`, each individually
- `1` through `0`, each individually

### Shift behavior

- `Shift + A`
- `Shift + 1` through `Shift + 0`
- double-tap `Shift`

### Documented Fn navigation

- `Fn + 6`
- `Fn + 7`
- `Fn + 8`
- `Fn + 9`
- `Fn + 0`

### Mode switching

- `Fn + W`
- `Fn + E`

These are intentionally near the end because they may change the keyboard’s mode mid-test.

### Vendor-specific claims

- `Alt + C`
- `Alt + S`
- `Alt + E`, then `A`
- `Alt + U`, then `U`
- `Alt + I`, then `I`
- `Alt + N`, then `N`
- hold `Ctrl + Space` for 3 seconds

### Unknown Fn-layer extras

The vendor notes are incomplete. I also want:

- two additional `Fn` combos that are visibly printed on the keyboard but not already in the list above

When these two “extra Fn” cases appear in the script, choose undocumented printed combos and note which physical combo you used in your later report.

## What To Look For In The Report

After running the probe, the important things to check are:

- Does the Windows/Cmd key show up as `KEY_LEFTMETA` or `KEY_RIGHTMETA`?
- Does `Fn + 6` really show up as `KEY_ESC`?
- Do `Fn + 7/8/9/0` really show up as arrow keys?
- Does double-tap `Shift` produce normal Linux `Caps Lock` behavior or something odd?
- Do `Alt + C` / `Alt + S` produce plain Linux modifier sequences or locale-transformed characters?
- Does `Ctrl + Space` emit normal key events or get swallowed by keyboard firmware?
- Are Android mode and Windows mode different in how modifiers are reported?

## Expected Outcome

We are not trying to ship the keyboard integration with this script.

We are trying to get enough hard data to:

- define a reliable meta key strategy
- choose safe command-mode triggers
- avoid unstable modifier combos
- document OS-mode dependencies
- build a robust keyboard abstraction layer later without guessing
