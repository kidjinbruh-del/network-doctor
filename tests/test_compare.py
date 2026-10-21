from __future__ import annotations

import unittest

from network_doctor.compare import (
    baseline_deviations,
    compare_snapshots,
    export_text,
    snapshot_stats,
    snapshot_summary,
)


def make_snapshot(
    snapshot_type: str,
    signals: list[float],
    ccq: list[float],
    noise: float,
    channel: str,
    disconnects: int = 0,
) -> dict:
    registration = [
        {"signal_dbm": signal, "tx_ccq": ccq_value}
        for signal, ccq_value in zip(signals, ccq)
    ]
    logs = [{"message": "disconnected"}] * disconnects
    return {
        "meta": {
            "snapshot_type": snapshot_type,
            "snapshot_label": "Офис" if snapshot_type == "office" else "Лаборатория",
            "collected_at": "2026-10-22T12:00:00+03:00",
            "host": "192.168.88.1",
            "router_identity": "TEST-ROUTER",
            "router_version": "7.19.4",
        },
        "registration_table": registration,
        "wireless_interfaces": [
            {"channel": channel, "noise_floor": noise, "name": "wlan1"}
        ],
        "logs": logs,
        "disconnect_events": logs,
        "spectral_scan": {"noise_floor_avg": noise, "samples": 8},
        "warnings": [],
    }


class ComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.office = make_snapshot(
            "office",
            [-50.0, -60.0],
            [90.0, 80.0],
            -95.0,
            "2412",
        )
        self.lab = make_snapshot(
            "lab",
            [-70.0, -80.0, -90.0],
            [60.0, 70.0, 80.0],
            -105.0,
            "2437",
            disconnects=1,
        )

    def test_stats(self) -> None:
        stats = snapshot_stats(self.office)
        self.assertEqual(stats["clients"], 2)
        self.assertEqual(stats["signal_avg"], -55.0)
        self.assertEqual(stats["signal_min"], -60.0)
        self.assertEqual(stats["ccq_avg"], 85.0)
        self.assertEqual(stats["snr_avg"], 40.0)
        self.assertEqual(stats["noise"], -95.0)
        self.assertEqual(stats["channels"], "2412")

    def test_comparison(self) -> None:
        rows = {row.name: row for row in compare_snapshots(self.office, self.lab)}
        self.assertEqual(rows["Клиенты"].office, "2")
        self.assertEqual(rows["Клиенты"].lab, "3")
        self.assertEqual(rows["Клиенты"].difference, "+1")
        self.assertEqual(rows["Средний сигнал"].office, "-55.0 dBm")
        self.assertEqual(rows["Средний сигнал"].difference, "-25.0 dBm")
        self.assertEqual(rows["Минимальный сигнал"].difference, "-30.0 dBm")
        self.assertEqual(rows["Средний TX CCQ"].difference, "-15.0%")
        self.assertEqual(rows["Шум (Noise floor)"].difference, "-10.0 dBm")
        self.assertEqual(rows["Отключения в логах"].difference, "+1")
        self.assertEqual(rows["Wireless интерфейсы"].difference, "0")
        self.assertEqual(rows["Каналы"].difference, "различаются")

    def test_summary_and_export(self) -> None:
        summary = snapshot_summary(self.office)
        self.assertIn("Клиенты: 2", summary)
        self.assertIn("Шум (Noise floor): -95.0 dBm", summary)

        rows = compare_snapshots(self.office, self.lab)
        exported = export_text(self.office, self.lab, rows)
        self.assertIn("Network Doctor", exported)
        self.assertIn("Параметр", exported)
        self.assertIn("-25.0 dBm", exported)

    def test_missing_values_render_safely(self) -> None:
        empty = {
            "meta": {},
            "registration_table": [],
            "wireless_interfaces": [],
            "logs": [],
            "disconnect_events": [],
            "spectral_scan": {},
            "warnings": ["wireless-интерфейсы не найдены"],
        }
        rows = {row.name: row for row in compare_snapshots(empty, empty)}
        self.assertTrue(rows["Средний сигнал"].office == "—")
        self.assertTrue(rows["Средний SNR"].office == "—")
        self.assertEqual(rows["Отключения в логах"].office, "0")
        self.assertEqual(rows["Каналы"].office, "—")
        exported = export_text(empty, empty, list(rows.values()))
        self.assertIn("wireless-интерфейсы не найдены", exported)

    def test_baseline_deviations(self) -> None:
        worse = make_snapshot(
            "office",
            [-75.0, -85.0],
            [70.0, 65.0],
            -88.0,
            "2412",
            disconnects=3,
        )
        deviations = baseline_deviations(worse, self.office)
        text = "\n".join(deviations)
        self.assertIn("Шум вырос", text)
        self.assertIn("SNR упал", text)
        self.assertIn("CCQ упал", text)
        self.assertIn("Отключений стало больше", text)


if __name__ == "__main__":
    unittest.main()
