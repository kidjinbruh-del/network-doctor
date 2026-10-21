from __future__ import annotations

import unittest

from network_doctor.client import MikroTikClient


class FakeResource:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def get(self) -> list[dict]:
        return self.rows


class FakeBinaryResource:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def call(self, command: str, arguments: dict) -> list[dict]:
        self.calls.append((command, arguments))
        return self.rows


class FakeApi:
    def __init__(self, wireless: list[dict] | None = None) -> None:
        self.wireless = wireless or []
        self.binary = FakeBinaryResource([{"noise-floor": "-97"}])

    def get_resource(self, path: str) -> FakeResource:
        rows = {
            "/system/identity": [{"name": "TEST-ROUTER"}],
            "/system/resource": [{"version": "7.19.4", "board-name": "CHR"}],
            "/interface/wireless/registration-table": [
                {
                    "interface": "wlan1",
                    "mac-address": "AA:BB:CC:DD:EE:FF",
                    "signal-strength": "-61dBm@6Mbps",
                    "tx-ccq": "88%",
                }
            ],
            "/interface/wireless": self.wireless,
            "/log": [
                {
                    "time": "12:00:00",
                    "topics": "wireless,info",
                    "message": "client disconnected",
                }
            ],
        }
        return FakeResource(rows.get(path, []))

    def get_binary_resource(self, path: str) -> FakeBinaryResource:
        return self.binary


class ClientTests(unittest.TestCase):
    def test_collect_snapshot(self) -> None:
        client = MikroTikClient("192.0.2.1", "admin", "secret")
        fake = FakeApi(wireless=[{"name": "wlan1", "channel": "2412/20-Ce/gn"}])
        client._api = fake

        snapshot = client.collect_snapshot("office")

        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["meta"]["router_identity"], "TEST-ROUTER")
        self.assertEqual(snapshot["meta"]["snapshot_label"], "Офис")
        self.assertEqual(snapshot["registration_table"][0]["signal_dbm"], -61.0)
        self.assertEqual(snapshot["registration_table"][0]["tx_ccq"], 88.0)
        self.assertEqual(snapshot["spectral_scan"]["noise_floor_avg"], -97.0)
        self.assertEqual(len(snapshot["disconnect_events"]), 1)
        self.assertEqual(snapshot["topology"]["mikrotik"]["identity"], "TEST-ROUTER")
        self.assertEqual(snapshot["wireless_scan"][0]["channel"], 1)
        self.assertIn("health", snapshot)
        self.assertIn("diagnostics", snapshot)
        self.assertEqual(len(snapshot["clients"]), 1)
        self.assertEqual(fake.binary.calls[0][0], "spectral-scan")
        self.assertEqual(fake.binary.calls[0][1]["interface"], "wlan1")
        self.assertEqual(fake.binary.calls[0][1]["duration"], 8)

    def test_collect_without_wireless(self) -> None:
        client = MikroTikClient("192.0.2.1", "admin", "secret")
        client._api = FakeApi()

        snapshot = client.collect_snapshot("lab")

        self.assertEqual(snapshot["meta"]["snapshot_label"], "Лаборатория")
        self.assertEqual(snapshot["wireless_interfaces"], [])
        self.assertIsNone(snapshot["spectral_scan"]["noise_floor_avg"])
        self.assertTrue(
            any("wireless-интерфейсы не найдены" in item for item in snapshot["warnings"])
        )

    def test_friendly_refused_error(self) -> None:
        client = MikroTikClient("192.0.2.1", "admin", "secret")
        message = client._human_error(
            OSError(
                10061,
                "Подключение не установлено, т.к. конечный компьютер отверг запрос",
            )
        )
        self.assertIn("192.0.2.1:8728", message)
        self.assertIn("/ip service enable api", message)


if __name__ == "__main__":
    unittest.main()
