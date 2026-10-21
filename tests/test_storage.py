from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from network_doctor.storage import SnapshotError, SnapshotStore


def snapshot(snapshot_type: str = "office") -> dict:
    return {
        "meta": {
            "snapshot_type": snapshot_type,
            "snapshot_label": "Офис" if snapshot_type == "office" else "Лаборатория",
            "collected_at": "2026-10-22T12:00:00+03:00",
        },
        "registration_table": [],
        "wireless_interfaces": [],
        "logs": [],
        "disconnect_events": [],
        "spectral_scan": {"noise_floor_avg": None, "samples": 0},
        "warnings": [],
    }


class StorageTests(unittest.TestCase):
    def test_save_load_and_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            first = store.save(snapshot("office"))
            second = store.save(snapshot("lab"))
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertEqual(store.load(first)["meta"]["snapshot_type"], "office")
            self.assertEqual(len(store.list()), 2)
            self.assertEqual(len(store.list("office")), 1)
            self.assertEqual(len(store.list("lab")), 1)

    def test_duplicate_names_are_unique(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            first = store.save(snapshot())
            second = store.save(snapshot())
            self.assertNotEqual(first, second)

    def test_invalid_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text("not json", encoding="utf-8")
            store = SnapshotStore(directory)
            with self.assertRaises(SnapshotError):
                store.load(path)


if __name__ == "__main__":
    unittest.main()
