from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .paths import profiles_path

_SERVICE = "network-doctor"


@dataclass
class ConnectionProfile:
    """Serializable connection settings without the secret itself."""

    name: str
    connection_type: str = "api"
    host: str = ""
    port: int = 8728
    username: str = "admin"
    ssl_verify: bool = False
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_key: str = ""
    ssh_remote_host: str = ""
    ssh_remote_port: int = 8728
    save_password: bool = False


class ProfileStore:
    """JSON profile store with optional secrets in the OS keyring."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else profiles_path()

    def load(self) -> list[ConnectionProfile]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        values = data.get("profiles", []) if isinstance(data, dict) else data
        result: list[ConnectionProfile] = []
        allowed = {item.name for item in fields(ConnectionProfile)}
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            clean = {key: value for key, value in item.items() if key in allowed}
            if not clean.get("name"):
                continue
            try:
                result.append(ConnectionProfile(**clean))
            except TypeError:
                continue
        return result

    def save(self, profiles: list[ConnectionProfile]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"profiles": [asdict(profile) for profile in profiles]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def upsert(self, profile: ConnectionProfile) -> None:
        profiles = [item for item in self.load() if item.name != profile.name]
        profiles.append(profile)
        self.save(sorted(profiles, key=lambda item: item.name.lower()))

    def delete(self, name: str) -> None:
        profiles = self.load()
        target = next((item for item in profiles if item.name == name), None)
        if target is not None:
            delete_password(target)
        self.save([item for item in profiles if item.name != name])

    def get(self, name: str) -> ConnectionProfile | None:
        return next((item for item in self.load() if item.name == name), None)


def set_password(profile: ConnectionProfile, password: str) -> bool:
    """Store a profile password in the OS keyring. Returns success state."""
    if not password:
        delete_password(profile)
        return True
    try:
        import keyring

        keyring.set_password(_SERVICE, _key(profile), password)
        return True
    except Exception:
        return False


def get_password(profile: ConnectionProfile) -> str:
    try:
        import keyring

        return keyring.get_password(_SERVICE, _key(profile)) or ""
    except Exception:
        return ""


def delete_password(profile: ConnectionProfile) -> None:
    try:
        import keyring

        keyring.delete_password(_SERVICE, _key(profile))
    except Exception:
        pass


def _key(profile: ConnectionProfile) -> str:
    return f"{profile.name}|{profile.host}|{profile.username}"
