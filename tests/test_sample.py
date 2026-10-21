from __future__ import annotations

import json
from pathlib import Path

from network_doctor.diagnose import VERDICT_BEHIND_KEENETIC, diagnose_no_internet
from network_doctor.health import health_rows
from network_doctor.parser import filter_virtual_clients
from network_doctor.schema import validate_snapshot

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "office_snapshot.json"


def load_sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_office_sample_matches_incident_expectations() -> None:
    snapshot = load_sample()
    assert validate_snapshot(snapshot) == []

    filtered = filter_virtual_clients(snapshot["clients"])
    assert len(snapshot["clients"]) == 23
    assert len(filtered.kept) == 14
    assert len(filtered.removed) == 9
    assert filtered.reasons["Proxmox VE MAC bc:24:11"] == 3
    assert filtered.reasons["локально назначенный MAC"] == 6

    rows = health_rows(snapshot)
    assert len(rows) == 14
    assert all(row["health"].state == "red" for row in rows)

    diagnosis = diagnose_no_internet(snapshot)
    assert diagnosis.verdict == VERDICT_BEHIND_KEENETIC
    assert diagnosis.severity == "critical"
