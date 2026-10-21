from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .utils import as_number, as_text


@dataclass(frozen=True)
class MetricRow:
    name: str
    office: str
    lab: str
    difference: str


def snapshot_stats(snapshot: dict[str, Any]) -> dict[str, Any]:
    clients = snapshot.get("clients", []) or []
    if clients:
        registration = [
            item
            for item in clients
            if isinstance(item, dict) and not item.get("virtual")
        ]
    else:
        registration = snapshot.get("registration_table", []) or []
    wireless = snapshot.get("wireless_interfaces", []) or []
    if not wireless:
        wireless = snapshot.get("wireless_scan", []) or []
    logs = snapshot.get("disconnect_events", []) or []
    spectral = snapshot.get("spectral_scan", {}) or {}

    signals = [
        value
        for item in registration
        if (value := as_number(item.get("signal_dbm"))) is not None
    ]
    ccq_values = [
        value
        for item in registration
        if (value := as_number(item.get("tx_ccq"))) is not None
    ]
    noise_values = [
        value
        for item in wireless
        if (value := as_number(item.get("noise_floor"))) is not None
    ]
    spectral_noise = as_number(spectral.get("noise_floor_avg"))
    noise = spectral_noise if spectral_noise is not None else _average(noise_values)
    channels = sorted(
        {
            as_text(item.get("channel"))
            for item in wireless
            if as_text(item.get("channel"))
        }
    )

    snr_values = [
        signal - noise
        for signal in signals
        if noise is not None
    ]
    return {
        "clients": len(registration),
        "signal_avg": _average(signals),
        "signal_min": min(signals) if signals else None,
        "snr_avg": _average(snr_values),
        "ccq_avg": _average(ccq_values),
        "noise": noise,
        "disconnects": len(logs),
        "interfaces": len(wireless),
        "channels": ", ".join(channels) if channels else None,
    }


def compare_snapshots(
    office: dict[str, Any], lab: dict[str, Any]
) -> list[MetricRow]:
    left = snapshot_stats(office)
    right = snapshot_stats(lab)
    return [
        _numeric_row("Клиенты", left["clients"], right["clients"], ""),
        _numeric_row(
            "Средний сигнал", left["signal_avg"], right["signal_avg"], " dBm", 1
        ),
        _numeric_row(
            "Минимальный сигнал", left["signal_min"], right["signal_min"], " dBm", 1
        ),
        _numeric_row("Средний SNR", left["snr_avg"], right["snr_avg"], " dB", 1),
        _numeric_row("Средний TX CCQ", left["ccq_avg"], right["ccq_avg"], "%", 1),
        _numeric_row("Шум (Noise floor)", left["noise"], right["noise"], " dBm", 1),
        _numeric_row(
            "Отключения в логах", left["disconnects"], right["disconnects"], ""
        ),
        _numeric_row(
            "Wireless интерфейсы", left["interfaces"], right["interfaces"], ""
        ),
        _text_row("Каналы", left["channels"], right["channels"]),
    ]


def baseline_deviations(
    current: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    """Describe changes of the current snapshot against a baseline."""
    left = snapshot_stats(current)
    right = snapshot_stats(baseline)
    lines: list[str] = []

    noise_delta = _delta(left["noise"], right["noise"])
    if noise_delta is not None and abs(noise_delta) >= 1:
        word = "вырос" if noise_delta > 0 else "снизился"
        note = "хуже" if noise_delta > 0 else "лучше"
        lines.append(f"Шум {word} на {abs(noise_delta):.1f} dB ({note}).")

    snr_delta = _delta(left["snr_avg"], right["snr_avg"])
    if snr_delta is not None and abs(snr_delta) >= 1:
        word = "упал" if snr_delta < 0 else "вырос"
        lines.append(f"SNR {word} на {abs(snr_delta):.1f} dB.")

    ccq_delta = _delta(left["ccq_avg"], right["ccq_avg"])
    if ccq_delta is not None and abs(ccq_delta) >= 1:
        word = "упал" if ccq_delta < 0 else "вырос"
        lines.append(f"CCQ {word} на {abs(ccq_delta):.1f}%.")

    clients_delta = left["clients"] - right["clients"]
    if clients_delta:
        word = "больше" if clients_delta > 0 else "меньше"
        lines.append(f"Клиентов стало {word} на {abs(clients_delta)}.")

    disconnect_delta = left["disconnects"] - right["disconnects"]
    if disconnect_delta:
        word = "больше" if disconnect_delta > 0 else "меньше"
        lines.append(f"Отключений стало {word} на {abs(disconnect_delta)}.")

    return lines or ["Существенных отклонений от эталона нет."]


def snapshot_summary(snapshot: dict[str, Any]) -> str:
    meta = snapshot.get("meta", {}) or {}
    stats = snapshot_stats(snapshot)
    health = snapshot.get("health", {}) or {}
    warnings = snapshot.get("warnings", []) or []
    lines = [
        f"Тип: {as_text(meta.get('snapshot_label')) or '—'}",
        f"Время: {as_text(meta.get('collected_at')) or '—'}",
        f"Роутер: {as_text(meta.get('router_identity')) or '—'}",
        f"Версия: {as_text(meta.get('router_version')) or '—'}",
        f"Хост: {as_text(meta.get('host')) or '—'}",
        "",
        f"Клиенты: {stats['clients']}",
        f"Средний сигнал: {_plain(stats['signal_avg'], ' dBm', 1)}",
        f"Минимальный сигнал: {_plain(stats['signal_min'], ' dBm', 1)}",
        f"Средний TX CCQ: {_plain(stats['ccq_avg'], '%', 1)}",
        f"Шум (Noise floor): {_plain(stats['noise'], ' dBm', 1)}",
        f"Отключения в логах: {stats['disconnects']}",
        f"Wireless интерфейсы: {stats['interfaces']}",
        f"Каналы: {stats['channels'] or '—'}",
    ]
    loss = as_number(health.get("loss_pct"))
    jitter = as_number(health.get("jitter_ms"))
    latency = as_number(health.get("latency_ms"))
    if any(value is not None for value in (loss, jitter, latency)):
        lines.extend(
            [
                "",
                f"Потери до {as_text(health.get('target')) or '8.8.8.8'}: "
                f"{_plain(loss, '%', 1)}",
                f"Джиттер: {_plain(jitter, ' мс', 1)}",
                f"Задержка: {_plain(latency, ' мс', 0)}",
            ]
        )
    if warnings:
        lines.extend(["", "Предупреждения:"])
        lines.extend(f"• {warning}" for warning in warnings)
    return "\n".join(lines)


def export_text(
    office: dict[str, Any],
    lab: dict[str, Any],
    rows: list[MetricRow],
) -> str:
    office_meta = office.get("meta", {}) or {}
    lab_meta = lab.get("meta", {}) or {}
    lines = [
        "Network Doctor — сравнение снимков",
        "=" * 76,
        f"Офис:       {as_text(office_meta.get('collected_at')) or '—'} "
        f"({as_text(office_meta.get('router_identity')) or '—'})",
        f"Лаборатория:{as_text(lab_meta.get('collected_at')) or '—'} "
        f"({as_text(lab_meta.get('router_identity')) or '—'})",
        "",
        f"{'Параметр':<28} | {'Офис':<18} | {'Лаборатория':<18} | Разница",
        "-" * 76,
    ]
    for row in rows:
        lines.append(
            f"{row.name:<28} | {row.office:<18} | {row.lab:<18} | {row.difference}"
        )

    all_warnings: list[str] = []
    for label, snapshot in (("Офис", office), ("Лаборатория", lab)):
        for warning in snapshot.get("warnings", []) or []:
            all_warnings.append(f"{label}: {warning}")
    if all_warnings:
        lines.extend(["", "Предупреждения:"])
        lines.extend(f"- {warning}" for warning in all_warnings)
    return "\n".join(lines) + "\n"


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _delta(left: Any, right: Any) -> float | None:
    left_number = as_number(left)
    right_number = as_number(right)
    if left_number is None or right_number is None:
        return None
    return left_number - right_number


def _plain(value: Any, unit: str, digits: int) -> str:
    number = as_number(value)
    if number is None:
        return "—"
    if digits:
        return f"{number:.{digits}f}{unit}"
    return f"{int(number)}{unit}"


def _numeric_row(
    name: str,
    left: Any,
    right: Any,
    unit: str,
    digits: int = 0,
) -> MetricRow:
    left_number = as_number(left)
    right_number = as_number(right)
    if left_number is None or right_number is None:
        return MetricRow(name, _plain(left, unit, digits), _plain(right, unit, digits), "—")

    difference = right_number - left_number
    if abs(difference) < (10 ** (-digits) / 2 if digits else 0.5):
        difference_text = "0"
    else:
        sign = "+" if difference > 0 else ""
        if digits:
            difference_text = f"{sign}{difference:.{digits}f}{unit}"
        else:
            difference_text = f"{sign}{int(difference)}{unit}"
    return MetricRow(
        name,
        _plain(left, unit, digits),
        _plain(right, unit, digits),
        difference_text,
    )


def _text_row(name: str, left: Any, right: Any) -> MetricRow:
    left_text = as_text(left) or "—"
    right_text = as_text(right) or "—"
    if left_text == "—" or right_text == "—":
        difference = "—"
    elif left_text == right_text:
        difference = "совпадает"
    else:
        difference = "различаются"
    return MetricRow(name, left_text, right_text, difference)
