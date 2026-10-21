from __future__ import annotations

import json
from pathlib import Path

from network_doctor.compare import compare_snapshots
from network_doctor.report import build_report_context, write_html, write_pdf

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "office_snapshot.json"


def make_snapshot() -> dict:
    return {
        "meta": {
            "snapshot_type": "office",
            "snapshot_label": "Офис",
            "collected_at": "2026-10-22T12:00:00+03:00",
            "host": "192.0.2.10",
            "router_identity": "rfc-test",
            "router_version": "7.19.4",
        },
        "registration_table": [
            {
                "mac": "AA:BB:CC:DD:EE:01",
                "interface": "wlan1",
                "signal_dbm": -84,
                "tx_ccq": 62,
                "retry_pct": 28,
            }
        ],
        "wireless_interfaces": [{"channel": "2412", "noise_floor": -88}],
        "logs": [],
        "disconnect_events": [],
        "spectral_scan": {"noise_floor_avg": -88},
        "warnings": ["Тестовое предупреждение"],
    }


def test_html_and_pdf_reports(tmp_path) -> None:
    snapshot = make_snapshot()
    rows = compare_snapshots(snapshot, snapshot)
    context = build_report_context(snapshot, rows)
    series = [("2026-10-22T11:00:00+03:00", -94), ("2026-10-22T12:00:00+03:00", -88)]

    html_path = tmp_path / "report.html"
    write_html(html_path, context, series)
    html = html_path.read_text(encoding="utf-8")
    assert "Network Doctor" in html
    assert "AA:BB:CC:DD:EE:01" in html
    assert "data:image/png;base64," in html

    pdf_path = tmp_path / "report.pdf"
    write_pdf(pdf_path, context, series)
    data = pdf_path.read_bytes()
    assert data.startswith(b"%PDF")
    assert len(data) > 1000


def test_reports_include_diagnosis_block(tmp_path) -> None:
    snapshot = json.loads(SAMPLE.read_text(encoding="utf-8"))
    rows = compare_snapshots(snapshot, snapshot)
    context = build_report_context(snapshot, rows)
    series = [("2026-10-22T01:00:00+03:00", -82.0)]

    assert context["diagnosis"]["verdict"].startswith("Проблема за Keenetic")

    html_path = tmp_path / "incident.html"
    write_html(html_path, context, series)
    html = html_path.read_text(encoding="utf-8")
    assert "Диагностика" in html
    assert "Проблема за Keenetic" in html

    pdf_path = tmp_path / "incident.pdf"
    write_pdf(pdf_path, context, series)
    assert pdf_path.read_bytes().startswith(b"%PDF")
