from __future__ import annotations

import os
from pathlib import Path


def app_data_dir() -> Path:
    """Return the per-user directory for Network Doctor state."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        path = base / "NetworkDoctor"
    else:
        path = Path.home() / ".local" / "share" / "network-doctor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return app_data_dir() / "history.sqlite"


def profiles_path() -> Path:
    return app_data_dir() / "profiles.json"


def reports_dir() -> Path:
    path = app_data_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path
