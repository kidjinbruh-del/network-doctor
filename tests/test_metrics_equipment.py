import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from network_doctor.fleet_store import FleetStore
from network_doctor.equipment import DeviceProfile
from network_doctor.metrics import render, build_server
from tests.test_equipment import snapshot


def test_metrics_escape_labels_and_omit_stale_values(tmp_path):
    store = FleetStore(tmp_path / "fleet.db")
    p = DeviceProfile("lab", "Lab", "127.0.0.1", username="readonly", site='a"b\nc')
    store.upsert(p)
    sample = snapshot(time.time())
    store.record(p, sample)
    text = render(store)
    from prometheus_client.parser import text_string_to_metric_families

    assert list(text_string_to_metric_families(text))
    assert 'site="a\\"b\\nc"' in text
    assert "nd_port_rx_bytes_total" in text
    assert text.count("# TYPE nd_port_rx_bytes_total ") == 1
    store.record(
        p, {"device_id": "lab", "at": time.time(), "ok": False, "error": "offline"}
    )
    text = render(store)
    assert "nd_port_rx_bytes_total" not in text
    assert "} 0" in text
    store.close()


def test_metrics_auth_is_required_and_has_no_control_routes(tmp_path):
    store = FleetStore(tmp_path / "fleet.db")
    server = build_server(store, "fixture-token-for-tests", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_port)
    try:
        with urlopen(url + "/healthz") as r:
            assert json.load(r)["service"] == "network-doctor"
        with pytest.raises(HTTPError) as error:
            urlopen(url + "/metrics")
        assert error.value.code == 401
        request = Request(
            url + "/metrics",
            headers={"Authorization": "Bearer fixture-token-for-tests"},
        )
        with urlopen(request) as r:
            assert b"nd_incidents" in r.read()
        with pytest.raises(HTTPError) as error:
            urlopen(url + "/api/settings")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        store.close()


def test_headless_cli_profile_validation_and_report(tmp_path):
    root = Path(__file__).resolve().parents[1]
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(DeviceProfile("lab", "Lab", "127.0.0.1", protocol="icmp").public())
    )
    base = [
        sys.executable,
        "-m",
        "network_doctor",
        "--data-dir",
        str(tmp_path / "data"),
    ]

    def run(*args):
        return subprocess.run(
            [*base, *args], cwd=root, capture_output=True, text=True, timeout=10
        )

    assert run("add", str(profile)).returncode == 0
    result = run("status")
    assert json.loads(result.stdout)["devices"][0]["profile"]["id"] == "lab"
    path = tmp_path / "report.html"
    assert run("export", str(path)).returncode == 0
    assert path.exists()
    data = json.loads(profile.read_text())
    data["password"] = "not-allowed"
    profile.write_text(json.dumps(data))
    assert run("add", str(profile)).returncode == 1
