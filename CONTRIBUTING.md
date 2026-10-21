# Contributing / Вклад в проект

Спасибо за интерес к Network Doctor. / Thanks for taking the time to contribute.

## Быстрый старт / Getting started

```bash
git clone https://github.com/kidjinbruh-del/network-doctor.git
cd network-doctor
python -m venv .venv

# Windows
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
# Linux
.venv/bin/python -m pip install -r requirements-dev.txt
```

## Тесты / Tests

```bash
python -m pytest -q
```

- Все Pull Request **обязаны** проходить `pytest` и `python -m compileall network_doctor app.py`.
- Новый код сопровождается тестами в `tests/` (используется `unittest.TestCase`
  и/или стиль pytest).
- Тесты не должны требовать реального оборудования, сети или GUI: используйте
  фикстуры и `tempfile`.
- PR с изменениями снимков обязаны обновлять `examples/office_snapshot.json`
  и проверку в `tests/test_sample.py`.

## Стиль кода / Code style

- PEP 8, 4 пробела, максимальная длина строки — 100 символов.
- Типизация аннотациями (`from __future__ import annotations`, `str | None`).
- Имена: `snake_case` для функций и переменных, `PascalCase` для классов,
  `UPPER_CASE` для констант.
- Docstrings на английском, короткие, в стиле Google.
- Комментарии объясняют «почему», а не «что».
- Никаких зависимостей без обсуждения: сначала обсуждение в Issue.

## Процесс / Workflow

1. Откройте Issue с описанием проблемы или идеи (баги — с логами и версией).
2. Создайте ветку от `main`: `git checkout -b feature/short-description`.
3. Внесите изменения, добавьте тесты, убедитесь, что `pytest` зелёный.
4. Отправьте Pull Request в `main`, опишите, что изменилось и почему.
5. CI (`.github/workflows/ci.yml`) проверяет Ubuntu и Windows; ревью обязательно.

## Безопасность / Security

Не публикуйте учётные данные, IP-адреса и MAC-адреса реальных сетей.
Пароли хранятся в системном keyring и не должны попадать в код, логи или снимки.
*Never commit real credentials, IP addresses or MAC addresses. Passwords live in
the system keyring and must not appear in code, logs or snapshots.*

## Лицензия / License

Отправляя Pull Request, вы соглашаетесь с лицензией
[MIT](LICENSE) для ваших изменений.
