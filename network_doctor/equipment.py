"""Manufacturer-independent, evidence-based equipment model (SNMP standards)."""

from __future__ import annotations
import ipaddress
import re
from dataclasses import asdict, dataclass, field
from typing import Any

# IANA Private Enterprise Numbers, not guesses from a device's marketing name.
ENTERPRISES = {
    9: "Cisco",
    11: "HPE",
    171: "D-Link",
    890: "Zyxel",
    1916: "Extreme Networks",
    2011: "Huawei",
    2636: "Juniper",
    4526: "NETGEAR",
    11863: "TP-Link",
    12356: "Fortinet",
    14823: "Aruba",
    14988: "MikroTik",
    41112: "Ubiquiti",
}
SYS = {
    "description": "1.3.6.1.2.1.1.1.0",
    "object_id": "1.3.6.1.2.1.1.2.0",
    "uptime_ticks": "1.3.6.1.2.1.1.3.0",
    "name": "1.3.6.1.2.1.1.5.0",
    "location": "1.3.6.1.2.1.1.6.0",
}
TABLES = {
    "interfaces": "1.3.6.1.2.1.2.2.1",
    "extended_interfaces": "1.3.6.1.2.1.31.1.1.1",
    "arp": "1.3.6.1.2.1.4.22.1",
    "bridge_ports": "1.3.6.1.2.1.17.1.4.1",
    "forwarding": "1.3.6.1.2.1.17.4.3.1",
    "lldp": "1.0.8802.1.1.2.1.4.1.1",
    "vlans": "1.3.6.1.2.1.17.7.1.4.3.1",
}


@dataclass
class DeviceProfile:
    id: str
    name: str
    host: str
    protocol: str = "snmpv3"
    port: int = 161
    username: str = ""
    auth_protocol: str = "sha256"
    privacy_protocol: str = "aes128"
    community_env: str = "ND_SNMP_COMMUNITY"
    auth_env: str = "ND_SNMP_AUTH"
    privacy_env: str = "ND_SNMP_PRIVACY"
    site: str = ""
    owner: str = ""
    tables: list[str] = field(
        default_factory=lambda: ["interfaces", "extended_interfaces"]
    )
    expected_ports: list[int] = field(default_factory=list)
    timeout: float = 1.5
    budget: int = 25
    enabled: bool = True

    def validate(self):
        for key in (
            "id",
            "name",
            "host",
            "protocol",
            "username",
            "auth_protocol",
            "privacy_protocol",
            "community_env",
            "auth_env",
            "privacy_env",
            "site",
            "owner",
        ):
            if not isinstance(getattr(self, key), str):
                raise ValueError(f"{key}: ожидается строка")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.id):
            raise ValueError("ID: только буквы, цифры, _ и -, не больше 64 символов")
        if not self.name.strip() or len(self.name) > 160:
            raise ValueError("Укажите название устройства (до 160 символов)")
        host = self.host.strip()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host
            ):
                raise ValueError("Нужен IP или DNS-имя, без URL и параметров") from None
        if host != self.host:
            raise ValueError("Удалите пробелы вокруг адреса")
        if self.protocol not in ("snmpv3", "snmpv2c", "icmp"):
            raise ValueError("Неизвестный протокол")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("Некорректный порт")
        if not isinstance(self.timeout, (int, float)) or not 0.2 <= self.timeout <= 5:
            raise ValueError("Тайм-аут: 0.2–5 секунд")
        if not isinstance(self.budget, int) or not 5 <= self.budget <= 60:
            raise ValueError("Лимит опроса: 5–60 секунд")
        if self.auth_protocol not in (
            "sha256",
            "sha1",
        ) or self.privacy_protocol not in ("aes128",):
            raise ValueError("Поддерживаются SHA-256/SHA-1 и AES-128")
        if self.protocol == "snmpv3" and (not self.username or len(self.username) > 32):
            raise ValueError("Укажите SNMPv3 пользователя (до 32 символов)")
        for value in (self.community_env, self.auth_env, self.privacy_env):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
                raise ValueError("Некорректное имя переменной окружения")
        if (
            not isinstance(self.tables, list)
            or len(self.tables) > len(TABLES)
            or any(v not in TABLES for v in self.tables)
        ):
            raise ValueError("Неизвестная таблица SNMP")
        if (
            not isinstance(self.expected_ports, list)
            or len(self.expected_ports) > 512
            or any(
                type(v) != int or not 1 <= v <= 2147483647 for v in self.expected_ports
            )
        ):
            raise ValueError("Ожидаемые порты задаются индексами ifIndex")
        if len(self.site) > 160 or len(self.owner) > 160:
            raise ValueError("Площадка/ответственный: до 160 символов")
        if type(self.enabled) != bool:
            raise ValueError("enabled должен быть bool")
        return self

    def public(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Профиль должен быть объектом")
        if set(data) - set(cls.__dataclass_fields__):
            raise ValueError(
                "Профиль содержит неизвестные поля; секреты сюда не записываются"
            )
        try:
            return cls(**data).validate()
        except (TypeError, AttributeError):
            raise ValueError(
                "Не заполнены обязательные поля профиля или неверны типы значений"
            ) from None


def number(value):
    try:
        return int(value) if value is not None and value != "" else None
    except (ValueError, TypeError, OverflowError):
        return None


def manufacturer(oid):
    values = str(oid or "").strip(".").split(".")
    prefix = ["1", "3", "6", "1", "4", "1"]
    if values[:6] == prefix and len(values) > 6:
        pen = number(values[6])
        return ENTERPRISES.get(pen, "Неизвестный производитель"), pen
    return "Неизвестный производитель", None


def cells(rows, root):
    """column -> suffix -> value. Table suffixes must stay numeric, no MIB downloads."""
    output = {}
    prefix = root + "."
    for oid, value in rows.items():
        if not oid.startswith(prefix):
            continue
        suffix = oid[len(prefix) :].split(".")
        if len(suffix) < 2 or not all(p.isdigit() for p in suffix):
            continue
        output.setdefault(int(suffix[0]), {})[".".join(suffix[1:])] = value
    return output


def mac(value):
    if isinstance(value, bytes):
        return ":".join(f"{v:02x}" for v in value) if len(value) == 6 else ""
    text = str(value or "").lower().replace("-", ":")
    return text if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", text) else ""


def normalize(profile, scalars, tables, capabilities, at):
    vendor, pen = manufacturer(scalars.get(SYS["object_id"]))
    info = {key: scalars.get(oid) for key, oid in SYS.items()}
    info["vendor"] = vendor
    info["enterprise"] = pen
    info["uptime_seconds"] = number(info.pop("uptime_ticks", None))
    if info["uptime_seconds"] is not None:
        info["uptime_seconds"] /= 100
    base = cells(tables.get("interfaces", {}), TABLES["interfaces"])
    extended = cells(
        tables.get("extended_interfaces", {}), TABLES["extended_interfaces"]
    )
    interfaces = []
    ids = set(base.get(1, {})) | set(base.get(2, {})) | set(extended.get(1, {}))
    for key in sorted(ids, key=lambda v: int(v) if v.isdigit() else 0)[:512]:
        if not key.isdigit():
            continue
        b = lambda col: base.get(col, {}).get(key)
        e = lambda col: extended.get(col, {}).get(key)
        speed = number(e(15))
        speed = speed * 1_000_000 if speed is not None and speed > 0 else number(b(5))
        if speed is not None and (speed <= 0 or speed == 4294967295):
            speed = None
        hc_in, hc_out = number(e(6)), number(e(10))
        interfaces.append(
            {
                "index": int(key),
                "name": str(e(1) or b(2) or key)[:256],
                "alias": str(e(18) or "")[:256],
                "mac": mac(b(6)),
                "speed_bps": speed,
                "admin_status": number(b(7)),
                "oper_status": number(b(8)),
                "rx_bytes": hc_in if hc_in is not None else number(b(10)),
                "tx_bytes": hc_out if hc_out is not None else number(b(16)),
                "rx_counter_bits": 64 if hc_in is not None else 32,
                "tx_counter_bits": 64 if hc_out is not None else 32,
                "rx_errors": number(b(14)),
                "tx_errors": number(b(20)),
                "rx_discards": number(b(13)),
                "tx_discards": number(b(19)),
                "discontinuity_ticks": number(e(19)),
            }
        )
    neighbors = []
    arp = cells(tables.get("arp", {}), TABLES["arp"])
    for key, value in arp.get(2, {}).items():
        address = arp.get(3, {}).get(key)
        if isinstance(address, bytes) and len(address) == 4:
            address = str(ipaddress.ip_address(address))
        if address:
            neighbors.append(
                {
                    "source": "ARP",
                    "address": str(address),
                    "mac": mac(value),
                    "local_port": number(arp.get(1, {}).get(key)),
                }
            )
    bridge = cells(tables.get("bridge_ports", {}), TABLES["bridge_ports"])
    vlan_cells = cells(tables.get("vlans", {}), TABLES["vlans"])
    vlans = []

    def ports(value):
        if not isinstance(value, bytes):
            return []
        return [
            index * 8 + bit + 1
            for index, byte in enumerate(value[:512])
            for bit in range(8)
            if byte & (128 >> bit)
        ]

    for key, value in vlan_cells.get(1, {}).items():
        egress = ports(vlan_cells.get(2, {}).get(key))
        vlans.append(
            {
                "id": number(key),
                "name": str(value)[:256],
                "egress_bridge_ports": egress,
                "untagged_bridge_ports": ports(vlan_cells.get(4, {}).get(key)),
                "egress_ifindexes": [
                    number(bridge.get(2, {}).get(str(port))) for port in egress
                ],
                "row_status": number(vlan_cells.get(5, {}).get(key)),
            }
        )
    forwarding = cells(tables.get("forwarding", {}), TABLES["forwarding"])
    for key, value in forwarding.get(1, {}).items():
        port = number(forwarding.get(2, {}).get(key))
        neighbors.append(
            {
                "source": "FDB",
                "mac": mac(value),
                "bridge_port": port,
                "local_port": number(bridge.get(2, {}).get(str(port))),
            }
        )
    lldp = cells(tables.get("lldp", {}), TABLES["lldp"])
    keys = set(lldp.get(9, {})) | set(lldp.get(7, {}))
    for key in sorted(keys)[:512]:
        parts = key.split(".")
        remote_port = lldp.get(7, {}).get(key)
        if isinstance(remote_port, bytes):
            remote_port = (
                mac(remote_port)
                if number(lldp.get(6, {}).get(key)) == 3
                else remote_port.decode("utf-8", errors="replace")
            )
        chassis = lldp.get(5, {}).get(key)
        if isinstance(chassis, bytes):
            chassis = (
                mac(chassis) if number(lldp.get(4, {}).get(key)) == 4 else chassis.hex()
            )
        neighbors.append(
            {
                "source": "LLDP",
                "local_lldp_port": number(parts[1]) if len(parts) >= 3 else None,
                "name": str(lldp.get(9, {}).get(key) or "")[:256],
                "remote_port": str(remote_port or "")[:256],
                "chassis_id": str(chassis or "")[:256],
                "description": str(lldp.get(10, {}).get(key) or "")[:512],
            }
        )
    warnings = []
    if profile.protocol == "snmpv2c":
        warnings.append(
            "SNMPv2c передаёт community без шифрования; используйте доверенную management-сеть"
        )
    if not interfaces:
        warnings.append(
            "Порты не получены. Это не означает отсутствие портов: проверьте MIB и права чтения"
        )
    if len(ids) > 512:
        warnings.append("Перечень портов ограничен 512 записями")
    if len(neighbors) > 4096:
        warnings.append("Перечень соседей ограничен 4096 записями")
    for name, state in capabilities.items():
        if state["status"] != "ok":
            warnings.append(f"{name}: {state['status']}")
    return {
        "schema": "network-doctor-equipment/1",
        "device_id": profile.id,
        "at": at,
        "host": profile.host,
        "name": profile.name,
        "protocol": profile.protocol,
        "site": profile.site,
        "owner": profile.owner,
        "system": info,
        "interfaces": interfaces,
        "neighbors": neighbors[:4096],
        "vlans": vlans[:4096],
        "capabilities": capabilities,
        "warnings": warnings,
        "ok": True,
    }


def derive(current, previous):
    """Rates require two valid comparable samples; reset/wrap is never guessed."""
    if not previous or not previous.get("ok"):
        return current
    if current.get("host") != previous.get("host") or current.get(
        "protocol"
    ) != previous.get("protocol"):
        return current
    if current.get("system", {}).get("object_id") != previous.get("system", {}).get(
        "object_id"
    ):
        return current
    interval = current["at"] - previous["at"]
    if interval < 1 or interval > 3600:
        return current
    uptime = current.get("system", {}).get("uptime_seconds")
    old_uptime = previous.get("system", {}).get("uptime_seconds")
    if uptime is None or old_uptime is None:
        return current
    if uptime is not None and old_uptime is not None and uptime < old_uptime:
        current["warnings"].append(
            "Перезапуск устройства: скорости до следующего снимка неизвестны"
        )
        return current
    old = {row["index"]: row for row in previous.get("interfaces", [])}
    for row in current.get("interfaces", []):
        baseline = old.get(row["index"])
        if not baseline or row["name"] != baseline["name"]:
            continue
        if row.get("mac") != baseline.get("mac"):
            continue
        if row.get("discontinuity_ticks") != baseline.get("discontinuity_ticks"):
            continue
        for direction in ("rx", "tx"):
            new, prior = row.get(direction + "_bytes"), baseline.get(
                direction + "_bytes"
            )
            if new is None or prior is None or new < prior:
                continue
            bits = row.get(direction + "_counter_bits", 32)
            if bits != baseline.get(direction + "_counter_bits", 32):
                continue
            speed = row.get("speed_bps")
            if bits == 32 and not speed:
                continue
            if bits == 32 and speed and speed * interval / 8 >= 2**32:
                continue
            rate = (new - prior) * 8 / interval
            if speed and rate > speed * 1.1:
                continue
            row[direction + "_bps"] = round(rate, 2)
        for metric in ("rx_errors", "tx_errors", "rx_discards", "tx_discards"):
            new, prior = row.get(metric), baseline.get(metric)
            if new is not None and prior is not None and new >= prior:
                row[metric + "_delta"] = new - prior
        if row.get("speed_bps") and "rx_bps" in row and "tx_bps" in row:
            row["utilization_pct"] = round(
                max(row["rx_bps"], row["tx_bps"]) / row["speed_bps"] * 100, 2
            )
    return current


def findings(snapshot, expected_ports):
    output = []
    for port in snapshot.get("interfaces", []):
        prefix = "port:" + str(port["index"])
        if (
            port["index"] in expected_ports
            and port.get("admin_status") == 1
            and port.get("oper_status") == 2
        ):
            output.append(
                (
                    prefix + ":down",
                    "critical",
                    f"Ожидаемый порт {port['name']}: link down",
                )
            )
        if (port.get("rx_errors_delta") or 0) + (port.get("tx_errors_delta") or 0) > 0:
            output.append(
                (prefix + ":errors", "warning", f"Растут ошибки порта {port['name']}")
            )
        if (port.get("utilization_pct") or 0) >= 85:
            output.append(
                (prefix + ":load", "warning", f"Высокая загрузка порта {port['name']}")
            )
    return output
