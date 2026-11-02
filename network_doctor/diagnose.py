from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .utils import as_number, as_text

KEENETIC_LAN_SUBNET = "192.168.1.0/24"
KEENETIC_PORTS = (22, 80, 443, 8291)
KEENETIC_MAC_PREFIX = "50:ff:20"

# Calibration from the office incident of 15-22 Oct:
# NAT masquerade counters were 29 071 883 bytes / 299 472 packets, the Keenetic
# WAN address was a private address of the MikroTik LAN, and its management
# ports 22/80/443/8291 were closed from the MikroTik-side LAN.
VERDICT_BEHIND_KEENETIC = (
    "Keenetic обнаружен, но доступ к управлению не подтверждён. Причина отсутствия интернета не установлена."
)
VERDICT_KEENETIC_DOWN = "Доступность Keenetic не подтверждена по ARP. Проверьте кабель, питание и ограничения сети."


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    status: str
    summary: str
    details: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": list(self.details),
        }


@dataclass(frozen=True)
class Diagnosis:
    verdict: str
    severity: str
    checks: tuple[DiagnosticCheck, ...]
    actions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "severity": self.severity,
            "checks": [check.as_dict() for check in self.checks],
            "actions": list(self.actions),
        }

    def as_text(self) -> str:
        lines = [f"Вердикт: {self.verdict}"]
        for check in self.checks:
            mark = {
                "ok": "OK",
                "warn": "!",
                "fail": "FAIL",
                "unknown": "?",
            }.get(check.status, "?")
            lines.append(f"[{mark}] {check.name}: {check.summary}")
            for detail in check.details:
                lines.append(f"    {detail}")
        if self.actions:
            lines.append("Что сделать:")
            lines.extend(f"  • {action}" for action in self.actions)
        return "\n".join(lines)


def diagnose_no_internet(snapshot: Mapping[str, Any]) -> Diagnosis:
    """Explain why office Wi-Fi clients cannot reach the internet.

    The function works on both the canonical v2 snapshot and the older legacy
    snapshot keys. Missing evidence is reported as ``unknown`` rather than
    guessed.
    """
    route = _default_route(snapshot)
    nat = _nat_state(snapshot)
    keenetic_ip = _keenetic_ip(snapshot)
    arp = _arp_entry(snapshot, keenetic_ip) if keenetic_ip else None
    ports = _keenetic_ports(snapshot)
    closed_ports = sorted(port for port, opened in ports.items() if not opened)

    checks: list[DiagnosticCheck] = []
    checks.append(_route_check(route))
    checks.append(_nat_check(nat))
    checks.append(_arp_check(keenetic_ip, arp))
    checks.append(_ports_check(ports, closed_ports))

    if not route.get('known',True):
        return Diagnosis('Недостаточно данных для определения причины. Проверьте права чтения и сбор маршрутов.',
                         'warning',tuple(checks),('Повторить сбор доступных таблиц без активных тестов.',))

    if not route["present"] or not route["active"]:
        return Diagnosis(
            verdict=(
                "Нет активного маршрута по умолчанию. Проверьте PPPoE/WAN "
                "на MikroTik."
            ),
            severity="critical",
            checks=tuple(checks),
            actions=(
                "Проверить статус pppoe-out1 и логи MikroTik.",
                "Проверить кабель провайдера и параметры PPPoE.",
            ),
        )

    if not keenetic_ip:
        return Diagnosis(
            verdict="Не удалось определить Keenetic. Проверьте ARP и топологию.",
            severity="warning",
            checks=tuple(checks),
            actions=("Собрать снимок ARP на MikroTik.",),
        )

    if arp is None or not arp["complete"]:
        return Diagnosis(
            verdict=VERDICT_KEENETIC_DOWN,
            severity="warning",
            checks=tuple(checks),
            actions=(
                "Проверить кабель между MikroTik и Keenetic.",
                "Проверить питание Keenetic.",
                f"Проверить, что WAN Keenetic получил адрес {keenetic_ip}.",
            ),
        )

    # Exactly the incident pattern: ARP to the Keenetic exists, but every
    # management port is closed and the cause is behind the Keenetic.
    if ports and closed_ports == sorted(ports):
        actions = [
            "Проверить WAN-статус Keenetic в его веб-интерфейсе "
            f"с LAN {KEENETIC_LAN_SUBNET}.",
            "Проверить NAT и DNS на Keenetic.",
            "Проверить, что клиенты получают шлюз 192.168.1.1.",
        ]
        if nat["growing"] is False:
            actions.insert(
                0,
                "NAT MikroTik не растёт: проверить, идёт ли трафик от Keenetic.",
            )
        return Diagnosis(
            verdict=VERDICT_BEHIND_KEENETIC,
            severity="warning",
            checks=tuple(checks),
            actions=tuple(actions),
        )

    if nat["growing"] is False:
        return Diagnosis(
            verdict="Счётчик NAT не вырос. Без контрольного трафика это не доказывает неисправность NAT.",
            severity="warning",
            checks=tuple(checks),
            actions=(
                "Проверить /ip firewall nat и счётчики masquerade.",
                "Проверить, что клиенты используют Keenetic как шлюз.",
            ),
        )

    return Diagnosis(
        verdict=(
            "Получены данные маршрута, ARP и счётчиков. Работоспособность интернета "
            "этим не доказана: проверьте назначенные DNS и соединение с клиента."
        ),
        severity="warning",
        checks=tuple(checks),
        actions=(
            "Проверить DNS, назначенные клиенту через DHCP.",
            "Проверить, что клиенты получают IP и шлюз от Keenetic.",
            "Проверить загрузку используемого радиоканала.",
        ),
    )


def apply_nat_delta(
    current: dict[str, Any],
    previous: Mapping[str, Any] | None,
) -> None:
    """Fill NAT growth by comparing the current and previous snapshots."""
    if previous is None:
        return
    current_nat = _mapping(_mapping(current.get("diagnostics")).get("nat"))
    previous_nat = _mapping(_mapping(previous.get("diagnostics")).get("nat"))
    current_packets = as_number(current_nat.get("packets"))
    previous_packets = as_number(previous_nat.get("packets"))
    if current_packets is None or previous_packets is None:
        return
    delta = int(current_packets - previous_packets)
    current_nat["delta_packets"] = delta
    current_nat["growing"] = delta > 0
    current.setdefault("diagnostics", {})["nat"] = current_nat


def _route_check(route: Mapping[str, Any]) -> DiagnosticCheck:
    if not route.get('known',True):
        return DiagnosticCheck(name='Маршрут по умолчанию',status='unknown',summary='данные маршрута не получены')
    if not route["present"]:
        return DiagnosticCheck(
            name="Маршрут по умолчанию",
            status="fail",
            summary="маршрут 0.0.0.0/0 отсутствует",
        )
    if not route["active"]:
        return DiagnosticCheck(
            name="Маршрут по умолчанию",
            status="fail",
            summary="маршрут есть, но не активен",
            details=(f"шлюз: {route['gateway'] or '—'}",),
        )
    return DiagnosticCheck(
        name="Маршрут по умолчанию",
        status="ok",
        summary="активен",
        details=(f"шлюз: {route['gateway'] or '—'}",),
    )


def _nat_check(nat: Mapping[str, Any]) -> DiagnosticCheck:
    details = []
    if nat["bytes"] is not None:
        details.append(f"байт: {int(nat['bytes']):,}".replace(",", " "))
    if nat["packets"] is not None:
        details.append(f"пакетов: {int(nat['packets']):,}".replace(",", " "))
    if nat["delta_packets"] is not None:
        details.append(f"прирост пакетов: {int(nat['delta_packets'])}")
    if nat["growing"] is True:
        return DiagnosticCheck(
            name="Счётчики NAT",
            status="ok",
            summary="растут",
            details=tuple(details),
        )
    if nat["growing"] is False:
        return DiagnosticCheck(
            name="Счётчики NAT",
            status="warn",
            summary="не растут",
            details=tuple(details),
        )
    return DiagnosticCheck(
        name="Счётчики NAT",
        status="unknown",
        summary="нет двух замеров для оценки роста",
        details=tuple(details),
    )


def _arp_check(ip: str, arp: Mapping[str, Any] | None) -> DiagnosticCheck:
    if arp is None:
        return DiagnosticCheck(
            name=f"ARP до Keenetic ({ip})",
            status="fail",
            summary="записи нет",
        )
    if not arp["complete"]:
        return DiagnosticCheck(
            name=f"ARP до Keenetic ({ip})",
            status="fail",
            summary="запись неполная",
            details=(f"MAC: {arp['mac'] or '—'}",),
        )
    return DiagnosticCheck(
        name=f"ARP до Keenetic ({ip})",
        status="ok",
        summary="запись ARP есть; она может быть устаревшей",
        details=(f"MAC: {arp['mac'] or '—'}",),
    )


def _ports_check(
    ports: Mapping[int, bool], closed_ports: list[int]
) -> DiagnosticCheck:
    if not ports:
        return DiagnosticCheck(
            name="Порты управления Keenetic",
            status="unknown",
            summary="не проверялись",
        )
    details = tuple(
        f"{port}: {'открыт' if opened else 'закрыт'}"
        for port, opened in sorted(ports.items())
    )
    if len(closed_ports) == len(ports):
        return DiagnosticCheck(
            name="Порты управления Keenetic",
            status="fail",
            summary="все проверенные порты закрыты",
            details=details,
        )
    if closed_ports:
        return DiagnosticCheck(
            name="Порты управления Keenetic",
            status="warn",
            summary=f"закрыты: {', '.join(map(str, closed_ports))}",
            details=details,
        )
    return DiagnosticCheck(
        name="Порты управления Keenetic",
        status="ok",
        summary="порты открыты",
        details=details,
    )


def _default_route(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics = _mapping(snapshot.get("diagnostics"))
    route = _mapping(diagnostics.get("default_route"))
    if not route:
        topology = _mapping(snapshot.get("topology"))
        mikrotik = _mapping(topology.get("mikrotik"))
        route = _mapping(mikrotik.get("default_route"))
    if not route and isinstance(snapshot.get("routes"), list):
        for item in snapshot["routes"]:
            if not isinstance(item, Mapping):
                continue
            if as_text(item.get("dst-address")) in {"0.0.0.0/0", "0.0.0.0"}:
                route = item
                break
    if not route:
        return {"present": False, "active": False, "gateway": "", "known":isinstance(snapshot.get('routes'),list)}
    present = route.get("present")
    active = route.get("active")
    return {
        "known": route.get('known',True),
        "present": True if present is None else _truthy(present),
        "active": _truthy(active) if active is not None else True,
        "gateway": as_text(route.get("gateway") or route.get("gateway-interface")),
    }


def _nat_state(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics = _mapping(snapshot.get("diagnostics"))
    nat = _mapping(diagnostics.get("nat"))
    if not nat:
        nat = _mapping(snapshot.get("nat"))
    growth = nat.get("growing")
    delta = nat.get("delta_packets", nat.get("packets_delta"))
    delta_number = as_number(delta)
    growing: bool | None = None
    if isinstance(growth, bool):
        growing = growth
    elif delta_number is not None:
        growing = delta_number > 0
    return {
        "bytes": as_number(nat.get("bytes")),
        "packets": as_number(nat.get("packets")),
        "delta_packets": delta_number,
        "growing": growing,
    }


def _keenetic_ip(snapshot: Mapping[str, Any]) -> str:
    topology = _mapping(snapshot.get("topology"))
    keenetic = _mapping(topology.get("keenetic"))
    ip = as_text(keenetic.get("ip") or keenetic.get("wan_ip"))
    if ip:
        return ip
    diagnostics = _mapping(snapshot.get("diagnostics"))
    known = _mapping(diagnostics.get("keenetic"))
    ip = as_text(known.get("ip"))
    if ip:
        return ip
    for entry in _arp_entries(snapshot):
        mac = as_text(entry.get("mac")).lower()
        if mac.startswith("50:ff:20"):
            return as_text(entry.get("ip") or entry.get("address"))
    return ""


def _arp_entry(
    snapshot: Mapping[str, Any], ip: str
) -> dict[str, Any] | None:
    for entry in _arp_entries(snapshot):
        entry_ip = as_text(entry.get("ip") or entry.get("address"))
        if entry_ip != ip:
            continue
        complete = entry.get("complete")
        invalid = _truthy(entry.get("invalid"))
        return {
            "mac": as_text(entry.get("mac-address") or entry.get("mac")),
            "complete": True if complete is None else _truthy(complete) and not invalid,
        }
    diagnostics = _mapping(snapshot.get("diagnostics"))
    known = _mapping(diagnostics.get("keenetic"))
    if _truthy(known.get("arp")):
        return {"mac": as_text(known.get("mac")), "complete": True}
    return None


def _arp_entries(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    diagnostics = _mapping(snapshot.get("diagnostics"))
    entries: Any = diagnostics.get("arp") or snapshot.get("arp")
    if isinstance(entries, Mapping):
        result: list[Mapping[str, Any]] = []
        for ip, value in entries.items():
            if isinstance(value, Mapping):
                item = dict(value)
                item.setdefault("ip", ip)
                result.append(item)
        return result
    if isinstance(entries, Iterable) and not isinstance(entries, (str, bytes)):
        return [item for item in entries if isinstance(item, Mapping)]
    return []


def _keenetic_ports(snapshot: Mapping[str, Any]) -> dict[int, bool]:
    diagnostics = _mapping(snapshot.get("diagnostics"))
    keenetic = _mapping(diagnostics.get("keenetic"))
    raw = keenetic.get("ports") or keenetic.get("management_ports")
    if raw is None:
        topology = _mapping(snapshot.get("topology"))
        raw = _mapping(topology.get("keenetic")).get("management_ports")
    result: dict[int, bool] = {}
    if isinstance(raw, Mapping):
        for port, value in raw.items():
            try:
                number = int(port)
            except (TypeError, ValueError):
                continue
            result[number] = _truthy(value)
    elif isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
        for item in raw:
            number = int(item) if str(item).isdigit() else None
            if number is not None:
                result[number] = True
    return result


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return as_text(value).strip().lower() in {"1", "true", "yes", "on", "open"}
