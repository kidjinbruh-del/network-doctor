from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .health import calculate_health, snapshot_noise
from .utils import as_number, as_text

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class StoredSnapshot:
    id: int
    snapshot_type: str
    collected_at: str
    host: str
    router_identity: str
    clients: int
    noise: float | None
    disconnects: int
    demo: bool


@dataclass(frozen=True)
class TimelinePoint:
    collected_at: str
    signal_dbm: float | None
    snr_db: float | None
    ccq: float | None
    noise: float | None
    health_score: int | None
    health_state: str


class Database:
    """SQLite storage for monitoring history.

    All writes happen inside explicit transactions and child rows are written
    with ``executemany``. The monitor thread never writes through the UI thread.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        source_path: str | Path | None = None,
        demo: bool | None = None,
    ) -> int:
        """Persist one snapshot and its child rows in a single transaction."""
        with self._lock, self._conn:
            return self._write_snapshot(snapshot, source_path, demo)

    def save(
        self,
        snapshot: dict[str, Any],
        *,
        source_path: str | Path | None = None,
        demo: bool | None = None,
    ) -> int:
        """Compatibility alias for the enterprise ``snapshots`` table API."""
        return self.save_snapshot(snapshot, source_path=source_path, demo=demo)

    def load(self, snapshot_id: int) -> dict[str, Any] | None:
        """Load one snapshot by primary key."""
        with self._lock:
            row = self._conn.execute(
                "SELECT raw_json FROM snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        return _decode_snapshot(row["raw_json"]) if row else None

    def save_batch(
        self,
        snapshots: Iterable[tuple[dict[str, Any], str | Path | None]],
        *,
        demo: bool = False,
    ) -> list[int]:
        """Persist multiple snapshots in one transaction.

        The monitor uses this method when it has queued more than one sample.
        Child tables are populated with ``executemany`` to avoid per-row commit
        overhead.
        """
        ids: list[int] = []
        with self._lock, self._conn:
            for snapshot, source_path in snapshots:
                ids.append(self._write_snapshot(snapshot, source_path, demo))
        return ids

    def set_baseline(self, snapshot_type: str, snapshot_id: int) -> None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Снимок {snapshot_id} не найден в базе.")
            self._conn.execute(
                """
                INSERT INTO baselines (snapshot_type, snapshot_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(snapshot_type) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id,
                    created_at = excluded.created_at
                """,
                (
                    snapshot_type,
                    snapshot_id,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )

    def get_baseline(self, snapshot_type: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT s.raw_json
                FROM baselines b
                JOIN snapshots s ON s.id = b.snapshot_id
                WHERE b.snapshot_type = ?
                """,
                (snapshot_type,),
            ).fetchone()
        return _decode_snapshot(row["raw_json"]) if row else None

    def baseline_id(self, snapshot_type: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT snapshot_id FROM baselines WHERE snapshot_type = ?",
                (snapshot_type,),
            ).fetchone()
        return int(row["snapshot_id"]) if row else None

    def find_snapshot_id(self, source_path: str | Path) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM snapshots WHERE source_path = ?",
                (str(source_path),),
            ).fetchone()
        return int(row["id"]) if row else None

    def latest_snapshot(
        self, snapshot_type: str | None = None
    ) -> dict[str, Any] | None:
        query = "SELECT raw_json FROM snapshots"
        params: tuple[Any, ...] = ()
        if snapshot_type is not None:
            query += " WHERE snapshot_type = ?"
            params = (snapshot_type,)
        query += " ORDER BY collected_epoch DESC, id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        return _decode_snapshot(row["raw_json"]) if row else None

    def recent_snapshots(self, limit: int = 50) -> list[StoredSnapshot]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, snapshot_type, collected_at, host, router_identity,
                       clients_count, noise, disconnects, demo
                FROM snapshots
                ORDER BY collected_epoch DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            StoredSnapshot(
                id=int(row["id"]),
                snapshot_type=as_text(row["snapshot_type"]),
                collected_at=as_text(row["collected_at"]),
                host=as_text(row["host"]),
                router_identity=as_text(row["router_identity"]),
                clients=int(row["clients_count"] or 0),
                noise=as_number(row["noise"]),
                disconnects=int(row["disconnects"] or 0),
                demo=bool(row["demo"]),
            )
            for row in rows
        ]

    def timeline_clients(self, hours: int = 24) -> list[str]:
        cutoff = time.time() - hours * 3600
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT c.mac, MAX(s.collected_epoch) AS last_seen
                FROM clients c
                JOIN snapshots s ON s.id = c.snapshot_id
                WHERE s.collected_epoch >= ?
                GROUP BY c.mac
                ORDER BY last_seen DESC
                """,
                (cutoff,),
            ).fetchall()
        return [as_text(row["mac"]) for row in rows if as_text(row["mac"])]

    def timeline_series(self, mac: str, hours: int = 24) -> list[TimelinePoint]:
        cutoff = time.time() - hours * 3600
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.collected_at, c.signal_dbm, c.snr_db, c.ccq,
                       s.noise, c.health_score, c.health_state
                FROM clients c
                JOIN snapshots s ON s.id = c.snapshot_id
                WHERE c.mac = ? AND s.collected_epoch >= ?
                ORDER BY s.collected_epoch ASC, s.id ASC
                """,
                (mac, cutoff),
            ).fetchall()
        return [
            TimelinePoint(
                collected_at=as_text(row["collected_at"]),
                signal_dbm=as_number(row["signal_dbm"]),
                snr_db=as_number(row["snr_db"]),
                ccq=as_number(row["ccq"]),
                noise=as_number(row["noise"]),
                health_score=(
                    int(row["health_score"])
                    if row["health_score"] is not None
                    else None
                ),
                health_state=as_text(row["health_state"]),
            )
            for row in rows
        ]

    def noise_series(
        self, hours: int = 24, snapshot_type: str = "office"
    ) -> list[tuple[str, float]]:
        cutoff = time.time() - hours * 3600
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT collected_at, noise
                FROM snapshots
                WHERE snapshot_type = ? AND collected_epoch >= ?
                  AND noise IS NOT NULL
                ORDER BY collected_epoch ASC, id ASC
                """,
                (snapshot_type, cutoff),
            ).fetchall()
        return [
            (as_text(row["collected_at"]), float(row["noise"]))
            for row in rows
        ]

    def count_snapshots(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM snapshots").fetchone()
        return int(row["count"] or 0)

    def _write_snapshot(
        self,
        snapshot: dict[str, Any],
        source_path: str | Path | None,
        demo: bool | None,
    ) -> int:
        meta = snapshot.get("meta", {}) or {}
        snapshot_type = as_text(meta.get("snapshot_type")) or "office"
        collected_at = as_text(meta.get("collected_at")) or datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        epoch = _epoch(collected_at)
        raw_clients = snapshot.get("clients") or snapshot.get("registration_table") or []
        clients = [
            item
            for item in raw_clients
            if isinstance(item, dict) and not item.get("virtual")
        ]
        wireless = [
            item
            for item in snapshot.get("wireless_interfaces", []) or []
            if isinstance(item, dict)
        ]
        events = [
            item
            for item in snapshot.get("disconnect_events", []) or []
            if isinstance(item, dict)
        ]
        noise = snapshot_noise(snapshot)
        demo_flag = bool(meta.get("demo")) if demo is None else demo
        source = str(source_path) if source_path is not None else None

        snapshot_id: int | None = None
        if source is not None:
            row = self._conn.execute(
                "SELECT id FROM snapshots WHERE source_path = ?", (source,)
            ).fetchone()
            if row is not None:
                snapshot_id = int(row["id"])
                self._conn.execute(
                    "DELETE FROM clients WHERE snapshot_id = ?", (snapshot_id,)
                )
                self._conn.execute(
                    "DELETE FROM wireless WHERE snapshot_id = ?", (snapshot_id,)
                )
                self._conn.execute(
                    "DELETE FROM events WHERE snapshot_id = ?", (snapshot_id,)
                )
                self._conn.execute(
                    """
                    UPDATE snapshots SET
                        snapshot_type = ?, collected_at = ?, collected_epoch = ?,
                        host = ?, router_identity = ?, router_version = ?,
                        router_board = ?, noise = ?, clients_count = ?,
                        disconnects = ?, demo = ?, raw_json = ?
                    WHERE id = ?
                    """,
                    (
                        snapshot_type,
                        collected_at,
                        epoch,
                        as_text(meta.get("host")),
                        as_text(meta.get("router_identity")),
                        as_text(meta.get("router_version")),
                        as_text(meta.get("router_board")),
                        noise,
                        len(clients),
                        len(events),
                        1 if demo_flag else 0,
                        json.dumps(snapshot, ensure_ascii=False, default=as_text),
                        snapshot_id,
                    ),
                )

        if snapshot_id is None:
            cursor = self._conn.execute(
                """
                INSERT INTO snapshots (
                    source_path, snapshot_type, collected_at, collected_epoch,
                    saved_at, host, router_identity, router_version, router_board,
                    noise, clients_count, disconnects, demo, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    snapshot_type,
                    collected_at,
                    epoch,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    as_text(meta.get("host")),
                    as_text(meta.get("router_identity")),
                    as_text(meta.get("router_version")),
                    as_text(meta.get("router_board")),
                    noise,
                    len(clients),
                    len(events),
                    1 if demo_flag else 0,
                    json.dumps(snapshot, ensure_ascii=False, default=as_text),
                ),
            )
            snapshot_id = int(cursor.lastrowid)

        client_rows = []
        for item in clients:
            result = calculate_health(
                item.get("signal_dbm"),
                noise,
                item.get("tx_ccq"),
                item.get("retry_pct", item.get("tx_retries")),
            )
            client_rows.append(
                (
                    snapshot_id,
                    as_text(item.get("mac")) or "—",
                    as_text(item.get("interface")),
                    result.signal_dbm,
                    result.snr_db,
                    result.ccq,
                    result.retry_pct,
                    result.score,
                    result.state,
                    as_text(item.get("tx_rate")),
                    as_text(item.get("uptime")),
                )
            )
        if client_rows:
            self._conn.executemany(
                """
                INSERT INTO clients (
                    snapshot_id, mac, interface, signal_dbm, snr_db, ccq,
                    retry_pct, health_score, health_state, tx_rate, uptime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                client_rows,
            )

        wireless_rows = [
            (
                snapshot_id,
                as_text(item.get("name")),
                as_text(item.get("menu")),
                as_text(item.get("channel")),
                as_number(item.get("frequency")),
                as_number(item.get("noise_floor")),
                as_number(item.get("tx_power")),
                as_number(item.get("overall_tx_ccq")),
                as_number(item.get("clients")),
                1 if item.get("disabled") else 0,
            )
            for item in wireless
        ]
        if wireless_rows:
            self._conn.executemany(
                """
                INSERT INTO wireless (
                    snapshot_id, name, menu, channel, frequency, noise_floor,
                    tx_power, overall_tx_ccq, clients, disabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                wireless_rows,
            )

        event_rows = [
            (
                snapshot_id,
                as_text(item.get("time")),
                as_text(item.get("topics")),
                as_text(item.get("message")),
            )
            for item in events
        ]
        if event_rows:
            self._conn.executemany(
                """
                INSERT INTO events (snapshot_id, event_time, topics, message)
                VALUES (?, ?, ?, ?)
                """,
                event_rows,
            )

        return snapshot_id

    def _create_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT UNIQUE,
                    snapshot_type TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    collected_epoch REAL NOT NULL,
                    saved_at TEXT NOT NULL,
                    host TEXT,
                    router_identity TEXT,
                    router_version TEXT,
                    router_board TEXT,
                    noise REAL,
                    clients_count INTEGER NOT NULL DEFAULT 0,
                    disconnects INTEGER NOT NULL DEFAULT 0,
                    demo INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL,
                    timestamp TEXT GENERATED ALWAYS AS (collected_at) VIRTUAL,
                    type TEXT GENERATED ALWAYS AS (snapshot_type) VIRTUAL,
                    data_json TEXT GENERATED ALWAYS AS (raw_json) VIRTUAL
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_type_time
                    ON snapshots(snapshot_type, collected_epoch);
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL
                        REFERENCES snapshots(id) ON DELETE CASCADE,
                    mac TEXT NOT NULL,
                    interface TEXT,
                    signal_dbm REAL,
                    snr_db REAL,
                    ccq REAL,
                    retry_pct REAL,
                    health_score INTEGER,
                    health_state TEXT,
                    tx_rate TEXT,
                    uptime TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_clients_mac
                    ON clients(mac, snapshot_id);
                CREATE TABLE IF NOT EXISTS wireless (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL
                        REFERENCES snapshots(id) ON DELETE CASCADE,
                    name TEXT,
                    menu TEXT,
                    channel TEXT,
                    frequency REAL,
                    noise_floor REAL,
                    tx_power REAL,
                    overall_tx_ccq REAL,
                    clients REAL,
                    disabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_wireless_snapshot
                    ON wireless(snapshot_id);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL
                        REFERENCES snapshots(id) ON DELETE CASCADE,
                    event_time TEXT,
                    topics TEXT,
                    message TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_snapshot
                    ON events(snapshot_id);
                CREATE TABLE IF NOT EXISTS baselines (
                    snapshot_type TEXT PRIMARY KEY,
                    snapshot_id INTEGER NOT NULL
                        REFERENCES snapshots(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );
                PRAGMA user_version = 2;
                """
            )
            self._migrate_snapshot_aliases()

    def _migrate_snapshot_aliases(self) -> None:
        """Expose the required ``timestamp``/``type``/``data_json`` names.

        The richer canonical columns remain the source of truth. Generated
        aliases keep the public schema compatible without duplicating data.
        """
        columns = {
            as_text(row["name"])
            for row in self._conn.execute(
                "PRAGMA table_xinfo(snapshots)"
            ).fetchall()
        }
        aliases = {
            "timestamp": "collected_at",
            "type": "snapshot_type",
            "data_json": "raw_json",
        }
        for alias, source in aliases.items():
            if alias in columns:
                continue
            self._conn.execute(
                f"ALTER TABLE snapshots ADD COLUMN {alias} TEXT "
                f"GENERATED ALWAYS AS ({source}) VIRTUAL"
            )


def _epoch(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return time.time()


def _decode_snapshot(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
