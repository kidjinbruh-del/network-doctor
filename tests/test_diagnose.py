from __future__ import annotations

import copy
import json
from pathlib import Path

from network_doctor.diagnose import (
    VERDICT_BEHIND_KEENETIC,
    VERDICT_KEENETIC_DOWN,
    apply_nat_delta,
    diagnose_no_internet,
)

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "office_snapshot.json"


def load_sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_incident_verdict_is_behind_keenetic() -> None:
    diagnosis = diagnose_no_internet(load_sample())

    assert diagnosis.verdict == VERDICT_BEHIND_KEENETIC
    assert diagnosis.severity == "warning"
    assert any(check.name.startswith("ARP до Keenetic") and check.status == "ok" for check in diagnosis.checks)
    assert any(check.name == "Счётчики NAT" and check.status == "ok" for check in diagnosis.checks)


def test_missing_arp_means_keenetic_is_down() -> None:
    snapshot = load_sample()
    snapshot["diagnostics"]["arp"] = []
    snapshot["diagnostics"]["keenetic"]["arp"] = False
    snapshot["topology"]["keenetic"]["reachable"] = False

    diagnosis = diagnose_no_internet(snapshot)

    assert diagnosis.verdict == VERDICT_KEENETIC_DOWN
    assert diagnosis.severity == "warning"


def test_missing_default_route_has_priority() -> None:
    snapshot = load_sample()
    snapshot["diagnostics"]["default_route"] = {
        "present": False,
        "active": False,
        "gateway": "",
    }

    diagnosis = diagnose_no_internet(snapshot)

    assert "маршрута по умолчанию" in diagnosis.verdict
    assert diagnosis.checks[0].status == "fail"


def test_nat_failure_is_reported_when_keenetic_ports_open() -> None:
    snapshot = load_sample()
    snapshot["diagnostics"]["nat"]["growing"] = False
    snapshot["diagnostics"]["nat"]["delta_packets"] = 0
    snapshot["diagnostics"]["keenetic"]["ports"] = {
        "22": True,
        "80": True,
        "443": True,
        "8291": True,
    }
    snapshot["topology"]["keenetic"]["management_ports"] = {
        "22": True,
        "80": True,
        "443": True,
        "8291": True,
    }

    diagnosis = diagnose_no_internet(snapshot)

    assert "Счётчик NAT не вырос" in diagnosis.verdict
    assert any(check.name == "Счётчики NAT" and check.status == "warn" for check in diagnosis.checks)


def test_closed_ports_keep_incident_verdict_even_if_nat_is_flat() -> None:
    snapshot = load_sample()
    snapshot["diagnostics"]["nat"]["growing"] = False
    snapshot["diagnostics"]["nat"]["delta_packets"] = 0

    diagnosis = diagnose_no_internet(snapshot)

    assert diagnosis.verdict == VERDICT_BEHIND_KEENETIC
    assert any(check.name == "Счётчики NAT" and check.status == "warn" for check in diagnosis.checks)
    assert any("NAT MikroTik не растёт" in action for action in diagnosis.actions)


def test_apply_nat_delta_uses_previous_packets() -> None:
    current = load_sample()
    previous = copy.deepcopy(current)
    previous["diagnostics"]["nat"]["packets"] = 299000

    apply_nat_delta(current, previous)

    assert current["diagnostics"]["nat"]["delta_packets"] == 472
    assert current["diagnostics"]["nat"]["growing"] is True


def test_missing_evidence_does_not_crash() -> None:
    diagnosis = diagnose_no_internet(
        {
            "diagnostics": {
                "default_route": {"present": True, "active": True, "gateway": "pppoe-out1"}
            }
        }
    )

    assert "Не удалось определить Keenetic" in diagnosis.verdict
    assert all(check.status in {"ok", "warn", "fail", "unknown"} for check in diagnosis.checks)


def test_missing_route_collection_is_not_reported_as_router_failure():
    diagnosis=diagnose_no_internet({})
    assert diagnosis.severity=='warning'
    assert diagnosis.checks[0].status=='unknown'
