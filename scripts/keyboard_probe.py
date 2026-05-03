#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import select
import sys
import textwrap
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import evdev


IDLE_TIMEOUT_SECONDS = 1.0
ARM_TIMEOUT_SECONDS = 20.0
FREE_EXPLORE_SECONDS = 45.0


@dataclass(slots=True)
class TestCase:
    slug: str
    prompt: str
    note: str = ""


@dataclass(slots=True)
class ObservedEvent:
    timestamp: float
    keycode: str
    keystate: str
    scancode: int
    value: int


TEST_CASES = [
    TestCase("wake-any-key", "Press any single key to confirm the keyboard is awake and reconnecting cleanly."),
    TestCase("meta-left-or-right", "Press the Windows/Cmd key once.", "This tells us whether the cyberdeck meta key arrives as KEY_LEFTMETA/KEY_RIGHTMETA."),
    TestCase("tab", "Press Tab once."),
    TestCase("backspace", "Press Backspace once."),
    TestCase("enter", "Press Enter once."),
    TestCase("space", "Press Space once."),
    TestCase("shift-left-or-right", "Press Shift once."),
    TestCase("ctrl", "Press Ctrl once."),
    TestCase("alt", "Press Alt once."),
    TestCase("a", "Press A once."),
    TestCase("b", "Press B once."),
    TestCase("c", "Press C once."),
    TestCase("d", "Press D once."),
    TestCase("e", "Press E once."),
    TestCase("f", "Press F once."),
    TestCase("g", "Press G once."),
    TestCase("h", "Press H once."),
    TestCase("i", "Press I once."),
    TestCase("j", "Press J once."),
    TestCase("k", "Press K once."),
    TestCase("l", "Press L once."),
    TestCase("m", "Press M once."),
    TestCase("n", "Press N once."),
    TestCase("o", "Press O once."),
    TestCase("p", "Press P once."),
    TestCase("q", "Press Q once."),
    TestCase("r", "Press R once."),
    TestCase("s", "Press S once."),
    TestCase("t", "Press T once."),
    TestCase("u", "Press U once."),
    TestCase("v", "Press V once."),
    TestCase("w", "Press W once."),
    TestCase("x", "Press X once."),
    TestCase("y", "Press Y once."),
    TestCase("z", "Press Z once."),
    TestCase("1", "Press 1 once."),
    TestCase("2", "Press 2 once."),
    TestCase("3", "Press 3 once."),
    TestCase("4", "Press 4 once."),
    TestCase("5", "Press 5 once."),
    TestCase("6", "Press 6 once."),
    TestCase("7", "Press 7 once."),
    TestCase("8", "Press 8 once."),
    TestCase("9", "Press 9 once."),
    TestCase("0", "Press 0 once."),
    TestCase("shift-a", "Hold Shift and press A."),
    TestCase("shift-1", "Hold Shift and press 1."),
    TestCase("shift-2", "Hold Shift and press 2."),
    TestCase("shift-3", "Hold Shift and press 3."),
    TestCase("shift-4", "Hold Shift and press 4."),
    TestCase("shift-5", "Hold Shift and press 5."),
    TestCase("shift-6", "Hold Shift and press 6."),
    TestCase("shift-7", "Hold Shift and press 7."),
    TestCase("shift-8", "Hold Shift and press 8."),
    TestCase("shift-9", "Hold Shift and press 9."),
    TestCase("shift-0", "Hold Shift and press 0."),
    TestCase("double-shift", "Double-tap Shift.", "The docs claim this toggles Caps Lock; we need to see the actual Linux-visible event sequence."),
    TestCase("fn-6", "Hold Fn and press 6.", "Documented as ESC."),
    TestCase("fn-7", "Hold Fn and press 7.", "Documented as Arrow Up."),
    TestCase("fn-8", "Hold Fn and press 8.", "Documented as Arrow Down."),
    TestCase("fn-9", "Hold Fn and press 9.", "Documented as Arrow Left."),
    TestCase("fn-0", "Hold Fn and press 0.", "Documented as Arrow Right."),
    TestCase("fn-w", "Hold Fn and press W.", "Documented as Android mode. This may change keyboard mode; run this near the end."),
    TestCase("fn-e", "Hold Fn and press E.", "Documented as Windows mode. This may change keyboard mode; run this near the end."),
    TestCase("alt-c", "Hold Alt and press C.", "Docs claim this may emit Ç."),
    TestCase("alt-s", "Hold Alt and press S.", "Docs claim this may emit ß."),
    TestCase("alt-e-then-a", "Press Alt+E, release, then press A.", "Accent composition test from the vendor notes."),
    TestCase("alt-u-then-u", "Press Alt+U, release, then press U.", "Accent composition test from the vendor notes."),
    TestCase("alt-i-then-i", "Press Alt+I, release, then press I.", "Accent composition test from the vendor notes."),
    TestCase("alt-n-then-n", "Press Alt+N, release, then press N.", "Accent composition test from the vendor notes."),
    TestCase("ctrl-space-hold", "Hold Ctrl+Space for 3 seconds.", "Vendor claims this toggles backlight. We want to see whether Linux receives normal key events."),
    TestCase("extra-fn-1", "Press one undocumented Fn combo that is physically printed on the keyboard.", "Pick a visible Fn-labeled combo not already tested and note which one in the final report."),
    TestCase("extra-fn-2", "Press another undocumented Fn combo that is physically printed on the keyboard.", "Pick a second visible Fn-labeled combo not already tested and note which one in the final report."),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive Bluetooth keyboard probe for Raspberry Pi over SSH.")
    parser.add_argument("--device-substring", default="M4", help="Substring used to auto-select the evdev device by name.")
    parser.add_argument("--output-dir", default="artifacts/keyboard-probe", help="Directory where JSON and Markdown reports are written.")
    return parser.parse_args()


def choose_device(device_substring: str) -> evdev.InputDevice:
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    keyboards = [device for device in devices if evdev.ecodes.EV_KEY in device.capabilities()]
    if not keyboards:
        raise RuntimeError("No evdev keyboard devices found.")

    exact = [device for device in keyboards if device_substring.lower() in device.name.lower()]
    candidates = exact or keyboards

    print("\nAvailable keyboard-like devices:\n")
    for index, device in enumerate(candidates, start=1):
        print(f"  {index}. {device.name!r} at {device.path}")

    if len(candidates) == 1:
        print(f"\nAuto-selecting {candidates[0].name!r}.\n")
        return candidates[0]

    while True:
        choice = input("Select device number: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                print()
                return candidates[idx]
        print("Invalid selection.")


def keystate_name(value: int) -> str:
    return {0: "up", 1: "down", 2: "hold"}.get(value, str(value))


def wait_for_case_events(device: evdev.InputDevice) -> list[ObservedEvent]:
    observed: list[ObservedEvent] = []
    first_event_at: float | None = None
    deadline = time.monotonic() + ARM_TIMEOUT_SECONDS

    while True:
        timeout = min(0.25, max(0.0, deadline - time.monotonic()))
        ready, _, _ = select.select([device.fd], [], [], timeout)
        if ready:
            for raw_event in device.read():
                if raw_event.type != evdev.ecodes.EV_KEY:
                    continue
                key_event = evdev.categorize(raw_event)
                keycode = key_event.keycode
                if isinstance(keycode, list):
                    keycode = ",".join(keycode)
                observed.append(
                    ObservedEvent(
                        timestamp=raw_event.timestamp(),
                        keycode=str(keycode),
                        keystate=keystate_name(raw_event.value),
                        scancode=key_event.scancode,
                        value=raw_event.value,
                    )
                )
                if first_event_at is None:
                    first_event_at = time.monotonic()
                    deadline = first_event_at + IDLE_TIMEOUT_SECONDS
                else:
                    deadline = time.monotonic() + IDLE_TIMEOUT_SECONDS
        elif first_event_at is None and time.monotonic() >= deadline:
            return []
        elif first_event_at is not None and time.monotonic() >= deadline:
            return observed


def summarize_events(events: list[ObservedEvent]) -> dict[str, object]:
    keycodes = [event.keycode for event in events if event.keystate == "down"]
    unique = []
    seen = set()
    for keycode in keycodes:
        if keycode not in seen:
            seen.add(keycode)
            unique.append(keycode)
    return {
        "pressed_keycodes": keycodes,
        "unique_pressed_keycodes": unique,
        "event_count": len(events),
    }


def run_guided_probe(device: evdev.InputDevice) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    print(textwrap.dedent(
        f"""
        Guided keyboard probe starting.

        Device: {device.name!r} at {device.path}
        The probe will run straight through.
        For each step:
          - read the instruction printed in SSH
          - perform the requested key or combo on the M4
          - wait for the script to print the observed result

        If you miss a step or press the wrong thing, let it continue.
        We can interpret the raw report afterward.
        """
    ).strip())
    print()

    for index, case in enumerate(TEST_CASES, start=1):
        print(f"[{index}/{len(TEST_CASES)}] {case.slug}")
        print(f"  {case.prompt}")
        if case.note:
            print(f"  Note: {case.note}")
        print("  perform the key or combo on the M4 now...")
        events = wait_for_case_events(device)
        summary = summarize_events(events)
        status = "ok" if events else "no-events"
        results.append(
            {
                "slug": case.slug,
                "prompt": case.prompt,
                "note": case.note,
                "status": status,
                "events": [asdict(event) for event in events],
                "summary": summary,
            }
        )
        if events:
            print(f"  observed: {summary['unique_pressed_keycodes']}\n")
        else:
            print("  observed: no key events\n")

    return results


def run_free_explore(device: evdev.InputDevice, known_keycodes: set[str]) -> dict[str, object]:
    print(textwrap.dedent(
        f"""
        Free exploration phase.

        For the next {FREE_EXPLORE_SECONDS:.0f} seconds, press any extra keys or combinations
        you can find on the keyboard that were not explicitly covered by the guided plan.

        Good candidates:
          - undocumented Fn combos
          - media-like combos
          - OS-specific keys
          - any weird legends printed on the keycaps
        """
    ).strip())
    print("Exploration started: press extra keys on the M4 now...")

    observed: list[ObservedEvent] = []
    deadline = time.monotonic() + FREE_EXPLORE_SECONDS
    while time.monotonic() < deadline:
        timeout = min(0.25, max(0.0, deadline - time.monotonic()))
        ready, _, _ = select.select([device.fd], [], [], timeout)
        if not ready:
            continue
        for raw_event in device.read():
            if raw_event.type != evdev.ecodes.EV_KEY:
                continue
            key_event = evdev.categorize(raw_event)
            keycode = key_event.keycode
            if isinstance(keycode, list):
                keycode = ",".join(keycode)
            observed.append(
                ObservedEvent(
                    timestamp=raw_event.timestamp(),
                    keycode=str(keycode),
                    keystate=keystate_name(raw_event.value),
                    scancode=key_event.scancode,
                    value=raw_event.value,
                )
            )

    summary = summarize_events(observed)
    discovered = [
        keycode for keycode in summary["unique_pressed_keycodes"]
        if keycode not in known_keycodes
    ]
    print(f"Exploration finished. Newly seen keycodes: {discovered}\n")
    return {
        "slug": "free-explore",
        "prompt": f"Press any additional undocumented keys or combos for {FREE_EXPLORE_SECONDS:.0f} seconds.",
        "note": "Use this to discover unexpected Linux-visible keycodes that were not in the guided script.",
        "status": "ok" if observed else "no-events",
        "events": [asdict(event) for event in observed],
        "summary": {
            **summary,
            "new_keycodes_vs_guided": discovered,
        },
    }


def write_reports(output_dir: Path, device: evdev.InputDevice, results: list[dict[str, object]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "generated_at": timestamp,
        "device_name": device.name,
        "device_path": device.path,
        "results": results,
    }
    json_path = output_dir / f"keyboard-probe-{timestamp}.json"
    md_path = output_dir / f"keyboard-probe-{timestamp}.md"
    json_path.write_text(json.dumps(payload, indent=2))

    lines = [
        "# Keyboard Probe Report",
        "",
        f"- Generated at: `{timestamp}`",
        f"- Device: `{device.name}`",
        f"- Path: `{device.path}`",
        "",
        "## Results",
        "",
    ]
    for result in results:
        lines.append(f"### {result['slug']}")
        lines.append("")
        lines.append(f"- Prompt: {result['prompt']}")
        if result["note"]:
            lines.append(f"- Note: {result['note']}")
        lines.append(f"- Status: `{result['status']}`")
        summary = result.get("summary") or {}
        if summary:
            lines.append(f"- Unique pressed keycodes: `{summary.get('unique_pressed_keycodes', [])}`")
            lines.append(f"- Event count: `{summary.get('event_count', 0)}`")
        if result["events"]:
            lines.append("")
            lines.append("| timestamp | keycode | state | scancode | value |")
            lines.append("|---|---|---|---:|---:|")
            for event in result["events"]:
                lines.append(
                    f"| {event['timestamp']:.6f} | `{event['keycode']}` | `{event['keystate']}` | {event['scancode']} | {event['value']} |"
                )
        lines.append("")
    md_path.write_text("\n".join(lines))
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")


def main() -> int:
    args = parse_args()
    device = choose_device(args.device_substring)
    try:
        device.grab()
        grabbed = True
    except OSError:
        grabbed = False
        print("Warning: could not grab the device exclusively. Continuing without grab.\n")

    try:
        results = run_guided_probe(device)
        known_keycodes: set[str] = set()
        for result in results:
            summary = result.get("summary") or {}
            for keycode in summary.get("unique_pressed_keycodes", []):
                known_keycodes.add(keycode)
        results.append(run_free_explore(device, known_keycodes))
    finally:
        if grabbed:
            device.ungrab()
        device.close()

    write_reports(Path(args.output_dir), device, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
