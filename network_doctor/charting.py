from __future__ import annotations

import base64
import io
from datetime import datetime
from typing import Sequence

from matplotlib.figure import Figure

from .db import TimelinePoint

_BG = "#0f172a"
_GRID = "#334155"
_TEXT = "#cbd5e1"
_BLUE = "#60a5fa"
_GREEN = "#34d399"
_AMBER = "#fbbf24"


def timeline_figure(
    points: Sequence[TimelinePoint],
    mac: str,
    *,
    width: float = 8.0,
    height: float = 3.4,
) -> Figure:
    """Build a signal/noise/CCQ chart for the UI and reports."""
    figure = Figure(figsize=(width, height), dpi=100, facecolor=_BG)
    axis = figure.add_subplot(111)
    axis.set_facecolor(_BG)
    labels = [_short_time(point.collected_at) for point in points]
    x_values = list(range(len(points)))

    signal = [point.signal_dbm for point in points]
    noise = [point.noise for point in points]
    ccq = [point.ccq for point in points]

    if any(value is not None for value in signal):
        axis.plot(x_values, signal, color=_BLUE, marker="o", linewidth=2, label="Сигнал, dBm")
    if any(value is not None for value in noise):
        axis.plot(x_values, noise, color=_AMBER, marker=".", linewidth=1.5, label="Шум, dBm")
    axis.set_ylabel("dBm", color=_TEXT)
    axis.tick_params(colors=_TEXT, labelsize=8)
    axis.grid(color=_GRID, alpha=0.5, linewidth=0.6)
    axis.set_xticks(x_values)
    axis.set_xticklabels(labels, rotation=35, ha="right")

    handles, names = axis.get_legend_handles_labels()
    if any(value is not None for value in ccq):
        ccq_axis = axis.twinx()
        line, = ccq_axis.plot(
            x_values,
            ccq,
            color=_GREEN,
            marker="s",
            linewidth=1.5,
            alpha=0.8,
            label="CCQ, %",
        )
        ccq_axis.set_ylim(0, 100)
        ccq_axis.set_ylabel("CCQ, %", color=_GREEN)
        ccq_axis.tick_params(colors=_GREEN, labelsize=8)
        handles.append(line)
        names.append("CCQ, %")

    axis.set_title(f"История клиента {mac}", color=_TEXT, fontsize=11, loc="left")
    if handles:
        axis.legend(
            handles,
            names,
            loc="lower left",
            facecolor=_BG,
            edgecolor=_GRID,
            labelcolor=_TEXT,
            fontsize=8,
            framealpha=0.9,
        )
    figure.tight_layout()
    return figure


def noise_chart_png(
    series: Sequence[tuple[str, float]],
    *,
    width: float = 7.0,
    height: float = 2.8,
) -> bytes:
    """Render the noise-floor history as PNG bytes for the HTML report."""
    figure = Figure(figsize=(width, height), dpi=110, facecolor="white")
    axis = figure.add_subplot(111)
    if series:
        x_values = list(range(len(series)))
        y_values = [value for _, value in series]
        axis.plot(x_values, y_values, color="#2563eb", marker="o", linewidth=2)
        axis.set_xticks(x_values)
        axis.set_xticklabels(
            [_short_time(label) for label, _ in series],
            rotation=35,
            ha="right",
            fontsize=7,
        )
        axis.set_ylim(min(y_values) - 3, max(y_values) + 3)
    else:
        axis.text(0.5, 0.5, "Нет данных о шуме", ha="center", va="center")
    axis.set_ylabel("Noise floor, dBm")
    axis.set_title("Динамика шума эфира")
    axis.grid(color="#cbd5e1", alpha=0.6, linewidth=0.7)
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    return buffer.getvalue()


def png_data_uri(value: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(value).decode("ascii")


def _short_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value[-8:] or value
    return parsed.strftime("%d.%m %H:%M")
