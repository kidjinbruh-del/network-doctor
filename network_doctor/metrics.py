"""Prometheus exposition from cached SQLite samples; HTTP never polls hardware."""

import hmac
import json
import math
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def label(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render(store, max_age=180):
    families = {}

    def add(name, kind, help_text, value, labels=None):
        if value is None or not math.isfinite(float(value)):
            return
        if name not in families:
            families[name] = (kind, help_text, [])
        dimensions = (
            "{"
            + ",".join(k + '="' + label(v) + '"' for k, v in (labels or {}).items())
            + "}"
            if labels
            else ""
        )
        families[name][2].append(name + dimensions + " " + str(value))

    for device in store.state():
        profile = device["profile"]
        sample = device["latest"]
        dimensions = {"device": profile["id"], "site": profile["site"]}
        age = max(0, time.time() - sample["at"]) if sample else None
        fresh = bool(sample and sample["ok"] and age <= max_age)
        add(
            "nd_equipment_up",
            "gauge",
            "Recent successful collection (not an internet health assertion).",
            int(fresh),
            dimensions,
        )
        add(
            "nd_equipment_poll_age_seconds",
            "gauge",
            "Age of the most recent attempt.",
            age,
            dimensions,
        )
        if not fresh:
            continue
        add(
            "nd_equipment_uptime_seconds",
            "gauge",
            "Device uptime from SNMP sysUpTime.",
            sample.get("system", {}).get("uptime_seconds"),
            dimensions,
        )
        for port in sample.get("interfaces", []):
            labels = {**dimensions, "port": str(port["index"]), "name": port["name"]}
            if port.get("oper_status") in (1, 2, 7):
                add(
                    "nd_port_oper_up",
                    "gauge",
                    "1 up; 0 down/lowerLayerDown; unknown omitted.",
                    int(port["oper_status"] == 1),
                    labels,
                )
            add(
                "nd_port_speed_bps",
                "gauge",
                "Reported interface capacity.",
                port.get("speed_bps"),
                labels,
            )
            for direction in ("rx", "tx"):
                add(
                    "nd_port_octet_counter_bits",
                    "gauge",
                    "Available octet counter width.",
                    port.get(direction + "_counter_bits"),
                    {**labels, "direction": direction},
                )
                if port.get(direction + "_counter_bits") == 64:
                    add(
                        "nd_port_" + direction + "_bytes_total",
                        "counter",
                        "64-bit SNMP octet counter.",
                        port.get(direction + "_bytes"),
                        labels,
                    )
                add(
                    "nd_port_" + direction + "_errors_total",
                    "counter",
                    "SNMP error counter.",
                    port.get(direction + "_errors"),
                    labels,
                )
    for status in ("open", "acknowledged", "resolved"):
        add(
            "nd_incidents",
            "gauge",
            "Incident count among latest 500 records.",
            sum(i["status"] == status for i in store.incidents()),
            {"status": status},
        )
    lines = []
    for name, (kind, description, samples) in families.items():
        lines.extend(
            [
                "# HELP " + name + " " + description,
                "# TYPE " + name + " " + kind,
                *samples,
            ]
        )
    return "\n".join(lines) + "\n"


def build_server(store, token, port=8776, max_age=180):
    if not token or len(token) < 16:
        raise ValueError("ND_METRICS_TOKEN должен содержать минимум 16 символов")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"service":"network-doctor","status":"ok"}')
                return
            if self.path != "/metrics":
                self.send_error(404)
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                self.send_response(401)
                self.send_header("WWW-Authenticate", "Bearer")
                self.end_headers()
                return
            payload = render(store, max_age).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
