from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from routeros_api import RouterOsApiPool

from .parser import filter_virtual_clients
from .spectral import SpectralResult, parse_spectral_scan
from .utils import as_integer, as_number, as_text, clean_row
from .vendors import vendor_for_mac

_DISCONNECT_RE = re.compile(
    r"(?i)(disconnect|deauth|disassociated|failed|error|terminated|reconnect|"
    r"invalid|timeout|timed out)"
)
KEENETIC_MAC_PREFIX = "50:ff:20"


class RouterError(RuntimeError):
    pass


@dataclass(frozen=True)
class RouterInfo:
    identity: str
    version: str
    board: str
    host: str

    def as_text(self) -> str:
        parts = [self.identity or "MikroTik"]
        if self.version:
            parts.append(f"RouterOS {self.version}")
        if self.board:
            parts.append(self.board)
        return " · ".join(parts)


class MikroTikClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 8728,
        timeout: int = 8,
        use_ssl: bool = False,
        ssl_verify: bool = False,
    ) -> None:
        self.host = host.strip()
        self.username = username.strip()
        self.password = password
        self.port = port
        self.timeout = timeout
        self.use_ssl = use_ssl
        self.ssl_verify = ssl_verify
        self._pool: RouterOsApiPool | None = None
        self._api: Any = None

    def __enter__(self) -> MikroTikClient:
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def connect(self) -> None:
        if not self.host:
            raise RouterError("Укажите IP-адрес роутера.")
        try:
            self._pool = RouterOsApiPool(
                self.host,
                self.username,
                self.password,
                port=self.port,
                plaintext_login=True,
                use_ssl=self.use_ssl,
                ssl_verify=self.ssl_verify,
                ssl_verify_hostname=self.ssl_verify,
            )
            self._pool.set_timeout(self.timeout)
            self._api = self._pool.get_api()
        except Exception as exc:
            self.close()
            raise RouterError(self._human_error(exc)) from exc

    def close(self) -> None:
        if self._pool is not None:
            try:
                self._pool.disconnect()
            except Exception:
                pass
        self._pool = None
        self._api = None

    def test_connection(self) -> RouterInfo:
        identity_rows = self._get("/system/identity")
        resource_rows = self._get("/system/resource")
        identity = as_text(identity_rows[0].get("name")) if identity_rows else ""
        resource = resource_rows[0] if resource_rows else {}
        return RouterInfo(
            identity=identity or self.host,
            version=as_text(resource.get("version")),
            board=as_text(resource.get("board-name")),
            host=self.host,
        )

    def collect_snapshot(
        self,
        snapshot_type: str,
        *,
        health_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Collect the v2 enterprise snapshot while keeping legacy keys."""
        info = self.test_connection()
        registration_raw, _, registration_error = self._try_paths(
            (
                "/interface/wireless/registration-table",
                "/interface/wifi/registration-table",
            )
        )
        wireless_raw, wireless_path, wireless_error = self._try_paths(
            ("/interface/wireless", "/interface/wifi")
        )
        logs_raw, logs_error = self._try_get("/log")
        arp_raw, arp_error = self._try_get("/ip/arp")
        lease_raw, lease_error = self._try_get("/ip/dhcp-server/lease")
        route_raw, route_error = self._try_get("/ip/route")
        address_raw, address_error = self._try_get("/ip/address")
        nat_raw, nat_error = self._try_get("/ip/firewall/nat")
        dhcp_raw, dhcp_error = self._try_get("/ip/dhcp-server/network")

        warnings = [
            message
            for message in (
                registration_error,
                wireless_error,
                logs_error,
                arp_error,
                lease_error,
                route_error,
                address_error,
                nat_error,
                dhcp_error,
            )
            if message
        ]

        registration = [
            self._normalize_registration(clean_row(row))
            for row in registration_raw
            if isinstance(row, dict)
        ]
        wireless = [
            self._normalize_wireless(clean_row(row), wireless_path or "/interface/wireless")
            for row in wireless_raw
            if isinstance(row, dict)
        ]
        logs = [
            self._normalize_log(clean_row(row))
            for row in logs_raw[-300:]
            if isinstance(row, dict)
        ]
        disconnect_events = [
            row for row in logs if _DISCONNECT_RE.search(as_text(row.get("topics")) + " " + as_text(row.get("message")))
        ]

        spectral, spectral_warnings = self._collect_spectral(wireless)
        warnings.extend(spectral_warnings)

        arp = [self._normalize_arp(clean_row(row)) for row in arp_raw if isinstance(row, dict)]
        leases = [
            self._normalize_lease(clean_row(row))
            for row in lease_raw
            if isinstance(row, dict)
        ]
        clients, removed_clients = self._build_clients(registration, arp, leases)
        if removed_clients:
            warnings.append(
                "Отфильтровано виртуальных/локальных MAC: "
                f"{len(removed_clients)} "
                "(52:54:00, bc:24:11, locally administered, virbr*/vnet*/veth*)."
            )

        topology = self._build_topology(
            info,
            route_raw,
            address_raw,
            arp,
            dhcp_raw,
            wireless,
        )
        ports = self._probe_ports(as_text(topology["keenetic"].get("ip")))
        topology["keenetic"]["management_ports"] = ports
        topology["keenetic"]["reachable"] = _arp_complete(
            arp, as_text(topology["keenetic"].get("ip"))
        )
        closed_ports = sorted(port for port, opened in ports.items() if not opened)
        if closed_ports:
            warnings.append(
                "Порты управления Keenetic закрыты: "
                + ", ".join(str(port) for port in closed_ports)
                + ". Причина недоступности панели — за Keenetic."
            )

        diagnostics = {
            "default_route": topology["mikrotik"]["default_route"],
            "nat": self._nat_state(nat_raw),
            "arp": arp,
            "keenetic": {
                "ip": topology["keenetic"].get("ip"),
                "arp": topology["keenetic"].get("reachable"),
                "ports": ports,
            },
        }

        health = {
            "target": "8.8.8.8",
            "loss_pct": None,
            "loss_max_pct": None,
            "jitter_ms": None,
            "jitter_max_ms": None,
            "latency_ms": None,
            "latency_max_ms": None,
            "samples": 0,
            "source": "not_collected",
        }
        if health_metrics:
            health.update(health_metrics)
            health["source"] = as_text(health.get("source")) or "external"
            icmp_error = as_text(health.get("error"))
            if icmp_error:
                warnings.append(f"ICMP-проба: {icmp_error}")

        collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return {
            "schema_version": 2,
            "meta": {
                "snapshot_type": snapshot_type,
                "snapshot_label": "Офис" if snapshot_type == "office" else "Лаборатория",
                "collected_at": collected_at,
                "host": info.host,
                "router_identity": info.identity,
                "router_version": info.version,
                "router_board": info.board,
            },
            "topology": topology,
            "wireless_scan": self._build_wireless_scan(wireless, registration),
            "clients": clients,
            "health": health,
            "warnings": warnings,
            "diagnostics": diagnostics,
            "registration_table": registration,
            "wireless_interfaces": wireless,
            "logs": logs,
            "disconnect_events": disconnect_events,
            "spectral_scan": spectral.to_dict(),
        }

    def _build_clients(
        self,
        registration: list[dict[str, Any]],
        arp: list[dict[str, Any]],
        leases: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        arp_by_mac = {
            as_text(item.get("mac")).lower(): item
            for item in arp
            if as_text(item.get("mac"))
        }
        lease_by_mac = {
            as_text(item.get("mac")).lower(): item
            for item in leases
            if as_text(item.get("mac"))
        }
        items: list[dict[str, Any]] = []
        for item in registration:
            mac = as_text(item.get("mac"))
            key = mac.lower()
            arp_item = arp_by_mac.get(key, {})
            lease = lease_by_mac.get(key, {})
            items.append(
                {
                    **item,
                    "ip": as_text(lease.get("address") or arp_item.get("address")),
                    "vendor": vendor_for_mac(mac),
                    "hostname": as_text(lease.get("hostname")),
                    "interface": as_text(item.get("interface") or arp_item.get("interface")),
                }
            )
        filtered = filter_virtual_clients(items)
        return filtered.kept + filtered.removed, filtered.removed

    @staticmethod
    def _build_wireless_scan(
        wireless: list[dict[str, Any]],
        registration: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_interface: dict[str, float] = {}
        for client in registration:
            signal = as_number(client.get("signal_dbm"))
            interface = as_text(client.get("interface"))
            if signal is None or not interface:
                continue
            by_interface[interface] = max(signal, by_interface.get(interface, signal))
        rows: list[dict[str, Any]] = []
        for item in wireless:
            name = as_text(item.get("name"))
            signal = by_interface.get(name)
            channel_text = as_text(item.get("channel"))
            frequency = as_integer(item.get("frequency"))
            channel = _channel_from_text(channel_text)
            if channel is None and frequency is not None:
                channel = _channel_from_frequency(frequency)
            if channel is None or signal is None:
                continue
            rows.append(
                {
                    "channel": channel,
                    "frequency": frequency,
                    "ssid": as_text(item.get("ssid")),
                    "bssid": as_text(item.get("mac")) or None,
                    "signal": signal,
                    "security": None,
                    "hidden": not bool(as_text(item.get("ssid"))),
                }
            )
        return rows

    def _build_topology(
        self,
        info: RouterInfo,
        routes: list[dict[str, Any]],
        addresses: list[dict[str, Any]],
        arp: list[dict[str, Any]],
        dhcp_networks: list[dict[str, Any]],
        wireless: list[dict[str, Any]],
    ) -> dict[str, Any]:
        route = _route_state(routes)
        normalized_addresses = [
            _normalize_address(clean_row(row))
            for row in addresses
            if isinstance(row, dict)
        ]
        lan = next(
            (
                item
                for item in normalized_addresses
                if item["network"] and not item["address"].startswith("127.")
            ),
            {"address": "", "network": "", "interface": ""},
        )
        subnet_rows = [
            {
                "network": as_text(item.get("network")),
                "gateway": as_text(item.get("gateway")),
                "interface": as_text(item.get("interface")),
            }
            for item in dhcp_networks
            if isinstance(item, dict)
        ]
        keenetic_arp = next(
            (
                item
                for item in arp
                if as_text(item.get("mac")).lower().startswith(KEENETIC_MAC_PREFIX)
            ),
            {},
        )
        # The Keenetic access point is recognised by its own BSSID; any other
        # visible SSID is used as a fallback so the app works in other networks.
        keenetic_wireless = next(
            (
                item
                for item in wireless
                if as_text(item.get("bssid")).lower().startswith(KEENETIC_MAC_PREFIX)
            ),
            {},
        )
        if not keenetic_wireless:
            keenetic_wireless = next(
                (item for item in wireless if as_text(item.get("ssid"))),
                {},
            )
        return {
            "mikrotik": {
                "host": info.host,
                "identity": info.identity,
                "version": info.version,
                "board": info.board,
                "lan_ip": lan["address"],
                "lan_subnet": lan["network"],
                "wan_interface": route["gateway"],
                "default_route": route,
            },
            "keenetic": {
                "ip": as_text(keenetic_arp.get("address")),
                "wan_ip": as_text(keenetic_arp.get("address")),
                "mac": as_text(keenetic_arp.get("mac")),
                "lan_subnet": None,
                "ssid": as_text(keenetic_wireless.get("ssid")),
                "channel": as_text(keenetic_wireless.get("channel")),
                "reachable": False,
                "management_ports": {},
            },
            "subnets": normalized_addresses + subnet_rows,
        }

    @staticmethod
    def _nat_state(rows: list[dict[str, Any]]) -> dict[str, Any]:
        rules = [row for row in rows if isinstance(row, dict)]
        masquerade = [
            row
            for row in rules
            if as_text(row.get("action")).lower() == "masquerade"
            and not as_text(row.get("disabled")).lower() in {"true", "yes"}
        ]
        selected = masquerade or rules
        total_bytes = sum(as_number(row.get("bytes")) or 0 for row in selected)
        total_packets = sum(as_number(row.get("packets")) or 0 for row in selected)
        return {
            "bytes": int(total_bytes) if selected else None,
            "packets": int(total_packets) if selected else None,
            "rules": len(selected),
            "growing": None,
        }

    @staticmethod
    def _probe_ports(ip: str) -> dict[int, bool]:
        """Probe the Keenetic management ports with a short per-port deadline."""
        if not ip:
            return {}
        result: dict[int, bool] = {}
        for port in (22, 80, 443, 8291):
            try:
                with socket.create_connection((ip, port), timeout=0.35):
                    result[port] = True
            except OSError:
                result[port] = False
        return result

    @staticmethod
    def _normalize_arp(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "address": as_text(row.get("address")),
            "mac": as_text(row.get("mac-address") or row.get("mac")),
            "interface": as_text(row.get("interface")),
            "complete": as_text(row.get("complete")).lower() in {"true", "yes", "1"},
            "invalid": as_text(row.get("invalid")).lower() in {"true", "yes", "1"},
        }

    @staticmethod
    def _normalize_lease(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "address": as_text(row.get("address")),
            "mac": as_text(row.get("mac-address") or row.get("mac")),
            "hostname": as_text(row.get("host-name") or row.get("hostname")),
            "status": as_text(row.get("status")),
            "comment": as_text(row.get("comment")),
        }

    def _get(self, path: str) -> list[dict[str, Any]]:
        if self._api is None:
            raise RouterError("Нет активного подключения к роутеру.")
        try:
            return list(self._api.get_resource(path).get())
        except Exception as exc:
            raise RouterError(f"Не удалось прочитать {path}: {exc}") from exc

    def _try_get(self, path: str) -> tuple[list[dict[str, Any]], str | None]:
        try:
            return self._get(path), None
        except RouterError as exc:
            return [], str(exc)

    def _try_paths(
        self, paths: tuple[str, ...]
    ) -> tuple[list[dict[str, Any]], str | None, str | None]:
        errors: list[str] = []
        for path in paths:
            try:
                return self._get(path), path, None
            except RouterError as exc:
                errors.append(str(exc))
        return [], None, "; ".join(errors)

    def _collect_spectral(
        self, wireless: list[dict[str, Any]]
    ) -> tuple[SpectralResult, list[str]]:
        if not wireless:
            return SpectralResult(None, 0, "none"), [
                "Spectral-scan пропущен: wireless-интерфейсы не найдены."
            ]

        interfaces: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in wireless:
            name = as_text(item.get("name"))
            menu = as_text(item.get("menu")) or "/interface/wireless"
            if not name or item.get("disabled"):
                continue
            key = (menu, name)
            if key not in seen:
                seen.add(key)
                interfaces.append(key)
        if not interfaces:
            return SpectralResult(None, 0, "none"), [
                "Spectral-scan пропущен: все wireless-интерфейсы отключены."
            ]

        warnings: list[str] = []
        results: list[tuple[str, SpectralResult]] = []
        for menu, interface in interfaces[:2]:
            try:
                resource = self._api.get_binary_resource(menu)
                rows = list(
                    resource.call(
                        "spectral-scan",
                        {"interface": interface, "duration": 8},
                    )
                )
                result = parse_spectral_scan(rows)
                if result.noise_floor_avg is None:
                    warnings.append(
                        f"Spectral-scan {interface}: не удалось выделить Noise floor."
                    )
                else:
                    results.append((interface, result))
            except Exception as exc:
                warnings.append(f"Spectral-scan {interface}: {exc}")

        if not results:
            return SpectralResult(None, 0, "none"), warnings

        total_samples = sum(result.samples for _, result in results)
        average = sum(
            result.noise_floor_avg * result.samples
            for _, result in results
            if result.noise_floor_avg is not None
        ) / total_samples
        method = results[0][1].method if len(results) == 1 else "combined"
        raw = "\n\n".join(
            f"[{interface}]\n{result.raw}" for interface, result in results
        )
        return SpectralResult(round(average, 1), total_samples, method, raw), warnings

    @staticmethod
    def _normalize_registration(row: dict[str, Any]) -> dict[str, Any]:
        signal_raw = as_text(row.get("signal-strength") or row.get("signal"))
        return {
            "interface": as_text(row.get("interface")),
            "mac": as_text(row.get("mac-address") or row.get("mac")),
            "signal_dbm": as_number(signal_raw),
            "signal_raw": signal_raw,
            "tx_ccq": as_number(row.get("tx-ccq")),
            "rx_ccq": as_number(row.get("rx-ccq")),
            "retry_pct": as_number(
                row.get("tx-retries-percentage")
                or row.get("tx-retries")
                or row.get("retry-pct")
            ),
            "tx_rate": as_text(row.get("tx-rate")),
            "rx_rate": as_text(row.get("rx-rate")),
            "uptime": as_text(row.get("uptime")),
        }

    @staticmethod
    def _normalize_wireless(row: dict[str, Any], menu: str) -> dict[str, Any]:
        disabled = as_text(row.get("disabled")).lower() in {"true", "yes", "1"}
        config = row.get("configuration")
        ssid = as_text(row.get("ssid"))
        if not ssid and isinstance(config, dict):
            ssid = as_text(config.get("ssid"))
        return {
            "name": as_text(row.get("name")),
            "menu": menu,
            "mac": as_text(row.get("mac-address")),
            "ssid": ssid,
            "channel": as_text(row.get("channel")),
            "frequency": as_integer(row.get("frequency")),
            "band": as_text(row.get("band")),
            "tx_power": as_number(row.get("tx-power")),
            "noise_floor": as_number(row.get("noise-floor")),
            "overall_tx_ccq": as_number(row.get("overall-tx-ccq")),
            "clients": as_integer(row.get("registered-clients")),
            "disabled": disabled,
        }

    @staticmethod
    def _normalize_log(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "time": as_text(row.get("time")),
            "topics": as_text(row.get("topics")),
            "message": as_text(row.get("message")),
        }

    def _human_error(self, exc: Exception) -> str:
        message = str(exc) or exc.__class__.__name__
        lowered = message.lower()
        if (
            "10061" in message
            or "connection refused" in lowered
            or "отверг" in lowered
            or "отклон" in lowered
        ):
            return (
                f"{self.host}:{self.port} доступен, но соединение отклонено. "
                "Проверьте, что это IP MikroTik и что API включён: "
                "/ip service enable api; /ip service print."
            )
        if "10060" in message or "timed out" in lowered or "timeout" in lowered:
            return (
                f"Не удалось подключиться к {self.host}:{self.port} за "
                f"{self.timeout} с. Проверьте IP, доступность API и firewall."
            )
        if "10065" in message or "no route" in lowered or "unreachable" in lowered:
            return (
                f"Нет маршрута до {self.host}. Проверьте, что компьютер "
                "находится в той же сети, что и роутер."
            )
        if "11001" in message or "getaddrinfo" in lowered or "name or service" in lowered:
            return f"Не удалось определить адрес {self.host}."
        if "invalid user" in lowered or "login" in lowered or "password" in lowered:
            return "Роутер отклонил логин или пароль."
        return message


def _channel_from_text(value: str) -> int | None:
    match = re.search(r"\d+", value)
    if not match:
        return None
    number = int(match.group(0))
    if 1 <= number <= 233:
        return number
    if 2000 <= number <= 7200:
        return _channel_from_frequency(number)
    return None


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


def _route_state(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        if not isinstance(row, dict):
            continue
        destination = as_text(row.get("dst-address"))
        if destination not in {"0.0.0.0/0", "0.0.0.0"}:
            continue
        disabled = as_text(row.get("disabled")).lower() in {"true", "yes", "1"}
        active = row.get("active")
        return {
            "present": True,
            "active": (
                as_text(active).lower() in {"true", "yes", "1"}
                if active is not None
                else not disabled
            ),
            "gateway": as_text(row.get("gateway") or row.get("gateway-status")),
        }
    return {"present": False, "active": False, "gateway": ""}


def _normalize_address(row: dict[str, Any]) -> dict[str, Any]:
    address = as_text(row.get("address"))
    network = as_text(row.get("network"))
    if not network and "/" in address:
        try:
            network = str(ipaddress.ip_interface(address).network)
        except ValueError:
            network = ""
    return {
        "address": address,
        "network": network,
        "interface": as_text(row.get("interface")),
    }


def _arp_complete(arp: list[dict[str, Any]], ip: str) -> bool:
    if not ip:
        return False
    for item in arp:
        if as_text(item.get("address")) != ip:
            continue
        return bool(item.get("complete")) and not bool(item.get("invalid"))
    return False
