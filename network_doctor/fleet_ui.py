"""Equipment workspace. Worker threads communicate with Tk through a queue."""

import json
import queue
import threading
from datetime import datetime
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
from .equipment import DeviceProfile
from .fleet import FleetService
from .fleet_store import FleetStore
from .paths import app_data_dir
from .snmp_transport import Secrets, save_credentials
from .fleet_report import export

PROTOCOLS = {
    "SNMPv3 · шифрование": "snmpv3",
    "SNMPv2c · старое оборудование": "snmpv2c",
    "ICMP · только доступность": "icmp",
}


class FleetPanel(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Equipment.Treeview",
            background="#111827",
            fieldbackground="#111827",
            foreground="#e5edf8",
            rowheight=28,
            borderwidth=0,
        )
        style.configure(
            "Equipment.Treeview.Heading",
            background="#24324a",
            foreground="#e5edf8",
            padding=7,
            relief="flat",
            borderwidth=0,
            bordercolor="#24324a",
            lightcolor="#24324a",
            darkcolor="#24324a",
        )
        style.map("Equipment.Treeview", background=[("selected", "#234c85")])
        self.store = FleetStore(app_data_dir() / "equipment.sqlite")
        self.service = FleetService(self.store)
        self.events = queue.SimpleQueue()
        self.closed = False
        self.busy = False
        self.entries = {}
        self.table_vars = {}
        self.selected = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.status = ctk.CTkLabel(
            self,
            text="Добавьте устройство по IP. Автопоиск и опрос при запуске не выполняются.",
            anchor="w",
            wraplength=950,
        )
        self.status.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=8)
        editor = ctk.CTkScrollableFrame(self, width=300, fg_color="#162033")
        editor.grid(row=1, column=0, sticky="ns", padx=(8, 12), pady=8)
        editor.grid_columnconfigure(0, weight=1)
        self.selector = ctk.CTkComboBox(
            editor, values=["Новый профиль"], command=self.load, width=280
        )
        self.selector.grid(row=0, column=0, sticky="ew", pady=6)
        row = 1
        for key, label, default in [
            ("id", "ID (латиница)", "switch-1"),
            ("name", "Название", "Коммутатор"),
            ("host", "IP / DNS", ""),
            ("port", "SNMP порт", "161"),
            ("username", "SNMPv3 пользователь", ""),
        ]:
            ctk.CTkLabel(editor, text=label, anchor="w").grid(
                row=row, column=0, sticky="ew"
            )
            row += 1
            entry = ctk.CTkEntry(editor, width=280)
            entry.insert(0, default)
            entry.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            row += 1
            self.entries[key] = entry
        self.protocol = ctk.CTkComboBox(editor, values=list(PROTOCOLS), width=280)
        self.protocol.set(next(iter(PROTOCOLS)))
        self.protocol.grid(row=row, column=0, sticky="ew", pady=6)
        row += 1
        self.auth = ctk.CTkComboBox(editor, values=["sha256", "sha1"])
        self.auth.set("sha256")
        self.auth.grid(row=row, column=0, sticky="ew", pady=6)
        row += 1
        for key, label in [
            ("auth_secret", "Community (v2c) / auth password (v3)"),
            ("privacy_secret", "SNMPv3 privacy password · AES-128"),
        ]:
            ctk.CTkLabel(editor, text=label, anchor="w").grid(
                row=row, column=0, sticky="ew"
            )
            row += 1
            entry = ctk.CTkEntry(editor, show="*")
            entry.grid(row=row, column=0, sticky="ew", pady=6)
            self.entries[key] = entry
            row += 1
        self.keyring = ctk.CTkCheckBox(editor, text="Сохранить секреты в keyring")
        self.keyring.grid(row=row, column=0, sticky="w", pady=6)
        row += 1
        ctk.CTkLabel(
            editor,
            text="Без этой отметки поля действуют только для\nручного опроса выбранного устройства.\nДля фонового опроса: keyring или окружение.",
            justify="left",
            text_color="#9cabbd",
        ).grid(row=row, column=0, sticky="ew")
        row += 1
        for key, label in [
            ("site", "Площадка"),
            ("owner", "Ответственный"),
            ("expected_ports", "Ожидаемые ifIndex через запятую"),
        ]:
            ctk.CTkLabel(editor, text=label, anchor="w").grid(
                row=row, column=0, sticky="ew"
            )
            row += 1
            entry = ctk.CTkEntry(editor, width=280)
            entry.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            self.entries[key] = entry
            row += 1
        for key, default in [
            ("community_env", "ND_SNMP_COMMUNITY"),
            ("auth_env", "ND_SNMP_AUTH"),
            ("privacy_env", "ND_SNMP_PRIVACY"),
        ]:
            ctk.CTkLabel(editor, text="Env: " + key, anchor="w").grid(
                row=row, column=0, sticky="ew"
            )
            row += 1
            entry = ctk.CTkEntry(editor)
            entry.insert(0, default)
            entry.grid(row=row, column=0, sticky="ew", pady=4)
            self.entries[key] = entry
            row += 1
        for name in (
            "interfaces",
            "extended_interfaces",
            "arp",
            "bridge_ports",
            "forwarding",
            "lldp",
            "vlans",
        ):
            check = ctk.CTkCheckBox(editor, text=name)
            if name in ("interfaces", "extended_interfaces"):
                check.select()
            check.grid(row=row, column=0, sticky="w", pady=4)
            row += 1
            self.table_vars[name] = check
        self.enabled = ctk.CTkCheckBox(editor, text="Устройство включено")
        self.enabled.select()
        self.enabled.grid(row=row, column=0, sticky="w", pady=8)
        row += 1
        ctk.CTkButton(editor, text="Сохранить профиль", command=self.save).grid(
            row=row, column=0, sticky="ew", pady=8
        )
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=1, column=1, sticky="nsew", padx=(0, 8), pady=8)
        content.grid_rowconfigure(2, weight=1)
        content.grid_columnconfigure(0, weight=1)
        toolbar = ctk.CTkFrame(content, fg_color="#162033")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for index, (label, fn) in enumerate(
            [
                ("Опросить выбранное", self.poll_selected),
                ("Опросить все", self.poll_all),
                ("Экспорт", self.export_report),
            ]
        ):
            ctk.CTkButton(toolbar, text=label, command=fn, width=135).grid(
                row=0, column=index, padx=4, pady=6
            )
        interval_box = ctk.CTkFrame(toolbar, fg_color="transparent")
        interval_box.grid(row=1, column=0, padx=4, pady=6, sticky="w")
        ctk.CTkLabel(interval_box, text="Сек:").grid(row=0, column=0, padx=4)
        self.interval = ctk.CTkEntry(interval_box, width=65)
        self.interval.insert(0, "60")
        self.interval.grid(row=0, column=1)
        self.toggle = ctk.CTkButton(
            toolbar, text="Мониторинг выключен", command=self.toggle_monitor
        )
        self.toggle.grid(row=1, column=1, columnspan=2, padx=4, pady=6, sticky="ew")
        self.inventory = self.tree(
            content, ("id", "name", "vendor", "state", "site"), height=5
        )
        self.inventory.master.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.inventory.bind("<<TreeviewSelect>>", self.select_device)
        tabs = ctk.CTkTabview(content, fg_color="#162033")
        tabs.grid(row=2, column=0, sticky="nsew")
        ports = tabs.add("Порты")
        ports.grid_rowconfigure(0, weight=1)
        ports.grid_columnconfigure(0, weight=1)
        self.ports = self.tree(
            ports,
            (
                "index",
                "name",
                "link",
                "Mbps",
                "RX Mbps",
                "TX Mbps",
                "load %",
                "errors Δ",
            ),
        )
        self.ports.master.grid(row=0, column=0, sticky="nsew")
        neighbors = tabs.add("Соседи")
        neighbors.grid_rowconfigure(0, weight=1)
        neighbors.grid_columnconfigure(0, weight=1)
        self.neighbors = self.tree(
            neighbors, ("source", "local", "remote", "remote_port", "mac")
        )
        self.neighbors.master.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(
            neighbors,
            text="ARP/FDB — таблицы наблюдений, не доказательство прямого кабельного соединения.",
            wraplength=560,
        ).grid(row=1, column=0, sticky="ew")
        vlans = tabs.add("VLAN")
        vlans.grid_rowconfigure(0, weight=1)
        vlans.grid_columnconfigure(0, weight=1)
        self.vlans = self.tree(vlans, ("id", "name", "egress", "untagged"))
        self.vlans.master.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(
            vlans,
            text="Статические VLAN Q-BRIDGE-MIB; списки содержат номера bridgePort, не ifIndex.",
            wraplength=560,
        ).grid(row=1, column=0, sticky="ew")
        history = tabs.add("История")
        history.grid_rowconfigure(0, weight=1)
        history.grid_columnconfigure(0, weight=1)
        self.history = self.tree(history, ("time", "state", "ports", "details"))
        self.history.master.grid(row=0, column=0, sticky="nsew")
        topology = tabs.add("Данные")
        topology.grid_rowconfigure(0, weight=1)
        topology.grid_columnconfigure(0, weight=1)
        self.details = ctk.CTkTextbox(topology, wrap="word")
        self.details.grid(row=0, column=0, sticky="nsew")
        incidents = tabs.add("Инциденты")
        incidents.grid_rowconfigure(0, weight=1)
        incidents.grid_columnconfigure(0, weight=1)
        self.incidents = self.tree(
            incidents, ("id", "device", "title", "status"), height=8
        )
        self.incidents.master.grid(row=0, column=0, sticky="nsew")
        buttons = ctk.CTkFrame(incidents, fg_color="#162033")
        buttons.grid(row=1, column=0, sticky="ew")
        for index, (label, action) in enumerate(
            [("Принять", "acknowledge"), ("Закрыть", "resolve")]
        ):
            ctk.CTkButton(
                buttons, text=label, command=lambda a=action: self.change_incident(a)
            ).grid(row=0, column=index, padx=4, pady=6)
        self.details_key = None
        self.refresh()
        self.timer = self.after(150, self.drain)

    def tree(self, master, columns, height=12):
        frame = ctk.CTkFrame(master)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=height,
            style="Equipment.Treeview",
        )
        labels = {
            "name": "Название",
            "vendor": "Производитель",
            "state": "Состояние",
            "site": "Площадка",
            "index": "ifIndex",
            "link": "Oper/Admin",
            "device": "Устройство",
            "title": "Проблема",
            "status": "Статус",
            "source": "Источник",
            "local": "Локальный порт",
            "remote": "Сосед / адрес",
            "remote_port": "Удалённый порт",
            "mac": "MAC",
            "egress": "Egress bridgePort",
            "untagged": "Untagged bridgePort",
            "time": "Время",
            "ports": "Портов",
            "details": "Результат",
        }
        for key in columns:
            tree.heading(key, text=labels.get(key, key))
            widths = {
                "index": 55,
                "name": 120,
                "link": 100,
                "Mbps": 65,
                "RX Mbps": 85,
                "TX Mbps": 85,
                "load %": 65,
                "errors Δ": 80,
            }
            tree.column(key, width=widths.get(key, 100), minwidth=45, stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        return tree

    def profile(self):
        value = lambda key: self.entries[key].get().strip()
        return DeviceProfile(
            id=value("id"),
            name=value("name"),
            host=value("host"),
            protocol=PROTOCOLS[self.protocol.get()],
            port=int(value("port")),
            username=value("username"),
            site=value("site"),
            owner=value("owner"),
            auth_protocol=self.auth.get(),
            community_env=value("community_env"),
            auth_env=value("auth_env"),
            privacy_env=value("privacy_env"),
            tables=[name for name, check in self.table_vars.items() if check.get()],
            expected_ports=[
                int(v.strip()) for v in value("expected_ports").split(",") if v.strip()
            ],
            enabled=bool(self.enabled.get()),
        ).validate()

    def secrets(self):
        value = self.entries["auth_secret"].get()
        return Secrets(
            community=value, auth=value, privacy=self.entries["privacy_secret"].get()
        )

    def save(self):
        try:
            profile = self.profile()
            self.store.upsert(profile)
            if self.keyring.get():
                save_credentials(profile, self.secrets())
            self.selected = profile.id
            self.refresh()
            self.status.configure(
                text="Профиль сохранён. Запись в конфигурацию устройства не выполнялась."
            )
        except Exception as error:
            messagebox.showerror("Профиль", str(error), parent=self)

    def load(self, value):
        if value == "Новый профиль":
            self.selected = None
            for key, entry in self.entries.items():
                entry.delete(0, "end")
                defaults = {
                    "name": "Устройство",
                    "port": "161",
                    "community_env": "ND_SNMP_COMMUNITY",
                    "auth_env": "ND_SNMP_AUTH",
                    "privacy_env": "ND_SNMP_PRIVACY",
                }
                entry.insert(0, defaults.get(key, ""))
            self.protocol.set(next(iter(PROTOCOLS)))
            self.keyring.deselect()
            self.refresh_details()
            return
        ident = value.rsplit(" [", 1)[-1].rstrip("]")
        profile = self.store.profile(ident)
        if not profile:
            return
        self.selected = ident
        for key, entry in self.entries.items():
            entry.delete(0, "end")
            if key not in ("auth_secret", "privacy_secret"):
                v = getattr(profile, key, "")
                if key == "expected_ports":
                    v = ",".join(map(str, v))
                entry.insert(0, str(v))
        self.protocol.set(
            next(k for k, v in PROTOCOLS.items() if v == profile.protocol)
        )
        self.auth.set(profile.auth_protocol)
        self.enabled.select() if profile.enabled else self.enabled.deselect()
        self.keyring.deselect()
        for name, check in self.table_vars.items():
            check.select() if name in profile.tables else check.deselect()
        self.refresh_details()

    def select_device(self, event=None):
        selection = self.inventory.selection()
        if selection:
            ident = selection[0]
            profile = self.store.profile(ident)
            if profile:
                self.load(profile.name + " [" + ident + "]")

    def run(self, fn):
        if self.busy or self.service.running:
            messagebox.showwarning(
                "Опрос",
                "Дождитесь завершения опроса или остановите мониторинг",
                parent=self,
            )
            return
        self.busy = True
        self.status.configure(text="Опрос выполняется в фоне…")

        def worker():
            try:
                self.events.put(("done", fn()))
            except Exception as error:
                self.events.put(("error", str(error)))

        threading.Thread(target=worker, name="nd-equipment-ui", daemon=True).start()

    def poll_selected(self):
        try:
            if self.busy or self.service.running:
                raise ValueError("Сначала завершите опрос или остановите мониторинг")
            profile = self.profile()
            self.store.upsert(profile)
            self.selected = profile.id
            secrets = self.secrets()
            self.service.resume_manual()
            self.run(lambda: self.service.poll(profile.id, secrets))
        except Exception as error:
            messagebox.showerror("Опрос", str(error), parent=self)

    def poll_all(self):
        try:
            self.service.resume_manual()
            self.run(lambda: self.service.poll())
        except Exception as error:
            messagebox.showerror("Опрос", str(error), parent=self)

    def toggle_monitor(self):
        try:
            if self.service.running:
                self.service.stop()
            elif self.busy:
                raise ValueError("Сначала завершите ручной опрос")
            else:
                self.service.start(int(self.interval.get()))
            self.toggle.configure(
                text=(
                    "Остановить мониторинг"
                    if self.service.running
                    else "Мониторинг выключен"
                )
            )
        except Exception as error:
            messagebox.showerror("Мониторинг", str(error), parent=self)

    def drain(self):
        if self.closed:
            return
        while not self.events.empty():
            kind, value = self.events.get()
            self.busy = False
            self.status.configure(
                text=(
                    (
                        "Опрос завершён. Ошибок: "
                        + str(sum(not row["ok"] for row in value))
                    )
                    if kind == "done"
                    else value
                )
            )
        self.refresh()
        self.timer = self.after(1500, self.drain)

    def refresh(self):
        rows = self.store.state()
        profiles = [row["profile"] for row in rows]
        self.selector.configure(
            values=["Новый профиль"]
            + [p["name"] + " [" + p["id"] + "]" for p in profiles]
        )
        active = {p["id"] for p in profiles}
        for profile in profiles:
            if profile["id"] == self.selected:
                self.selector.set(f"{profile['name']} [{profile['id']}]")
                break
        for ident in self.inventory.get_children():
            if ident not in active:
                self.inventory.delete(ident)
        for row in rows:
            p = row["profile"]
            latest = row["latest"]
            good = row["last_success"]
            vendor = good.get("system", {}).get("vendor", "—") if good else "—"
            state = (
                "не опрошен"
                if not latest
                else "ответ" if latest["ok"] else "ошибка / старые данные"
            )
            values = (p["id"], p["name"], vendor, state, p["site"])
            if self.inventory.exists(p["id"]):
                self.inventory.item(p["id"], values=values)
            else:
                self.inventory.insert("", "end", iid=p["id"], values=values)
        incidents = self.store.incidents()
        ids = {str(i["id"]) for i in incidents}
        for ident in self.incidents.get_children():
            if ident not in ids:
                self.incidents.delete(ident)
        for incident in incidents:
            ident = str(incident["id"])
            values = (
                incident["id"],
                incident["device_id"],
                incident["title"],
                incident["status"],
            )
            if self.incidents.exists(ident):
                self.incidents.item(ident, values=values)
            else:
                self.incidents.insert("", "end", iid=ident, values=values)
        self.refresh_details()

    def refresh_details(self):
        row = next(
            (
                row
                for row in self.store.state()
                if row["profile"]["id"] == self.selected
            ),
            None,
        )
        key = (self.selected, (row["latest"] or {}).get("at") if row else None)
        if key == self.details_key:
            return
        self.details_key = key
        self.ports.delete(*self.ports.get_children())
        self.neighbors.delete(*self.neighbors.get_children())
        self.vlans.delete(*self.vlans.get_children())
        self.history.delete(*self.history.get_children())
        self.details.delete("1.0", "end")
        if not row:
            return
        latest = row["latest"]
        sample = row["last_success"]
        for point in self.store.history(self.selected):
            self.history.insert(
                "",
                "end",
                values=(
                    datetime.fromtimestamp(point["at"]).strftime("%d.%m %H:%M:%S"),
                    "ответ" if point["ok"] else "ошибка",
                    len(point.get("interfaces", [])),
                    point.get("error", "снимок сохранён"),
                ),
            )
        if not sample:
            self.details.insert(
                "end", str(latest.get("error")) if latest else "Данных пока нет."
            )
            return
        for port in sample.get("interfaces", []):
            fmt = lambda key, scale=1: (
                "—" if port.get(key) is None else round(port[key] / scale, 2)
            )
            self.ports.insert(
                "",
                "end",
                values=(
                    port["index"],
                    port["name"],
                    {
                        1: "up",
                        2: "down",
                        3: "testing",
                        4: "unknown",
                        5: "dormant",
                        6: "notPresent",
                        7: "lowerLayerDown",
                    }.get(port["oper_status"], "—")
                    + "/"
                    + {1: "up", 2: "down", 3: "testing"}.get(port["admin_status"], "—"),
                    fmt("speed_bps", 1e6),
                    fmt("rx_bps", 1e6),
                    fmt("tx_bps", 1e6),
                    fmt("utilization_pct"),
                    str(port.get("rx_errors_delta", "—"))
                    + "/"
                    + str(port.get("tx_errors_delta", "—")),
                ),
            )
        for neighbor in sample.get("neighbors", []):
            local = neighbor.get("local_port") or (
                "LLDP#" + str(neighbor["local_lldp_port"])
                if neighbor.get("local_lldp_port")
                else "—"
            )
            self.neighbors.insert(
                "",
                "end",
                values=(
                    neighbor["source"],
                    local,
                    neighbor.get("name") or neighbor.get("address") or "—",
                    neighbor.get("remote_port", "—"),
                    neighbor.get("mac", "—"),
                ),
            )
        for vlan in sample.get("vlans", []):
            self.vlans.insert(
                "",
                "end",
                values=(
                    vlan["id"],
                    vlan["name"],
                    ",".join(map(str, vlan["egress_bridge_ports"])),
                    ",".join(map(str, vlan["untagged_bridge_ports"])),
                ),
            )
        text = {
            "collected": datetime.fromtimestamp(sample["at"]).isoformat(
                timespec="seconds"
            ),
            "latest_error": (
                latest.get("error") if latest and not latest["ok"] else None
            ),
            "system": sample.get("system"),
            "capabilities": sample.get("capabilities"),
            "warnings": sample.get("warnings"),
            "neighbors": sample.get("neighbors"),
            "vlans": sample.get("vlans"),
            "note": "LLDP local port number не всегда ifIndex. ARP/FDB не доказывают прямое кабельное соединение.",
            "history_samples": len(self.store.history(self.selected)),
        }
        self.details.insert("end", json.dumps(text, ensure_ascii=False, indent=2))

    def change_incident(self, action):
        selection = self.incidents.selection()
        if not selection:
            return
        try:
            self.store.change_incident(int(selection[0]), action)
            self.refresh()
        except ValueError as error:
            messagebox.showerror("Инцидент", str(error), parent=self)

    def export_report(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".html",
            filetypes=[("HTML", ".html"), ("JSON", ".json")],
        )
        if path:
            try:
                export(self.store, path)
                self.status.configure(text="Отчёт сохранён: " + path)
            except Exception as error:
                messagebox.showerror("Экспорт", str(error), parent=self)

    def close(self):
        self.closed = True
        self.after_cancel(self.timer)
        self.service.stop()
        if self.service.lock.acquire(blocking=False):
            try:
                self.store.close()
            finally:
                self.service.lock.release()
