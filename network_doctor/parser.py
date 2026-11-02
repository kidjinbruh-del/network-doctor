from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .utils import as_number, as_text

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SEPARATOR_RE = re.compile(r"^[\s|+\-=_]+$")
BSSID_RE = re.compile(
    r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b"
)

_HIDDEN_SSIDS = {
    "",
    "-",
    "--",
    "—",
    "n/a",
    "na",
    "hidden",
    "скрытая",
    "скрытый",
    "<hidden>",
    "<скрытая>",
}
_CHANNEL_HEADERS = {"channel", "chan", "ch", "канал"}
_FREQUENCY_HEADERS = {"freq", "frequency", "частота", "мгц"}
_SIGNAL_HEADERS = {
    "signal",
    "сигнал",
    "level",
    "rssi",
    "signal-strength",
    "strength",
    "мощность",
}
_SSID_HEADERS = {"ssid", "имя", "name", "network"}
_BSSID_HEADERS = {"bssid", "mac", "mac-address", "address", "bss"}
_SECURITY_HEADERS = {"security", "auth", "encryption", "защита", "шифрование"}

_VIRTUAL_INTERFACE_PREFIXES = (
    "virbr",
    "vnet",
    "veth",
    "docker",
)
_VIRTUAL_OUI_REASONS = (
    ("52:54:00", "QEMU/KVM MAC 52:54:00"),
    ("bc:24:11", "Proxmox VE MAC bc:24:11"),
)


@dataclass(frozen=True)
class VirtualFilterResult:
    """Inventory after removing virtual/randomized devices."""

    kept: list[dict[str, Any]]
    removed: list[dict[str, Any]]
    reasons: dict[str, int]

    @property
    def total(self) -> int:
        return len(self.kept) + len(self.removed)


def parse_spectral_scan(data: Any) -> list[dict[str, Any]]:
    """Parse a Wi-Fi scan/spectral table into normalized rows.

    Accepts raw RouterOS-style text, ANSI-coloured terminal output, a list of
    dictionaries, or one dictionary. Empty SSIDs from hidden networks are
    preserved as ``""`` instead of being dropped.
    """
    if data is None:
        return []
    if isinstance(data, (str, bytes)):
        return parse_spectral_text(as_text(data))
    if isinstance(data, Mapping):
        return _parse_mapping_rows([data])
    try:
        rows = list(data)
    except TypeError:
        return parse_spectral_text(as_text(data))
    if not rows:
        return []
    if isinstance(rows[0], (str, bytes)):
        return parse_spectral_text("\n".join(as_text(row) for row in rows))
    return _parse_mapping_rows(rows)


def parse_spectral_text(raw: str) -> list[dict[str, Any]]:
    """Parse a whitespace separated channel/SSID/signal table."""
    text = _ANSI_RE.sub("", raw).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    header_index, header = _find_header(lines)
    if header is not None:
        columns = _column_indexes(header)
        return _parse_with_header(lines[header_index + 1 :], columns)

    rows: list[dict[str, Any]] = []
    for line in lines:
        row = _parse_unstructured_line(line)
        if row is not None:
            rows.append(row)
    return rows


def _find_header(lines: Sequence[str]) -> tuple[int, list[str] | None]:
    for index, line in enumerate(lines):
        tokens = line.strip().split()
        lowered = {_normalize_header(token) for token in tokens}
        has_channel = bool(lowered & _CHANNEL_HEADERS or lowered & _FREQUENCY_HEADERS)
        has_signal = bool(lowered & _SIGNAL_HEADERS)
        has_ssid = bool(lowered & _SSID_HEADERS)
        if has_channel and (has_signal or has_ssid):
            return index, tokens
    return -1, None


def _normalize_header(token: str) -> str:
    return re.sub(r"[^a-zа-я0-9_-]+", "", token.lower())


def _column_indexes(header: Sequence[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, token in enumerate(header):
        key = _normalize_header(token)
        if key in _CHANNEL_HEADERS and "channel" not in result:
            result["channel"] = index
        elif key in _FREQUENCY_HEADERS and "frequency" not in result:
            result["frequency"] = index
        elif key in _SIGNAL_HEADERS and "signal" not in result:
            result["signal"] = index
        elif key in _SSID_HEADERS and "ssid" not in result:
            result["ssid"] = index
        elif key in _BSSID_HEADERS and "bssid" not in result:
            result["bssid"] = index
        elif key in _SECURITY_HEADERS and "security" not in result:
            result["security"] = index
    return result


def _parse_with_header(
    lines: Sequence[str], columns: Mapping[str, int]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        if _SEPARATOR_RE.match(line) or not re.search(r"\d", line):
            continue
        tokens = line.strip().split()
        channel_token = _at(tokens, columns.get("channel"))
        frequency_token = _at(tokens, columns.get("frequency"))
        signal_token = _at(tokens, columns.get("signal"))
        ssid_token = _at(tokens, columns.get("ssid"))
        bssid_token = _at(tokens, columns.get("bssid"))
        security_token = _at(tokens, columns.get("security"))
        signal_index = columns.get("signal")
        if signal_token is None and tokens:
            signal_token = tokens[-1]
            signal_index = len(tokens) - 1
            ssid_token = None

        channel = _parse_channel(channel_token)
        frequency = _parse_frequency(frequency_token)
        if channel is None and frequency is not None:
            channel = _channel_from_frequency(frequency)
        if channel is None and frequency is None:
            continue
        signal = _parse_signal(signal_token)
        if signal is None:
            continue

        effective = dict(columns)
        effective["signal"] = signal_index
        if ssid_token is None and bssid_token is None:
            ssid = _ssid_from_middle(tokens, effective)
            bssid = _bssid_from_tokens(tokens, effective)
        else:
            ssid = _clean_ssid(ssid_token)
            bssid = _clean_bssid(bssid_token)
        if bssid is None:
            bssid = _bssid_from_tokens(tokens, effective)

        rows.append(
            _row(
                channel=channel,
                frequency=frequency,
                ssid=ssid,
                bssid=bssid,
                signal=signal,
                security=_clean_optional(security_token),
            )
        )
    return rows


def _at(tokens: Sequence[str], index: int | None) -> str | None:
    if index is None or index < 0 or index >= len(tokens):
        return None
    return tokens[index]


def _ssid_from_middle(tokens: Sequence[str], columns: Mapping[str, int]) -> str:
    channel_index = columns.get("channel", columns.get("frequency", 0))
    signal_index = columns.get("signal", len(tokens) - 1)
    stop_indexes = {
        index
        for index in (columns.get("bssid"), columns.get("security"), signal_index)
        if index is not None
    }
    pieces = [
        token
        for index, token in enumerate(tokens)
        if index > channel_index and index not in stop_indexes
    ]
    return _clean_ssid(" ".join(pieces))


def _bssid_from_tokens(
    tokens: Sequence[str], columns: Mapping[str, int]
) -> str | None:
    token = _at(tokens, columns.get("bssid"))
    if token:
        cleaned = _clean_bssid(token)
        if cleaned:
            return cleaned
    for candidate in tokens:
        if BSSID_RE.fullmatch(candidate.strip(" ,;()[]")):
            return _normalize_mac(candidate)
    return None


def _parse_unstructured_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if _SEPARATOR_RE.match(stripped):
        return None
    signal_match = re.search(
        r"(?P<signal>-?\d+(?:[.,]\d+)?\s*(?:dBm|dbm|%))(?:\s|$)", stripped
    )
    if signal_match is None:
        tokens = stripped.split()
        if len(tokens) < 2 or not re.fullmatch(r"-?\d+(?:[.,]\d+)?", tokens[-1]):
            return None
        signal_token = tokens[-1]
        prefix_tokens = tokens[:-1]
    else:
        signal_token = signal_match.group("signal")
        prefix_tokens = stripped[: signal_match.start()].split()

    if not prefix_tokens:
        return None
    channel = _parse_channel(prefix_tokens[0])
    frequency = _parse_frequency(prefix_tokens[0])
    if channel is None and frequency is not None:
        channel = _channel_from_frequency(frequency)
    if channel is None:
        return None
    signal = _parse_signal(signal_token)
    if signal is None:
        return None

    remainder = prefix_tokens[1:]
    bssid = next(
        (
            _normalize_mac(token)
            for token in remainder
            if BSSID_RE.fullmatch(token.strip(" ,;()[]"))
        ),
        None,
    )
    remainder = [
        token for token in remainder if not BSSID_RE.fullmatch(token.strip(" ,;()[]"))
    ]
    ssid = _clean_ssid(" ".join(remainder))
    return _row(
        channel=channel,
        frequency=frequency,
        ssid=ssid,
        bssid=bssid,
        signal=signal,
    )


def _parse_mapping_rows(rows: Sequence[Any]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        row = {_normalize_key(as_text(key)): value for key, value in raw.items()}
        channel = _parse_channel(
            row.get("channel") or row.get("chan") or row.get("ch") or row.get("канал")
        )
        frequency = _parse_frequency(
            row.get("frequency") or row.get("freq") or row.get("частота")
        )
        if channel is None and frequency is not None:
            channel = _channel_from_frequency(frequency)
        if channel is None:
            continue
        signal = _parse_signal(
            row.get("signal")
            or row.get("signal-strength")
            or row.get("level")
            or row.get("rssi")
            or row.get("сигнал")
        )
        if signal is None:
            continue
        ssid = _clean_ssid(
            row.get("ssid") or row.get("name") or row.get("network")
        )
        bssid = _clean_bssid(
            row.get("bssid") or row.get("mac-address") or row.get("mac")
        )
        parsed.append(
            _row(
                channel=channel,
                frequency=frequency,
                ssid=ssid,
                bssid=bssid,
                signal=signal,
                security=_clean_optional(row.get("security") or row.get("auth")),
            )
        )
    return parsed


def _row(
    *,
    channel: int,
    frequency: int | None,
    ssid: str,
    bssid: str | None,
    signal: int | float,
    security: str | None = None,
) -> dict[str, Any]:
    return {
        "channel": channel,
        "frequency": frequency,
        "ssid": ssid,
        "bssid": bssid,
        "signal": signal,
        "security": security,
        "hidden": not bool(ssid),
    }


def _normalize_key(value: str) -> str:
    return re.sub(r"[\s_]+", "-", value.strip().lower())


def _clean_ssid(value: Any) -> str:
    text = as_text(value).strip().strip("\"'")
    if text.lower() in _HIDDEN_SSIDS or text in _HIDDEN_SSIDS:
        return ""
    return text


def _clean_optional(value: Any) -> str | None:
    text = as_text(value).strip()
    return text or None


def _clean_bssid(value: Any) -> str | None:
    text = as_text(value).strip()
    if not text or text.lower() in {"-", "--", "n/a", "na"}:
        return None
    match = BSSID_RE.search(text)
    return _normalize_mac(match.group(0)) if match else None


def _normalize_mac(mac: Any) -> str:
    return as_text(mac).strip().lower().replace("-", ":")


def _parse_channel(value: Any) -> int | None:
    text = as_text(value).strip()
    if not text:
        return None
    match = re.search(r"\d+", text)
    if not match:
        return None
    number = int(match.group(0))
    if 1 <= number <= 233:
        return number
    return None


def _parse_frequency(value: Any) -> int | None:
    text = as_text(value).strip()
    if not text:
        return None
    match = re.search(r"\d{4,5}", text)
    if not match:
        return None
    number = int(match.group(0))
    return number if 2000 <= number <= 7200 else None


def _channel_from_frequency(frequency: int) -> int | None:
    if frequency == 2484:
        return 14
    if 2412 <= frequency <= 2472:
        return (frequency - 2407) // 5
    if 5000 <= frequency <= 5900:
        return (frequency - 5000) // 5
    if 5955 <= frequency <= 7115:
        return (frequency - 5950) // 5
    return None


def _parse_signal(value: Any) -> int | float | None:
    number = as_number(value)
    if number is None:
        return None
    if float(number).is_integer():
        return int(number)
    return round(float(number), 1)


def virtual_mac_reason(mac: Any, interface: Any = "") -> str | None:
    """Return why a device must be excluded, or ``None`` for real hardware."""
    interface_text = as_text(interface).strip().lower()
    for prefix in _VIRTUAL_INTERFACE_PREFIXES:
        if interface_text.startswith(prefix):
            return f"виртуальный интерфейс {prefix}*"

    normalized = _normalize_mac(mac)
    compact = normalized.replace(":", "")
    for prefix, reason in _VIRTUAL_OUI_REASONS:
        if compact.startswith(prefix.replace(":", "")):
            return reason

    octets = normalized.split(":")
    if len(octets) != 6:
        return None
    try:
        first = int(octets[0], 16)
    except ValueError:
        return None
    if first & 0x01:
        return "multicast MAC"
    # Locally administered addresses include real phones with privacy MACs.
    # They do not prove virtualization and must not be discarded.
    return None


def is_virtual_mac(mac: Any, interface: Any = "") -> bool:
    return virtual_mac_reason(mac, interface) is not None


def filter_virtual_clients(
    devices: Sequence[Mapping[str, Any]],
    *,
    mac_key: str = "mac",
    interface_key: str = "interface",
) -> VirtualFilterResult:
    """Split an inventory into real office hardware and virtual/noise entries."""
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    for device in devices:
        item = dict(device)
        normalized_mac = _normalize_mac(item.get(mac_key))
        try:item["locally_administered"] = bool(int(normalized_mac.split(":")[0],16) & 2)
        except ValueError:item["locally_administered"] = False
        reason = virtual_mac_reason(item.get(mac_key), item.get(interface_key))
        if reason is None:
            item["virtual"] = False
            item.pop("virtual_reason", None)
            kept.append(item)
        else:
            item["virtual"] = True
            item["virtual_reason"] = reason
            removed.append(item)
            reasons[reason] = reasons.get(reason, 0) + 1
    return VirtualFilterResult(kept=kept, removed=removed, reasons=reasons)
