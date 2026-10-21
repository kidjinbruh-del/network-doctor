from __future__ import annotations

import re
from typing import Any, Mapping

_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER_RE.search(as_text(value).replace(",", "."))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def as_integer(value: Any) -> int | None:
    number = as_number(value)
    return int(number) if number is not None else None


def clean_row(row: Mapping[Any, Any]) -> dict[str, Any]:
    return {as_text(key): value for key, value in row.items()}


def safe_filename(value: str) -> str:
    return _SAFE_RE.sub("_", value).strip("._") or "snapshot"
