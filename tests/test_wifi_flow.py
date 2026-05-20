from __future__ import annotations

from types import SimpleNamespace
import unittest

from altoids.ui.base import ScreenContext
from altoids.ui.system import SystemScreen
from altoids.wifi import WifiNetwork


class FakeWifi:
    def __init__(self) -> None:
        self.last_message = "scan ok 3 nets"
        self.connected_ssid = ""
        self.networks = [
            WifiNetwork("Lab", 80, "WPA2"),
            WifiNetwork("Cafe", 70, ""),
            WifiNetwork("Pocket", 60, "WPA2"),
        ]

    def scan(self, force: bool = False, allow_refresh: bool = True) -> list[WifiNetwork]:
        return list(self.networks)

    def connect(self, network: WifiNetwork, password: str | None = None) -> tuple[bool, str]:
        self.connected_ssid = network.ssid
        return True, f"connected {network.ssid}"


class WifiFlowTest(unittest.TestCase):
    def make_screen(self) -> SystemScreen:
        app = SimpleNamespace(wifi=FakeWifi())
        return SystemScreen(ScreenContext(app=app))

    def test_wifi_buttons_wrap_selection(self) -> None:
        screen = self.make_screen()
        screen._enter_wifi_config(force_scan=True)

        screen._select_wifi_network(-1)
        self.assertEqual(screen.selected_index, 2)
        self.assertEqual(screen.status_line, "3/3 selected Pocket")

        screen._select_wifi_network(1)
        self.assertEqual(screen.selected_index, 0)
        self.assertEqual(screen.status_line, "1/3 selected Lab")

    def test_refresh_preserves_selected_ssid(self) -> None:
        screen = self.make_screen()
        screen._enter_wifi_config(force_scan=True)
        screen._select_wifi_network(1)
        screen.status_line = "2/3 selected Cafe"

        screen.context.app.wifi.networks = [
            WifiNetwork("Pocket", 65, "WPA2"),
            WifiNetwork("Cafe", 75, ""),
            WifiNetwork("Lab", 85, "WPA2"),
        ]

        self.assertTrue(screen.update(1.0))
        self.assertEqual(screen.networks[screen.selected_index].ssid, "Cafe")
        self.assertEqual(screen.status_line, "2/3 selected Cafe")

    def test_forced_scan_prefers_active_network(self) -> None:
        screen = self.make_screen()
        screen.context.app.wifi.networks = [
            WifiNetwork("Lab", 80, "WPA2"),
            WifiNetwork("Cafe", 70, ""),
            WifiNetwork("Pocket", 60, "WPA2", active=True),
        ]

        screen._enter_wifi_config(force_scan=True)

        self.assertEqual(screen.networks[screen.selected_index].ssid, "Pocket")

    def test_connect_uses_selected_network_not_active_network(self) -> None:
        screen = self.make_screen()
        screen.context.app.accents = SimpleNamespace(trigger=lambda cue: None)
        screen.context.app.wifi.networks = [
            WifiNetwork("Lab", 80, "WPA2", active=True),
            WifiNetwork("Cafe", 70, ""),
        ]
        screen._enter_wifi_config(force_scan=True)
        screen._select_wifi_network(1)

        screen._connect_selected_network()

        self.assertEqual(screen.context.app.wifi.connected_ssid, "Cafe")


if __name__ == "__main__":
    unittest.main()
