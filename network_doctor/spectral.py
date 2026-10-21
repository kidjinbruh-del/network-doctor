from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .utils import as_number, as_text, clean_row

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_NOISE_PAIR_RE = re.compile(
    r"(?i)(?:noise[-_ ]?floor|noise|шум)\s*[:=]\s*(-?\d+(?:[.,]\d+)?)"
)
_S_COLUMN_RE = re.compile(r"^s\d+$")


@dataclass(frozen=True)
class SpectralResult:
    noise_floor_avg: float | None
    samples: int
    method: str
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "noise_floor_avg": self.noise_floor_avg,
            "samples": self.samples,
            "method": self.method,
            "raw": self.raw[:16000],
        }


def parse_spectral_scan(data: Any) -> SpectralResult:
    if data is None:
        return SpectralResult(None, 0, "none")

    if isinstance(data, (str, bytes)):
        return _parse_text(as_text(data))

    if isinstance(data, Mapping):
        return _parse_rows([data])

    try:
        rows = list(data)
    except TypeError:
        return _parse_text(as_text(data))

    if not rows:
        return SpectralResult(None, 0, "none")

    if isinstance(rows[0], (str, bytes)):
        return _parse_text("\n".join(as_text(row) for row in rows))

    return _parse_rows(rows)


def _result(values: list[float], method: str, raw: str) -> SpectralResult:
    if not values:
        return SpectralResult(None, 0, "none", raw[:16000])
    return SpectralResult(
        round(sum(values) / len(values), 1),
        len(values),
        method,
        raw[:16000],
    )


def _valid_noise(value: float | None) -> bool:
    return value is not None and -130.0 <= value <= -10.0


def _parse_rows(rows: list[Any]) -> SpectralResult:
    clean_rows = [clean_row(row) for row in rows if isinstance(row, Mapping)]
    try:
        raw = json.dumps(clean_rows, ensure_ascii=False, default=as_text)
    except (TypeError, ValueError):
        raw = str(clean_rows)

    noise_values: list[float] = []
    s_values: list[float] = []
    fallback: list[float] = []

    for row in clean_rows:
        for key, value in row.items():
            key_lower = key.lower().replace("_", "-")
            parsed = as_number(value)
            if parsed is None:
                continue
            if "noise" in key_lower or key_lower in {"nf", "noise-floor"}:
                if _valid_noise(parsed):
                    noise_values.append(parsed)
            elif _S_COLUMN_RE.match(key_lower):
                if _valid_noise(parsed):
                    s_values.append(parsed)
            elif _valid_noise(parsed) and -115.0 <= parsed <= -20.0:
                fallback.append(parsed)

    if noise_values:
        return _result(noise_values, "dict-noise", raw)
    if s_values:
        return _result(s_values, "dict-s-columns", raw)
    return _result(fallback, "dict-values", raw)


def _parse_text(raw: str) -> SpectralResult:
    text = _ANSI_RE.sub("", raw).replace("\r\n", "\n").replace("\r", "\n")
    flat_values = [
        float(match.group(1).replace(",", "."))
        for match in _NOISE_PAIR_RE.finditer(text)
    ]
    flat_values = [value for value in flat_values if _valid_noise(value)]
    if flat_values:
        return _result(flat_values, "text-noise", text)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header_index: int | None = None
    header: list[str] | None = None

    for index, line in enumerate(lines):
        tokens = re.split(r"\s+", line)
        lowered = [token.lower() for token in tokens]
        has_frequency = any("freq" in token for token in lowered)
        has_noise = any(
            "noise" in token or token in {"nf", "шум"} for token in lowered
        )
        has_s_columns = any(_S_COLUMN_RE.match(token) for token in lowered)
        if has_frequency and (has_noise or has_s_columns):
            header_index = index
            header = tokens
            break

    values: list[float] = []
    method = "text-values"
    if header is not None and header_index is not None:
        lowered_header = [token.lower() for token in header]
        noise_index = next(
            (
                index
                for index, token in enumerate(lowered_header)
                if "noise" in token or token in {"nf", "шум"}
            ),
            None,
        )
        s_indexes = [
            index
            for index, token in enumerate(lowered_header)
            if _S_COLUMN_RE.match(token)
        ]

        for line in lines[header_index + 1:]:
            if line.startswith("#") or not re.search(r"\d", line):
                continue
            tokens = re.split(r"\s+", line)
            if noise_index is not None and noise_index < len(tokens):
                parsed = as_number(tokens[noise_index])
                if _valid_noise(parsed):
                    values.append(parsed)
                    method = "text-noise-column"
                continue
            if s_indexes:
                for index in s_indexes:
                    if index < len(tokens):
                        parsed = as_number(tokens[index])
                        if _valid_noise(parsed):
                            values.append(parsed)
                method = "text-s-columns"
                continue
            for token in tokens:
                parsed = as_number(token)
                if _valid_noise(parsed) and -115.0 <= parsed <= -20.0:
                    values.append(parsed)

    if values:
        return _result(values, method, text)

    for line in lines:
        for token in re.split(r"\s+", line):
            parsed = as_number(token)
            if _valid_noise(parsed) and -115.0 <= parsed <= -20.0:
                values.append(parsed)
    return _result(values, "text-values", text)
