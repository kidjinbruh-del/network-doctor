# Network Doctor Enterprise

![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Tests](https://img.shields.io/badge/tests-65%20passed-success.svg)
![CI](https://github.com/kidjinbruh-del/network-doctor/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)

Диагностика Wi-Fi сети по данным MikroTik (RouterOS API) и Keenetic: инвентарь
клиентов, Health Score, поиск причины отсутствия интернета, история в SQLite и
экспорт отчётов в HTML/PDF.

> Wi-Fi network diagnostics for MikroTik (RouterOS API) and Keenetic gear:
> client inventory, Health Score, "no internet" root-cause analysis, SQLite
> history and HTML/PDF reports.

---

## Возможности / Features

- **Подключение к MikroTik** — RouterOS `API`, `API-SSL` или SSH-туннель, профили
  подключения с хранением пароля в системном keyring.
  *RouterOS API / API-SSL / SSH tunnel with keyring-backed credentials.*
- **Парсер `spectral-scan`** — шумовой пол, каналы, средний noise floor и сырые
  точки спектра из логов MikroTik.
  *Spectral-scan parsing: noise floor, channels and raw spectrum points.*
- **Фильтр виртуальных MAC** — отсеивает QEMU (`52:54:00`), Proxmox
  (`bc:24:11`), локально назначенные адреса и интерфейсы `virbr*`, `vnet*`,
  `veth*`, `docker*`.
  *Virtual MAC filtering for QEMU, Proxmox, Docker and locally administered addresses.*
- **Диагностика «нет интернета»** — маршрут по умолчанию, рост счётчиков NAT,
  ARP до Keenetic и доступность портов управления `22/80/443/8291` с вердиктом
  «проблема за Keenetic».
  *No-internet root cause: default route, NAT counters, ARP and management ports.*
- **Health Score клиента** — потери, джиттер, задержка, сигнал, SNR, CCQ и retry
  с цветовой шкалой (красный `<50`, жёлтый `50–80`, зелёный `>80`).
  *Per-client Health Score with a red/yellow/green scale.*
- **ICMP-зонд** — потери, джиттер и задержка до целевого хоста: перед сбором
  снимка и каждые 30 секунд фонового мониторинга.
  *ICMP probe for loss, jitter and latency before each snapshot and every 30 s.*
- **SQLite-история и baseline** — снимки, клиенты, события, сравнение
  «Офис ↔ Лаборатория» и «Текущий ↔ Эталон».
  *SQLite history, baselines and office/lab comparison.*
- **Экспорт в HTML/PDF** — отчёт со сводкой, Health Score, топологией и блоком
  диагностики.
  *HTML/PDF reports with a diagnostics section.*
- **Кроссплатформенный GUI** — CustomTkinter, адаптивное окно, вертикальная
  прокрутка, ярлыки с иконкой для Windows и Linux.
  *CustomTkinter GUI with adaptive window, scrolling and desktop shortcuts.*

## Скриншоты / Screenshots

<!-- TODO: добавить скриншоты UI -->
<!-- TODO: add UI screenshots -->

| Вкладка / Tab | Скриншот / Screenshot |
| -------------- | --------------------- |
| Подключение / Connection | _(pending)_ |
| Диагностика / Diagnostics | _(pending)_ |
| Здоровье / Health | _(pending)_ |
| Мониторинг / Monitoring | _(pending)_ |
| Сравнение / Comparison | _(pending)_ |

## Установка / Installation

Требуется Python 3.11+ и доступ к MikroTik по RouterOS API.
*Requires Python 3.11+ and RouterOS API access to a MikroTik device.*

### Windows

```powershell
git clone https://github.com/kidjinbruh-del/network-doctor.git
cd network-doctor
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Linux

```bash
git clone https://github.com/kidjinbruh-del/network-doctor.git
cd network-doctor
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Для GUI нужен Tk: `sudo apt-get install -y python3-tk`.
*Tk is required for the GUI: `sudo apt-get install -y python3-tk`.*

## Запуск / Run

```bash
python app.py
```

или через venv: `.venv\Scripts\python.exe app.py` / `.venv/bin/python app.py`.

### Ярлыки на рабочем столе / Desktop shortcuts

Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\install-shortcut.ps1
```

Linux:

```bash
./linux/install-desktop.sh
```

Обе команды создают ярлык с иконкой (`assets/network-doctor.ico`,
`linux/network-doctor.svg`).
*Both commands create a shortcut with an icon.*

## Тесты / Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Ожидаемо: **65 тестов** без реального оборудования и без GUI.
*Expected: **65 tests**, no real hardware and no GUI required.*

CI (GitHub Actions) прогоняет тесты на `ubuntu-latest` и `windows-latest`
с Python 3.11: `.github/workflows/ci.yml`.

## Структура проекта / Project structure

```text
network-doctor/
├── app.py                      # точка входа / entry point
├── network_doctor/
│   ├── client.py               # сбор снимка через RouterOS API
│   ├── connections.py          # API / API-SSL / SSH tunnel
│   ├── parser.py               # spectral-scan и фильтр виртуальных MAC
│   ├── spectral.py             # агрегация noise floor
│   ├── diagnose.py             # «почему нет интернета»
│   ├── health.py               # Health Score клиента
│   ├── probe.py                # ICMP-зонд: loss / jitter / latency
│   ├── monitor.py              # фоновый мониторинг и backoff
│   ├── schema.py               # валидация JSON-снимка v2
│   ├── compare.py              # сравнение снимков и baseline
│   ├── demo.py                 # демо-данные без оборудования
│   ├── storage.py              # JSON-снимки
│   ├── db.py                   # SQLite-история
│   ├── report.py               # HTML/PDF-отчёты
│   ├── charting.py             # графики matplotlib
│   ├── ui.py                   # GUI на CustomTkinter
│   ├── paths.py                # пути данных в профиле пользователя
│   ├── profiles.py             # профили подключения + keyring
│   ├── vendors.py              # OUI → вендор
│   └── utils.py                # вспомогательные функции
├── assets/                     # иконки приложения
├── docs/snapshot-schema.md     # формат снимка v2 и калибровка
├── examples/office_snapshot.json
├── linux/                      # запуск, установка, .desktop, иконка
├── windows/                    # установка ярлыка Windows
└── tests/                      # 65 тестов (unittest + pytest)
```

## Данные / Data

Снимки, база и профили хранятся вне репозитория:

| OS | Путь / Path |
| -- | ----------- |
| Windows | `%LOCALAPPDATA%\NetworkDoctor` |
| Linux | `~/.local/share/network-doctor` |

## Ограничения / Limitations

- `API-SSL` **не проверен** на реальном RouterOS: используйте `API` или
  SSH-туннель, пока сертификат не подтверждён.
  *`API-SSL` is **not verified** on real hardware: use `API` or the SSH tunnel.*
- Live-метрики потерь/джиттера/задержки считаются системным `ping`; в сетях,
  где ICMP запрещён, зонд вернёт ошибку, а метрики останутся пустыми.
  *Loss/jitter/latency rely on the system `ping`; blocked ICMP yields empty metrics.*
- Маршрутизаторы CHR **без Wi-Fi-радиомодуля**: клиентские и спектральные
  данные недоступны, видна только служебная часть.
  *CHR routers without a Wi-Fi radio expose no client or spectral data.*
- Локально назначенные MAC (второй бит) отсекаются, поэтому рандомизированные
  адреса телефонов могут не попасть в «реальный» инвентарь.
  *Locally administered MACs are filtered out, so randomised phone addresses may be missing.*
- `numpy`/`pandas` не тянутся при старте: matplotlib и reportlab импортируются
  лениво только при построении графика или экспорте отчёта.
  *`numpy`/`pandas` are not loaded at startup.*

## Лицензия / License

[MIT](LICENSE) © 2026 kidjinbruh-del

## Contributing

См. [CONTRIBUTING.md](CONTRIBUTING.md).
