from __future__ import annotations

import ctypes
import os
import re
import subprocess
from statistics import mean
from typing import Any

PING_TARGET = "8.8.8.8"

_RTT_RE = re.compile(
    r"[=<]\s*(\d+(?:[.,]\d+)?)\s*(?:ms|мс|milliseconds?)",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_TRANSMITTED_RE = re.compile(r"(\d+)\s+packets?\s+transmitted", re.IGNORECASE)
_RECEIVED_RE = re.compile(r"(\d+)\s+(?:packets?\s+)?received", re.IGNORECASE)
_WINDOWS_SENT_RE = re.compile(r"sent\s*=\s*(\d+)", re.IGNORECASE)
_WINDOWS_RECEIVED_RE = re.compile(r"received\s*=\s*(\d+)", re.IGNORECASE)


def parse_ping_output(output: str) -> dict[str, Any]:
    """Parse Linux or Windows ping output without depending on locale."""
    text = output or ""
    rtts = _reply_rtts(text)

    loss: float | None = None
    percent = _PERCENT_RE.search(text)
    if percent is not None:
        loss = float(percent.group(1).replace(",", "."))

    sent = _number(_TRANSMITTED_RE.search(text)) or _number(
        _WINDOWS_SENT_RE.search(text)
    )
    received = _number(_RECEIVED_RE.search(text)) or _number(
        _WINDOWS_RECEIVED_RE.search(text)
    )
    if loss is None and sent and received is not None:
        loss = round((sent - received) / sent * 100, 1)
    if loss is None and sent and not rtts:
        loss = 100.0

    latency = round(mean(rtts), 1) if rtts else None
    jitter = None
    if len(rtts) >= 2:
        jitter = round(mean(abs(right - left) for left, right in zip(rtts, rtts[1:])), 1)

    return {
        "samples": len(rtts),
        "received": received if received is not None else len(rtts),
        "loss_pct": loss,
        "jitter_ms": jitter,
        "latency_ms": latency,
    }


def measure_icmp(
    target: str = PING_TARGET,
    *,
    count: int = 10,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Measure packet loss, jitter and latency with the system ping command."""
    count = max(1, int(count))
    command = _ping_command(target, count)
    output = ""
    error = ""
    returncode = 0
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = "\n".join(
            part
            for part in (
                _decode_output(result.stdout),
                _decode_output(result.stderr),
            )
            if part
        )
        returncode = result.returncode
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            part
            for part in (_decode_output(exc.stdout), _decode_output(exc.stderr))
            if part
        )
        error = "ping timeout"
    except FileNotFoundError:
        return _unavailable(target, "системная команда ping не найдена")
    except OSError as exc:
        return _unavailable(target, f"ping недоступен: {exc}")
    except Exception as exc:
        return _unavailable(target, f"ошибка ping: {exc}")

    metrics = parse_ping_output(output)
    if metrics["loss_pct"] is None and (error or returncode != 0):
        metrics["loss_pct"] = 100.0

    return {
        "target": target,
        "samples": metrics["samples"],
        "received": metrics["received"],
        "loss_pct": metrics["loss_pct"],
        "jitter_ms": metrics["jitter_ms"],
        "latency_ms": metrics["latency_ms"],
        "source": "icmp",
        "error": error,
    }


def _unavailable(target: str, error: str) -> dict[str, Any]:
    return {
        "target": target,
        "samples": 0,
        "received": 0,
        "loss_pct": None,
        "jitter_ms": None,
        "latency_ms": None,
        "source": "icmp",
        "error": error,
    }


def _ping_command(target: str, count: int) -> list[str]:
    if os.name == "nt":
        return ["ping", "-n", str(count), "-w", "1000", target]
    return ["ping", "-c", str(count), "-W", "2", target]


def _reply_rtts(text: str) -> list[float]:
    values: list[float] = []
    for line in text.splitlines():
        lowered = line.lower()
        if "ttl" not in lowered and "icmp_seq" not in lowered:
            continue
        match = _RTT_RE.search(line)
        if match is not None:
            values.append(float(match.group(1).replace(",", ".")))
    return values


def _number(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _decode_output(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, bytes):
        return str(value)

    encodings: list[str] = []
    if os.name == "nt":
        try:
            encodings.append(f"cp{ctypes.windll.kernel32.GetOEMCP()}")
        except Exception:
            pass
        encodings.extend(("cp866", "cp1251"))
    encodings.append("utf-8")
    for encoding in encodings:
        try:
            return value.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")
