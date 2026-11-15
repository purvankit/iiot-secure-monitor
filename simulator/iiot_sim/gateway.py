"""Edge gateway: polls the PLC over Modbus TCP and forwards readings over MQTT/TLS.

This is the protocol-conversion role an industrial gateway plays: a legacy,
unauthenticated fieldbus on one side, an authenticated cloud connection on the
other. The gateway owns the cloud identity (its own certificate); the PLC has none.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from iiot_sim import envelope, registers
from iiot_sim.mqtt_client import TlsFiles, connect

log = logging.getLogger("gateway")
DEVICE_TYPE = "pump_station"


def poll_plc(plc: ModbusTcpClient, unit_id: int) -> dict | None:
    """Read and decode the register block; None if the PLC is unreachable or errors."""
    try:
        if not plc.connected and not plc.connect():
            log.warning("PLC unreachable")
            return None
        rr = plc.read_holding_registers(0, count=registers.REGISTER_COUNT, device_id=unit_id)
    except ModbusException as e:
        log.warning("Modbus error: %s", e)
        return None
    if rr.isError():
        log.warning("PLC returned exception: %s", rr)
        return None
    return registers.decode(rr.registers)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device-id", default=os.getenv("GATEWAY_ID", "edge-gw-01"))
    p.add_argument("--site", default=os.getenv("SITE", "pune-factory-01"))
    p.add_argument("--plc-host", default=os.getenv("PLC_HOST", "localhost"))
    p.add_argument("--plc-port", type=int, default=int(os.getenv("PLC_PORT", "5020")))
    p.add_argument("--plc-unit", type=int, default=1)
    p.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "8883")))
    p.add_argument("--certs", type=Path, default=Path(os.getenv("CERTS_DIR", "../local/certs")))
    p.add_argument("--interval", type=float, default=float(os.getenv("INTERVAL_S", "5")))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    plc = ModbusTcpClient(args.plc_host, port=args.plc_port, timeout=3)
    mqtt = connect(args.device_id, args.host, args.port,
                   TlsFiles.for_device(args.certs, args.device_id))
    seqr = envelope.Sequencer()
    topic = envelope.telemetry_topic(args.device_id)
    try:
        while True:
            readings = poll_plc(plc, args.plc_unit)
            if readings is not None:
                msg = envelope.build_message(
                    device_id=args.device_id, device_type=DEVICE_TYPE, site=args.site,
                    readings=readings, sequencer=seqr,
                    extra={"source": {"protocol": "modbus-tcp", "plc_unit": args.plc_unit}},
                )
                mqtt.publish(topic, envelope.encode(msg), qos=1)
                log.info("seq=%d %s", msg["seq"], readings)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        plc.close()
        mqtt.disconnect()
        mqtt.loop_stop()


if __name__ == "__main__":
    main()
