# Network Doctor Enterprise — схема снимка и структура

## Структура проекта

```text
network-doctor/
├── app.py
├── README.md
├── CONTRIBUTING.md
├── LICENSE
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── network_doctor/
│   ├── client.py        # RouterOS API + сбор v2-снимка
│   ├── connections.py   # API / API-SSL / SSH tunnel
│   ├── compare.py       # сравнение «Офис» / «Лаборатория»
│   ├── demo.py          # демо-снимки (schema v2)
│   ├── parser.py        # spectral-scan table + virtual MAC filter
│   ├── diagnose.py      # «Почему нет интернета»
│   ├── health.py        # Health Score (сеть + беспроводной режим)
│   ├── db.py            # SQLite history
│   ├── schema.py        # проверка JSON-снимка v2
│   ├── monitor.py       # фоновый мониторинг, backoff, ICMP-зонд
│   ├── probe.py         # ICMP-зонд: loss / jitter / latency
│   ├── report.py        # HTML/PDF
│   ├── charting.py      # графики matplotlib
│   ├── spectral.py      # агрегация noise floor
│   ├── storage.py       # JSON snapshots
│   ├── profiles.py      # профили подключения + keyring
│   ├── paths.py         # пути данных в профиле пользователя
│   ├── vendors.py       # OUI → вендор
│   ├── utils.py         # вспомогательные функции
│   └── ui.py            # CustomTkinter + скролл + иконка
├── assets/network-doctor.ico   # иконка приложения
├── assets/network-doctor.png
├── windows/install-shortcut.ps1 # ярлык «Пуск» + рабочий стол
├── linux/install-desktop.sh     # .desktop-ярлык
├── linux/run.sh
├── linux/network-doctor.svg
├── docs/snapshot-schema.md
├── examples/office_snapshot.json
├── tests/test_client.py
├── tests/test_compare.py
├── tests/test_db.py
├── tests/test_demo.py
├── tests/test_diagnose.py
├── tests/test_health.py
├── tests/test_monitor.py
├── tests/test_parser.py
├── tests/test_probe.py
├── tests/test_report.py
├── tests/test_sample.py
├── tests/test_schema.py
├── tests/test_spectral.py
├── tests/test_startup.py
└── tests/test_storage.py
```

## JSON-снимок v2

Полный пример на данных инцидента: `examples/office_snapshot.json`.
Идентификаторы в примере обезличены (подсеть `10.10.10.0/24`, SSID `OFFICE-WIFI`,
MAC `50:ff:20:00:00:0x`); структура, объёмы и калибровка соответствуют боевому
снимку.

Ключи верхнего уровня:

| Ключ | Назначение |
| ---- | ---------- |
| `schema_version` | версия схемы, сейчас `2` |
| `meta` | тип и метка снимка, время сбора, хост, модель и версия роутера |
| `topology` | `mikrotik`, `keenetic`, подсети и шлюзы |
| `wireless_scan` | найденные сети: канал, частота, SSID, BSSID, сигнал, security |
| `clients` | канонический инвентарь: `mac`, `ip`, `interface`, `signal_dbm`, `tx_ccq`, `rx_ccq`, `retry_pct`, `vendor`, `virtual` |
| `health` | метрики канала: `target`, `samples`, `loss_pct`, `jitter_ms`, `latency_ms`, `source` |
| `warnings` | предупреждения снимка (виртуальные MAC, закрытые порты, потери) |
| `diagnostics` | детали диагностики: маршрут, NAT, ARP, порты Keenetic |
| `registration_table`, `wireless_interfaces`, `logs`, `disconnect_events`, `spectral_scan` | legacy-секции для совместимости |

Обязательные поля проверяет `network_doctor/schema.py` (`validate_snapshot`).

```json
{
  "schema_version": 2,
  "meta": {
    "snapshot_type": "office",
    "snapshot_label": "Офис",
    "collected_at": "2026-10-22T01:45:00+03:00",
    "host": "10.10.10.1",
    "router_identity": "Mikrotik-office",
    "router_version": "7.21.4",
    "router_board": "L009UiGS"
  },
  "topology": {
    "mikrotik": {
      "host": "10.10.10.1",
      "identity": "Mikrotik-office",
      "version": "7.21.4",
      "board": "L009UiGS",
      "lan_ip": "10.10.10.1/24",
      "lan_subnet": "10.10.10.0/24",
      "wan_interface": "pppoe-out1",
      "default_route": {"present": true, "active": true, "gateway": "pppoe-out1"}
    },
    "keenetic": {
      "ip": "10.10.10.10",
      "wan_ip": "10.10.10.10",
      "mac": "50:ff:20:00:00:02",
      "lan_ip": "192.168.1.1",
      "lan_subnet": "192.168.1.0/24",
      "ssid": "OFFICE-WIFI",
      "channel": "4",
      "reachable": true,
      "management_ports": {"22": false, "80": false, "443": false, "8291": false}
    },
    "subnets": [
      {"network": "10.10.10.0/24", "gateway": "10.10.10.1", "interface": "bridge"},
      {"network": "192.168.1.0/24", "gateway": "192.168.1.1", "interface": "Keenetic LAN/WiFi"}
    ]
  },
  "wireless_scan": [
    {"channel": 4, "frequency": 2427, "ssid": "", "bssid": null, "signal": 100, "security": null, "hidden": true},
    {"channel": 4, "frequency": 2427, "ssid": "OFFICE-WIFI", "bssid": null, "signal": 100, "security": null, "hidden": false}
  ],
  "clients": [
    {
      "mac": "50:ff:20:00:00:01",
      "ip": "192.168.1.1",
      "interface": "wlan1",
      "signal_dbm": null,
      "tx_ccq": null,
      "retry_pct": null,
      "vendor": "Keenetic",
      "virtual": false
    },
    {
      "mac": "bc:24:11:8f:56:38",
      "ip": "192.168.1.131",
      "interface": "wlan1",
      "signal_dbm": null,
      "tx_ccq": null,
      "retry_pct": null,
      "vendor": "Proxmox VE",
      "virtual": true,
      "virtual_reason": "Proxmox VE MAC bc:24:11"
    }
  ],
  "health": {
    "target": "8.8.8.8",
    "samples": 42254,
    "window_hours": 168,
    "loss_pct": 7.6,
    "jitter_ms": 80.2,
    "latency_ms": 225.0,
    "avg_loss_pct": 0.29,
    "avg_jitter_ms": 18.3,
    "avg_latency_ms": 58.7
  },
  "warnings": [
    "К каналу 4 подключены три Keenetic с сигналом 100.",
    "Порты управления Keenetic 22/80/443/8291 закрыты."
  ],
  "diagnostics": {
    "default_route": {"present": true, "active": true, "gateway": "pppoe-out1"},
    "nat": {"bytes": 29071883, "packets": 299472, "delta_packets": 4172, "growing": true},
    "arp": [
      {"address": "10.10.10.10", "mac": "50:ff:20:00:00:02", "interface": "bridge", "complete": true, "invalid": false}
    ],
    "keenetic": {
      "ip": "10.10.10.10",
      "arp": true,
      "ports": {"22": false, "80": false, "443": false, "8291": false}
    }
  },
  "registration_table": [],
  "wireless_interfaces": [],
  "logs": [],
  "disconnect_events": [],
  "spectral_scan": {"noise_floor_avg": null, "samples": 0, "method": "none", "raw": ""}
}
```

`registration_table`, `wireless_interfaces`, `logs`, `disconnect_events` и
`spectral_scan` сохранены для совместимости с прежними снимками и отчётами.

## SQLite

Основная таблица истории и обязательные публичные имена:

```sql
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT UNIQUE,
    snapshot_type TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    collected_epoch REAL NOT NULL,
    saved_at TEXT NOT NULL,
    host TEXT,
    router_identity TEXT,
    router_version TEXT,
    router_board TEXT,
    noise REAL,
    clients_count INTEGER NOT NULL DEFAULT 0,
    disconnects INTEGER NOT NULL DEFAULT 0,
    demo INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL,
    timestamp TEXT GENERATED ALWAYS AS (collected_at) VIRTUAL,
    type TEXT GENERATED ALWAYS AS (snapshot_type) VIRTUAL,
    data_json TEXT GENERATED ALWAYS AS (raw_json) VIRTUAL
);
```

`timestamp`, `type` и `data_json` — генерируемые алиасы, поэтому данные не
дублируются. Дочерние таблицы: `clients`, `wireless`, `events`, `baselines`.
Запись идёт транзакциями, дочерние строки — через `executemany`.

## Калибровка Health Score

- Потери: пик **7,6 %** → до `-50`.
- Джиттер: пик **80,2 мс** → до `-30`.
- Задержка: пик **225 мс** → до `-20`.
- Сигнал ниже **-70 dBm** → до `-25`.
- Цвет: красный `<50`, жёлтый `50–80`, зелёный `>80`.

Прежний беспроводной режим (`SNR <20 dB`, `CCQ <80 %`, `retry >=20 %`)
сохранён для снимков без сетевых метрик.

## ICMP-зонд

`network_doctor/probe.py` измеряет качество канала до целевого хоста
(по умолчанию `8.8.8.8`) системной командой `ping`:

- Windows: `ping -n <count> -w <timeout_ms> <target>`.
- Linux: `ping -c <count> -W <timeout_s> <target>`.

Возвращается словарь:

```json
{
  "target": "8.8.8.8",
  "samples": 10,
  "received": 10,
  "loss_pct": 0.0,
  "jitter_ms": 1.4,
  "latency_ms": 42.3,
  "source": "icmp",
  "error": ""
}
```

Разбор учитывает локализованный вывод `ping` (русский/английский) и
кодировку консоли Windows (OEM). Джиттер — среднее абсолютное отклонение
времени отклика. Зонд вызывается перед ручным сбором снимка и каждые
30 секунд фонового мониторинга (`probe_interval_seconds`).

## Диагностика

`diagnose_no_internet(snapshot)` проверяет:

1. Маршрут по умолчанию.
2. Рост счётчиков NAT (нужны два замера, `apply_nat_delta`).
3. ARP до `10.10.10.10`.
4. Порты Keenetic `22/80/443/8291`.

Если ARP есть, а порты закрыты:

> Проблема за Keenetic. Проверьте его WAN-статус вручную.

Если ARP нет:

> Keenetic не отвечает. Проверьте кабель/питание.

## Интерфейс и ярлыки

- Окно подстраивается под экран: ширина `min(1240, экран-60)`, высота
  `min(860, экран-100)`, поэтому приложение помещается на 1366x768.
- Панель вкладок закреплена сверху, содержимое каждой вкладки лежит в
  `CTkScrollableFrame`: вертикальный скроллбар прокручивает страницу,
  нижние блоки (сводка, таблицы, график, отчёты) не обрезаются.
- Иконка окна: `assets/network-doctor.ico` (Windows) и
  `assets/network-doctor.png` (Linux).
- Ярлыки: `windows/install-shortcut.ps1` (рабочий стол и меню «Пуск»),
  `linux/install-desktop.sh` (`.desktop` в `~/.local/share/applications`).

## Ограничения

- Нет `numpy`/`pandas` при старте: matplotlib и reportlab импортируются
  лениво только при построении графика или экспорте отчёта.
- Сетевые обработчики используют короткие таймауты; SSH tunnel и проверка
  портов не блокируют UI-поток.
- Локально назначенные MAC отсекаются по требованию, поэтому рандомизированные
  телефоны могут не попасть в «реальный» инвентарь.
- `API-SSL` не подтверждён на реальном RouterOS: работать следует через
  `API` или SSH-туннель.
- ICMP может быть запрещён в сети: зонд вернёт `error`, метрики останутся
  пустыми, снимок сохранится без потерь/джиттера/задержки.
- Маршрутизаторы CHR без Wi-Fi: клиентские и спектральные данные недоступны.
