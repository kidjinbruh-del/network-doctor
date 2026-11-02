from __future__ import annotations

import os
from pathlib import Path


def app_data_dir() -> Path:
    """Return the per-user directory for Network Doctor state."""
    if os.environ.get('NETWORK_DOCTOR_DATA_DIR'):
        path=Path(os.environ['NETWORK_DOCTOR_DATA_DIR']).expanduser().resolve()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        path = base / "NetworkDoctor"
    else:
        path = Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local'/'share'))) / 'network-doctor'
    path.mkdir(parents=True, exist_ok=True,mode=0o700)
    return path


def database_path() -> Path:
    return app_data_dir() / "history.sqlite"


def profiles_path() -> Path:
    return app_data_dir() / "profiles.json"


def reports_dir() -> Path:
    path = app_data_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path
