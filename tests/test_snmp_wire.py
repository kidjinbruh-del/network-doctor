import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
import pytest
from network_doctor.equipment import DeviceProfile
from network_doctor.snmp_transport import Secrets, collect, EquipmentError


@pytest.fixture(scope="module")
def agent():
    command = [
        sys.executable,
        str(Path(__file__).with_name("snmp_agent.py")),
        "--port",
        "0",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    ready = queue.Queue()
    threading.Thread(
        target=lambda: ready.put(process.stdout.readline()), daemon=True
    ).start()
    try:
        line = ready.get(timeout=10)
        assert line.startswith("READY "), line
        yield int(line.split()[1])
    finally:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


@pytest.mark.parametrize("protocol", ["snmpv2c", "snmpv3"])
def test_actual_udp_transport_authpriv_and_standard_tables(agent, protocol):
    p = DeviceProfile(
        "fixture",
        "Fixture",
        "127.0.0.1",
        protocol=protocol,
        username="fixture-user",
        port=agent,
        tables=[
            "interfaces",
            "extended_interfaces",
            "lldp",
            "vlans",
            "bridge_ports",
            "arp",
        ],
    )
    secrets = Secrets(
        "fixture-community", "fixture-auth-password", "fixture-privacy-password"
    )
    result = collect(p, secrets)
    assert result["system"]["vendor"] == "D-Link"
    assert result["interfaces"][0]["rx_bytes"] == 2**40 + 1
    assert result["interfaces"][0]["rx_counter_bits"] == 64
    assert result["neighbors"][0]["source"] == "LLDP"
    assert result["vlans"][0]["egress_bridge_ports"] == [1, 2]
    assert result["vlans"][0]["untagged_bridge_ports"] == [2]
    assert result["vlans"][0]["egress_ifindexes"] == [1, 2]
    assert result["capabilities"]["arp"]["status"] == "not_exposed"
    assert "fixture-auth-password" not in json.dumps(result)


def test_wrong_community_never_looks_like_empty_success(agent):
    p = DeviceProfile(
        "fixture",
        "Fixture",
        "127.0.0.1",
        protocol="snmpv2c",
        port=agent,
        timeout=0.2,
        budget=5,
    )
    with pytest.raises(EquipmentError) as error:
        collect(p, Secrets(community="wrong-community-secret"))
    assert "wrong-community-secret" not in str(error.value)


def test_wrong_v3_privacy_password_fails_without_downgrade(agent):
    p = DeviceProfile(
        "fixture",
        "Fixture",
        "127.0.0.1",
        username="fixture-user",
        port=agent,
        timeout=0.2,
        budget=5,
    )
    with pytest.raises(EquipmentError) as error:
        collect(
            p, Secrets(auth="fixture-auth-password", privacy="wrong-privacy-secret")
        )
    assert "wrong-privacy-secret" not in str(error.value)


def test_cancel_interrupts_inflight_async_collection(monkeypatch):
    import asyncio

    closed = []

    async def wait(*_):
        try:
            await asyncio.sleep(30)
        finally:
            closed.append(True)

    monkeypatch.setattr("network_doctor.snmp_transport.collect_async", wait)
    event = threading.Event()
    timer = threading.Timer(0.05, event.set)
    timer.start()
    p = DeviceProfile("fixture", "Fixture", "127.0.0.1", username="fixture-user")
    started = time.monotonic()
    with pytest.raises(EquipmentError, match="отменён"):
        collect(
            p, Secrets(auth="password-one", privacy="password-two"), stop_event=event
        )
    assert time.monotonic() - started < 1 and closed == [True]
    timer.join()
