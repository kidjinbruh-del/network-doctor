"""Read-only, bounded SNMPv3 authPriv / explicit v2c collector. No SET support."""

from __future__ import annotations
import asyncio
import ipaddress
import os
import time
from dataclasses import dataclass, field
from .equipment import DeviceProfile, SYS, TABLES, normalize


class EquipmentError(RuntimeError):
    pass


@dataclass(repr=False)
class Secrets:
    community: str = field(default="", repr=False)
    auth: str = field(default="", repr=False)
    privacy: str = field(default="", repr=False)

    def __repr__(self):
        return "Secrets(<redacted>)"


def _secret_key(profile):
    return f"{profile.id}@{profile.host}/{profile.protocol}/{profile.username}"


def credentials(profile, overrides=None):
    values = Secrets(
        os.environ.get(profile.community_env, ""),
        os.environ.get(profile.auth_env, ""),
        os.environ.get(profile.privacy_env, ""),
    )
    if overrides:
        for key in ("community", "auth", "privacy"):
            value = getattr(overrides, key, "")
            if value:
                setattr(values, key, value)
    needed = ("auth", "privacy") if profile.protocol == "snmpv3" else ("community",)
    if any(not getattr(values, key) for key in needed):
        try:
            import keyring, json

            stored = keyring.get_password(
                "network-doctor-equipment", _secret_key(profile)
            )
            data = json.loads(stored) if stored else {}
            for key in needed:
                if not getattr(values, key) and isinstance(data.get(key), str):
                    setattr(values, key, data[key])
        except Exception:
            pass
    if profile.protocol == "snmpv2c" and not values.community:
        raise EquipmentError("Задайте community в окружении, keyring или поле сеанса")
    if profile.protocol == "snmpv3" and (
        not 8 <= len(values.auth) <= 255 or not 8 <= len(values.privacy) <= 255
    ):
        raise EquipmentError(
            "SNMPv3 authPriv требует пароли аутентификации и шифрования длиной 8–255 символов"
        )
    return values


def save_credentials(profile, values):
    import keyring, json

    try:
        keyring.set_password(
            "network-doctor-equipment",
            _secret_key(profile),
            json.dumps(
                {
                    "community": values.community,
                    "auth": values.auth,
                    "privacy": values.privacy,
                }
            ),
        )
    except Exception:
        raise EquipmentError(
            "Системное хранилище недоступно. Секреты не сохранены; используйте окружение или поля сеанса"
        ) from None


def _value(oid, value):
    from pyasn1.type import univ
    from pysnmp.proto.rfc1902 import IpAddress
    from pysnmp.proto.rfc1905 import NoSuchObject, NoSuchInstance, EndOfMibView

    if isinstance(value, (NoSuchObject, NoSuchInstance, EndOfMibView)):
        return None
    if isinstance(value, univ.Integer):
        return int(value)
    if isinstance(value, univ.ObjectIdentifier):
        return value.prettyPrint()
    if isinstance(value, IpAddress):
        return value.prettyPrint()
    if isinstance(value, univ.OctetString):
        binary_prefixes = (
            TABLES["interfaces"] + ".6.",
            TABLES["arp"] + ".2.",
            TABLES["forwarding"] + ".1.",
            TABLES["vlans"] + ".2.",
            TABLES["vlans"] + ".3.",
            TABLES["vlans"] + ".4.",
            TABLES["lldp"] + ".5.",
            TABLES["lldp"] + ".7.",
        )
        if oid.startswith(binary_prefixes):
            return value.asOctets()
        return value.asOctets().decode("utf-8", errors="replace")[:2048]
    return str(value)[:2048]


async def collect_async(profile: DeviceProfile, secrets: Secrets):
    from pysnmp.hlapi.v3arch.asyncio import (
        SnmpEngine,
        CommunityData,
        UsmUserData,
        UdpTransportTarget,
        Udp6TransportTarget,
        ContextData,
        ObjectType,
        ObjectIdentity,
        get_cmd,
        bulk_walk_cmd,
        USM_AUTH_HMAC192_SHA256,
        USM_AUTH_HMAC96_SHA,
        USM_PRIV_CFB128_AES,
    )

    engine = SnmpEngine()
    try:
        if profile.protocol == "snmpv3":
            auth = UsmUserData(
                profile.username,
                authKey=secrets.auth,
                privKey=secrets.privacy,
                authProtocol=(
                    USM_AUTH_HMAC192_SHA256
                    if profile.auth_protocol == "sha256"
                    else USM_AUTH_HMAC96_SHA
                ),
                privProtocol=USM_PRIV_CFB128_AES,
            )
        else:
            auth = CommunityData(secrets.community, mpModel=1)
        try:
            ipv6 = ipaddress.ip_address(profile.host).version == 6
        except ValueError:
            ipv6 = False
        target = await (Udp6TransportTarget if ipv6 else UdpTransportTarget).create(
            (profile.host, profile.port), timeout=profile.timeout, retries=1
        )
        error, status, index, bindings = await get_cmd(
            engine,
            auth,
            target,
            ContextData(),
            *(ObjectType(ObjectIdentity(oid)) for oid in SYS.values()),
            lookupMib=False,
        )
        if error or status:
            raise EquipmentError(
                "SNMP не ответил: проверьте адрес, ACL, версию и реквизиты доступа"
            )
        scalars = {str(oid): _value(str(oid), value) for oid, value in bindings}
        if not scalars.get(SYS["description"]) and not scalars.get(SYS["object_id"]):
            raise EquipmentError("Агент не вернул основные объекты SNMPv2-MIB")
        capabilities = {}
        data = {}
        total_bytes = 0
        semaphore = asyncio.Semaphore(3)

        async def table(name):
            nonlocal total_bytes
            async with semaphore:
                root = TABLES[name]
                rows = {}
                state = "ok"
                try:
                    async for err, stat, _, varbinds in bulk_walk_cmd(
                        engine,
                        auth,
                        target,
                        ContextData(),
                        0,
                        25,
                        ObjectType(ObjectIdentity(root)),
                        lookupMib=False,
                        lexicographicMode=False,
                        maxRows=16385,
                        maxCalls=700,
                    ):
                        if err or stat:
                            state = "unavailable"
                            break
                        for oid, value in varbinds:
                            oid = str(oid)
                            if not oid.startswith(root + "."):
                                continue
                            converted = _value(oid, value)
                            if converted is not None:
                                size = len(oid) + (
                                    len(converted)
                                    if isinstance(converted, bytes)
                                    else len(str(converted).encode("utf-8"))
                                )
                                if total_bytes + size > 4 * 1024 * 1024:
                                    state = "truncated"
                                    break
                                total_bytes += size
                                rows[oid] = converted
                            if len(rows) > 16384:
                                state = "truncated"
                                break
                        if state == "truncated":
                            break
                    if not rows and state == "ok":
                        state = "not_exposed"
                    elif len(rows) >= 16384:
                        state = "truncated"
                except asyncio.CancelledError:
                    capabilities[name] = {"status": "timeout", "cells": len(rows)}
                    data[name] = rows
                    raise
                except Exception:
                    state = "unavailable"
                capabilities[name] = {"status": state, "cells": len(rows)}
                data[name] = rows

        tasks = [
            asyncio.create_task(table(name)) for name in dict.fromkeys(profile.tables)
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return normalize(profile, scalars, data, capabilities, time.time())
    finally:
        engine.close_dispatcher()


def collect(profile: DeviceProfile, overrides=None, stop_event=None):
    profile.validate()
    if profile.protocol == "icmp":
        from .probe import measure_icmp

        result = measure_icmp(profile.host, count=3, timeout=8)
        if not result.get("samples"):
            raise EquipmentError(
                "Нет ICMP-ответа; устройство может блокировать ping, состояние неизвестно"
            )
        return {
            "schema": "network-doctor-equipment/1",
            "device_id": profile.id,
            "at": time.time(),
            "name": profile.name,
            "host": profile.host,
            "site": profile.site,
            "owner": profile.owner,
            "protocol": "icmp",
            "system": {},
            "interfaces": [],
            "neighbors": [],
            "capabilities": {"icmp": {"status": "ok"}},
            "reachability": result,
            "warnings": [
                "Только ICMP: тип устройства, порты и работоспособность сервисов не подтверждены"
            ],
            "ok": True,
        }
    secrets = credentials(profile, overrides)

    async def run():
        task = asyncio.create_task(collect_async(profile, secrets))

        async def watch():
            while not task.done():
                if stop_event is not None and stop_event.is_set():
                    task.cancel()
                    return
                await asyncio.sleep(0.1)

        watcher = asyncio.create_task(watch())
        try:
            return await asyncio.wait_for(task, timeout=profile.budget)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    try:
        return asyncio.run(run())
    except asyncio.CancelledError:
        raise EquipmentError("Опрос отменён") from None
    except EquipmentError:
        raise
    except TimeoutError:
        raise EquipmentError(
            "Превышен общий лимит опроса. Уменьшите перечень таблиц или проверьте сеть"
        ) from None
    except Exception:
        raise EquipmentError(
            "Ошибка SNMP: проверьте транспорт, права, алгоритмы и доступность агента"
        ) from None
