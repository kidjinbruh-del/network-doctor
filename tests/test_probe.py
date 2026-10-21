from __future__ import annotations

import subprocess
from unittest.mock import patch

from network_doctor.probe import measure_icmp, parse_ping_output


def test_parse_linux_ping_output() -> None:
    output = """
PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=10.0 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=20.0 ms
64 bytes from 8.8.8.8: icmp_seq=3 ttl=117 time=30.0 ms

--- 8.8.8.8 ping statistics ---
4 packets transmitted, 3 received, 25% packet loss, time 3004ms
rtt min/avg/max/mdev = 10.000/20.000/30.000/8.165 ms
"""
    metrics = parse_ping_output(output)

    assert metrics["samples"] == 3
    assert metrics["loss_pct"] == 25.0
    assert metrics["latency_ms"] == 20.0
    assert metrics["jitter_ms"] == 10.0


def test_parse_windows_and_localized_ping_output() -> None:
    windows = """
Обмен пакетами с 8.8.8.8 по с 32 байтами данных:
Ответ от 8.8.8.8: число байт=32 время=12мс TTL=117
Ответ от 8.8.8.8: число байт=32 время=18мс TTL=117

Статистика Ping для 8.8.8.8:
    Пакетов: отправлено = 2, получено = 2, потеряно = 0
    (0% потерь)
"""
    metrics = parse_ping_output(windows)

    assert metrics["samples"] == 2
    assert metrics["loss_pct"] == 0.0
    assert metrics["latency_ms"] == 15.0
    assert metrics["jitter_ms"] == 6.0


def test_parse_full_packet_loss_without_rtt() -> None:
    metrics = parse_ping_output(
        "5 packets transmitted, 0 received, 100% packet loss, time 4085ms"
    )

    assert metrics["samples"] == 0
    assert metrics["loss_pct"] == 100.0
    assert metrics["latency_ms"] is None
    assert metrics["jitter_ms"] is None


def test_measure_icmp_reports_missing_ping_binary() -> None:
    with patch("network_doctor.probe.subprocess.run", side_effect=FileNotFoundError):
        metrics = measure_icmp()

    assert metrics["source"] == "icmp"
    assert metrics["loss_pct"] is None
    assert "ping" in metrics["error"]


def test_measure_icmp_passes_command_and_parses_reply() -> None:
    completed = subprocess.CompletedProcess(
        args=["ping"],
        returncode=0,
        stdout=(
            "2 packets transmitted, 2 received, 0% packet loss\n"
            "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=11.5 ms\n"
            "64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=14.5 ms\n"
        ),
        stderr="",
    )
    with patch("network_doctor.probe.subprocess.run", return_value=completed) as run:
        metrics = measure_icmp(count=2)

    command = run.call_args.args[0]
    assert command[0] == "ping"
    assert "8.8.8.8" in command
    assert metrics["loss_pct"] == 0.0
    assert metrics["latency_ms"] == 13.0
    assert metrics["jitter_ms"] == 3.0
