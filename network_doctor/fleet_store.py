"""Transactional equipment profiles, samples and incident lifecycle."""

from __future__ import annotations
import json
import sqlite3
import threading
import time
from pathlib import Path
from .equipment import DeviceProfile, derive, findings


class FleetStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS equipment(id TEXT PRIMARY KEY,profile TEXT NOT NULL,failures INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS equipment_samples(id INTEGER PRIMARY KEY,device_id TEXT NOT NULL REFERENCES equipment(id),at REAL NOT NULL,ok INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS equipment_sample_device ON equipment_samples(device_id,id DESC);
        CREATE TABLE IF NOT EXISTS equipment_incidents(id INTEGER PRIMARY KEY,device_id TEXT NOT NULL REFERENCES equipment(id),code TEXT NOT NULL,severity TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,opened REAL NOT NULL,updated REAL NOT NULL,occurrences INTEGER NOT NULL DEFAULT 1);
        CREATE UNIQUE INDEX IF NOT EXISTS equipment_incident_active ON equipment_incidents(device_id,code) WHERE status IN ('open','acknowledged');
        CREATE TABLE IF NOT EXISTS equipment_timeline(id INTEGER PRIMARY KEY,incident_id INTEGER NOT NULL REFERENCES equipment_incidents(id),at REAL NOT NULL,action TEXT NOT NULL,note TEXT NOT NULL);
        """)
        self.db.commit()

    def profiles(self):
        with self.lock:
            return [
                DeviceProfile.from_dict(json.loads(row["profile"]))
                for row in self.db.execute("SELECT profile FROM equipment ORDER BY id")
            ]

    def upsert(self, profile):
        profile.validate()
        with self.lock, self.db:
            if not self.db.execute(
                "SELECT 1 FROM equipment WHERE id=?", (profile.id,)
            ).fetchone():
                if (
                    self.db.execute("SELECT count(*) FROM equipment").fetchone()[0]
                    >= 64
                ):
                    raise ValueError("Лимит: 64 устройства")
            self.db.execute(
                "INSERT INTO equipment(id,profile) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET profile=excluded.profile",
                (profile.id, json.dumps(profile.public(), ensure_ascii=False)),
            )

    def profile(self, ident):
        return next((p for p in self.profiles() if p.id == ident), None)

    def _latest(self, ident, success=False):
        query = (
            "SELECT payload FROM equipment_samples WHERE device_id=?"
            + (" AND ok=1" if success else "")
            + " ORDER BY id DESC LIMIT 1"
        )
        row = self.db.execute(query, (ident,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def _timeline(self, ident, action, note=""):
        self.db.execute(
            "INSERT INTO equipment_timeline(incident_id,at,action,note) VALUES(?,?,?,?)",
            (ident, time.time(), action, note),
        )

    def _open(self, ident, code, severity, title, at):
        row = self.db.execute(
            "SELECT id FROM equipment_incidents WHERE device_id=? AND code=? AND status IN ('open','acknowledged')",
            (ident, code),
        ).fetchone()
        if row:
            self.db.execute(
                "UPDATE equipment_incidents SET updated=?,occurrences=occurrences+1 WHERE id=?",
                (at, row["id"]),
            )
        else:
            cursor = self.db.execute(
                "INSERT INTO equipment_incidents(device_id,code,severity,title,status,opened,updated) VALUES(?,?,?,?,?,?,?)",
                (ident, code, severity, title, "open", at, at),
            )
            self._timeline(cursor.lastrowid, "opened", title)

    def _resolve(self, ident, code, note):
        row = self.db.execute(
            "SELECT id FROM equipment_incidents WHERE device_id=? AND code=? AND status IN ('open','acknowledged')",
            (ident, code),
        ).fetchone()
        if row:
            self.db.execute(
                "UPDATE equipment_incidents SET status='resolved',updated=? WHERE id=?",
                (time.time(), row["id"]),
            )
            self._timeline(row["id"], "resolved", note)

    def record(self, profile, snapshot):
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            prior = self._latest(profile.id, True)
            if snapshot["device_id"] != profile.id:
                raise ValueError("Снимок относится к другому устройству")
            if snapshot.get("ok"):
                snapshot = derive(snapshot, prior)
                self.db.execute(
                    "UPDATE equipment SET failures=0 WHERE id=?", (profile.id,)
                )
                self._resolve(profile.id, "unreachable", "Устройство снова отвечает")
                alerts = findings(snapshot, profile.expected_ports)
                for code, severity, title in alerts:
                    self._open(profile.id, code, severity, title, snapshot["at"])
                present = {code for code, _, _ in alerts}
                for port in snapshot.get("interfaces", []):
                    prefix = "port:" + str(port["index"])
                    evidence = {
                        "down": port.get("admin_status") is not None
                        and port.get("oper_status") is not None,
                        "errors": "rx_errors_delta" in port
                        and "tx_errors_delta" in port,
                        "load": "utilization_pct" in port,
                    }
                    for suffix, known in evidence.items():
                        code = prefix + ":" + suffix
                        if known and code not in present:
                            self._resolve(
                                profile.id,
                                code,
                                "Новые показатели подтверждают восстановление",
                            )
            else:
                self.db.execute(
                    "UPDATE equipment SET failures=failures+1 WHERE id=?", (profile.id,)
                )
                failures = self.db.execute(
                    "SELECT failures FROM equipment WHERE id=?", (profile.id,)
                ).fetchone()[0]
                if failures >= 2:
                    self._open(
                        profile.id,
                        "unreachable",
                        "critical",
                        "Нет ответа: " + profile.name,
                        snapshot["at"],
                    )
            self.db.execute(
                "INSERT INTO equipment_samples(device_id,at,ok,payload) VALUES(?,?,?,?)",
                (
                    profile.id,
                    snapshot["at"],
                    int(snapshot["ok"]),
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
            self.db.execute(
                "DELETE FROM equipment_samples WHERE device_id=? AND id NOT IN (SELECT id FROM equipment_samples WHERE device_id=? ORDER BY id DESC LIMIT 100)",
                (profile.id, profile.id),
            )
        return snapshot

    def state(self):
        with self.lock:
            return [
                {
                    "profile": profile.public(),
                    "latest": self._latest(profile.id),
                    "last_success": self._latest(profile.id, True),
                }
                for profile in self.profiles()
            ]

    def history(self, ident):
        with self.lock:
            return [
                json.loads(row["payload"])
                for row in self.db.execute(
                    "SELECT payload FROM equipment_samples WHERE device_id=? ORDER BY id DESC LIMIT 100",
                    (ident,),
                )
            ]

    def incidents(self):
        with self.lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM equipment_incidents ORDER BY updated DESC LIMIT 500"
                )
            ]
            for row in rows:
                row["timeline"] = [
                    dict(v)
                    for v in self.db.execute(
                        "SELECT at,action,note FROM equipment_timeline WHERE incident_id=? ORDER BY id",
                        (row["id"],),
                    )
                ]
            return rows

    def change_incident(self, ident, action, note=""):
        if action not in ("acknowledge", "resolve", "comment"):
            raise ValueError("Неизвестное действие")
        note = str(note).strip()[:2000]
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT status FROM equipment_incidents WHERE id=?", (ident,)
            ).fetchone()
            if not row:
                raise ValueError("Инцидент не найден")
            if action == "acknowledge":
                if row["status"] != "open":
                    raise ValueError("Инцидент уже принят или закрыт")
                self.db.execute(
                    "UPDATE equipment_incidents SET status='acknowledged',updated=? WHERE id=?",
                    (time.time(), ident),
                )
            elif action == "resolve":
                if row["status"] == "resolved":
                    raise ValueError("Инцидент уже закрыт")
                self.db.execute(
                    "UPDATE equipment_incidents SET status='resolved',updated=? WHERE id=?",
                    (time.time(), ident),
                )
            elif not note:
                raise ValueError("Комментарий пуст")
            self._timeline(ident, action, note)

    def close(self):
        with self.lock:
            self.db.close()
