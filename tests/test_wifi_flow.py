from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import unittest

from altoids.config import AltoidsConfig, DisplayConfig, WifiConfig
from altoids.input_keyboard import KeyboardEvent
from altoids.wifi import ConnectResult, WifiNetwork, WifiStatus
from altoids.ui.system import SystemScreen


@dataclass
class MockAccentStatus:
    whisplay_available: bool = False
    audio_available: bool = False
    led_available: bool = False
    audio_enabled: bool = False
    led_enabled: bool = False
    muted: bool = False
    volume_percent: int = 70
    sleeping: bool = False
    audio_status: str = "ok"
    audio_error: str = ""
    last_cue: str = "idle"


class MockAccents:
    def __init__(self) -> None:
        self.triggered: list[str] = []
        self._status = MockAccentStatus()

    @property
    def status(self) -> MockAccentStatus:
        return self._status

    def trigger(self, cue: str) -> None:
        self.triggered.append(cue)


class MockWifiManager:
    def __init__(self, networks: list[WifiNetwork] | None = None) -> None:
        self._networks = networks or []
        self._status = WifiStatus()
        self._connect_result = ConnectResult()
        self.connect_calls: list[tuple[str, str]] = []

    @property
    def available(self) -> bool:
        return True

    def status(self, allow_refresh: bool = True) -> WifiStatus:
        return self._status

    def scan(self, force: bool = False) -> list[WifiNetwork]:
        return list(self._networks)

    def connect_async(self, ssid: str, password: str = "") -> None:
        self.connect_calls.append((ssid, password))

    def poll_connect(self) -> ConnectResult:
        return ConnectResult(
            state=self._connect_result.state,
            message=self._connect_result.message,
            ssid=self._connect_result.ssid,
        )

    def reset_connect(self) -> None:
        self._connect_result = ConnectResult()

    def poll(self) -> None:
        pass


SAMPLE_NETWORKS = [
    WifiNetwork(ssid="HomeWifi", signal=80, security="WPA2", known=True),
    WifiNetwork(ssid="CoffeeShop", signal=60, security="WPA2"),
    WifiNetwork(ssid="OpenNet", signal=45, security=""),
    WifiNetwork(ssid="Neighbor", signal=30, security="WPA3 SAE"),
]


def _fake_app(networks: list[WifiNetwork] | None = None) -> SimpleNamespace:
    config = AltoidsConfig(
        root_dir=Path("/fake"),
        display=DisplayConfig(width=320, height=240),
        wifi=WifiConfig(),
    )
    wifi = MockWifiManager(networks)
    accents = MockAccents()
    bluetooth_status = SimpleNamespace(connected=False, device_name="")
    font = _stub_font()
    return SimpleNamespace(
        config=config,
        wifi=wifi,
        accents=accents,
        bluetooth_status=bluetooth_status,
        font=font,
        side_bar_width=0,
        system_snapshot=lambda: {
            "cpu_pct": 0.1, "mem_pct": 0.3, "temperature_c": 45.0,
            "temperature_pct": 0.45, "temperature_label": "45C",
            "temperature_hot": False, "disk_label": "2.0G / 8.0G",
            "uptime": "1h 5m", "ip_address": "192.168.1.75",
            "terminal_windows": 2,
        },
    )


def _stub_font():
    """Return an object with a getbbox method for draw_label compatibility."""
    return SimpleNamespace(getbbox=lambda text: (0, 0, len(text) * 7, 12))


def _key(key: str, text: str = "", ctrl: bool = False, alt: bool = False) -> KeyboardEvent:
    return KeyboardEvent(key=key, raw_key="", text=text, ctrl=ctrl, alt=alt)


class WifiFlowTest(unittest.TestCase):
    def _make_screen(self, networks: list[WifiNetwork] | None = None) -> tuple[SystemScreen, SimpleNamespace]:
        app = _fake_app(networks)
        screen = SystemScreen(SimpleNamespace(app=app))
        return screen, app

    def test_w_key_opens_wifi_detail_in_roster_state(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        self.assertEqual(screen.detail_active, "wifi")
        self.assertEqual(screen.wifi_state, "roster")
        self.assertEqual(len(screen.wifi_networks), 4)

    def test_up_down_scrolls_selection_clamped(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))

        self.assertEqual(screen.wifi_selection, 0)
        screen.on_keyboard_event(_key("up"))
        self.assertEqual(screen.wifi_selection, 0)  # clamped at top

        screen.on_keyboard_event(_key("down"))
        self.assertEqual(screen.wifi_selection, 1)
        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("down"))
        self.assertEqual(screen.wifi_selection, 3)
        screen.on_keyboard_event(_key("down"))
        self.assertEqual(screen.wifi_selection, 3)  # clamped at bottom

    def test_enter_on_known_network_skips_password(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        # First network is known
        screen.on_keyboard_event(_key("enter"))
        self.assertEqual(screen.wifi_state, "connecting")
        self.assertEqual(app.wifi.connect_calls, [("HomeWifi", "")])

    def test_enter_on_open_network_skips_password(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        # Navigate to OpenNet (index 2)
        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("down"))
        self.assertEqual(screen.wifi_selection, 2)
        screen.on_keyboard_event(_key("enter"))
        self.assertEqual(screen.wifi_state, "connecting")
        self.assertEqual(app.wifi.connect_calls, [("OpenNet", "")])

    def test_enter_on_unknown_secured_network_prompts_password(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        # Navigate to CoffeeShop (index 1, not known, WPA2)
        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("enter"))
        self.assertEqual(screen.wifi_state, "password")
        self.assertEqual(screen.wifi_password, "")

    def test_password_typing_and_backspace(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("enter"))
        self.assertEqual(screen.wifi_state, "password")

        for ch in "secret":
            screen.on_keyboard_event(_key(ch, text=ch))
        self.assertEqual(screen.wifi_password, "secret")

        screen.on_keyboard_event(_key("backspace"))
        self.assertEqual(screen.wifi_password, "secre")

    def test_password_tab_toggles_visibility(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("enter"))

        self.assertFalse(screen.wifi_password_visible)
        screen.on_keyboard_event(_key("tab"))
        self.assertTrue(screen.wifi_password_visible)
        screen.on_keyboard_event(_key("tab"))
        self.assertFalse(screen.wifi_password_visible)

    def test_password_enter_submits_with_correct_ssid_and_password(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("down"))  # CoffeeShop
        screen.on_keyboard_event(_key("enter"))  # password prompt

        for ch in "mypass":
            screen.on_keyboard_event(_key(ch, text=ch))
        screen.on_keyboard_event(_key("enter"))

        self.assertEqual(screen.wifi_state, "connecting")
        self.assertEqual(app.wifi.connect_calls, [("CoffeeShop", "mypass")])

    def test_empty_password_enter_does_nothing(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("enter"))
        self.assertEqual(screen.wifi_state, "password")

        screen.on_keyboard_event(_key("enter"))
        self.assertEqual(screen.wifi_state, "password")  # stays in password
        self.assertEqual(app.wifi.connect_calls, [])

    def test_connect_success_transitions_to_result_and_triggers_accent(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("enter"))  # known, goes to connecting
        self.assertEqual(screen.wifi_state, "connecting")

        # Simulate success
        app.wifi._connect_result = ConnectResult(state="success", message="192.168.1.100", ssid="HomeWifi")
        screen.update(0.1)

        self.assertEqual(screen.wifi_state, "result")
        self.assertTrue(screen._wifi_result_success)
        self.assertIn("wifi_success", app.accents.triggered)

    def test_connect_failure_transitions_to_result_and_triggers_accent(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("enter"))
        self.assertEqual(screen.wifi_state, "connecting")

        app.wifi._connect_result = ConnectResult(state="failed", message="auth failed", ssid="HomeWifi")
        screen.update(0.1)

        self.assertEqual(screen.wifi_state, "result")
        self.assertFalse(screen._wifi_result_success)
        self.assertIn("wifi_error", app.accents.triggered)

    def test_any_key_from_result_returns_to_roster(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("enter"))
        app.wifi._connect_result = ConnectResult(state="success", message="ok", ssid="HomeWifi")
        screen.update(0.1)
        self.assertEqual(screen.wifi_state, "result")

        screen.on_keyboard_event(_key("enter"))
        self.assertEqual(screen.wifi_state, "roster")

    def test_esc_from_roster_leaves_detail(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        self.assertEqual(screen.detail_active, "wifi")

        screen.on_keyboard_event(_key("escape"))
        self.assertIsNone(screen.detail_active)

    def test_esc_from_password_returns_to_roster_and_clears_password(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("enter"))
        for ch in "partial":
            screen.on_keyboard_event(_key(ch, text=ch))

        screen.on_keyboard_event(_key("escape"))

        self.assertEqual(screen.wifi_state, "roster")
        self.assertEqual(screen.wifi_password, "")

    def test_r_key_triggers_rescan_and_resets_selection(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("down"))
        self.assertEqual(screen.wifi_selection, 2)

        screen.on_keyboard_event(_key("r"))
        self.assertEqual(screen.wifi_selection, 0)

    def test_button_hints_change_per_state(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        # Overview hints
        hints = screen.get_button_hints()
        self.assertEqual(hints.get("right_bottom"), "detail")

        screen.on_keyboard_event(_key("w"))
        hints = screen.get_button_hints()
        self.assertEqual(hints.get("right_top"), "join")

        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("enter"))  # password
        hints = screen.get_button_hints()
        self.assertEqual(hints.get("right_top"), "ok")
        self.assertEqual(hints.get("right_bottom"), "cancel")

    def test_leave_detail_resets_all_wifi_state(self) -> None:
        screen, app = self._make_screen(SAMPLE_NETWORKS)
        screen.on_keyboard_event(_key("w"))
        screen.on_keyboard_event(_key("down"))
        screen.on_keyboard_event(_key("enter"))
        for ch in "pw":
            screen.on_keyboard_event(_key(ch, text=ch))

        screen.on_keyboard_event(_key("escape"))  # back to roster
        screen.on_keyboard_event(_key("escape"))  # leave detail

        self.assertIsNone(screen.detail_active)
        self.assertEqual(screen.wifi_state, "roster")
        self.assertEqual(screen.wifi_selection, 0)
        self.assertEqual(screen.wifi_password, "")
        self.assertFalse(screen.wifi_password_visible)
        self.assertIn("W", screen.status_line.upper())


if __name__ == "__main__":
    unittest.main()
