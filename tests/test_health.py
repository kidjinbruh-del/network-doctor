from __future__ import annotations

from network_doctor.health import (
    calculate_client_health,
    calculate_health,
    calculate_snr,
    health_rows,
)


def test_healthy_client_scores_100() -> None:
    result = calculate_health(-60, -95, 92, 5)
    assert result.score == 100
    assert result.state == "green"
    assert result.snr_db == 35.0


def test_all_penalties_apply() -> None:
    result = calculate_health(-80, -90, 70, 25)
    assert result.snr_db == 10.0
    assert result.score == 30
    assert result.state == "red"
    assert len(result.reasons) == 3


def test_ccq_only_penalty_is_yellow() -> None:
    result = calculate_health(-70, -95, 70)
    assert result.score == 80
    assert result.state == "yellow"


def test_missing_noise_does_not_crash_scoring() -> None:
    result = calculate_health(-65, None, 90)
    assert result.snr_db is None
    assert result.score == 100
    assert any("SNR неизвестен" in reason for reason in result.reasons)


def test_no_measurements_is_unknown_not_100_percent_healthy():
    result=calculate_health(None,None,None)
    assert result.score is None and result.state=='unknown'
    assert result.color=='#8fa3bf'


def test_snr_helper() -> None:
    assert calculate_snr(-60, -95) == 35.0
    assert calculate_snr(None, -95) is None


def test_health_rows_sorted_worst_first() -> None:
    snapshot = {
        "spectral_scan": {"noise_floor_avg": -95},
        "registration_table": [
            {
                "mac": "AA:00:00:00:00:01",
                "interface": "wlan1",
                "signal_dbm": -55,
                "tx_ccq": 95,
            },
            {
                "mac": "AA:00:00:00:00:02",
                "interface": "wlan1",
                "signal_dbm": -85,
                "tx_ccq": 60,
            },
        ],
    }
    rows = health_rows(snapshot)

    assert rows[0]["mac"] == "AA:00:00:00:00:02"
    assert rows[0]["health"].score < rows[1]["health"].score


def test_network_mode_uses_incident_peaks() -> None:
    result = calculate_health(
        -85,
        -95,
        70,
        25,
        loss_pct=7.6,
        jitter_ms=80.2,
        latency_ms=225.0,
    )

    assert result.score == 0
    assert result.state == "red"
    assert result.loss_pct == 7.6
    assert result.jitter_ms == 80.2
    assert result.latency_ms == 225.0
    assert len(result.reasons) == 4


def test_network_mode_average_incident_is_not_critical() -> None:
    result = calculate_health(
        -60,
        -95,
        92,
        5,
        loss_pct=0.29,
        jitter_ms=18.3,
        latency_ms=58.7,
    )

    assert result.score == 86
    assert result.state == "green"


def test_client_health_uses_snapshot_network_metrics() -> None:
    result = calculate_client_health(
        {"signal_dbm": -80, "tx_ccq": 90},
        {"loss_pct": 7.6, "jitter_ms": 80.2, "latency_ms": 225.0},
        noise_dbm=-95,
    )

    assert result.score == 0
    assert result.state == "red"
    assert any("Потери пакетов" in reason for reason in result.reasons)
