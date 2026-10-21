from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any

from .utils import as_text

_KNOWN = {
    "50:ff:20": "Keenetic",
    "52:54:00": "QEMU/KVM",
    "bc:24:11": "Proxmox VE",
    "04:f4:1c": "MikroTik",
}

_OUI_PATHS = (
    Path("/usr/share/ieee-data/oui.txt"),
    Path("/usr/share/misc/oui.txt"),
    Path("/usr/local/share/ieee-data/oui.txt"),
)

_lock = Lock()
_loaded = False
_oui: dict[str, str] = {}


def vendor_for_mac(mac: Any) -> str:
    """Return a vendor name from a small cache or the system IEEE OUI file."""
    normalized = as_text(mac).strip().lower().replace("-", ":")
    prefix = normalized[:8]
    known = _KNOWN.get(prefix)
    if known:
        return known
    _load_oui()
    return _oui.get(prefix, "")


def _load_oui() -> None:
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        for path in _OUI_PATHS:
            if not path.is_file():
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if "(hex)" not in line:
                            continue
                        fields = line.split("\t")
                        if len(fields) < 3:
                            continue
                        hex_prefix = fields[0].strip().lower()
                        if len(hex_prefix) != 6:
                            continue
                        prefix = ":".join(
                            hex_prefix[index : index + 2]
                            for index in range(0, 6, 2)
                        )
                        _oui[prefix] = fields[2].strip()
            except OSError:
                continue
            break
        _loaded = True
