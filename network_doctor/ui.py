from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
from tkinter import PhotoImage, filedialog, messagebox, simpledialog
from typing import Any, Callable

import customtkinter as ctk

from .compare import MetricRow, baseline_deviations, compare_snapshots, export_text, snapshot_summary
from .connections import CONNECTION_LABELS, DEFAULT_PORTS, ConnectionConfig, open_client
from .db import Database
from .demo import create_demo_snapshots
from .diagnose import apply_nat_delta, diagnose_no_internet
from .health import health_rows
from .monitor import MonitorService
from .paths import database_path
from .probe import measure_icmp
from .profiles import (
    ConnectionProfile,
    ProfileStore,
    delete_password,
    get_password,
    set_password,
)
from .storage import SnapshotStore
from .utils import as_number, as_text

BG = "#0b1120"
PANEL = "#111827"
PANEL_ALT = "#162033"
BORDER = "#26344d"
TEXT = "#e5edf8"
MUTED = "#8fa3bf"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
GREEN = "#34d399"
_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"
RED = "#f87171"
AMBER = "#fbbf24"


def _parse_port(value: str, label: str = "Порт") -> int:
    text = (value or "").strip()
    try:
        port = int(text)
    except ValueError as exc:
        raise ValueError(f"{label} должен быть числом от 1 до 65535.") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{label} должен быть числом от 1 до 65535.")
    return port


def _set_entry(entry: ctk.CTkEntry, value: str) -> None:
    entry.delete(0, "end")
    entry.insert(0, value)


def _parse_int(value: str, label: str, minimum: int, maximum: int) -> int:
    try:
        number = int((value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} должен быть целым числом.") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{label} должен быть от {minimum} до {maximum}.")
    return number


def _format_metric(value: Any, suffix: str, digits: int) -> str:
    number = as_number(value)
    if number is None:
        return "—"
    return f"{number:.{digits}f}{suffix}"


def as_snapshot_label(meta: dict[str, Any]) -> str:
    label = as_text(meta.get("snapshot_label"))
    if label:
        return label
    return "Лаборатория" if meta.get("snapshot_type") == "lab" else "Офис"


class NetworkDoctorApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("Network Doctor — MikroTik Wi-Fi")
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(1240, max(820, screen_width - 60))
        height = min(860, max(560, screen_height - 100))
        left = max(0, (screen_width - width) // 2)
        top = max(0, (screen_height - height) // 2 - 20)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.minsize(min(980, width), min(620, height))
        self.configure(fg_color=BG)
        self._apply_window_icon()

        self.store = SnapshotStore()
        self.db = Database(database_path())
        self.profile_store = ProfileStore()
        self.monitor: MonitorService | None = None
        self._busy = False
        self._last_comparison: tuple[
            dict[str, Any], dict[str, Any], list[MetricRow]
        ] | None = None
        self._last_baseline: dict[str, Any] | None = None
        self._last_compare_label = "Лаборатория"
        self._office_paths: dict[str, Path] = {}
        self._lab_paths: dict[str, Path] = {}
        self._timeline_canvas: FigureCanvasTkAgg | None = None
        self._last_snapshot: dict[str, Any] | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_header()
        self._build_tabs()
        self._build_status_bar()

        self._log("Network Doctor запущен.")
        self.after(200, self._refresh_snapshot_lists)
        self.after(300, self._load_latest_health)
        self.after(320, self._refresh_baseline_status)

    def _apply_window_icon(self) -> None:
        ico = _ASSET_DIR / "network-doctor.ico"
        png = _ASSET_DIR / "network-doctor.png"
        try:
            if os.name == "nt" and ico.exists():
                self.iconbitmap(str(ico))
            elif png.exists():
                self._icon_photo = PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        header.grid_columnconfigure(1, weight=1)

        logo = ctk.CTkLabel(
            header,
            text="ND",
            width=52,
            height=52,
            corner_radius=16,
            fg_color=ACCENT,
            text_color="white",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
        )
        logo.grid(row=0, column=0, rowspan=2, padx=(0, 14))

        title = ctk.CTkLabel(
            header,
            text="Network Doctor",
            anchor="w",
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=27, weight="bold"),
        )
        title.grid(row=0, column=1, sticky="sw")

        subtitle = ctk.CTkLabel(
            header,
            text="Диагностика MikroTik Wi-Fi: снимки, шум эфира и сравнение офиса с лабораторией",
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        subtitle.grid(row=1, column=1, sticky="nw")

        badge = ctk.CTkLabel(
            header,
            text="RouterOS API",
            width=150,
            height=32,
            corner_radius=10,
            fg_color=PANEL_ALT,
            text_color=MUTED,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        badge.grid(row=0, column=2, rowspan=2, sticky="e")

    def _build_tabs(self) -> None:
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=PANEL,
            segmented_button_fg_color=PANEL_ALT,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=PANEL_ALT,
            segmented_button_unselected_hover_color=BORDER,
            text_color=TEXT,
            corner_radius=18,
            border_width=1,
            border_color=BORDER,
        )
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 12))

        connection = self.tabview.add("Подключение")
        diagnostics = self.tabview.add("Диагностика")
        health = self.tabview.add("Здоровье")
        monitoring = self.tabview.add("Мониторинг")
        comparison = self.tabview.add("Сравнение")

        self._build_connection_tab(connection)
        self._build_diagnostics_tab(diagnostics)
        self._build_health_tab(health)
        self._build_monitoring_tab(monitoring)
        self._build_comparison_tab(comparison)
        self.tabview.set("Подключение")

    def _build_connection_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        card = ctk.CTkScrollableFrame(tab, fg_color=PANEL_ALT, corner_radius=16)
        card.grid(row=0, column=0, padx=28, pady=24, sticky="nsew")
        card.grid_columnconfigure(1, weight=1)

        heading = ctk.CTkLabel(
            card,
            text="Подключение к роутеру",
            anchor="w",
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
        )
        heading.grid(row=0, column=0, columnspan=2, sticky="w", padx=24, pady=(22, 4))

        hint = ctk.CTkLabel(
            card,
            text=(
                "API — 8728, API-SSL — 8729, SSH — 22. Для SSH укажите "
                "SSH-сервер в поле IP и параметры туннеля."
            ),
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            wraplength=780,
            justify="left",
        )
        hint.grid(row=1, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 14))

        self._field_label(card, 2, "Тип подключения")
        self.connection_type = ctk.CTkComboBox(
            card,
            values=list(CONNECTION_LABELS),
            state="readonly",
            height=40,
            fg_color=BG,
            border_color=BORDER,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=PANEL,
            dropdown_hover_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            command=self._on_connection_type_changed,
        )
        self.connection_type.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 14)
        )
        self.connection_type.set("API")

        self._field_label(card, 4, "Профиль подключения")
        profile_frame = ctk.CTkFrame(card, fg_color="transparent")
        profile_frame.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 14)
        )
        profile_frame.grid_columnconfigure(0, weight=1)
        self.profile_combo = ctk.CTkComboBox(
            profile_frame,
            values=["—"],
            state="readonly",
            height=38,
            fg_color=BG,
            border_color=BORDER,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=PANEL,
            dropdown_hover_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.profile_combo.grid(row=0, column=0, sticky="ew")
        self.profile_combo.set("—")
        for column, (text, command) in enumerate(
            (
                ("Загрузить", self._on_profile_load),
                ("Сохранить", self._on_profile_save),
                ("Удалить", self._on_profile_delete),
            ),
            start=1,
        ):
            button = ctk.CTkButton(
                profile_frame,
                text=text,
                width=92,
                height=34,
                fg_color=PANEL,
                hover_color=BORDER,
                border_width=1,
                border_color=BORDER,
                text_color=TEXT,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                command=command,
            )
            button.grid(row=0, column=column, padx=(8, 0))

        self.host_entry = self._entry_row(
            card, 6, "IP-адрес / SSH-сервер", "192.168.88.1"
        )
        self.port_entry = self._entry_row(card, 8, "Порт API / SSH", "8728")
        self.port_entry.insert(0, "8728")
        self.user_entry = self._entry_row(card, 10, "Логин администратора", "admin")
        self.user_entry.insert(0, "admin")

        self._field_label(card, 12, "Пароль")
        password_frame = ctk.CTkFrame(card, fg_color="transparent")
        password_frame.grid(
            row=13, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 8)
        )
        password_frame.grid_columnconfigure(0, weight=1)
        self.password_entry = ctk.CTkEntry(
            password_frame,
            height=42,
            show="*",
            fg_color=BG,
            border_color=BORDER,
            text_color=TEXT,
            placeholder_text="Пароль администратора",
            font=ctk.CTkFont(family="Segoe UI", size=14),
        )
        self.password_entry.grid(row=0, column=0, sticky="ew")
        self.show_password = ctk.CTkCheckBox(
            password_frame,
            text="Показать",
            command=self._toggle_password,
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.show_password.grid(row=0, column=1, padx=(12, 0))
        self.save_password = ctk.CTkCheckBox(
            password_frame,
            text="В keyring",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.save_password.grid(row=0, column=2, padx=(12, 0))

        self.ssl_verify = ctk.CTkCheckBox(
            card,
            text="Проверять SSL-сертификат",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.ssl_verify.grid(
            row=14, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 10)
        )
        self.ssl_verify.grid_remove()

        self.ssh_frame = ctk.CTkFrame(card, fg_color=BG, corner_radius=12)
        self.ssh_frame.grid(
            row=15, column=0, columnspan=2, sticky="ew", padx=24, pady=(4, 14)
        )
        self.ssh_frame.grid_columnconfigure(1, weight=1)
        ssh_title = ctk.CTkLabel(
            self.ssh_frame,
            text="Параметры SSH-туннеля",
            anchor="w",
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        )
        ssh_title.grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 2)
        )
        self.ssh_remote_host_entry = self._entry_row(
            self.ssh_frame, 1, "Роутер за туннелем", "192.168.88.1"
        )
        self.ssh_remote_port_entry = self._entry_row(
            self.ssh_frame, 3, "Порт API за туннелем", "8728"
        )
        self.ssh_remote_port_entry.insert(0, "8728")
        self.ssh_user_entry = self._entry_row(
            self.ssh_frame, 5, "SSH-пользователь", "admin"
        )
        self.ssh_key_entry = self._entry_row(
            self.ssh_frame, 7, "SSH-ключ (необязательно)", "C:\\Users\\...\\id_rsa"
        )
        self.ssh_frame.grid_remove()

        self.test_button = ctk.CTkButton(
            card,
            text="Проверить связь",
            height=48,
            width=220,
            corner_radius=12,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            command=self._on_test_connection,
        )
        self.test_button.grid(row=16, column=0, sticky="w", padx=24, pady=(16, 20))

        self.connection_status = ctk.CTkLabel(
            card,
            text="Статус: не подключено",
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.connection_status.grid(
            row=16, column=1, sticky="w", padx=(12, 24), pady=(16, 20)
        )

        self._refresh_profiles()
        self._on_connection_type_changed("API")

    def _tab_body(self, tab: ctk.CTkFrame) -> ctk.CTkScrollableFrame:
        body = ctk.CTkScrollableFrame(
            tab,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=ACCENT,
        )
        body.grid(row=0, column=0, sticky="nsew")
        return body

    def _build_diagnostics_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        body = self._tab_body(tab)
        body.grid_columnconfigure(0, weight=1)

        controls = ctk.CTkFrame(body, fg_color=PANEL_ALT, corner_radius=16)
        controls.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 12))
        controls.grid_columnconfigure(1, weight=1)

        label = ctk.CTkLabel(
            controls,
            text="Тип снимка",
            anchor="w",
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
        )
        label.grid(row=0, column=0, padx=(24, 18), pady=22, sticky="w")

        self.snapshot_type = ctk.CTkSegmentedButton(
            controls,
            values=["Офис", "Лаборатория"],
            height=36,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=BG,
            unselected_hover_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self.snapshot_type.grid(row=0, column=1, sticky="w", pady=22)
        self.snapshot_type.set("Офис")

        self.collect_button = ctk.CTkButton(
            controls,
            text="Собрать данные",
            height=42,
            width=190,
            corner_radius=11,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            command=self._on_collect,
        )
        self.collect_button.grid(row=0, column=2, padx=24, pady=22, sticky="e")

        self.summary_box = ctk.CTkTextbox(
            body,
            fg_color=PANEL_ALT,
            border_color=BORDER,
            border_width=1,
            corner_radius=16,
            text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="word",
            height=320,
        )
        self.summary_box.grid(
            row=2, column=0, sticky="ew", padx=28, pady=(0, 24)
        )
        self._set_text(
            self.summary_box,
            "Здесь появится сводка снимка: клиенты, сигнал, CCQ, Noise floor, "
            "каналы и предупреждения.\n\nСнимок сохраняется в папку snapshots.",
        )

        note = ctk.CTkLabel(
            body,
            text="Spectral-scan выполняется до 8 секунд на интерфейс.",
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        note.grid(row=1, column=0, sticky="w", padx=30, pady=(0, 8))

    def _build_health_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        body = self._tab_body(tab)
        body.grid_columnconfigure(0, weight=1)

        controls = ctk.CTkFrame(body, fg_color=PANEL_ALT, corner_radius=16)
        controls.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 12))
        controls.grid_columnconfigure(1, weight=1)

        title = ctk.CTkLabel(
            controls,
            text="Client Health Score",
            anchor="w",
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=19, weight="bold"),
        )
        title.grid(row=0, column=0, sticky="w", padx=24, pady=(18, 2))

        self.health_summary = ctk.CTkLabel(
            controls,
            text="Нет данных. Соберите снимок или запустите мониторинг.",
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.health_summary.grid(row=1, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 16))

        self.health_refresh_button = ctk.CTkButton(
            controls,
            text="Обновить из БД",
            height=38,
            width=160,
            fg_color=PANEL,
            hover_color=BORDER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_refresh_health,
        )
        self.health_refresh_button.grid(
            row=0, column=1, rowspan=2, padx=24, pady=18, sticky="e"
        )

        self.health_table = ctk.CTkScrollableFrame(
            body,
            fg_color=PANEL_ALT,
            border_color=BORDER,
            border_width=1,
            corner_radius=16,
            height=380,
        )
        self.health_table.grid(
            row=2, column=0, sticky="ew", padx=28, pady=(0, 24)
        )
        for column, weight in enumerate((2, 1, 1, 1, 1, 1, 2, 4)):
            self.health_table.grid_columnconfigure(column, weight=weight)
        self._render_health(None)

    def _build_monitoring_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        body = self._tab_body(tab)
        body.grid_columnconfigure(0, weight=1)

        controls = ctk.CTkFrame(body, fg_color=PANEL_ALT, corner_radius=16)
        controls.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 10))
        controls.grid_columnconfigure(1, weight=1)

        title = ctk.CTkLabel(
            controls,
            text="Сторож: непрерывный мониторинг",
            anchor="w",
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=19, weight="bold"),
        )
        title.grid(row=0, column=0, sticky="w", padx=24, pady=(18, 2))

        self.monitor_status = ctk.CTkLabel(
            controls,
            text="Остановлен",
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.monitor_status.grid(
            row=1, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 14)
        )

        options = ctk.CTkFrame(controls, fg_color="transparent")
        options.grid(row=2, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 16))
        options.grid_columnconfigure(4, weight=1)

        ctk.CTkLabel(
            options,
            text="Интервал, мин",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        ).grid(row=0, column=0, padx=(0, 8))

        self.monitor_interval = ctk.CTkEntry(
            options,
            width=72,
            height=36,
            fg_color=BG,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.monitor_interval.grid(row=0, column=1, padx=(0, 18))
        self.monitor_interval.insert(0, "5")

        self.monitor_type = ctk.CTkSegmentedButton(
            options,
            values=["Офис", "Лаборатория"],
            height=34,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=BG,
            unselected_hover_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.monitor_type.grid(row=0, column=2, padx=(0, 18))
        self.monitor_type.set("Офис")

        self.monitor_start_button = ctk.CTkButton(
            options,
            text="Запустить",
            width=120,
            height=38,
            fg_color=GREEN,
            hover_color="#059669",
            text_color="#052e1b",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._on_monitor_start,
        )
        self.monitor_start_button.grid(row=0, column=3, padx=(0, 8))

        self.monitor_stop_button = ctk.CTkButton(
            options,
            text="Остановить",
            width=115,
            height=38,
            fg_color=PANEL,
            hover_color=BORDER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_monitor_stop,
            state="disabled",
        )
        self.monitor_stop_button.grid(row=0, column=4, sticky="w")

        timeline_controls = ctk.CTkFrame(body, fg_color="transparent")
        timeline_controls.grid(row=1, column=0, sticky="ew", padx=30, pady=(0, 6))
        timeline_controls.grid_columnconfigure(4, weight=1)

        ctk.CTkLabel(
            timeline_controls,
            text="Клиент",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        ).grid(row=0, column=0, padx=(0, 8))

        self.timeline_client = ctk.CTkComboBox(
            timeline_controls,
            values=["—"],
            state="readonly",
            width=230,
            height=36,
            fg_color=BG,
            border_color=BORDER,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=PANEL,
            dropdown_hover_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.timeline_client.grid(row=0, column=1, padx=(0, 16))
        self.timeline_client.set("—")

        ctk.CTkLabel(
            timeline_controls,
            text="Период",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        ).grid(row=0, column=2, padx=(0, 8))

        self.timeline_period = ctk.CTkOptionMenu(
            timeline_controls,
            values=["1 час", "6 часов", "24 часа", "7 дней", "30 дней"],
            width=120,
            height=36,
            fg_color=PANEL_ALT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=PANEL,
            dropdown_hover_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.timeline_period.grid(row=0, column=3, padx=(0, 12))
        self.timeline_period.set("24 часа")

        self.timeline_button = ctk.CTkButton(
            timeline_controls,
            text="Показать",
            width=110,
            height=36,
            fg_color=PANEL_ALT,
            hover_color=BORDER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_timeline_refresh,
        )
        self.timeline_button.grid(row=0, column=4, sticky="w")

        self.timeline_hint = ctk.CTkLabel(
            body,
            text="Выберите клиента и период. Данные берутся из локальной истории SQLite.",
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.timeline_hint.grid(row=2, column=0, sticky="w", padx=30, pady=(0, 4))

        self.timeline_frame = ctk.CTkFrame(
            body,
            fg_color=PANEL_ALT,
            border_color=BORDER,
            border_width=1,
            corner_radius=16,
            height=340,
        )
        self.timeline_frame.grid(
            row=3, column=0, sticky="ew", padx=28, pady=(0, 24)
        )
        self.timeline_frame.grid_rowconfigure(0, weight=1)
        self.timeline_frame.grid_columnconfigure(0, weight=1)

    def _build_comparison_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        body = self._tab_body(tab)
        body.grid_columnconfigure(0, weight=1)

        controls = ctk.CTkFrame(body, fg_color=PANEL_ALT, corner_radius=16)
        controls.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 12))
        controls.grid_columnconfigure(1, weight=1)
        controls.grid_columnconfigure(3, weight=1)

        self._field_label(controls, 0, "Снимок офиса", padx=(24, 8))
        self.office_combo = self._snapshot_combo(controls)
        self.office_combo.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(24, 8), pady=(0, 16))

        self._field_label(
            controls,
            0,
            "Снимок лаборатории",
            padx=(8, 24),
            column=2,
        )
        self.lab_combo = self._snapshot_combo(controls)
        self.lab_combo.grid(row=1, column=2, columnspan=2, sticky="ew", padx=(8, 24), pady=(0, 16))

        self.show_demo = ctk.CTkCheckBox(
            controls,
            text="Показывать демо-снимки",
            command=self._refresh_snapshot_lists,
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.show_demo.grid(
            row=2, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 16)
        )

        self.compare_mode = ctk.CTkSegmentedButton(
            controls,
            values=["Офис ↔ Лаборатория", "Текущий ↔ Эталон"],
            height=34,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=BG,
            unselected_hover_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.compare_mode.grid(
            row=3, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 14)
        )
        self.compare_mode.set("Офис ↔ Лаборатория")

        self.baseline_button = ctk.CTkButton(
            controls,
            text="Сохранить офис как эталон",
            height=34,
            width=220,
            fg_color=PANEL,
            hover_color=BORDER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_save_baseline,
        )
        self.baseline_button.grid(
            row=3, column=2, columnspan=2, sticky="w", padx=(8, 24), pady=(0, 14)
        )

        self.baseline_status = ctk.CTkLabel(
            controls,
            text="Эталон: не сохранён",
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.baseline_status.grid(
            row=4, column=0, columnspan=4, sticky="w", padx=24, pady=(0, 14)
        )

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 12))
        actions.grid_columnconfigure(2, weight=1)

        self.refresh_button = ctk.CTkButton(
            actions,
            text="Обновить список",
            height=38,
            width=160,
            fg_color=PANEL_ALT,
            hover_color=BORDER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            command=self._on_refresh,
        )
        self.refresh_button.grid(row=0, column=0, padx=(0, 10))

        self.compare_button = ctk.CTkButton(
            actions,
            text="Сравнить",
            height=42,
            width=160,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            command=self._on_compare,
        )
        self.compare_button.grid(row=0, column=1, padx=(0, 10))

        self.demo_button = ctk.CTkButton(
            actions,
            text="Демо-данные",
            height=38,
            width=150,
            fg_color=PANEL_ALT,
            hover_color=BORDER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            command=self._on_demo,
        )
        self.demo_button.grid(row=0, column=2, sticky="w")

        self.export_format = ctk.CTkOptionMenu(
            actions,
            values=["TXT", "HTML", "PDF"],
            width=92,
            height=38,
            fg_color=PANEL_ALT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=PANEL,
            dropdown_hover_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.export_format.grid(row=0, column=3, padx=(0, 8))
        self.export_format.set("HTML")

        self.export_button = ctk.CTkButton(
            actions,
            text="Экспорт",
            height=38,
            width=120,
            fg_color=PANEL_ALT,
            hover_color=BORDER,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            command=self._on_export,
            state="disabled",
        )
        self.export_button.grid(row=0, column=4, sticky="e")

        self.comparison_note = ctk.CTkLabel(
            body,
            text="",
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            wraplength=1100,
            justify="left",
        )
        self.comparison_note.grid(
            row=2, column=0, sticky="w", padx=30, pady=(0, 6)
        )

        self.table = ctk.CTkScrollableFrame(
            body,
            fg_color=PANEL_ALT,
            border_color=BORDER,
            border_width=1,
            corner_radius=16,
            height=340,
        )
        self.table.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 24))
        self.table.grid_columnconfigure(0, weight=3)
        self.table.grid_columnconfigure(1, weight=2)
        self.table.grid_columnconfigure(2, weight=2)
        self.table.grid_columnconfigure(3, weight=2)

        self._render_comparison([])

    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=112)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_propagate(False)

        self.status_label = ctk.CTkLabel(
            bar,
            text="Готово",
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=24, pady=(10, 4))

        self.log_box = ctk.CTkTextbox(
            bar,
            height=68,
            fg_color=BG,
            border_width=0,
            corner_radius=0,
            text_color=MUTED,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
        )
        self.log_box.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.log_box.configure(state="disabled")

    def _entry_row(
        self, parent: ctk.CTkFrame, row: int, label: str, placeholder: str
    ) -> ctk.CTkEntry:
        self._field_label(parent, row, label)
        entry = ctk.CTkEntry(
            parent,
            height=42,
            fg_color=BG,
            border_color=BORDER,
            text_color=TEXT,
            placeholder_text=placeholder,
            font=ctk.CTkFont(family="Segoe UI", size=14),
        )
        entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 14))
        return entry

    def _field_label(
        self,
        parent: ctk.CTkFrame,
        row: int,
        label: str,
        padx: tuple[int, int] = (24, 24),
        column: int = 0,
    ) -> None:
        widget = ctk.CTkLabel(
            parent,
            text=label,
            anchor="w",
            text_color=MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        widget.grid(
            row=row,
            column=column,
            columnspan=2,
            sticky="w",
            padx=padx,
            pady=(8, 5),
        )

    def _snapshot_combo(self, parent: ctk.CTkFrame) -> ctk.CTkComboBox:
        combo = ctk.CTkComboBox(
            parent,
            values=["—"],
            state="readonly",
            height=38,
            fg_color=BG,
            border_color=BORDER,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=PANEL,
            dropdown_hover_color=BORDER,
            text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        combo.set("—")
        return combo

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password.get() else "*")

    def _load_latest_health(self) -> None:
        snapshot = self.db.latest_snapshot()
        if snapshot is not None:
            self._last_snapshot = snapshot
            self._render_health(snapshot)
        self._refresh_timeline_clients()

    def _on_refresh_health(self) -> None:
        snapshot = self._last_snapshot or self.db.latest_snapshot()
        if snapshot is None:
            messagebox.showwarning(
                "Здоровье клиентов",
                "История пуста. Соберите снимок или запустите мониторинг.",
                parent=self,
            )
            return
        self._last_snapshot = snapshot
        self._render_health(snapshot)
        self._refresh_timeline_clients()
        self._log("Health Score обновлён из последнего снимка.")

    def _render_health(self, snapshot: dict[str, Any] | None) -> None:
        for child in self.health_table.winfo_children():
            child.destroy()
        if snapshot is None:
            self.health_summary.configure(
                text="Нет данных. Соберите снимок или запустите мониторинг."
            )
            empty = ctk.CTkLabel(
                self.health_table,
                text="Health Score появится после первого снимка.",
                anchor="w",
                text_color=MUTED,
                font=ctk.CTkFont(family="Segoe UI", size=13),
            )
            empty.grid(row=0, column=0, columnspan=8, sticky="w", padx=12, pady=20)
            return

        rows = health_rows(snapshot)
        meta = snapshot.get("meta", {}) or {}
        red = sum(1 for item in rows if item["health"].state == "red")
        yellow = sum(1 for item in rows if item["health"].state == "yellow")
        green = sum(1 for item in rows if item["health"].state == "green")
        self.health_summary.configure(
            text=(
                f"{as_snapshot_label(meta)} · клиентов: {len(rows)} · "
                f"критичных: {red} · внимание: {yellow} · норма: {green}"
            )
        )
        headers = [
            "Клиент",
            "Интерфейс",
            "Сигнал",
            "SNR",
            "CCQ",
            "Retry",
            "Health",
            "Причины",
        ]
        for column, header in enumerate(headers):
            ctk.CTkLabel(
                self.health_table,
                text=header,
                anchor="w",
                height=34,
                text_color=MUTED,
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            ).grid(
                row=0,
                column=column,
                sticky="ew",
                padx=(12, 8) if column == 0 else 8,
                pady=(4, 6),
            )

        if not rows:
            ctk.CTkLabel(
                self.health_table,
                text="В снимке нет зарегистрированных клиентов.",
                anchor="w",
                text_color=MUTED,
                font=ctk.CTkFont(family="Segoe UI", size=13),
            ).grid(row=1, column=0, columnspan=8, sticky="w", padx=12, pady=18)
            return

        for index, item in enumerate(rows, start=1):
            result = item["health"]
            background = PANEL_ALT if index % 2 else "#0f1a2d"
            frame = ctk.CTkFrame(self.health_table, fg_color=background, corner_radius=8)
            frame.grid(
                row=index,
                column=0,
                columnspan=8,
                sticky="ew",
                padx=4,
                pady=2,
            )
            for column, weight in enumerate((2, 1, 1, 1, 1, 1, 2, 4)):
                frame.grid_columnconfigure(column, weight=weight)

            values = [
                item["mac"],
                item["interface"],
                _format_metric(item["signal_dbm"], " dBm", 1),
                _format_metric(item["snr_db"], " dB", 1),
                _format_metric(item["ccq"], "%", 1),
                _format_metric(item["retry_pct"], "%", 1),
            ]
            for column, value in enumerate(values):
                ctk.CTkLabel(
                    frame,
                    text=value,
                    anchor="w",
                    height=34,
                    text_color=TEXT if column == 0 else MUTED,
                    font=ctk.CTkFont(
                        family="Consolas" if column else "Segoe UI",
                        size=11,
                        weight="bold" if column == 0 else "normal",
                    ),
                ).grid(
                    row=0,
                    column=column,
                    sticky="ew",
                    padx=(12, 8) if column == 0 else 8,
                    pady=4,
                )

            score_frame = ctk.CTkFrame(frame, fg_color="transparent")
            score_frame.grid(row=0, column=6, sticky="ew", padx=8, pady=4)
            score_frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                score_frame,
                text=str(result.score),
                width=30,
                text_color=result.color,
                font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            ).grid(row=0, column=0, padx=(0, 6))
            progress = ctk.CTkProgressBar(
                score_frame,
                width=64,
                height=7,
                progress_color=result.color,
                fg_color=BORDER,
            )
            progress.grid(row=0, column=1, sticky="ew")
            progress.set(result.score / 100)

            ctk.CTkLabel(
                frame,
                text="; ".join(result.reasons) or "—",
                anchor="w",
                height=34,
                wraplength=300,
                text_color=MUTED,
                font=ctk.CTkFont(family="Segoe UI", size=10),
            ).grid(row=0, column=7, sticky="ew", padx=(8, 12), pady=4)

    def _on_monitor_start(self) -> None:
        try:
            config = self._connection_config()
            minutes = _parse_int(self.monitor_interval.get(), "Интервал", 1, 1440)
        except ValueError as exc:
            messagebox.showwarning("Мониторинг", str(exc), parent=self)
            return
        if self.monitor is not None and self.monitor.running:
            return
        label = self.monitor_type.get()
        snapshot_type = "office" if label == "Офис" else "lab"
        self.monitor = MonitorService(
            self.db,
            interval_seconds=minutes * 60,
            snapshot_type=snapshot_type,
            on_snapshot=self._monitor_on_snapshot,
            on_error=self._monitor_on_error,
            health_probe=measure_icmp,
        )
        try:
            self.monitor.start(config)
        except Exception as exc:
            messagebox.showerror("Мониторинг", str(exc), parent=self)
            return
        self.monitor_status.configure(
            text=f"Работает · интервал {minutes} мин · ICMP каждые 30 с · {label}",
            text_color=GREEN,
        )
        self.monitor_start_button.configure(state="disabled")
        self.monitor_stop_button.configure(state="normal")
        self._log(f"Мониторинг запущен: {minutes} мин, {label}.")

    def _on_monitor_stop(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
        self.monitor_status.configure(text="Остановлен", text_color=MUTED)
        self.monitor_start_button.configure(state="normal")
        self.monitor_stop_button.configure(state="disabled")
        self._log("Мониторинг остановлен.")

    def _monitor_on_snapshot(
        self, snapshot: dict[str, Any], snapshot_id: int, delay: int
    ) -> None:
        self.after(
            0,
            lambda: self._monitor_snapshot_ui(snapshot, snapshot_id, delay),
        )

    def _monitor_snapshot_ui(
        self, snapshot: dict[str, Any], snapshot_id: int, delay: int
    ) -> None:
        self._last_snapshot = snapshot
        self._set_text(self.summary_box, snapshot_summary(snapshot))
        self._render_health(snapshot)
        self._refresh_timeline_clients()
        self._refresh_timeline_if_visible()
        self.monitor_status.configure(
            text=f"OK · снимок #{snapshot_id} · следующий через {delay // 60} мин",
            text_color=GREEN,
        )
        self._log(f"Мониторинг: сохранён снимок #{snapshot_id}.")

    def _monitor_on_error(self, message: str, delay: int) -> None:
        self.after(0, lambda: self._monitor_error_ui(message, delay))

    def _monitor_error_ui(self, message: str, delay: int) -> None:
        self.monitor_status.configure(
            text=f"Ошибка · повтор через {max(1, delay // 60)} мин",
            text_color=AMBER,
        )
        self._log(f"Мониторинг: {message}")

    def _timeline_hours(self) -> int:
        return {
            "1 час": 1,
            "6 часов": 6,
            "24 часа": 24,
            "7 дней": 24 * 7,
            "30 дней": 24 * 30,
        }.get(self.timeline_period.get(), 24)

    def _refresh_timeline_clients(self) -> None:
        current = self.timeline_client.get()
        clients = self.db.timeline_clients(self._timeline_hours())
        values = clients or ["—"]
        self.timeline_client.configure(values=values)
        if current not in values:
            self.timeline_client.set(values[0])

    def _on_timeline_refresh(self) -> None:
        mac = self.timeline_client.get()
        if not mac or mac == "—":
            self.timeline_hint.configure(text="За выбранный период клиентов нет.")
            return
        points = self.db.timeline_series(mac, self._timeline_hours())
        if not points:
            self.timeline_hint.configure(text="Для клиента нет точек за выбранный период.")
            return
        self._render_timeline(mac, points)
        self.timeline_hint.configure(
            text=f"{mac} · точек: {len(points)} · период: {self.timeline_period.get()}"
        )

    def _refresh_timeline_if_visible(self) -> None:
        mac = self.timeline_client.get()
        if not mac or mac == "—":
            return
        points = self.db.timeline_series(mac, self._timeline_hours())
        if points:
            self._render_timeline(mac, points)

    def _render_timeline(self, mac: str, points: list[Any]) -> None:
        for child in self.timeline_frame.winfo_children():
            child.destroy()
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        from .charting import timeline_figure

        figure = timeline_figure(points, mac)
        canvas = FigureCanvasTkAgg(figure, master=self.timeline_frame)
        canvas.draw()
        canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self._timeline_canvas = canvas

    def _on_close(self) -> None:
        if self.monitor is not None:
            self.monitor.stop(timeout=2)
        try:
            self.db.close()
        except Exception:
            pass
        self.destroy()

    def _on_connection_type_changed(self, label: str) -> None:
        kind = CONNECTION_LABELS.get(label, "api")
        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, str(DEFAULT_PORTS[kind]))
        if kind == "api_ssl":
            self.ssl_verify.grid()
        else:
            self.ssl_verify.grid_remove()
        if kind == "ssh":
            self.ssh_frame.grid()
        else:
            self.ssh_frame.grid_remove()
        self.connection_status.configure(
            text=f"Режим: {label}", text_color=MUTED
        )

    def _connection_config(self) -> ConnectionConfig:
        kind = CONNECTION_LABELS.get(self.connection_type.get(), "api")
        host = self.host_entry.get().strip()
        username = self.user_entry.get().strip()
        password = self.password_entry.get()
        port = _parse_port(self.port_entry.get(), "Порт")
        ssh_remote_host = self.ssh_remote_host_entry.get().strip()
        ssh_remote_port = _parse_port(
            self.ssh_remote_port_entry.get(), "Порт API за туннелем"
        )
        ssh_port = port if kind == "ssh" else 22
        config = ConnectionConfig(
            connection_type=kind,
            host=host,
            port=port if kind != "ssh" else DEFAULT_PORTS["api"],
            username=username,
            password=password,
            ssl_verify=bool(self.ssl_verify.get()),
            ssh_host=host,
            ssh_port=ssh_port,
            ssh_user=self.ssh_user_entry.get().strip(),
            ssh_key=self.ssh_key_entry.get().strip(),
            ssh_remote_host=ssh_remote_host,
            ssh_remote_port=ssh_remote_port,
        )
        config.validate()
        return config

    def _refresh_profiles(self) -> None:
        names = [profile.name for profile in self.profile_store.load()]
        values = names or ["—"]
        self.profile_combo.configure(values=values)
        if self.profile_combo.get() not in values:
            self.profile_combo.set(values[0])

    def _on_profile_load(self) -> None:
        name = self.profile_combo.get()
        profile = self.profile_store.get(name) if name and name != "—" else None
        if profile is None:
            messagebox.showwarning("Профиль", "Выберите сохранённый профиль.", parent=self)
            return
        label = next(
            (key for key, value in CONNECTION_LABELS.items() if value == profile.connection_type),
            "API",
        )
        self.connection_type.set(label)
        self._on_connection_type_changed(label)
        _set_entry(self.host_entry, profile.host)
        _set_entry(self.port_entry, str(profile.port))
        _set_entry(self.user_entry, profile.username)
        self.ssl_verify.select() if profile.ssl_verify else self.ssl_verify.deselect()
        _set_entry(self.ssh_remote_host_entry, profile.ssh_remote_host)
        _set_entry(self.ssh_remote_port_entry, str(profile.ssh_remote_port))
        _set_entry(self.ssh_user_entry, profile.ssh_user)
        _set_entry(self.ssh_key_entry, profile.ssh_key)
        self.save_password.select() if profile.save_password else self.save_password.deselect()
        _set_entry(
            self.password_entry,
            get_password(profile) if profile.save_password else "",
        )
        self._log(f"Загружен профиль: {profile.name}")
        self.connection_status.configure(
            text=f"Профиль: {profile.name}", text_color=GREEN
        )

    def _on_profile_save(self) -> None:
        try:
            config = self._connection_config()
        except ValueError as exc:
            messagebox.showwarning("Профиль", str(exc), parent=self)
            return
        current = self.profile_combo.get()
        initial = current if current and current != "—" else ""
        name = simpledialog.askstring(
            "Профиль подключения",
            "Название профиля:",
            initialvalue=initial,
            parent=self,
        )
        if not name:
            return
        profile = ConnectionProfile(
            name=name.strip(),
            connection_type=config.connection_type,
            host=config.host,
            port=config.port if config.connection_type != "ssh" else config.ssh_port,
            username=config.username,
            ssl_verify=config.ssl_verify,
            ssh_host=config.ssh_host,
            ssh_port=config.ssh_port,
            ssh_user=config.ssh_user,
            ssh_key=config.ssh_key,
            ssh_remote_host=config.ssh_remote_host,
            ssh_remote_port=config.ssh_remote_port,
            save_password=bool(self.save_password.get()),
        )
        saved = True
        if profile.save_password:
            saved = set_password(profile, config.password)
        else:
            delete_password(profile)
        self.profile_store.upsert(profile)
        self._refresh_profiles()
        self.profile_combo.set(profile.name)
        self._log(f"Профиль сохранён: {profile.name}")
        if not saved:
            messagebox.showwarning(
                "Профиль",
                "Профиль сохранён, но пароль не удалось записать в keyring. "
                "Проверьте системное хранилище секретов.",
                parent=self,
            )

    def _on_profile_delete(self) -> None:
        name = self.profile_combo.get()
        if not name or name == "—":
            return
        if not messagebox.askyesno(
            "Удалить профиль", f"Удалить профиль «{name}»?", parent=self
        ):
            return
        self.profile_store.delete(name)
        self._refresh_profiles()
        self._log(f"Профиль удалён: {name}")

    def _on_test_connection(self) -> None:
        try:
            config = self._connection_config()
        except ValueError as exc:
            messagebox.showwarning("Проверка связи", str(exc), parent=self)
            return

        def action() -> Any:
            with open_client(config) as client:
                return client.test_connection(), config

        def success(result: Any) -> None:
            info, cfg = result
            text = info.as_text()
            self.connection_status.configure(
                text=f"OK — {text}", text_color=GREEN
            )
            self._log(f"Связь установлена ({cfg.label}): {text}")
            messagebox.showinfo(
                "Проверка связи",
                f"Подключение успешно.\n\n{text}",
                parent=self,
            )

        self._run("Проверка связи...", action, success)

    def _on_collect(self) -> None:
        try:
            config = self._connection_config()
        except ValueError as exc:
            messagebox.showwarning("Снимок", str(exc), parent=self)
            return

        label = self.snapshot_type.get()
        snapshot_type = "office" if label == "Офис" else "lab"

        def action() -> Any:
            health_metrics = measure_icmp()
            with open_client(config) as client:
                snapshot = client.collect_snapshot(
                    snapshot_type,
                    health_metrics=health_metrics,
                )
            apply_nat_delta(snapshot, self.db.latest_snapshot(snapshot_type))
            path = self.store.save(snapshot)
            snapshot_id = self.db.save_snapshot(snapshot, source_path=path)
            return path, snapshot, snapshot_id

        def success(result: Any) -> None:
            path, snapshot, snapshot_id = result
            self._last_snapshot = snapshot
            summary = snapshot_summary(snapshot)
            diagnosis = diagnose_no_internet(snapshot)
            summary += "\n\n" + diagnosis.as_text()
            self._set_text(self.summary_box, summary)
            self._render_health(snapshot)
            self._refresh_snapshot_lists()
            self._refresh_timeline_clients()
            warnings = snapshot.get("warnings", [])
            self._log(f"Снимок сохранён: {path.name} · БД #{snapshot_id}")
            if warnings:
                text = "\n".join(f"• {warning}" for warning in warnings)
                messagebox.showwarning(
                    "Снимок сохранён с замечаниями",
                    f"Файл: {path}\n\n{text}",
                    parent=self,
                )
            else:
                messagebox.showinfo(
                    "Снимок сохранён",
                    f"Файл:\n{path}",
                    parent=self,
                )

        self._run(f"Сбор снимка «{label}»...", action, success)

    def _on_refresh(self) -> None:
        self._refresh_snapshot_lists()
        self._log("Список снимков обновлён.")

    def _on_demo(self) -> None:
        def action() -> Any:
            return create_demo_snapshots(self.store)

        def success(result: Any) -> None:
            office_path, lab_path = result
            self.show_demo.select()
            self._last_comparison = None
            self._last_baseline = None
            self.comparison_note.configure(text="", text_color=MUTED)
            self.export_button.configure(state="disabled")
            self._render_comparison([])
            self._refresh_snapshot_lists(office_path, lab_path)
            self._log("Демо-снимки созданы: офис и лаборатория.")
            messagebox.showinfo(
                "Демо-данные",
                "Созданы два демонстрационных снимка.\n"
                "Они находятся в папке snapshots/demo.\n\n"
                "Теперь можно нажать «Сравнить».",
                parent=self,
            )

        self._run("Создание демо-снимков...", action, success)

    def _refresh_snapshot_lists(
        self,
        office_path: Path | None = None,
        lab_path: Path | None = None,
    ) -> None:
        self._office_paths = self._load_combo(
            self.office_combo, "office", office_path
        )
        self._lab_paths = self._load_combo(self.lab_combo, "lab", lab_path)

    def _load_combo(
        self,
        combo: ctk.CTkComboBox,
        snapshot_type: str,
        preferred_path: Path | None = None,
    ) -> dict[str, Path]:
        infos = self.store.list(
            snapshot_type,
            include_demo=self.show_demo.get(),
        )
        mapping = {info.display: info.path for info in infos}
        values = list(mapping) or ["—"]
        combo.configure(values=values)
        if preferred_path is not None:
            for display, path in mapping.items():
                if path == preferred_path:
                    combo.set(display)
                    return mapping
        if combo.get() not in values:
            combo.set(values[0])
        return mapping

    def _on_compare(self) -> None:
        office_path = self._office_paths.get(self.office_combo.get())
        against_baseline = self.compare_mode.get() == "Текущий ↔ Эталон"
        if office_path is None:
            messagebox.showwarning(
                "Сравнение",
                "Выберите снимок офиса.",
                parent=self,
            )
            return
        if not against_baseline and self._lab_paths.get(self.lab_combo.get()) is None:
            messagebox.showwarning(
                "Сравнение",
                "Нужны снимки офиса и лаборатории. Сначала соберите данные.",
                parent=self,
            )
            return
        baseline = self.db.get_baseline("office") if against_baseline else None
        if against_baseline and baseline is None:
            messagebox.showwarning(
                "Эталон",
                "Эталон офиса не сохранён. Выберите снимок и нажмите "
                "«Сохранить офис как эталон».",
                parent=self,
            )
            return
        lab_path = self._lab_paths.get(self.lab_combo.get())

        def action() -> Any:
            current = self.store.load(office_path)
            other = baseline if against_baseline else self.store.load(lab_path)
            rows = compare_snapshots(current, other)
            return current, other, rows

        def success(result: Any) -> None:
            current, other, rows = result
            self._last_comparison = (current, other, rows)
            self._last_baseline = baseline if against_baseline else None
            self._last_compare_label = "Эталон" if against_baseline else "Лаборатория"
            self.export_button.configure(state="normal")
            self._render_comparison(rows)
            if against_baseline:
                deviations = baseline_deviations(current, baseline or {})
                self.comparison_note.configure(
                    text="Отклонения: " + " ".join(deviations),
                    text_color=AMBER,
                )
                self._log("Сравнение выполнено: текущий офис ↔ эталон.")
            else:
                self.comparison_note.configure(text="", text_color=MUTED)
                self._log(
                    f"Сравнение готово: {office_path.name} ↔ {lab_path.name if lab_path else '—'}"
                )

        title = "Сравнение с эталоном..." if against_baseline else "Сравнение снимков..."
        self._run(title, action, success)

    def _on_save_baseline(self) -> None:
        office_path = self._office_paths.get(self.office_combo.get())
        if office_path is None:
            messagebox.showwarning(
                "Эталон", "Выберите офисный снимок.", parent=self
            )
            return
        try:
            snapshot = self.store.load(office_path)
            snapshot_id = self.db.save_snapshot(
                snapshot,
                source_path=office_path,
                demo=bool((snapshot.get("meta", {}) or {}).get("demo")),
            )
            self.db.set_baseline("office", snapshot_id)
        except Exception as exc:
            messagebox.showerror("Эталон", str(exc), parent=self)
            return
        self._refresh_baseline_status()
        self._log(f"Эталон сохранён: {office_path.name} ({snapshot_id}).")
        messagebox.showinfo(
            "Эталон",
            f"Снимок сохранён как эталон:\n{office_path.name}",
            parent=self,
        )

    def _refresh_baseline_status(self) -> None:
        baseline = self.db.get_baseline("office")
        if baseline is None:
            self.baseline_status.configure(text="Эталон: не сохранён")
            return
        meta = baseline.get("meta", {}) or {}
        self.baseline_status.configure(
            text=(
                f"Эталон: {as_text(meta.get('collected_at')) or '—'} · "
                f"{as_text(meta.get('router_identity')) or 'MikroTik'}"
            ),
            text_color=GREEN,
        )

    def _on_export(self) -> None:
        if self._last_comparison is None:
            messagebox.showwarning("Экспорт", "Сначала выполните сравнение.", parent=self)
            return
        office, other, rows = self._last_comparison
        export_format = self.export_format.get()
        suffix = {"TXT": ".txt", "HTML": ".html", "PDF": ".pdf"}[export_format]
        default_name = (
            "network_doctor_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + suffix
        )
        filetypes = {
            "TXT": [("Текстовый файл", "*.txt")],
            "HTML": [("HTML-отчёт", "*.html")],
            "PDF": [("PDF-отчёт", "*.pdf")],
        }[export_format] + [("Все файлы", "*.*")]
        target = filedialog.asksaveasfilename(
            parent=self,
            title=f"Экспорт {export_format}",
            defaultextension=suffix,
            initialfile=default_name,
            filetypes=filetypes,
        )
        if not target:
            return
        try:
            if export_format == "TXT":
                Path(target).write_text(
                    export_text(office, other, rows), encoding="utf-8"
                )
            else:
                from .report import build_report_context, write_html, write_pdf

                context = build_report_context(
                    office,
                    rows,
                    baseline=self._last_baseline,
                    compare_label=self._last_compare_label,
                )
                meta = office.get("meta", {}) or {}
                series = self.db.noise_series(
                    hours=24,
                    snapshot_type=as_text(meta.get("snapshot_type")) or "office",
                )
                if export_format == "HTML":
                    write_html(target, context, series)
                else:
                    write_pdf(target, context, series)
        except Exception as exc:
            messagebox.showerror(
                "Экспорт", f"Не удалось сохранить файл:\n{exc}", parent=self
            )
            return
        self._log(f"Экспорт {export_format} выполнен: {target}")
        messagebox.showinfo("Экспорт", f"Сохранено:\n{target}", parent=self)

    def _render_comparison(self, rows: list[MetricRow]) -> None:
        for child in self.table.winfo_children():
            child.destroy()

        baseline_mode = self._last_baseline is not None
        headers = [
            "Параметр",
            "Текущий" if baseline_mode else "Офис",
            "Эталон" if baseline_mode else "Лаборатория",
            "Разница",
        ]
        for index, header in enumerate(headers):
            label = ctk.CTkLabel(
                self.table,
                text=header,
                anchor="w",
                height=38,
                text_color=MUTED,
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            )
            label.grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(12, 8) if index == 0 else 8,
                pady=(4, 6),
            )

        if not rows:
            prompt = (
                "Выберите снимок и эталон, затем нажмите «Сравнить»."
                if baseline_mode
                else "Выберите два снимка и нажмите «Сравнить»."
            )
            empty = ctk.CTkLabel(
                self.table,
                text=prompt,
                anchor="w",
                text_color=MUTED,
                font=ctk.CTkFont(family="Segoe UI", size=13),
            )
            empty.grid(row=1, column=0, columnspan=4, sticky="w", padx=12, pady=18)
            return

        for row_index, row in enumerate(rows, start=1):
            background = PANEL_ALT if row_index % 2 else "#0f1a2d"
            row_frame = ctk.CTkFrame(self.table, fg_color=background, corner_radius=8)
            row_frame.grid(
                row=row_index,
                column=0,
                columnspan=4,
                sticky="ew",
                padx=4,
                pady=2,
            )
            row_frame.grid_columnconfigure(0, weight=3)
            row_frame.grid_columnconfigure(1, weight=2)
            row_frame.grid_columnconfigure(2, weight=2)
            row_frame.grid_columnconfigure(3, weight=2)

            values = [row.name, row.office, row.lab, row.difference]
            for column, value in enumerate(values):
                color = MUTED if column == 0 else TEXT
                if column == 3:
                    color = GREEN if value in {"0", "совпадает"} else AMBER
                label = ctk.CTkLabel(
                    row_frame,
                    text=value,
                    anchor="w",
                    height=34,
                    text_color=color,
                    font=ctk.CTkFont(
                        family="Consolas" if column else "Segoe UI",
                        size=12,
                    ),
                )
                label.grid(
                    row=0,
                    column=column,
                    sticky="ew",
                    padx=(12, 8) if column == 0 else 8,
                    pady=4,
                )

    def _run(
        self,
        title: str,
        action: Callable[[], Any],
        success: Callable[[Any], None],
    ) -> None:
        if self._busy:
            messagebox.showwarning("Network Doctor", "Операция уже выполняется.", parent=self)
            return
        self._busy = True
        self._set_busy(True)
        self._set_status(title, AMBER)
        self._log(title)

        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                self.after(0, lambda message=message: self._failed(message))
            else:
                self.after(0, lambda result=result: self._succeeded(result, success))

        threading.Thread(target=worker, daemon=True).start()

    def _succeeded(self, result: Any, callback: Callable[[Any], None]) -> None:
        self._busy = False
        self._set_busy(False)
        self._set_status("OK", GREEN)
        try:
            callback(result)
        except Exception as exc:
            self._failed(str(exc) or exc.__class__.__name__)

    def _failed(self, message: str) -> None:
        self._busy = False
        self._set_busy(False)
        self._set_status("Error", RED)
        self._log(f"Ошибка: {message}")
        messagebox.showerror("Ошибка", message, parent=self)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (
            self.test_button,
            self.collect_button,
            self.refresh_button,
            self.compare_button,
            self.demo_button,
            self.baseline_button,
        ):
            button.configure(state=state)
        export_state = "normal" if not busy and self._last_comparison else "disabled"
        self.export_button.configure(state=export_state)

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.configure(text=text, text_color=color)

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    @staticmethod
    def _set_text(widget: ctk.CTkTextbox, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")
