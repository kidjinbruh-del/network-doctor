from __future__ import annotations

from datetime import datetime, timedelta, timezone

from network_doctor.db import Database


def make_snapshot(minutes_ago: int = 0, signal: float = -60.0) -> dict:
    collected_at = (
        datetime.now(timezone(timedelta(hours=3)))
        - timedelta(minutes=minutes_ago)
    ).isoformat(timespec="seconds")
    return {
        "meta": {
            "snapshot_type": "office",
            "snapshot_label": "Офис",
            "collected_at": collected_at,
            "host": "192.0.2.10",
            "router_identity": "rfc-test",
            "router_version": "7.19.4",
            "router_board": "CHR",
        },
        "registration_table": [
            {
                "interface": "wlan1",
                "mac": "AA:BB:CC:DD:EE:01",
                "signal_dbm": signal,
                "tx_ccq": 90.0,
                "retry_pct": 4.0,
                "tx_rate": "54Mbps",
                "uptime": "1h",
            }
        ],
        "wireless_interfaces": [
            {"name": "wlan1", "channel": "2412", "noise_floor": -95.0}
        ],
        "logs": [],
        "disconnect_events": [{"time": "12:00:00", "message": "disconnected"}],
        "spectral_scan": {"noise_floor_avg": -95.0, "samples": 10},
        "warnings": [],
    }


def test_save_and_read_snapshot(tmp_path) -> None:
    db = Database(tmp_path / "history.sqlite")
    snapshot_id = db.save_snapshot(make_snapshot())

    assert snapshot_id > 0
    assert db.count_snapshots() == 1
    loaded = db.latest_snapshot("office")
    assert loaded is not None
    assert loaded["meta"]["router_identity"] == "rfc-test"
    recent = db.recent_snapshots()
    assert recent[0].clients == 1
    assert recent[0].noise == -95.0
    assert recent[0].disconnects == 1
    db.close()


def test_timeline_series_and_clients(tmp_path) -> None:
    db = Database(tmp_path / "history.sqlite")
    db.save_snapshot(make_snapshot(minutes_ago=10, signal=-60))
    db.save_snapshot(make_snapshot(minutes_ago=5, signal=-68))

    clients = db.timeline_clients(hours=1)
    series = db.timeline_series("AA:BB:CC:DD:EE:01", hours=1)

    assert clients == ["AA:BB:CC:DD:EE:01"]
    assert [point.signal_dbm for point in series] == [-60.0, -68.0]
    assert series[0].health_score == 100
    db.close()


def test_baseline_roundtrip(tmp_path) -> None:
    db = Database(tmp_path / "history.sqlite")
    snapshot_id = db.save_snapshot(make_snapshot())
    db.set_baseline("office", snapshot_id)

    baseline = db.get_baseline("office")
    assert baseline is not None
    assert baseline["meta"]["snapshot_type"] == "office"
    assert db.baseline_id("office") == snapshot_id
    db.close()


def test_source_path_upsert_does_not_duplicate(tmp_path) -> None:
    db = Database(tmp_path / "history.sqlite")
    path = tmp_path / "office.json"
    first = db.save_snapshot(make_snapshot(), source_path=path)
    second = db.save_snapshot(
        make_snapshot(signal=-70), source_path=path
    )

    assert first == second
    assert db.count_snapshots() == 1
    assert db.timeline_series("AA:BB:CC:DD:EE:01", hours=1)[0].signal_dbm == -70.0
    db.close()


def test_batch_save(tmp_path) -> None:
    db = Database(tmp_path / "history.sqlite")
    ids = db.save_batch(
        [(make_snapshot(minutes_ago=2), None), (make_snapshot(), None)]
    )

    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert db.count_snapshots() == 2
    db.close()


def test_noise_series(tmp_path) -> None:
    db = Database(tmp_path / "history.sqlite")
    db.save_snapshot(make_snapshot())

    series = db.noise_series(hours=1, snapshot_type="office")
    assert len(series) == 1
    assert series[0][1] == -95.0
    db.close()


def test_required_snapshot_columns_and_load(tmp_path) -> None:
    db = Database(tmp_path / "history.sqlite")
    snapshot = make_snapshot()
    snapshot_id = db.save(snapshot)

    columns = {
        row["name"]
        for row in db._conn.execute("PRAGMA table_xinfo(snapshots)").fetchall()
    }
    assert {"id", "timestamp", "type", "data_json"} <= columns

    row = db._conn.execute(
        "SELECT timestamp, type, data_json FROM snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    assert row["timestamp"] == snapshot["meta"]["collected_at"]
    assert row["type"] == "office"
    assert '"router_identity": "rfc-test"' in row["data_json"]
    assert db.load(snapshot_id)["meta"]["snapshot_type"] == "office"
    db.close()
