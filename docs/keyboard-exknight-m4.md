# EXknight Mini Keyboard M4 - Integration Reference

## Quick Facts

| Property | Value |
|---|---|
| Model | EXknight M4 |
| Dimensions | 7.5 x 4.5 x 1.2 cm |
| Weight | 40 g |
| Connectivity | Bluetooth 5.0 |
| Latency | 4 ms |
| Range | ~10 m |
| Switch type | Membrane |
| Layout | QWERTY (US or UK selectable) |
| Material | ABS plastic |
| Battery | Non-standard Li-ion (built-in) |
| Battery life | ~12 h continuous |
| Charge time | ~2 h |
| Charge port | USB (side-mounted) |
| Auto-sleep | 5 minutes of inactivity |
| BT device name | "M4" |

## Integration Notes for Cyberdeck Build

### Physical Fitment

- Extremely compact: 7.5 x 4.5 cm footprint, 1.2 cm thick. Roughly credit-card width, shorter than a playing card.
- 40 g — negligible weight impact on the build.
- Power switch is a slide switch on the side (slide down = on).
- Charging port is on the side edge. Ensure the mounting position leaves both the charge port and power switch accessible.

### Connectivity

- Bluetooth 5.0 only — no wired USB HID mode. The USB port is charge-only.
- Pairs as **"M4"** in BT device scans.
- Auto-detects OS (iOS / Android / Windows). For Linux-based cyberdeck builds, it will likely present as a standard BT HID keyboard; manual OS mode may need to be forced if modifier keys misbehave:
  - `Fn + Q` = iOS mode
  - `Fn + W` = Android mode (usually best for Linux)
  - `Fn + E` = Windows mode
- Paired devices are saved — the keyboard will auto-reconnect on wake.

### Power On & Pairing Procedure

1. Slide the side switch **down** to power on. Backlight briefly flashes then turns off.
2. Hold `Fn + Q` for 2 seconds to enter pairing mode. Backlight will blink.
3. On the host device, scan Bluetooth and connect to **"M4"**.
4. If prompted, type the displayed pairing code on the M4 and press Enter.
5. Once paired, the keyboard remembers the device and auto-reconnects on subsequent power-ons.

To re-pair: forget "M4" on the host, power-cycle the keyboard, hold `Fn + Q` again, and repeat.

### Key Layout & Input

**Basics:**
- Standard QWERTY with `Fn` layer for symbols/special characters.
- `Shift` works normally for capitals.
- **Caps Lock** = double-tap `Shift`.
- Symbol input = `Shift + Number` or `Fn + Letter`.
- Many keys are dual-function via `Fn`. Keycap legends show secondary functions.

**Navigation & Escape:**
- `Fn + 6` = ESC
- `Fn + 7` = Arrow Up
- `Fn + 8` = Arrow Down
- `Fn + 9` = Arrow Left
- `Fn + 0` = Arrow Right

**Accented / Special Characters:**
- `Alt + C` = Ç
- `Alt + S` = ß
- `Alt + E/U/I/N`, then press a letter = accent marks (´ ¨ ^ ~)
- Long-press `Alt + any letter/number` for extended symbols (iOS only; see keycap reference for US/UK layouts)

**OS-Specific:**
- Virtual keyboard toggle key (iOS only; Android requires input method config).
- Voice Dictation via double-tap `Ctrl` (iOS only; must enable in Settings > General > Keyboard > Dictation > Dictation Shortcut > set to Ctrl).

### Backlight

- **Toggle:** Hold `Ctrl + Space` for 3 seconds.
- Coverage varies by manufacturing batch: some units light all keys, others only the top two rows.
- Backlight drains battery faster — factor into duty cycle planning.

### Power Management & Status Indicators

| Indicator | Meaning |
|---|---|
| Keys 1-2 backlight ON | Charging |
| Keys 1-2 backlight OFF | Fully charged |
| Keys 1-2 backlight BLINKING | Low battery |

- **Auto-sleep:** After 5 minutes of inactivity.
- **Wake:** Press any key. The keyboard reconnects automatically to the last paired device.
- For always-on cyberdeck use: consider keeping it on a slow trickle charge via USB, or factor in the any-key wake latency before issuing commands.

## Risks & Limitations

- **No wired fallback.** If BT is unavailable or the radio is busy, there is no USB HID mode.
- **Non-replaceable battery.** Long-term cyberdeck builds should plan for eventual battery degradation.
- **Membrane switches.** Adequate for light/occasional input, not ideal for heavy typing sessions.
- **Compact layout trade-offs.** Dual-function keys and small keycaps reduce typing speed and increase error rate for extended use. Best suited as a secondary/emergency input device.
- **Arrow keys require Fn combo.** `Fn + 7/8/9/0` — no dedicated arrow cluster. Shell/editor navigation will be slower.
- **ESC requires Fn combo.** `Fn + 6` — relevant for vim users or terminal workflows.
- **Some features are iOS-only.** Long-press accents, virtual keyboard toggle, and voice dictation shortcuts only work on iOS.

## Cyberdeck-Specific Recommendations

1. **Linux pairing:** Use `Fn + W` (Android mode) as the starting point. If modifier keys misbehave, try `Fn + E` (Windows mode).
2. **Mounting:** Leave side access for the power slide switch and USB charge port. A recessed pocket or clip mount works well given the 1.2 cm thickness.
3. **Keybinding config:** Remap critical shortcuts around the Fn layer limitations. Ensure ESC (`Fn+6`) and arrow keys (`Fn+7/8/9/0`) are tested in your shell and editor.
4. **Power strategy:** With 12 h battery and 5-min auto-sleep, the keyboard can last days of intermittent cyberdeck use. For long sessions, run a USB cable from the deck's power bus to the keyboard's charge port.
5. **Backlight:** Default off is fine for battery life. Toggle with `Ctrl+Space` (3s hold) only when needed in low-light conditions.

## Source

Product listing: https://manuals.plus/asin/B0FMDWSHS7
