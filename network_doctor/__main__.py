"""Headless equipment CLI; imports GUI only when explicitly requested."""

import argparse
import json
from pathlib import Path
from .equipment import DeviceProfile, ENTERPRISES
from .fleet_store import FleetStore
from .fleet import FleetService
from .paths import app_data_dir
from .fleet_report import export


def main():
    parser = argparse.ArgumentParser(
        description="Network Doctor equipment inventory (read-only)"
    )
    parser.add_argument("--data-dir", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("vendors")
    add = commands.add_parser("add")
    add.add_argument("profile", type=Path)
    poll = commands.add_parser("poll")
    poll.add_argument("--device")
    commands.add_parser("status")
    history = commands.add_parser("history")
    history.add_argument("--device", required=True)
    report = commands.add_parser("export")
    report.add_argument("path", type=Path)
    monitor = commands.add_parser("monitor")
    monitor.add_argument("--interval", type=int, default=60)
    monitor.add_argument("--metrics-port", type=int, default=8776)
    commands.add_parser("gui")
    args = parser.parse_args()
    if args.command == "vendors":
        print(
            json.dumps(
                {
                    "identification": ENTERPRISES,
                    "scope": "Standard MIB availability depends on model/firmware; unmanaged devices: ICMP only",
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0
    if args.command == "gui":
        if args.data_dir:
            import os

            os.environ["NETWORK_DOCTOR_DATA_DIR"] = str(args.data_dir.resolve())
        from .ui import NetworkDoctorApp

        NetworkDoctorApp().mainloop()
        return 0
    store = FleetStore((args.data_dir or app_data_dir()) / "equipment.sqlite")
    try:
        if args.command == "add":
            if args.profile.stat().st_size > 65536:
                raise ValueError("Файл профиля слишком велик")
            profile = DeviceProfile.from_dict(
                json.loads(args.profile.read_text(encoding="utf-8"))
            )
            store.upsert(profile)
            result = {"saved": profile.id}
        elif args.command == "poll":
            result = FleetService(store).poll(args.device)
        elif args.command == "export":
            result = {"path": str(export(store, args.path))}
        elif args.command == "history":
            result = store.history(args.device)
        elif args.command == "monitor":
            import os
            from .metrics import build_server

            if not 1 <= args.metrics_port <= 65535:
                raise ValueError("Некорректный порт метрик")
            server = build_server(
                store,
                os.environ.get("ND_METRICS_TOKEN", ""),
                args.metrics_port,
                max_age=max(90, args.interval * 3),
            )
            service = FleetService(store)
            try:
                service.start(args.interval)
                print(
                    "Metrics: http://127.0.0.1:" + str(args.metrics_port) + "/metrics",
                    flush=True,
                )
                server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
                service.stop(timeout=5)
            return 0
        else:
            result = {"devices": store.state(), "incidents": store.incidents()}
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return (
            2 if args.command == "poll" and any(not row["ok"] for row in result) else 0
        )
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True))
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
