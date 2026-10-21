from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .storage import SnapshotStore

_DEMO_WARNING = "Демонстрационные данные. Не использовать для реальной диагностики."
_MACS = [
    "10:ff:e0:4a:4f:7a",
    "24:9a:d8:29:24:17",
    "08:9d:f4:32:ec:95",
    "4c:d5:77:ab:7a:2d",
    "90:09:17:6b:48:e7",
    "74:56:3c:d9:fb:93",
]


def create_demo_snapshots(store: SnapshotStore) -> tuple[Path, Path]:
    store.clear_demo()
    now = datetime.now().astimezone().replace(microsecond=0)
    office = _snapshot(
        snapshot_type="office",
        label="Офис",
        ssid="DEMO-OFFICE",
        router_identity="DEMO-OFFICE",
        collected_at=now - timedelta(minutes=5),
        signals=[-51.0, -57.0, -62.0, -66.0, -70.0],
        ccq=[96.0, 93.0, 90.0, 88.0, 85.0],
        noise=-95.0,
        channels=["2412/20-Ce/gn", "5180/80-Ce/gn"],
        log_count=2,
        macs=_MACS[:5],
        health={
            "loss_pct": 0.3,
            "jitter_ms": 12.0,
            "latency_ms": 45.0,
        },
    )
    lab = _snapshot(
        snapshot_type="lab",
        label="Лаборатория",
        ssid="DEMO-LAB",
        router_identity="DEMO-LAB",
        collected_at=now,
        signals=[-68.0, -75.0, -81.0, -85.0, -88.0],
        ccq=[80.0, 74.0, 68.0, 62.0, 58.0],
        noise=-83.0,
        channels=["2437/20-Ce/gn"],
        log_count=4,
        macs=_MACS[:4] + [_MACS[5]],
        health={
            "loss_pct": 7.6,
            "jitter_ms": 80.2,
            "latency_ms": 225.0,
        },
    )
    return store.save(office, demo=True), store.save(lab, demo=True)


def _snapshot(
    snapshot_type: str,
    label: str,
    ssid: str,
    router_identity: str,
    collected_at: datetime,
    signals: list[float],
    ccq: list[float],
    noise: float,
    channels: list[str],
    log_count: int,
    macs: list[str],
    health: dict[str, float],
) -> dict[str, Any]:
    registration = [
        {
            "interface": "wlan1",
            "mac": mac,
            "signal_dbm": signal,
            "signal_raw": f"{signal:.0f}dBm@6Mbps",
            "tx_ccq": ccq_value,
            "rx_ccq": ccq_value,
            "retry_pct": 8.0 if signal >= -70 else 24.0,
            "tx_rate": "54Mbps",
            "rx_rate": "54Mbps",
            "uptime": "1h20m",
        }
        for mac, signal, ccq_value in zip(macs, signals, ccq)
    ]
    clients = [
        {
            "mac": row["mac"],
            "ip": f"192.168.1.{10 + index}",
            "interface": row["interface"],
            "signal_dbm": row["signal_dbm"],
            "tx_ccq": row["tx_ccq"],
            "rx_ccq": row["rx_ccq"],
            "retry_pct": row["retry_pct"],
            "vendor": "Demo",
            "virtual": False,
        }
        for index, row in enumerate(registration)
    ]
    wireless = [
        {
            "name": f"wlan{index + 1}",
            "menu": "/interface/wireless",
            "mac": f"00:11:22:33:44:{index + 1:02d}",
            "channel": channel,
            "frequency": _frequency(channel),
            "band": "2ghz-802.11" if channel.startswith("24") else "5ghz-802.11",
            "tx_power": 20.0,
            "noise_floor": noise,
            "overall_tx_ccq": max(ccq),
            "clients": len(registration),
            "disabled": False,
        }
        for index, channel in enumerate(channels)
    ]
    wireless_scan = [
        {
            "channel": _channel_number(channel),
            "frequency": _frequency(channel),
            "ssid": ssid,
            "bssid": f"00:11:22:33:44:{index + 1:02d}",
            "signal": max(signals) if signals else None,
            "security": "WPA2",
            "hidden": False,
        }
        for index, channel in enumerate(channels)
    ]
    logs = [
        {
            "time": f"12:0{index}:00",
            "topics": "wireless,info",
            "message": f"client {macs[index % len(macs)]} disconnected",
        }
        for index in range(log_count)
    ]
    points = [
        {"frequency": 2412 + index * 5, "noise": noise - (index % 3)}
        for index in range(12)
    ]
    return {
        "schema_version": 2,
        "meta": {
            "snapshot_type": snapshot_type,
            "snapshot_label": label,
            "collected_at": collected_at.isoformat(timespec="seconds"),
            "host": "demo",
            "router_identity": router_identity,
            "router_version": "demo",
            "router_board": "Demo",
            "demo": True,
        },
        "topology": {
            "mikrotik": {
                "host": "demo",
                "identity": router_identity,
                "version": "demo",
                "board": "Demo",
                "lan_ip": "192.168.1.1/24",
                "lan_subnet": "192.168.1.0/24",
                "wan_interface": "ether1",
                "default_route": {
                    "present": True,
                    "active": True,
                    "gateway": "ether1",
                },
            },
            "keenetic": {},
            "subnets": [
                {
                    "network": "192.168.1.0/24",
                    "gateway": "192.168.1.1",
                    "interface": "bridge",
                }
            ],
        },
        "wireless_scan": wireless_scan,
        "clients": clients,
        "health": {
            "target": "8.8.8.8",
            **health,
            "samples": 10,
            "source": "demo",
        },
        "warnings": [_DEMO_WARNING],
        "diagnostics": {
            "default_route": {
                "present": True,
                "active": True,
                "gateway": "ether1",
            },
            "nat": {"growing": True},
            "arp": [],
            "keenetic": {"ip": "", "arp": False, "ports": {}},
        },
        "registration_table": registration,
        "wireless_interfaces": wireless,
        "logs": logs,
        "disconnect_events": logs,
        "spectral_scan": {
            "noise_floor_avg": noise,
            "samples": len(points),
            "method": "demo",
            "raw": "demo spectral scan",
            "points": points,
        },
    }


def _frequency(channel: str) -> int | None:
    try:
        return int(channel.split("/", 1)[0])
    except (IndexError, ValueError):
        return None


def _channel_number(channel: str) -> int | None:
    frequency = _frequency(channel)
    if frequency is None:
        return None
    if frequency == 2484:
        return 14
    if 2412 <= frequency <= 2472:
        return (frequency - 2407) // 5
    if 5000 <= frequency <= 5900:
        return (frequency - 5000) // 5
    return frequency
