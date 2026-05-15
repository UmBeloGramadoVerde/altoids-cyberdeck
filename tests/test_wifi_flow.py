from __future__ import annotations

from types import SimpleNamespace
import time
import unittest

from altoids.input_keyboard import KeyboardEvent
from altoids.ui.base import ScreenContext
from altoids.ui.system import SystemScreen
from altoids.wifi import WifiManager, WifiNetwork


class FakeWifi:
    def __init__(self) -> None:
        self.last_message = "scan ok 3 nets"
        self.connected_ssid = ""
        self.passwords: dict[str, str] = {}
        self.scan_calls: list[bool] = []
        self.scan_refresh_calls: list[bool] = []
        self.networks = [
            WifiNetwork("Lab", 80, "WPA2"),
            WifiNetwork("Cafe", 70, ""),
            WifiNetwork("Pocket", 60, "WPA2"),
        ]

    def scan(self, force: bool = False, allow_refresh: bool = True) -> list[WifiNetwork]:
        self.scan_calls.append(force)
        self.scan_refresh_calls.append(allow_refresh)
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
        screen._scan_wifi_now(force_scan=True)

        screen._select_wifi_network(-1)
        self.assertEqual(screen.selected_index, 2)
        self.assertEqual(screen.status_line, "3/3 selected Pocket")

        screen._select_wifi_network(1)
        self.assertEqual(screen.selected_index, 0)
        self.assertEqual(screen.status_line, "1/3 selected Lab")

    def test_refresh_preserves_selected_ssid(self) -> None:
        screen = self.make_screen()
        screen._scan_wifi_now(force_scan=True)
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

        screen._scan_wifi_now(force_scan=True)

        self.assertEqual(screen.networks[screen.selected_index].ssid, "Pocket")

    def test_connect_uses_selected_network_not_active_network(self) -> None:
        screen = self.make_screen()
        screen.context.app.accents = SimpleNamespace(trigger=lambda cue: None)
        screen.context.app.wifi.networks = [
            WifiNetwork("Lab", 80, "WPA2", active=True),
            WifiNetwork("Cafe", 70, ""),
        ]
        screen._scan_wifi_now(force_scan=True)
        screen._select_wifi_network(1)

        screen._connect_selected_network()
        self.drain_wifi(screen)

        self.assertEqual(screen.context.app.wifi.connected_ssid, "Cafe")

    def test_rescan_keeps_existing_roster_while_busy(self) -> None:
        screen = self.make_screen()
        screen._scan_wifi_now(force_scan=True)

        screen._enter_wifi_config(force_scan=True)

        self.assertEqual([network.ssid for network in screen.networks], ["Lab", "Cafe", "Pocket"])
        self.assertEqual(screen._wifi_busy, "scan")
        self.assertIn("scanning wifi", screen.status_line)
        self.drain_wifi(screen)
        self.assertEqual(screen._wifi_busy, "")

    def test_opening_wireless_detail_uses_cache_without_scanning(self) -> None:
        screen = self.make_screen()

        self.assertTrue(screen.on_keyboard_event(KeyboardEvent(key="w", raw_key="KEY_W")))

        self.assertEqual(screen.detail_active, "wireless")
        self.assertEqual(screen._wifi_busy, "")
        self.assertEqual(screen.context.app.wifi.scan_calls, [False])
        self.assertEqual(screen.context.app.wifi.scan_refresh_calls, [False])

    def test_r_key_starts_wireless_scan_from_detail(self) -> None:
        screen = self.make_screen()
        screen.on_keyboard_event(KeyboardEvent(key="w", raw_key="KEY_W"))

        self.assertTrue(screen.on_keyboard_event(KeyboardEvent(key="r", raw_key="KEY_R")))

        self.assertEqual(screen._wifi_busy, "scan")
        self.drain_wifi(screen)
        self.assertIn(True, screen.context.app.wifi.scan_calls)

    def test_button_y_joins_selected_wireless_network(self) -> None:
        screen = self.make_screen()
        screen.context.app.accents = SimpleNamespace(trigger=lambda cue: None)
        screen._scan_wifi_now(force_scan=True)
        screen._select_wifi_network(1)

        self.assertTrue(screen._on_wireless_detail_button("Y", long_press=False))
        self.drain_wifi(screen)

        self.assertEqual(screen.context.app.wifi.connected_ssid, "Cafe")

    def test_panel_shortcut_toggles_same_detail_screen_closed(self) -> None:
        screen = self.make_screen()

        for key, panel in [("s", "core"), ("p", "load"), ("l", "link"), ("r", "rig"), ("a", "cues")]:
            self.assertTrue(screen.on_keyboard_event(KeyboardEvent(key=key, raw_key=f"KEY_{key.upper()}")))
            self.assertEqual(screen.detail_active, panel)
            self.assertTrue(screen.on_keyboard_event(KeyboardEvent(key=key, raw_key=f"KEY_{key.upper()}")))
            self.assertIsNone(screen.detail_active)

    def drain_wifi(self, screen: SystemScreen) -> None:
        deadline = time.monotonic() + 1.0
        while screen._wifi_busy and time.monotonic() < deadline:
            screen.update(0.2)
            time.sleep(0.01)
        screen.update(0.2)
        self.assertEqual(screen._wifi_busy, "")


class FakeNmcliWifi(WifiManager):
    @property
    def available(self) -> bool:
        return True

    def _run(self, *args: str):
        command = ("nmcli", *args)
        if args[:4] == ("-m", "multiline", "-f", "DEVICE,TYPE,STATE,CONNECTION"):
            return SimpleNamespace(
                returncode=0,
                stdout="\n".join(
                    [
                        "GENERAL.DEVICE:wlan0",
                        "GENERAL.TYPE:802-11-wireless",
                        "GENERAL.STATE:100 (connected)",
                        "GENERAL.CONNECTION:Home profile",
                        "",
                    ]
                ),
                stderr="",
                args=command,
            )
        if args[:4] == ("-m", "multiline", "-f", "IN-USE,SSID,SIGNAL,SECURITY"):
            return SimpleNamespace(
                returncode=0,
                stdout="\n".join(
                    [
                        "IN-USE:*",
                        "SSID:Actual SSID",
                        "SIGNAL:73",
                        "SECURITY:WPA2",
                        "",
                    ]
                ),
                stderr="",
                args=command,
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="", args=command)


class FakeScanWifi(WifiManager):
    @property
    def available(self) -> bool:
        return True

    def _run(self, *args: str):
        command = ("nmcli", *args)
        if args[:2] == ("radio", "wifi"):
            return SimpleNamespace(returncode=0, stdout="enabled\n", stderr="", args=command)
        if args[:4] == ("-m", "multiline", "-f", "IN-USE,BSSID,SSID,CHAN,SIGNAL,SECURITY"):
            return SimpleNamespace(
                returncode=0,
                stdout="\n".join(
                    [
                        "IN-USE:*",
                        "BSSID:AA:AA:AA:AA:AA:01",
                        "SSID:Mesh",
                        "CHAN:1",
                        "SIGNAL:50",
                        "SECURITY:WPA2",
                        "",
                        "IN-USE:",
                        "BSSID:AA:AA:AA:AA:AA:02",
                        "SSID:Mesh",
                        "CHAN:6",
                        "SIGNAL:80",
                        "SECURITY:WPA2",
                        "",
                        "IN-USE:",
                        "BSSID:BB:BB:BB:BB:BB:01",
                        "SSID:Cafe",
                        "CHAN:11",
                        "SIGNAL:55",
                        "SECURITY:",
                        "",
                    ]
                ),
                stderr="",
                args=command,
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="", args=command)


class FakeScanNoBlankWifi(FakeScanWifi):
    def _run(self, *args: str):
        command = ("nmcli", *args)
        if args[:2] == ("radio", "wifi"):
            return SimpleNamespace(returncode=0, stdout="enabled\n", stderr="", args=command)
        if args[:4] == ("-m", "multiline", "-f", "IN-USE,BSSID,SSID,CHAN,SIGNAL,SECURITY"):
            return SimpleNamespace(
                returncode=0,
                stdout="\n".join(
                    [
                        "IN-USE:*",
                        "BSSID:AA:AA:AA:AA:AA:01",
                        "SSID:Mesh",
                        "CHAN:1",
                        "SIGNAL:50",
                        "SECURITY:WPA2",
                        "IN-USE:",
                        "BSSID:AA:AA:AA:AA:AA:02",
                        "SSID:Mesh",
                        "CHAN:6",
                        "SIGNAL:80",
                        "SECURITY:WPA2",
                        "IN-USE:",
                        "BSSID:BB:BB:BB:BB:BB:01",
                        "SSID:Cafe",
                        "CHAN:11",
                        "SIGNAL:55",
                        "SECURITY:",
                    ]
                ),
                stderr="",
                args=command,
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="", args=command)


class WifiManagerTest(unittest.TestCase):
    def test_status_reports_active_ssid_not_connection_profile_name(self) -> None:
        status = FakeNmcliWifi().status()

        self.assertTrue(status.connected)
        self.assertEqual(status.ssid, "Actual SSID")
        self.assertEqual(status.signal, 73)

    def test_scan_preserves_each_visible_access_point(self) -> None:
        manager = FakeScanWifi()

        networks = manager.scan(force=False)

        self.assertEqual([network.ssid for network in networks], ["Mesh", "Mesh", "Cafe"])
        self.assertTrue(networks[0].active)
        self.assertEqual(networks[0].signal, 50)
        self.assertEqual(networks[1].signal, 80)
        self.assertEqual(networks[1].channel, "6")
        self.assertEqual(manager.last_message, "scan ok 3 nets")

    def test_scan_preserves_access_points_without_blank_record_separators(self) -> None:
        manager = FakeScanNoBlankWifi()

        networks = manager.scan(force=False)

        self.assertEqual([network.ssid for network in networks], ["Mesh", "Mesh", "Cafe"])
        self.assertEqual([network.bssid for network in networks], ["AA:AA:AA:AA:AA:01", "AA:AA:AA:AA:AA:02", "BB:BB:BB:BB:BB:01"])
        self.assertEqual(manager.last_message, "scan ok 3 nets")


if __name__ == "__main__":
    unittest.main()
