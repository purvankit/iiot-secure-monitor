"""Run the directly-connected simulated devices listed in a config file.

Each device gets its own MQTT connection and certificate, exactly like separate
physical devices would; they just share one process to keep the demo light.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from pathlib import Path

from iiot_sim import envelope
from iiot_sim.models import MODELS
from iiot_sim.mqtt_client import TlsFiles, connect

log = logging.getLogger("devices")


def load_config(path: Path) -> list[dict]:
    cfg = json.loads(path.read_text())
    devices = cfg["devices"]
    for d in devices:
        if d["type"] not in MODELS:
            raise ValueError(f"{d['device_id']}: unknown device type {d['type']!r}")
    ids = [d["device_id"] for d in devices]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate device_id in config")
    return devices


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=Path("config/devices.json"))
    p.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "8883")))
    p.add_argument("--certs", type=Path, default=Path(os.getenv("CERTS_DIR", "../local/certs")))
    p.add_argument("--interval", type=float, default=float(os.getenv("INTERVAL_S", "5")),
                   help="seconds between messages per device (cost: IoT Core bills per message)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    fleet = []
    for i, d in enumerate(load_config(args.config)):
        client = connect(d["device_id"], args.host, args.port,
                         TlsFiles.for_device(args.certs, d["device_id"]))
        model = MODELS[d["type"]](seed=d.get("seed", i))
        fleet.append((d, model, envelope.Sequencer(), client))

    # Stagger devices so they don't all publish on the same tick.
    next_due = [time.monotonic() + random.uniform(0, args.interval) for _ in fleet]
    last = list(next_due)
    try:
        while True:
            now = time.monotonic()
            for i, (d, model, seqr, client) in enumerate(fleet):
                if now < next_due[i]:
                    continue
                readings = model.step(dt=max(now - last[i], 0.1))
                last[i] = now
                msg = envelope.build_message(device_id=d["device_id"], device_type=d["type"],
                                             site=d["site"], readings=readings, sequencer=seqr)
                client.publish(envelope.telemetry_topic(d["device_id"]),
                               envelope.encode(msg), qos=1)
                log.info("%s seq=%d %s", d["device_id"], msg["seq"], readings)
                next_due[i] = now + args.interval
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        for *_, client in fleet:
            client.disconnect()
            client.loop_stop()


if __name__ == "__main__":
    main()
