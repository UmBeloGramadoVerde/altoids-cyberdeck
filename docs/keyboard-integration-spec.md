# EXknight M4 Keyboard Integration Spec

This document defines the keyboard integration behavior for the EXknight M4 on Raspberry Pi OS based on the empirical probe run in:

- [artifacts/keyboard-probe/keyboard-probe-20260503T143735Z.md](/Users/kaynaoliveira/Documents/GitHub/altoids/artifacts/keyboard-probe/keyboard-probe-20260503T143735Z.md:1)

It supersedes assumptions taken only from vendor documentation whenever the probe showed actual Linux-visible behavior.

## Scope

This spec is for:

- Raspberry Pi OS on the Pi Zero 2W
- Bluetooth HID operation
- Linux-visible input behavior through `evdev`

This spec is not trying to describe iOS-only or Android-only text features unless they were confirmed on Raspberry Pi OS.

## Confirmed Linux-Visible Behavior

The following inputs were confirmed by probe:

- Windows/Cmd key -> `KEY_LEFTMETA`
- Shift -> `KEY_LEFTSHIFT` in the direct test, `KEY_RIGHTSHIFT` also exists
- Ctrl -> `KEY_RIGHTCTRL`
- Alt -> `KEY_RIGHTALT`
- `Fn + 6` -> `KEY_ESC`
- `Fn + 7` -> `KEY_UP`
- `Fn + 8` -> `KEY_DOWN`
- `Fn + 9` -> `KEY_LEFT`
- `Fn + 0` -> `KEY_RIGHT`
- double-tap `Shift` -> emits `KEY_CAPSLOCK`

The following vendor claims were **not** validated as special Linux-visible behavior:

- `Alt + C` producing `Ç`
- `Alt + S` producing `ß`
- accent-composition claims using `Alt + E/U/I/N`

In the probe, those behaved as ordinary modifier-plus-letter sequences.

## Integration Principles

The cyberdeck integration should follow these rules:

1. Treat the keyboard as a normal terminal keyboard first.
2. Use the Windows/Cmd key as the cyberdeck command-mode trigger.
3. Do not rely on `Fn`-layer navigation for primary deck UX.
4. Preserve `Fn + 6` and `Fn + 7/8/9/0` as fallback terminal/editor keys.
5. Never build custom deck behavior on double-tap `Shift`.
6. Avoid `Ctrl + Space` because the keyboard firmware appears to special-case it.

## Logical Key Normalization

The app should normalize raw evdev key names into a smaller logical key space.

### Modifier normalization

Normalize these pairs:

- `KEY_LEFTSHIFT`, `KEY_RIGHTSHIFT` -> logical `shift`
- `KEY_LEFTCTRL`, `KEY_RIGHTCTRL` -> logical `ctrl`
- `KEY_LEFTALT`, `KEY_RIGHTALT` -> logical `alt`
- `KEY_LEFTMETA`, `KEY_RIGHTMETA` -> logical `meta`

### Non-modifier normalization

Use direct logical names for:

- letters `a` through `z`
- digits `0` through `9`
- `enter`
- `tab`
- `backspace`
- `space`
- `escape`
- `up`, `down`, `left`, `right`
- `home`, `end`, `pageup`, `pagedown`, `delete`, `insert`
- `capslock`

The application layer should not reason about physical left/right modifier variants directly unless a future device-specific need appears.

## Command Mode

The keyboard-specific command model is:

- tap logical `meta`
- command mode becomes active for a short timeout
- the next logical key triggers a deck action
- command mode exits immediately after the next key, whether matched or unmatched

### Command map

- `meta`, `q` -> home
- `meta`, `w` -> terminal
- `meta`, `e` -> system
- `meta`, `a` -> previous tmux window
- `meta`, `s` -> next tmux window
- `meta`, `z` -> previous screen
- `meta`, `x` -> next screen

System-screen-local commands:

- `meta`, `j` -> previous Wi‑Fi network
- `meta`, `k` -> next Wi‑Fi network
- `meta`, `r` -> Wi‑Fi rescan
- `meta`, `c` -> connect selected Wi‑Fi network

## Explicit Non-Goals

These should not be depended on for the production deck UX:

- double-Shift custom shortcuts
- `Ctrl + Space`
- vendor-documented special-character behavior
- implicit OS-mode detection from evdev alone

## OS Mode Notes

The vendor documentation suggests:

- `Fn + W` = Android mode
- `Fn + E` = Windows mode

Probe results indicate:

- `Fn + W` only produced a normal `W` key event at the Linux-visible layer
- `Fn + E` did not behave like a clean mode-switch signal in the probe

So the app should:

- document Android mode as the preferred starting mode for Raspberry Pi OS
- not attempt to infer or switch keyboard OS mode programmatically from evdev

## Residual Uncertainties

These are still open and should be treated as hardware-validation items:

- whether Android mode and Windows mode materially change modifier semantics in longer real use
- whether any swallowed `Fn` combinations are useful outside the Linux-visible key stream
- whether reconnect/wake behavior ever suppresses the first key after the 5-minute auto-sleep

## Implementation Guidance

The code should expose:

- normalized `KeyboardEvent` objects for the app
- raw-key details only for debugging or future probe tooling

The UI and command logic should consume only logical keys, not raw `KEY_LEFTMETA`-style names.
