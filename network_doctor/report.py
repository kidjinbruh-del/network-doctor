from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .charting import noise_chart_png, png_data_uri
from .compare import MetricRow, baseline_deviations, snapshot_stats
from .diagnose import diagnose_no_internet
from .health import health_rows, recommendations
from .utils import as_text

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_BLUE = colors.HexColor("#2563eb")
_LINE = colors.HexColor("#dbe3ee")
_MUTED = colors.HexColor("#64748b")
_RED = colors.HexColor("#dc2626")
_AMBER = colors.HexColor("#b45309")
_GREEN = colors.HexColor("#059669")


def build_report_context(
    current: dict[str, Any],
    rows: Sequence[MetricRow],
    *,
    baseline: dict[str, Any] | None = None,
    compare_label: str = "Эталон",
    title: str = "Отчёт по качеству Wi-Fi",
) -> dict[str, Any]:
    """Prepare all report fields independently from HTML/PDF rendering."""
    meta = current.get("meta", {}) or {}
    clients = health_rows(current)
    problems = [item for item in clients if item["health"].state != "green"]
    recs: list[str] = []
    for item in problems:
        for recommendation in recommendations(item["health"]):
            if recommendation not in recs:
                recs.append(recommendation)
    if not problems:
        recs.append("Критичных проблем не выявлено. Продолжайте плановый мониторинг.")
    if not recs:
        recs.append("Проверить проблемных клиентов вручную.")

    diagnosis = None
    topology = current.get("topology", {}) or {}
    keenetic = topology.get("keenetic", {}) or {}
    diagnostics = current.get("diagnostics", {}) or {}
    diagnostic_keenetic = diagnostics.get("keenetic", {}) or {}
    if keenetic.get("ip") or diagnostic_keenetic.get("ip"):
        diagnosis = diagnose_no_internet(current).as_dict()

    return {
        "title": title,
        "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "snapshot_label": as_text(meta.get("snapshot_label")) or "Офис",
        "router": " · ".join(
            value
            for value in (
                as_text(meta.get("router_identity")),
                as_text(meta.get("router_version")),
            )
            if value
        )
        or "MikroTik",
        "host": as_text(meta.get("host")) or "—",
        "collected_at": as_text(meta.get("collected_at")) or "—",
        "stats": snapshot_stats(current),
        "rows": list(rows),
        "compare_label": compare_label,
        "deviations": baseline_deviations(current, baseline) if baseline else [],
        "problems": problems,
        "recommendations": recs,
        "warnings": current.get("warnings", []) or [],
        "diagnosis": diagnosis,
    }


def write_html(
    path: str | Path,
    context: dict[str, Any],
    noise_series: Sequence[tuple[str, float]],
) -> None:
    """Render a self-contained HTML report with an embedded chart."""
    chart = noise_chart_png(noise_series)
    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = environment.get_template("report.html")
    html = template.render(
        **context,
        chart_data_uri=png_data_uri(chart),
    )
    Path(path).write_text(html, encoding="utf-8")


def write_pdf(
    path: str | Path,
    context: dict[str, Any],
    noise_series: Sequence[tuple[str, float]],
) -> None:
    """Render a PDF report using ReportLab."""
    font_name = _register_fonts()
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=2.1 * cm,
        bottomMargin=1.5 * cm,
        title=str(context.get("title", "Network Doctor")),
        author="Network Doctor",
    )
    styles = _styles(font_name)
    story: list[Any] = [
        Paragraph("Network Doctor", styles["Title"]),
        Paragraph(str(context.get("title", "")), styles["Subtitle"]),
        Spacer(1, 0.25 * cm),
        Paragraph(
            f"Снимок: <b>{escape(str(context.get('snapshot_label', '—')))}</b> · "
            f"Роутер: <b>{escape(str(context.get('router', '—')))}</b> · "
            f"Хост: <b>{escape(str(context.get('host', '—')))}</b>",
            styles["Meta"],
        ),
        Paragraph(
            f"Время снимка: {escape(str(context.get('collected_at', '—')))} · "
            f"Сформирован: {escape(str(context.get('generated_at', '—')))}",
            styles["Meta"],
        ),
        Spacer(1, 0.45 * cm),
    ]

    stats = context.get("stats", {}) or {}
    summary_data = [
        [
            _cell("Клиенты", font_name, bold=True),
            _cell(str(stats.get("clients", 0)), font_name),
            _cell("Средний сигнал", font_name, bold=True),
            _cell(_format(stats.get("signal_avg"), " dBm", 1), font_name),
        ],
        [
            _cell("Средний SNR", font_name, bold=True),
            _cell(_format(stats.get("snr_avg"), " dB", 1), font_name),
            _cell("Шум", font_name, bold=True),
            _cell(_format(stats.get("noise"), " dBm", 1), font_name),
        ],
    ]
    summary = Table(summary_data, colWidths=[3.4 * cm, 4.2 * cm, 3.8 * cm, 4.2 * cm])
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f8fc")),
                ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([summary, Spacer(1, 0.5 * cm)])

    deviations = context.get("deviations", []) or []
    if deviations:
        story.append(Paragraph("Отклонения от эталона", styles["H2"]))
        for item in deviations:
            story.append(Paragraph(f"• {escape(str(item))}", styles["Body"]))
        story.append(Spacer(1, 0.25 * cm))

    diagnosis = context.get("diagnosis") or {}
    if diagnosis:
        story.append(Paragraph("Диагностика", styles["H2"]))
        story.append(
            Paragraph(
                f"<b>Вердикт:</b> {escape(str(diagnosis.get('verdict', '—')))}",
                styles["Body"],
            )
        )
        check_rows = [
            [
                _cell("Проверка", font_name, bold=True),
                _cell("Статус", font_name, bold=True),
                _cell("Результат", font_name, bold=True),
            ]
        ]
        status_names = {
            "ok": "OK",
            "warn": "Внимание",
            "fail": "Ошибка",
            "unknown": "Неизвестно",
        }
        for check in diagnosis.get("checks", []):
            check_rows.append(
                [
                    _cell(str(check.get("name", "—")), font_name),
                    _cell(
                        status_names.get(str(check.get("status")), "—"),
                        font_name,
                    ),
                    _cell(str(check.get("summary", "—")), font_name),
                ]
            )
        check_table = Table(
            check_rows,
            colWidths=[5.0 * cm, 3.0 * cm, 9.2 * cm],
            repeatRows=1,
        )
        check_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3fb")),
                    ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(check_table)
        for action in diagnosis.get("actions", []):
            story.append(Paragraph(f"• {escape(str(action))}", styles["Body"]))
        story.append(Spacer(1, 0.35 * cm))

    story.append(Paragraph("Проблемные клиенты", styles["H2"]))
    problems = context.get("problems", []) or []
    if problems:
        data = [
            [
                _cell("Клиент", font_name, bold=True),
                _cell("Сигнал", font_name, bold=True),
                _cell("SNR", font_name, bold=True),
                _cell("CCQ", font_name, bold=True),
                _cell("Health", font_name, bold=True),
                _cell("Причины", font_name, bold=True),
            ]
        ]
        for item in problems:
            result = item["health"]
            data.append(
                [
                    _cell(str(item.get("mac", "—")), font_name),
                    _cell(_format(item.get("signal_dbm"), " dBm", 1), font_name),
                    _cell(_format(item.get("snr_db"), " dB", 1), font_name),
                    _cell(_format(item.get("ccq"), "%", 1), font_name),
                    _cell(f"{result.score} · {result.label}", font_name),
                    _cell("; ".join(result.reasons) or "—", font_name),
                ]
            )
        table = Table(
            data,
            colWidths=[3.0 * cm, 2.3 * cm, 2.1 * cm, 2.0 * cm, 2.5 * cm, 6.4 * cm],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3fb")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), _MUTED),
                    ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph("Критичных проблем не выявлено.", styles["Ok"]))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Сравнение показателей", styles["H2"]))
    comparison = [
        [
            _cell("Параметр", font_name, bold=True),
            _cell("Текущий", font_name, bold=True),
            _cell(str(context.get("compare_label", "Эталон")), font_name, bold=True),
            _cell("Разница", font_name, bold=True),
        ]
    ]
    for row in context.get("rows", []) or []:
        comparison.append(
            [
                _cell(row.name, font_name),
                _cell(row.office, font_name),
                _cell(row.lab, font_name),
                _cell(row.difference, font_name),
            ]
        )
    table = Table(comparison, colWidths=[5.6 * cm, 4.1 * cm, 4.1 * cm, 3.4 * cm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3fb")),
                ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend([table, Spacer(1, 0.4 * cm)])

    story.append(Paragraph("Динамика шума эфира", styles["H2"]))
    story.append(Image(io.BytesIO(noise_chart_png(noise_series)), width=17 * cm, height=6.8 * cm))
    story.append(PageBreak())

    story.append(Paragraph("Рекомендации", styles["H2"]))
    for item in context.get("recommendations", []) or []:
        story.append(Paragraph(f"• {escape(str(item))}", styles["Body"]))
    if context.get("warnings"):
        story.append(Paragraph("Предупреждения сбора", styles["H2"]))
        for item in context["warnings"]:
            story.append(Paragraph(f"• {escape(str(item))}", styles["Warn"]))

    document.build(
        story,
        onFirstPage=_draw_page,
        onLaterPages=_draw_page,
    )


def _styles(font_name: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "Title": ParagraphStyle(
            "NDTitle",
            parent=base["Title"],
            fontName=font_name,
            fontSize=22,
            leading=26,
            textColor=colors.HexColor("#0f172a"),
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "Subtitle": ParagraphStyle(
            "NDSubtitle",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=11,
            textColor=_MUTED,
            spaceAfter=10,
        ),
        "Meta": ParagraphStyle(
            "NDMeta",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=8.5,
            leading=12,
            textColor=_MUTED,
            spaceAfter=2,
        ),
        "H2": ParagraphStyle(
            "NDH2",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=13,
            leading=16,
            textColor=colors.HexColor("#0f172a"),
            spaceBefore=8,
            spaceAfter=6,
        ),
        "Body": ParagraphStyle(
            "NDBody",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#334155"),
            spaceAfter=3,
        ),
        "Ok": ParagraphStyle(
            "NDOk",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=9,
            textColor=_GREEN,
        ),
        "Warn": ParagraphStyle(
            "NDWarn",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=9,
            textColor=_AMBER,
        ),
    }


def _cell(value: str, font_name: str, *, bold: bool = False) -> Paragraph:
    style = ParagraphStyle(
        "Cell",
        fontName=font_name,
        fontSize=7.6,
        leading=10,
        textColor=colors.HexColor("#0f172a") if not bold else _MUTED,
    )
    text = escape(value)
    if bold:
        text = f"<b>{text}</b>"
    return Paragraph(text, style)


def _format(value: Any, suffix: str, digits: int) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.{digits}f}{suffix}"


def _register_fonts() -> str:
    try:
        from matplotlib import get_data_path

        ttf = Path(get_data_path()) / "fonts" / "ttf"
        pdfmetrics.registerFont(TTFont("NDSans", str(ttf / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("NDSans-Bold", str(ttf / "DejaVuSans-Bold.ttf")))
        return "NDSans"
    except Exception:
        return "Helvetica"


def _draw_page(canvas: Any, document: Any) -> None:
    width, height = A4
    canvas.saveState()
    canvas.setFillColor(_BLUE)
    canvas.roundRect(width - 2.9 * cm, height - 1.65 * cm, 1.25 * cm, 1.25 * cm, 0.25 * cm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawCentredString(width - 2.275 * cm, height - 1.13 * cm, "ND")
    canvas.setFillColor(_MUTED)
    try:
        canvas.setFont("NDSans", 7)
    except Exception:
        canvas.setFont("Helvetica", 7)
    canvas.drawString(1.5 * cm, 0.9 * cm, "Network Doctor · MikroTik Wi-Fi diagnostics")
    canvas.drawRightString(width - 1.5 * cm, 0.9 * cm, f"Page {document.page}")
    canvas.restoreState()
