"""Offline reports: HTML escapes device strings; exports never contain credentials."""

import json
from pathlib import Path
from jinja2 import Environment, select_autoescape

TEMPLATE = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Network Doctor — оборудование</title><style>body{font:15px system-ui;max-width:1200px;margin:40px auto;padding:0 20px;color:#172239;background:#f5f7fb}section{background:white;padding:24px;margin:20px 0;border:1px solid #dce2ed;border-radius:14px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px;border-bottom:1px solid #e6ebf3}pre{white-space:pre-wrap;overflow-wrap:anywhere}.muted{color:#526278}h1,h2{letter-spacing:-.03em}</style>
<h1>Network Doctor · Оборудование</h1><p class="muted">Снимок отчёта. Доступность SNMP/ICMP не доказывает работоспособность всех сервисов. Скорости неизвестны без двух сопоставимых измерений.</p>
{% for device in devices %}{% set p=device.profile %}{% set sample=device.latest %}{% set s=device.last_success %}
<section><h2>{{p.name}} · {{p.host}}</h2><p>{{p.site}} · {{p.owner}} · {{p.protocol}}</p>
<p>Последний опрос: {{sample.at if sample else 'нет'}} · {{'ответ получен' if sample and sample.ok else 'нет данных / ошибка'}} {{sample.error if sample else ''}}</p>
{% if s %}<p>{{s.system.vendor or 'производитель неизвестен'}} · {{s.system.name or ''}} · {{s.system.description or ''}}</p>
<table><tr><th>Порт</th><th>Link / Admin</th><th>Линия, bps</th><th>RX, bps</th><th>TX, bps</th><th>Загрузка, %</th></tr>
{% for port in s.interfaces %}<tr><td>{{port.name}} · {{port.alias}}</td><td>{{port.oper_status}} / {{port.admin_status}}</td><td>{{port.speed_bps if port.speed_bps is not none else '—'}}</td><td>{{port.rx_bps if port.rx_bps is defined else '—'}}</td><td>{{port.tx_bps if port.tx_bps is defined else '—'}}</td><td>{{port.utilization_pct if port.utilization_pct is defined else '—'}}</td></tr>{% endfor %}</table>
<h3>Соседи и топология: свидетельства</h3><p class="muted">LLDP local port number не приравнивается автоматически к ifIndex. FDB/ARP — наблюдения таблиц, а не доказательство прямого кабельного соединения.</p>
<pre>{{s.neighbors|tojson(indent=2)}}</pre><h3>VLAN (статическая конфигурация Q-BRIDGE-MIB)</h3><pre>{{s.vlans|default([])|tojson(indent=2)}}</pre><h3>Ограничения данных</h3><ul>{% for w in s.warnings %}<li>{{w}}</li>{% endfor %}</ul>{% endif %}</section>{% endfor %}
<section><h2>Инциденты</h2><table><tr><th>Устройство</th><th>Проблема</th><th>Статус</th><th>Повторения</th></tr>{% for i in incidents %}<tr><td>{{i.device_id}}</td><td>{{i.title}}</td><td>{{i.status}}</td><td>{{i.occurrences}}</td></tr>{% endfor %}</table></section></html>"""


def export(store, path):
    path = Path(path)
    payload = {
        "schema": "network-doctor-fleet-report/1",
        "devices": store.state(),
        "incidents": store.incidents(),
    }
    if path.suffix.lower() == ".json":
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif path.suffix.lower() == ".html":
        environment = Environment(autoescape=select_autoescape(default_for_string=True))
        path.write_text(
            environment.from_string(TEMPLATE).render(**payload), encoding="utf-8"
        )
    else:
        raise ValueError("Выберите .html или .json")
    return path
