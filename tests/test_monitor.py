from __future__ import annotations

from contextlib import contextmanager

from network_doctor.connections import ConnectionConfig
from network_doctor.db import Database
from network_doctor.monitor import MonitorService


class FakeClient:
    def __init__(self) -> None:
        self.health_metrics = None

    def collect_snapshot(self, snapshot_type: str, *, health_metrics=None):
        self.health_metrics = health_metrics
        return {
            "schema_version": 2,
            "meta": {
                "snapshot_type": snapshot_type,
                "collected_at": "2026-10-22T12:00:00+03:00",
                "host": "fake",
            },
            "clients": [],
            "wireless_scan": [],
            "diagnostics": {},
        }


def test_monitor_passes_icmp_metrics_to_collector(tmp_path) -> None:
    client = FakeClient()

    @contextmanager
    def opener(_config):
        yield client

    metrics = {
        "samples": 10,
        "loss_pct": 7.6,
        "jitter_ms": 80.2,
        "latency_ms": 225.0,
        "source": "icmp",
    }
    database = Database(tmp_path / "history.sqlite")
    service = MonitorService(
        database,
        client_opener=opener,
        health_probe=lambda: metrics,
    )
    service._refresh_health_metrics()
    snapshot, snapshot_id = service._poll_once(ConnectionConfig(host="fake"))

    assert client.health_metrics == metrics
    assert snapshot["schema_version"] == 2
    assert snapshot_id > 0
    database.close()


def test_monitor_survives_failed_probe(tmp_path) -> None:
    client = FakeClient()

    @contextmanager
    def opener(_config):
        yield client

    def broken_probe():
        raise RuntimeError("ping failed")

    database = Database(tmp_path / "history.sqlite")
    service = MonitorService(
        database,
        client_opener=opener,
        health_probe=broken_probe,
    )
    service._refresh_health_metrics()
    service._poll_once(ConnectionConfig(host="fake"))

    assert client.health_metrics is None
    database.close()
