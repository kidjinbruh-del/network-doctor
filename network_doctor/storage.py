from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import as_text, safe_filename

_LABELS = {"office": "Офис", "lab": "Лаборатория"}


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotInfo:
    path: Path
    snapshot_type: str
    label: str
    collected_at: str
    is_demo: bool = False

    @property
    def display(self) -> str:
        prefix = "ДЕМО · " if self.is_demo else ""
        return f"{prefix}{self.label} · {self.collected_at} · {self.path.name}"


class SnapshotStore:
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = (
            Path(base_dir)
            if base_dir is not None
            else Path(__file__).resolve().parent.parent / "snapshots"
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.demo_dir = self.base_dir / "demo"
        self.demo_dir.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: dict[str, Any], demo: bool = False) -> Path:
        meta = snapshot.setdefault("meta", {})
        if demo:
            meta["demo"] = True
        snapshot_type = as_text(meta.get("snapshot_type")) or "office"
        stamp = safe_filename(as_text(meta.get("collected_at")) or "snapshot")
        directory = self.demo_dir if demo else self.base_dir
        path = directory / f"{stamp}_{snapshot_type}.json"
        counter = 2
        while path.exists():
            path = directory / f"{stamp}_{snapshot_type}_{counter}.json"
            counter += 1

        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def clear_demo(self) -> None:
        for path in self.demo_dir.glob("*.json"):
            try:
                path.unlink()
            except OSError:
                pass

    def load(self, path: str | Path) -> dict[str, Any]:
        source = Path(path)
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SnapshotError(f"Не удалось прочитать снимок {source.name}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("meta"), dict):
            raise SnapshotError(f"Файл {source.name} не похож на снимок Network Doctor.")
        return data

    def list(
        self,
        snapshot_type: str | None = None,
        include_demo: bool = False,
    ) -> list[SnapshotInfo]:
        items: list[SnapshotInfo] = []
        paths = list(self.base_dir.glob("*.json"))
        if include_demo:
            paths.extend(self.demo_dir.glob("*.json"))

        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                meta = data.get("meta", {})
                current_type = as_text(meta.get("snapshot_type")) or "office"
                if snapshot_type is not None and current_type != snapshot_type:
                    continue
                collected_at = as_text(meta.get("collected_at")) or path.stem
                items.append(
                    SnapshotInfo(
                        path=path,
                        snapshot_type=current_type,
                        label=_LABELS.get(current_type, current_type),
                        collected_at=collected_at,
                        is_demo=path.parent == self.demo_dir,
                    )
                )
            except (OSError, ValueError, AttributeError):
                continue
        items.sort(key=lambda item: item.path.stat().st_mtime, reverse=True)
        return items
