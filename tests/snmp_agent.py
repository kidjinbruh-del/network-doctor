"""Loopback-only synthetic agent: GET/GETNEXT/GETBULK; no SET responder."""

import argparse
import asyncio
from bisect import bisect_right
from pysnmp.entity import engine, config
from pysnmp.entity.rfc3413 import context, cmdrsp
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.smi.instrum import AbstractMibInstrumController
from pysnmp.proto import rfc1902, rfc1905


def fixture(pen=171):
    values = {}

    def put(oid, value):
        values[tuple(map(int, oid.split(".")))] = value

    base = "1.3.6.1.2.1."
    put(
        base + "1.1.0", rfc1902.OctetString("Synthetic device for Network Doctor tests")
    )
    put(base + "1.2.0", rfc1902.ObjectIdentifier(f"1.3.6.1.4.1.{pen}.1"))
    put(base + "1.3.0", rfc1902.TimeTicks(300000))
    put(base + "1.5.0", rfc1902.OctetString("LAB-SWITCH"))
    put(base + "1.6.0", rfc1902.OctetString("isolated fixture"))
    for index in (1, 2):
        for col, value in {
            1: rfc1902.Integer32(index),
            2: rfc1902.OctetString("Ethernet" + str(index)),
            5: rfc1902.Gauge32(1_000_000_000),
            6: rfc1902.OctetString(bytes([0, 1, 2, 3, 4, index])),
            7: rfc1902.Integer32(1),
            8: rfc1902.Integer32(1 if index == 1 else 2),
            10: rfc1902.Counter32(10000),
            16: rfc1902.Counter32(20000),
            14: rfc1902.Counter32(0),
            20: rfc1902.Counter32(0),
            13: rfc1902.Counter32(0),
            19: rfc1902.Counter32(0),
        }.items():
            put(base + f"2.2.1.{col}.{index}", value)
        for col, value in {
            1: rfc1902.OctetString("port" + str(index)),
            6: rfc1902.Counter64(2**40 + index),
            10: rfc1902.Counter64(2**41 + index),
            15: rfc1902.Gauge32(1000),
            18: rfc1902.OctetString("uplink" if index == 1 else "spare"),
            19: rfc1902.TimeTicks(0),
        }.items():
            put(base + f"31.1.1.1.{col}.{index}", value)
    put("1.0.8802.1.1.2.1.4.1.1.9.0.1.1", rfc1902.OctetString("remote-lab-switch"))
    put("1.0.8802.1.1.2.1.4.1.1.7.0.1.1", rfc1902.OctetString("Gi1"))
    put("1.3.6.1.2.1.17.1.4.1.2.1", rfc1902.Integer32(1))
    put("1.3.6.1.2.1.17.1.4.1.2.2", rfc1902.Integer32(2))
    put("1.3.6.1.2.1.17.7.1.4.3.1.1.10", rfc1902.OctetString("Lab VLAN"))
    put("1.3.6.1.2.1.17.7.1.4.3.1.2.10", rfc1902.OctetString(b"\xc0"))
    put("1.3.6.1.2.1.17.7.1.4.3.1.4.10", rfc1902.OctetString(b"\x40"))
    put("1.3.6.1.2.1.17.7.1.4.3.1.5.10", rfc1902.Integer32(1))
    return values


class Instrument(AbstractMibInstrumController):
    def __init__(self, values):
        self.values = values
        self.keys = sorted(values)

    def read_variables(self, *bindings, **context):
        return [
            (oid, self.values.get(tuple(oid), rfc1905.noSuchInstance))
            for oid, _ in bindings
        ]

    def read_next_variables(self, *bindings, **context):
        output = []
        for oid, _ in bindings:
            index = bisect_right(self.keys, tuple(oid))
            if index == len(self.keys):
                output.append((oid, rfc1905.endOfMibView))
            else:
                key = self.keys[index]
                output.append((rfc1902.ObjectIdentifier(key), self.values[key]))
        return output


async def serve(port, pen):
    snmp = engine.SnmpEngine()
    carrier = udp.UdpTransport().open_server_mode(("127.0.0.1", port))
    config.add_transport(snmp, udp.DOMAIN_NAME, carrier)
    config.add_v1_system(snmp, "fixture-area", "fixture-community")
    config.add_vacm_user(snmp, 2, "fixture-area", "noAuthNoPriv", readSubTree=(1,))
    config.add_v3_user(
        snmp,
        "fixture-user",
        config.USM_AUTH_HMAC192_SHA256,
        "fixture-auth-password",
        config.USM_PRIV_CFB128_AES,
        "fixture-privacy-password",
    )
    config.add_vacm_user(snmp, 3, "fixture-user", "authPriv", readSubTree=(1,))
    ctx = context.SnmpContext(snmp)
    ctx.unregister_context_name(b"")
    ctx.register_context_name(b"", Instrument(fixture(pen)))
    responders = [
        cmdrsp.GetCommandResponder(snmp, ctx),
        cmdrsp.NextCommandResponder(snmp, ctx),
        cmdrsp.BulkCommandResponder(snmp, ctx),
    ]
    try:
        endpoint, _ = await carrier._lport
        print("READY " + str(endpoint.get_extra_info("sockname")[1]), flush=True)
        await asyncio.Event().wait()
    finally:
        snmp.close_dispatcher()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pen", type=int, default=171)
    args = parser.parse_args()
    asyncio.run(serve(args.port, args.pen))
