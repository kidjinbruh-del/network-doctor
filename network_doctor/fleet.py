"""Bounded fleet polling; no discovery or configuration changes on startup."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from .snmp_transport import collect, EquipmentError


class FleetService:
    def __init__(self, store, collector=collect):
        self.store = store
        self.collector = collector
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def poll(self, ident=None, overrides=None):
        if not self.lock.acquire(blocking=False):
            raise ValueError("Опрос уже выполняется")
        try:
            profiles = [
                p
                for p in self.store.profiles()
                if p.enabled and (ident is None or p.id == ident)
            ]
            if not profiles:
                raise ValueError("Нет выбранных включённых устройств")

            def one(profile):
                if self.stop_event.is_set():
                    return None
                try:
                    result = (
                        collect(
                            profile,
                            overrides if ident else None,
                            stop_event=self.stop_event,
                        )
                        if self.collector is collect
                        else self.collector(profile, overrides if ident else None)
                    )
                except Exception as error:
                    message = (
                        str(error)
                        if isinstance(error, EquipmentError)
                        else "Ошибка опроса. Проверьте настройки транспорта и доступность устройства"
                    )
                    result = {
                        "device_id": profile.id,
                        "at": time.time(),
                        "ok": False,
                        "error": message,
                        "host": profile.host,
                        "name": profile.name,
                    }
                return (
                    None
                    if self.stop_event.is_set()
                    else self.store.record(profile, result)
                )

            with ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="nd-equipment"
            ) as pool:
                return [row for row in pool.map(one, profiles) if row is not None]
        finally:
            self.lock.release()

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, interval=60):
        if self.running:
            raise ValueError("Мониторинг уже запущен")
        interval = int(interval)
        if not 15 <= interval <= 3600:
            raise ValueError("Интервал: 15–3600 секунд")
        self.stop_event.clear()

        def loop():
            while not self.stop_event.is_set():
                try:
                    self.poll()
                except ValueError:
                    pass
                if self.stop_event.wait(interval):
                    return

        self.thread = threading.Thread(
            target=loop, name="nd-fleet-monitor", daemon=True
        )
        self.thread.start()

    def stop(self, timeout=1):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout)

    def resume_manual(self):
        if self.running:
            raise ValueError("Сначала остановите мониторинг")
        self.stop_event.clear()
