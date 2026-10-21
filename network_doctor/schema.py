from __future__ import annotations

from typing import Any, Mapping

SNAPSHOT_SCHEMA_VERSION = 2

TOP_LEVEL_SECTIONS = (
    "schema_version",
    "meta",
    "topology",
    "wireless_scan",
    "clients",
    "health",
    "warnings",
    "diagnostics",
)


def validate_snapshot(snapshot: Mapping[str, Any]) -> list[str]:
    """Return human-readable schema errors; an empty list means valid."""
    errors: list[str] = []
    if not isinstance(snapshot, Mapping):
        return ["snapshot должен быть JSON-объектом"]

    version = snapshot.get("schema_version")
    if not isinstance(version, int):
        errors.append("schema_version должен быть целым числом")
    elif version > SNAPSHOT_SCHEMA_VERSION:
        errors.append(
            f"schema_version {version} новее поддерживаемого "
            f"{SNAPSHOT_SCHEMA_VERSION}"
        )

    meta = snapshot.get("meta")
    if not isinstance(meta, Mapping):
        errors.append("meta должен быть объектом")
    else:
        if meta.get("snapshot_type") not in {"office", "lab"}:
            errors.append("meta.snapshot_type должен быть office или lab")
        if not str(meta.get("collected_at") or "").strip():
            errors.append("meta.collected_at обязателен")

    topology = snapshot.get("topology", {})
    if not isinstance(topology, Mapping):
        errors.append("topology должен быть объектом")

    wireless = snapshot.get("wireless_scan", [])
    if not isinstance(wireless, list):
        errors.append("wireless_scan должен быть массивом")
    else:
        for index, item in enumerate(wireless):
            if not isinstance(item, Mapping):
                errors.append(f"wireless_scan[{index}] должен быть объектом")
                continue
            if not isinstance(item.get("channel"), (int, float)):
                errors.append(f"wireless_scan[{index}].channel должен быть числом")
            if not isinstance(item.get("signal"), (int, float)):
                errors.append(f"wireless_scan[{index}].signal должен быть числом")
            if not isinstance(item.get("ssid"), str):
                errors.append(f"wireless_scan[{index}].ssid должен быть строкой")

    clients = snapshot.get("clients", [])
    if not isinstance(clients, list):
        errors.append("clients должен быть массивом")
    else:
        for index, item in enumerate(clients):
            if not isinstance(item, Mapping):
                errors.append(f"clients[{index}] должен быть объектом")
                continue
            if not str(item.get("mac") or "").strip():
                errors.append(f"clients[{index}].mac обязателен")
            if not isinstance(item.get("virtual"), bool):
                errors.append(f"clients[{index}].virtual должен быть bool")

    health = snapshot.get("health", {})
    if not isinstance(health, Mapping):
        errors.append("health должен быть объектом")

    warnings = snapshot.get("warnings", [])
    if not isinstance(warnings, list):
        errors.append("warnings должен быть массивом")

    diagnostics = snapshot.get("diagnostics", {})
    if not isinstance(diagnostics, Mapping):
        errors.append("diagnostics должен быть объектом")

    return errors


class SnapshotValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_snapshot_or_raise(snapshot: Mapping[str, Any]) -> None:
    errors = validate_snapshot(snapshot)
    if errors:
        raise SnapshotValidationError(errors)
