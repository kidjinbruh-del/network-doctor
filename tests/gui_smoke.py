"""Real CTk UI with loopback SNMPv3 fixture; run under Xvfb on Linux."""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    errors = []
    with tempfile.TemporaryDirectory(prefix="nd-gui-") as directory:
        os.environ["NETWORK_DOCTOR_DATA_DIR"] = directory
        agent = subprocess.Popen(
            [sys.executable, str(ROOT / "tests" / "snmp_agent.py"), "--port", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        ready = queue.Queue()
        threading.Thread(
            target=lambda: ready.put(agent.stdout.readline()), daemon=True
        ).start()
        app = None
        try:
            line = ready.get(timeout=10)
            assert line.startswith("READY "), line
            port = int(line.split()[1])
            from network_doctor.ui import NetworkDoctorApp

            app = NetworkDoctorApp()
            app.report_callback_exception = lambda kind, value, trace: errors.append(
                str(value)
            )
            panel = app.fleet_panel
            assert app.tabview.get() == "Оборудование"

            def fill(key, value):
                entry = panel.entries[key]
                entry.delete(0, "end")
                entry.insert(0, value)

            for key, value in {
                "id": "fixture",
                "name": "SNMPv3 fixture",
                "host": "127.0.0.1",
                "port": str(port),
                "username": "fixture-user",
                "auth_secret": "fixture-auth-password",
                "privacy_secret": "fixture-privacy-password",
                "expected_ports": "2",
            }.items():
                fill(key, value)
            panel.table_vars["lldp"].select()
            panel.table_vars["vlans"].select()
            panel.table_vars["bridge_ports"].select()
            panel.poll_selected()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                app.update()
                time.sleep(0.03)
                if not panel.busy:
                    break
            assert not panel.busy, "UI did not finish polling"
            state = panel.store.state()
            assert state[0]["latest"]["ok"], state
            assert state[0]["last_success"]["system"]["vendor"] == "D-Link"
            assert len(panel.ports.get_children()) == 2
            assert len(panel.vlans.get_children()) == 1
            assert len(panel.neighbors.get_children()) == 1
            assert len(panel.history.get_children()) == 1
            incidents = panel.store.incidents()
            assert len(incidents) == 1 and incidents[0]["code"] == "port:2:down"
            # Widget selection survives periodic refresh and can acknowledge an incident.
            panel.incidents.selection_set(str(incidents[0]["id"]))
            panel.refresh()
            panel.change_incident("acknowledge")
            assert panel.store.incidents()[0]["status"] == "acknowledged"
            assert panel.entries["auth_secret"].get() == "fixture-auth-password"
            assert "fixture-auth-password" not in json.dumps(panel.store.state())
            assert "Lab VLAN" in panel.details.get("1.0", "end")
            app.update()
            from PIL import ImageGrab

            box = (
                app.winfo_rootx(),
                app.winfo_rooty(),
                app.winfo_rootx() + app.winfo_width(),
                app.winfo_rooty() + app.winfo_height(),
            )
            ImageGrab.grab(bbox=box).save(output / "equipment.png")
            assert not errors, errors
            (output / "gui-report.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "checks": [
                            "real_tk_ui",
                            "snmpv3_authpriv",
                            "D-Link_identification",
                            "ports",
                            "VLAN",
                            "LLDP",
                            "incident_acknowledgement",
                            "no_secret_persistence",
                        ],
                        "scope": "loopback synthetic agent; no physical equipment",
                    },
                    indent=2,
                )
            )
        finally:
            if app:
                app._on_close()
            agent.terminate()
            try:
                agent.wait(timeout=5)
            except subprocess.TimeoutExpired:
                agent.kill()
                agent.wait(timeout=5)


if __name__ == "__main__":
    main()
