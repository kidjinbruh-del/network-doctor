from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from typing import Any, Callable

from .connections import ConnectionConfig, open_client
from .db import Database
from .diagnose import apply_nat_delta

SnapshotCallback = Callable[[dict[str, Any], int, int], None]
ErrorCallback = Callable[[str, int], None]
ClientOpener = Callable[[ConnectionConfig], AbstractContextManager[Any]]
HealthProbe = Callable[[], dict[str, Any]]


class MonitorService:
    """Poll a MikroTik router in a background thread.

    A failed poll increases the delay exponentially up to ``max_backoff``
    seconds. The UI thread is never used for network or SQLite writes.
    """

    def __init__(
        self,
        database: Database,
        *,
        interval_seconds: int = 300,
        max_backoff: int = 1800,
        snapshot_type: str = "office",
        on_snapshot: SnapshotCallback | None = None,
        on_error: ErrorCallback | None = None,
        client_opener: ClientOpener = open_client,
        health_probe: HealthProbe | None = None,
        probe_interval_seconds: int = 30,
    ) -> None:
        self.database = database
        self.interval_seconds = max(10, int(interval_seconds))
        self.max_backoff = max(self.interval_seconds, int(max_backoff))
        self.snapshot_type = snapshot_type
        self.on_snapshot = on_snapshot
        self.on_error = on_error
        self.client_opener = client_opener
        self.health_probe = health_probe
        self.probe_interval_seconds = max(5, int(probe_interval_seconds))
        self._latest_health_metrics: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, config: ConnectionConfig) -> None:
        if self.running:
            raise RuntimeError("Мониторинг уже запущен.")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(config,),
            name="network-doctor-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        if thread is None or not thread.is_alive():
            self._thread = None

    def _run(self, config: ConnectionConfig) -> None:
        failures = 0
        while not self._stop.is_set():
            self._refresh_health_metrics()
            try:
                snapshot, snapshot_id = self._poll_once(config)
                failures = 0
                delay = self.interval_seconds
                if self.on_snapshot is not None:
                    self.on_snapshot(snapshot, snapshot_id, delay)
            except Exception as exc:
                failures += 1
                delay = min(
                    self.interval_seconds * (2 ** min(failures, 6)),
                    self.max_backoff,
                )
                message = str(exc) or exc.__class__.__name__
                if self.on_error is not None:
                    self.on_error(message, delay)
            if not self._wait_with_probes(delay):
                return

    def _poll_once(self, config: ConnectionConfig) -> tuple[dict[str, Any], int]:
        previous = self.database.latest_snapshot(self.snapshot_type)
        with self.client_opener(config) as client:
            snapshot = client.collect_snapshot(
                self.snapshot_type,
                health_metrics=self._latest_health_metrics,
            )
        apply_nat_delta(snapshot, previous)
        snapshot_id = self.database.save_batch([(snapshot, None)])[0]
        return snapshot, snapshot_id

    def _refresh_health_metrics(self) -> None:
        if self.health_probe is None:
            return
        try:
            self._latest_health_metrics = self.health_probe()
        except Exception:
            self._latest_health_metrics = None

    def _wait_with_probes(self, delay: int) -> bool:
        """Wait, refreshing ICMP metrics every probe interval."""
        if self.health_probe is None:
            return not self._stop.wait(delay)
        remaining = max(0, int(delay))
        while remaining > 0:
            chunk = min(remaining, self.probe_interval_seconds)
            if self._stop.wait(chunk):
                return False
            remaining -= chunk
            if remaining > 0:
                self._refresh_health_metrics()
        return True
