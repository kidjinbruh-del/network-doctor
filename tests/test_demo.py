from __future__ import annotations

import tempfile
import unittest

from network_doctor.compare import compare_snapshots
from network_doctor.demo import create_demo_snapshots
from network_doctor.schema import validate_snapshot
from network_doctor.storage import SnapshotStore


class DemoTests(unittest.TestCase):
    def test_creates_two_demo_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            office_path, lab_path = create_demo_snapshots(store)

            self.assertTrue(office_path.exists())
            self.assertTrue(lab_path.exists())
            self.assertEqual(office_path.parent, store.demo_dir)
            self.assertTrue(store.load(office_path)["meta"]["demo"])
            self.assertTrue(store.load(lab_path)["meta"]["demo"])
            self.assertEqual(len(store.list("office")), 0)
            self.assertEqual(len(store.list("office", include_demo=True)), 1)
            self.assertEqual(len(store.list("lab", include_demo=True)), 1)

    def test_demo_snapshots_use_v2_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            office_path, _ = create_demo_snapshots(store)
            snapshot = store.load(office_path)

            self.assertEqual(snapshot["schema_version"], 2)
            self.assertEqual(validate_snapshot(snapshot), [])
            self.assertEqual(len(snapshot["clients"]), 5)
            self.assertTrue(snapshot["wireless_scan"])
            self.assertEqual(snapshot["health"]["source"], "demo")

    def test_demo_snapshots_can_be_compared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            office_path, lab_path = create_demo_snapshots(store)
            rows = compare_snapshots(
                store.load(office_path),
                store.load(lab_path),
            )

            self.assertEqual(rows[0].name, "Клиенты")
            self.assertEqual(rows[0].office, "5")
            self.assertEqual(rows[0].lab, "5")
            self.assertNotEqual(rows[1].office, rows[1].lab)

    def test_demo_snapshots_are_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            create_demo_snapshots(store)
            create_demo_snapshots(store)

            self.assertEqual(len(list(store.demo_dir.glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
