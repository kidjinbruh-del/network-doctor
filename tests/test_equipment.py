import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from network_doctor.equipment import (
    DeviceProfile,
    manufacturer,
    derive,
    findings,
    normalize,
    SYS,
    TABLES,
)
from network_doctor.fleet_store import FleetStore
from network_doctor.fleet import FleetService
from network_doctor.fleet_report import export
from network_doctor.snmp_transport import Secrets, EquipmentError, credentials


def profile(**kwargs):
    return DeviceProfile("lab", "Lab", "127.0.0.1", username="readonly", **kwargs)


def snapshot(at=100, **kwargs):
    p = profile()
    result = {
        "device_id": "lab",
        "name": "Lab",
        "host": p.host,
        "protocol": "snmpv3",
        "at": at,
        "ok": True,
        "system": {"object_id": "1.3.6.1.4.1.171.1", "uptime_seconds": 10000 + at},
        "warnings": [],
        "interfaces": [
            {
                "index": 1,
                "name": "port1",
                "admin_status": 1,
                "oper_status": 1,
                "speed_bps": 1_000_000_000,
                "rx_bytes": 1000,
                "tx_bytes": 2000,
                "rx_counter_bits": 64,
                "tx_counter_bits": 64,
                "discontinuity_ticks": 0,
                "rx_errors": 0,
                "tx_errors": 0,
            }
        ],
        "neighbors": [],
        "vlans": [],
    }
    result.update(kwargs)
    return result


@pytest.mark.parametrize(
    "pen,vendor",
    [
        (171, "D-Link"),
        (14988, "MikroTik"),
        (9, "Cisco"),
        (11863, "TP-Link"),
        (2011, "Huawei"),
        (41112, "Ubiquiti"),
        (2636, "Juniper"),
        (14823, "Aruba"),
    ],
)
def test_iana_vendor_identification(pen, vendor):
    assert manufacturer("1.3.6.1.4.1." + str(pen) + ".1") == (vendor, pen)
    assert (
        manufacturer("1.3.6.1.4.1." + str(pen) + "0.1")[0]
        == "Неизвестный производитель"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"id": "../bad"},
        {"host": "http://router/"},
        {"protocol": "telnet"},
        {"port": True},
        {"username": ""},
        {"tables": ["arbitrary"]},
        {"auth_protocol": "md5"},
        {"site": []},
        {"expected_ports": [True]},
    ],
)
def test_profile_validation(change):
    data = profile().public()
    data.update(change)
    with pytest.raises(ValueError):
        DeviceProfile.from_dict(data)


def test_no_plaintext_secret_fields_or_repr(monkeypatch):
    data = profile().public()
    data["password"] = "not-persistable"
    with pytest.raises(ValueError):
        DeviceProfile.from_dict(data)
    assert "secret" not in repr(Secrets("secret", "secret", "secret"))
    monkeypatch.setenv("ND_SNMP_AUTH", "password-a")
    monkeypatch.setenv("ND_SNMP_PRIVACY", "password-b")
    values = credentials(profile())
    assert values.auth == "password-a" and values.privacy == "password-b"


def test_rate_calculation_and_full_duplex_utilization():
    old = snapshot(100)
    new = snapshot(110)
    new["interfaces"][0].update(
        rx_bytes=1_000_001_000, tx_bytes=1_000_002_000, rx_errors=2
    )
    result = derive(new, old)["interfaces"][0]
    assert result["rx_bps"] == 800_000_000 and result["tx_bps"] == 800_000_000
    assert result["utilization_pct"] == 80  # full duplex: don't sum RX and TX
    assert result["rx_errors_delta"] == 2
    assert findings(new, [])[0][0] == "port:1:errors"


@pytest.mark.parametrize(
    "case",
    [
        "reboot",
        "reset",
        "discontinuity",
        "identity",
        "clock",
        "counter-width",
        "wrap-risk",
    ],
)
def test_rate_not_invented_when_comparison_is_unsafe(case):
    old = snapshot(100)
    new = snapshot(110)
    new["interfaces"][0]["rx_bytes"] = 2000
    if case == "reboot":
        new["system"]["uptime_seconds"] = 1
    if case == "reset":
        new["interfaces"][0]["rx_bytes"] = 1
    if case == "discontinuity":
        new["interfaces"][0]["discontinuity_ticks"] = 10
    if case == "identity":
        new["system"]["object_id"] = "1.3.6.1.4.1.14988.1"
    if case == "clock":
        new["at"] = 99
    if case == "counter-width":
        new["interfaces"][0]["rx_counter_bits"] = 32
    if case == "wrap-risk":
        new["at"] = 160
        old["interfaces"][0]["rx_counter_bits"] = 32
        new["interfaces"][0]["rx_counter_bits"] = 32
    assert "rx_bps" not in derive(new, old)["interfaces"][0]


def test_expected_ports_only_generate_down_incident():
    sample = snapshot()
    sample["interfaces"][0]["oper_status"] = 2
    assert findings(sample, []) == []
    assert findings(sample, [1])[0][0] == "port:1:down"


def test_incident_lifecycle_dedup_restart_and_missing_evidence(tmp_path):
    path = tmp_path / "fleet.db"
    store = FleetStore(path)
    p = profile(expected_ports=[1])
    store.upsert(p)
    failure = {"device_id": "lab", "at": 100, "ok": False, "error": "unreachable"}
    store.record(p, failure)
    assert store.incidents() == []
    store.record(p, {**failure, "at": 101})
    store.record(p, {**failure, "at": 102})
    assert len(store.incidents()) == 1
    ident = store.incidents()[0]["id"]
    store.change_incident(ident, "acknowledge")
    store.close()
    store = FleetStore(path)
    assert store.incidents()[0]["status"] == "acknowledged"
    down = snapshot(110)
    down["interfaces"][0]["oper_status"] = 2
    store.record(p, down)
    assert (
        next(i for i in store.incidents() if i["code"] == "unreachable")["status"]
        == "resolved"
    )
    store.record(p, snapshot(120, interfaces=[]))
    assert (
        next(i for i in store.incidents() if i["code"] == "port:1:down")["status"]
        == "open"
    )
    store.record(p, snapshot(130))
    assert all(i["status"] == "resolved" for i in store.incidents())
    assert len(store.incidents()[0]["timeline"]) >= 2
    store.close()


def test_retention_limits_and_failed_transaction(tmp_path):
    store = FleetStore(tmp_path / "fleet.db")
    p = profile()
    store.upsert(p)
    for i in range(105):
        store.record(p, snapshot(100 + i))
    assert len(store.history("lab")) == 100
    with pytest.raises(ValueError):
        store.record(p, snapshot(device_id="other"))
    assert len(store.history("lab")) == 100
    store.close()


def test_safe_offline_html_export_and_no_credentials(tmp_path):
    store = FleetStore(tmp_path / "fleet.db")
    p = profile()
    p.name = "<script>alert(1)</script>"
    store.upsert(p)
    store.record(p, snapshot())
    path = export(store, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text and "&lt;script&gt;" in text
    export(store, tmp_path / "report.json")
    assert "privacy_password" not in (tmp_path / "report.json").read_text()
    store.close()


def test_fleet_limits_concurrency_and_sanitizes_errors(tmp_path):
    import threading

    store = FleetStore(tmp_path / "fleet.db")
    for i in range(12):
        store.upsert(DeviceProfile("id" + str(i), "Lab", "127.0.0.1", protocol="icmp"))
    lock = threading.Lock()
    active = 0
    maximum = 0

    def collector(p, _):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        raise RuntimeError("must-not-leak-secret")

    result = FleetService(store, collector).poll()
    assert len(result) == 12 and maximum <= 4
    assert "must-not-leak-secret" not in json.dumps(store.state())
    store.close()


def test_stop_does_not_record_cancelled_sample(tmp_path):
    import threading

    store = FleetStore(tmp_path / "fleet.db")
    store.upsert(profile())
    started = threading.Event()
    release = threading.Event()

    def collector(p, _):
        started.set()
        release.wait(2)
        return snapshot()

    service = FleetService(store, collector)
    thread = threading.Thread(target=service.poll)
    thread.start()
    assert started.wait(1)
    service.stop_event.set()
    release.set()
    thread.join(2)
    assert store.history("lab") == []
    store.close()
