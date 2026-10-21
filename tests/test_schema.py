from __future__ import annotations

import json
from pathlib import Path

from network_doctor.schema import validate_snapshot

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "office_snapshot.json"


def test_incident_sample_matches_schema() -> None:
    snapshot = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert validate_snapshot(snapshot) == []


def test_schema_reports_missing_required_sections() -> None:
    errors = validate_snapshot(
        {
            "schema_version": 2,
            "meta": {"snapshot_type": "guest", "collected_at": ""},
            "wireless_scan": [{"channel": "4", "signal": "100"}],
            "clients": [{"virtual": "no"}],
        }
    )

    assert any("snapshot_type" in error for error in errors)
    assert any("collected_at" in error for error in errors)
    assert any("wireless_scan[0].channel" in error for error in errors)
    assert any("clients[0].mac" in error for error in errors)
