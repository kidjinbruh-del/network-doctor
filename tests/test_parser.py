from __future__ import annotations

from network_doctor.parser import (
    filter_virtual_clients,
    is_virtual_mac,
    parse_spectral_scan,
    virtual_mac_reason,
)

# Real office survey from 15-22 Oct 2026. Channel 4 is shared by three
# Keenetic-class APs at signal 100; five-GHz channel 52 is also occupied.
INCIDENT_SCAN = """
CHANNEL   SSID                    SIGNAL
4         <hidden>                100
4         OFFICE-WIFI        100
4         OFFICE-TEST                  100
2         <hidden>                85
2         OFFICE-NEIGHBOR                 85
9         OFFICE-AP-1           54
9         OFFICE-DIRECT        32
1         OFFICE-AP-2           87
8         OFFICE-GUEST                     20
11        OFFICE-TPLINK            25
52        <hidden>                95
48        OFFICE-NEIGHBOR                 59
48        <hidden>                59
"""

OFFICE_DEVICES = [
    {"mac": "50:ff:20:00:00:01", "interface": "wlan1"},
    {"mac": "50:ff:20:00:00:03", "interface": "wlan1"},
    {"mac": "50:ff:20:00:00:04", "interface": "wlan1"},
    {"mac": "24:9a:d8:29:24:17", "interface": "wlan1"},
    {"mac": "08:9d:f4:32:ec:95", "interface": "wlan1"},
    {"mac": "7a:d3:3d:52:b1:1a", "interface": "wlan1"},
    {"mac": "6a:0c:f4:6a:ad:ad", "interface": "wlan1"},
    {"mac": "64:51:06:21:9a:58", "interface": "wlan1"},
    {"mac": "9e:39:76:49:95:c8", "interface": "wlan1"},
    {"mac": "d6:60:23:a5:78:e1", "interface": "wlan1"},
    {"mac": "bc:24:11:8f:56:38", "interface": "wlan1"},
    {"mac": "bc:24:11:be:38:1e", "interface": "wlan1"},
    {"mac": "bc:24:11:e3:d1:ed", "interface": "wlan1"},
    {"mac": "52:54:00:12:34:56", "interface": "virbr0"},
]


def test_parse_incident_scan_keeps_hidden_ssid() -> None:
    rows = parse_spectral_scan(INCIDENT_SCAN)

    assert len(rows) == 13
    assert rows[0]["channel"] == 4
    assert rows[0]["ssid"] == ""
    assert rows[0]["hidden"] is True
    assert rows[0]["signal"] == 100
    assert rows[1]["ssid"] == "OFFICE-WIFI"
    assert rows[1]["hidden"] is False
    assert rows[-1]["channel"] == 48


def test_parse_unstructured_hidden_row() -> None:
    rows = parse_spectral_scan("4  -62dBm\n6  OfficeWiFi  -55 dBm\n")
    assert [row["ssid"] for row in rows] == ["", "OfficeWiFi"]
    assert [row["signal"] for row in rows] == [-62, -55]


def test_empty_ssid_column_does_not_shift_signal() -> None:
    rows = parse_spectral_scan(
        "CHANNEL  SSID  SIGNAL\n"
        "4              100\n"
        "6        OfficeWiFi  -55\n"
    )
    assert [(row["channel"], row["ssid"], row["signal"]) for row in rows] == [
        (4, "", 100),
        (6, "OfficeWiFi", -55),
    ]


def test_parse_dictionary_rows() -> None:
    rows = parse_spectral_scan(
        [
            {
                "channel": 4,
                "ssid": "",
                "signal-strength": "-61dBm@6Mbps",
                "mac-address": "50:FF:20:00:00:03",
            }
        ]
    )
    assert rows[0]["channel"] == 4
    assert rows[0]["ssid"] == ""
    assert rows[0]["signal"] == -61
    assert rows[0]["bssid"] == "50:ff:20:00:00:03"


def test_virtual_mac_reasons_from_incident() -> None:
    assert virtual_mac_reason("52:54:00:12:34:56") == "QEMU/KVM MAC 52:54:00"
    assert virtual_mac_reason("bc:24:11:8f:56:38") == "Proxmox VE MAC bc:24:11"
    assert virtual_mac_reason("7a:d3:3d:52:b1:1a") == "локально назначенный MAC"
    assert virtual_mac_reason("50:ff:20:00:00:01") is None
    assert virtual_mac_reason("aa:bb:cc:dd:ee:ff", "virbr0") == (
        "виртуальный интерфейс virbr*"
    )
    assert is_virtual_mac("bc:24:11:e3:d1:ed") is True


def test_filter_real_office_inventory() -> None:
    result = filter_virtual_clients(OFFICE_DEVICES)

    assert result.total == 14
    assert len(result.removed) == 8
    assert len(result.kept) == 6
    assert {item["mac"] for item in result.kept} == {
        "50:ff:20:00:00:01",
        "50:ff:20:00:00:03",
        "50:ff:20:00:00:04",
        "24:9a:d8:29:24:17",
        "08:9d:f4:32:ec:95",
        "64:51:06:21:9a:58",
    }
    assert all(item["virtual"] is False for item in result.kept)
    assert all(item["virtual"] is True for item in result.removed)
    assert result.reasons["виртуальный интерфейс virbr*"] == 1
    assert result.reasons["Proxmox VE MAC bc:24:11"] == 3
    assert result.reasons["локально назначенный MAC"] == 4


def test_keeps_keenetic_router_mac() -> None:
    result = filter_virtual_clients(
        [{"mac": "50:ff:20:00:00:01", "interface": "bridge"}]
    )
    assert len(result.kept) == 1
    assert result.kept[0]["mac"] == "50:ff:20:00:00:01"
