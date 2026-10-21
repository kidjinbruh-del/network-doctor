from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .utils import as_number, as_text

SNR_WARNING_DB = 20.0
CCQ_WARNING_PERCENT = 80.0
RETRY_WARNING_PERCENT = 20.0
SIGNAL_WARNING_DBM = -70.0

# Calibration from the 15-22 Oct office incident: peak loss 7.6 %, peak
# jitter 80.2 ms and peak latency 225 ms. These are the worst-case values that
# turned the Wi-Fi channel into a red state.
LOSS_BAD_PCT = 7.6
JITTER_BAD_MS = 80.0
LATENCY_BAD_MS = 225.0
LOSS_MAX_PENALTY = 50
JITTER_MAX_PENALTY = 30
LATENCY_MAX_PENALTY = 20
SIGNAL_MAX_PENALTY = 25


@dataclass(frozen=True)
class HealthResult:
    """Integral client health score and the reasons behind it."""

    score: int
    state: str
    label: str
    signal_dbm: float | None
    noise_dbm: float | None
    snr_db: float | None
    ccq: float | None
    retry_pct: float | None
    reasons: tuple[str, ...]
    loss_pct: float | None = None
    jitter_ms: float | None = None
    latency_ms: float | None = None

    @property
    def color(self) -> str:
        return {"red": "#f87171", "yellow": "#fbbf24", "green": "#34d399"}[self.state]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "state": self.state,
            "label": self.label,
            "signal_dbm": self.signal_dbm,
            "noise_dbm": self.noise_dbm,
            "snr_db": self.snr_db,
            "ccq": self.ccq,
            "retry_pct": self.retry_pct,
            "loss_pct": self.loss_pct,
            "jitter_ms": self.jitter_ms,
            "latency_ms": self.latency_ms,
            "reasons": list(self.reasons),
        }


def calculate_snr(signal_dbm: Any, noise_dbm: Any) -> float | None:
    """Return signal-to-noise ratio in dB when both values are known."""
    signal = as_number(signal_dbm)
    noise = as_number(noise_dbm)
    if signal is None or noise is None:
        return None
    return round(signal - noise, 1)


def calculate_health(
    signal_dbm: Any,
    noise_dbm: Any,
    ccq: Any,
    retry_pct: Any = None,
    *,
    loss_pct: Any = None,
    jitter_ms: Any = None,
    latency_ms: Any = None,
) -> HealthResult:
    """Calculate a 0-100 client health score.

    Wireless mode (default):
    - SNR below 20 dB: -30 points.
    - CCQ below 80%: -20 points.
    - Retry rate of 20% or more: -20 points.

    Network mode (when loss/jitter/latency are supplied) follows the incident
    calibration and additionally rates signal below -70 dBm.
    """
    score = 100
    reasons: list[str] = []
    signal = as_number(signal_dbm)
    noise = as_number(noise_dbm)
    ccq_value = as_number(ccq)
    retry_value = as_number(retry_pct)
    loss_value = as_number(loss_pct)
    jitter_value = as_number(jitter_ms)
    latency_value = as_number(latency_ms)
    network_mode = any(
        value is not None
        for value in (loss_value, jitter_value, latency_value)
    )
    snr = calculate_snr(signal, noise)

    if not network_mode:
        if snr is None:
            reasons.append("SNR неизвестен: нет данных о шуме")
        elif snr < SNR_WARNING_DB:
            score -= 30
            reasons.append(f"Низкий SNR: {snr:.1f} dB")

        if ccq_value is None:
            reasons.append("CCQ неизвестен")
        elif ccq_value < CCQ_WARNING_PERCENT:
            score -= 20
            reasons.append(f"Низкий CCQ: {ccq_value:.1f}%")

        if retry_value is not None and retry_value >= RETRY_WARNING_PERCENT:
            score -= 20
            reasons.append(f"Высокий retry: {retry_value:.1f}%")
    else:
        if signal is not None and signal < SIGNAL_WARNING_DBM:
            penalty = min(
                SIGNAL_MAX_PENALTY,
                round((SIGNAL_WARNING_DBM - signal) / 25 * SIGNAL_MAX_PENALTY),
            )
            score -= penalty
            reasons.append(f"Слабый сигнал: {signal:.0f} dBm")

        if loss_value is not None and loss_value > 0:
            penalty = min(
                LOSS_MAX_PENALTY,
                round(loss_value / LOSS_BAD_PCT * LOSS_MAX_PENALTY),
            )
            score -= penalty
            reasons.append(f"Потери пакетов: {loss_value:.1f}%")

        if jitter_value is not None and jitter_value > 0:
            penalty = min(
                JITTER_MAX_PENALTY,
                round(jitter_value / JITTER_BAD_MS * JITTER_MAX_PENALTY),
            )
            score -= penalty
            reasons.append(f"Джиттер: {jitter_value:.1f} мс")

        if latency_value is not None and latency_value > 0:
            penalty = min(
                LATENCY_MAX_PENALTY,
                round(latency_value / LATENCY_BAD_MS * LATENCY_MAX_PENALTY),
            )
            score -= penalty
            reasons.append(f"Задержка: {latency_value:.0f} мс")

    score = max(0, min(100, score))
    if score < 50:
        state, label = "red", "Критично"
    elif score <= 80:
        state, label = "yellow", "Внимание"
    else:
        state, label = "green", "Норма"

    return HealthResult(
        score=score,
        state=state,
        label=label,
        signal_dbm=signal,
        noise_dbm=noise,
        snr_db=snr,
        ccq=ccq_value,
        retry_pct=retry_value,
        reasons=tuple(reasons),
        loss_pct=loss_value,
        jitter_ms=jitter_value,
        latency_ms=latency_value,
    )


def calculate_client_health(
    client: Mapping[str, Any],
    snapshot_health: Mapping[str, Any] | None = None,
    noise_dbm: Any = None,
) -> HealthResult:
    """Score one inventory client using its own or network-wide telemetry."""
    health = snapshot_health or {}
    return calculate_health(
        client.get("signal_dbm"),
        noise_dbm,
        client.get("tx_ccq", client.get("ccq")),
        client.get("retry_pct", client.get("tx_retries")),
        loss_pct=client.get("loss_pct", health.get("loss_pct")),
        jitter_ms=client.get("jitter_ms", health.get("jitter_ms")),
        latency_ms=client.get("latency_ms", health.get("latency_ms")),
    )


def health_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Build health rows for all clients in a snapshot, worst first."""
    noise = snapshot_noise(snapshot)
    health = snapshot.get("health", {}) or {}
    clients = snapshot.get("clients")
    if not clients:
        clients = snapshot.get("registration_table", [])
    rows: list[dict[str, Any]] = []
    for client in clients or []:
        if not isinstance(client, dict) or client.get("virtual"):
            continue
        result = calculate_client_health(client, health, noise)
        rows.append(
            {
                "mac": as_text(client.get("mac")) or "—",
                "interface": as_text(client.get("interface")) or "—",
                "signal_dbm": result.signal_dbm,
                "snr_db": result.snr_db,
                "ccq": result.ccq,
                "retry_pct": result.retry_pct,
                "health": result,
            }
        )
    rows.sort(key=lambda item: (item["health"].score, item["signal_dbm"] or -999))
    return rows


def recommendations(result: HealthResult) -> list[str]:
    """Return actionable recommendations for a health result."""
    items: list[str] = []
    if result.snr_db is not None and result.snr_db < SNR_WARNING_DB:
        items.append("Проверить канал и уровень помех; при возможности сменить канал.")
    if result.ccq is not None and result.ccq < CCQ_WARNING_PERCENT:
        items.append("Переподключить клиента и проверить retry на интерфейсе.")
    if result.retry_pct is not None and result.retry_pct >= RETRY_WARNING_PERCENT:
        items.append("Проверить качество радиолинка и загрузку эфира.")
    if result.signal_dbm is not None and result.signal_dbm < -75:
        items.append("Приблизить клиента к точке доступа или проверить антенну.")
    if result.loss_pct is not None and result.loss_pct >= 1:
        items.append("Проверить потери до 8.8.8.8 и загрузку канала 4 (2,4 ГГц).")
    if result.jitter_ms is not None and result.jitter_ms >= 30:
        items.append("Проверить джиттер на WAN Keenetic и загрузку эфира.")
    if result.latency_ms is not None and result.latency_ms >= 120:
        items.append("Проверить задержку WAN/PPPoE и приоритеты трафика.")
    if not items and result.score <= 80:
        items.append("Проверить клиента вручную: доступных метрик недостаточно.")
    return items


def snapshot_noise(snapshot: dict[str, Any]) -> float | None:
    spectral = snapshot.get("spectral_scan", {}) or {}
    noise = as_number(spectral.get("noise_floor_avg"))
    if noise is not None:
        return noise
    values = [
        as_number(item.get("noise_floor"))
        for item in snapshot.get("wireless_interfaces", []) or []
        if isinstance(item, dict)
    ]
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 1) if values else None
