"""Generate the README screenshots from the live UI.

The script starts the real application, fills every tab with representative
data and stores a PNG of the window for each tab.

Requirements (development only, not part of the runtime dependencies):

    python -m pip install pillow

Usage:

    python tools/screenshots.py

Output: docs/screenshots/*.png
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "docs" / "screenshots"
SAMPLE = ROOT / "examples" / "office_snapshot.json"

TABS = [
    ("Подключение", "connection.png"),
    ("Диагностика", "diagnostics.png"),
    ("Здоровье", "health.png"),
    ("Мониторинг", "monitoring.png"),
    ("Сравнение", "comparison.png"),
]


def pump(app, cycles: int = 25) -> None:
    """Let Tk process pending events and redraw the window."""
    for _ in range(cycles):
        app.update()
        time.sleep(0.03)


def grab(app, target: Path) -> None:
    pump(app)
    box = (
        app.winfo_rootx(),
        app.winfo_rooty(),
        app.winfo_rootx() + app.winfo_width(),
        app.winfo_rooty() + app.winfo_height(),
    )
    from PIL import ImageGrab

    image = ImageGrab.grab(bbox=box)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    print(f"saved {target.relative_to(ROOT)} {image.size}")


def scroll_tab(app, bodies: dict, index: int, fraction: float) -> None:
    """Scroll the body of a tab so the interesting block is visible."""
    name = list(app.tabview._tab_dict.keys())[index]
    body = bodies.get(app.tabview._tab_dict[name])
    if body is None:
        return
    body._parent_canvas.yview_moveto(fraction)
    pump(app, 10)


def timeline_points() -> list:
    from network_doctor.db import TimelinePoint

    start = datetime(2026, 10, 21, 8, 0, 0)
    points = []
    for index in range(24):
        signal = -58 - 12 * (index / 23) + 3 * ((index % 5) - 2)
        snr = 40 - 8 * (index / 23)
        ccq = 96 - 10 * (index / 23)
        points.append(
            TimelinePoint(
                collected_at=(start + timedelta(hours=index)).isoformat(),
                signal_dbm=round(signal, 1),
                snr_db=round(snr, 1),
                ccq=round(ccq, 1),
                noise=-92.0,
                health_score=int(96 - 14 * (index / 23)),
                health_state="green" if index < 18 else "yellow",
            )
        )
    return points


def main() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="nd-screenshots-"))
    try:
        from network_doctor import ui as ui_module
        from network_doctor.compare import compare_snapshots, snapshot_summary
        from network_doctor.demo import create_demo_snapshots
        from network_doctor.diagnose import diagnose_no_internet
        from network_doctor.storage import SnapshotStore

        store = SnapshotStore(workspace / "snapshots")
        ui_module.SnapshotStore = lambda *args, **kwargs: store
        ui_module.database_path = lambda: workspace / "history.sqlite"

        bodies: dict = {}
        original_body = ui_module.NetworkDoctorApp._tab_body

        def remember_body(self, tab):
            body = original_body(self, tab)
            bodies[tab] = body
            return body

        ui_module.NetworkDoctorApp._tab_body = remember_body

        app = ui_module.NetworkDoctorApp()
        app.geometry("1240x840+30+10")
        try:
            app.attributes("-topmost", True)
        except Exception:
            pass
        app.lift()
        pump(app, 40)

        sample = json.loads(SAMPLE.read_text(encoding="utf-8"))

        # Вкладка «Подключение»: заполненная форма профиля.
        app.connection_type.set("API")
        app.host_entry.delete(0, "end")
        app.host_entry.insert(0, "10.10.10.1")
        app.port_entry.delete(0, "end")
        app.port_entry.insert(0, "8728")
        app.user_entry.delete(0, "end")
        app.user_entry.insert(0, "admin")

        # Вкладка «Диагностика»: вердикт по инциденту и сводка снимка.
        app.snapshot_type.set("Офис")
        diagnosis = diagnose_no_internet(sample)
        app._set_text(
            app.summary_box,
            diagnosis.as_text() + "\n\n" + snapshot_summary(sample),
        )

        # Вкладка «Сравнение»: офис против лаборатории.
        office_path, lab_path = create_demo_snapshots(store)
        app.show_demo.select()
        app._refresh_snapshot_lists(office_path, lab_path)
        office = store.load(office_path)
        lab = store.load(lab_path)
        rows = compare_snapshots(office, lab)
        app._last_comparison = (office, lab, rows)
        app._render_comparison(rows)
        app.export_button.configure(state="normal")
        app.comparison_note.configure(
            text="Демо-снимки: офис (норма) ↔ лаборатория (инцидент).",
            text_color=ui_module.MUTED,
        )

        # Вкладка «Здоровье»: Health Score клиентов.
        app._render_health(office)

        # Вкладка «Мониторинг»: статус сторожа и график клиента.
        app.monitor_status.configure(
            text="ICMP каждые 30 с · офис · потери 7,6 % · джиттер 80,2 мс"
        )
        mac = "50:ff:20:00:00:01"
        app.timeline_client.set(mac)
        app.timeline_client.configure(values=[mac])
        app._render_timeline(mac, timeline_points())
        pump(app, 20)

        scroll_fractions = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.55}
        for index, (name, filename) in enumerate(TABS):
            app.tabview.set(name)
            pump(app, 15)
            scroll_tab(app, bodies, index, scroll_fractions.get(index, 0.0))
            grab(app, OUT_DIR / filename)

        app._on_close()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
