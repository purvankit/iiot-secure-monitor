"""Attack demos against the deployed platform (AWS IoT Core).

Threat model: the attacker has STOLEN feeder-01's certificate and private key (a
compromised device or leaked credentials). Without a valid cert they can't connect at all
(blocked at the TLS handshake), so this is the interesting case: authenticated misuse.

Each attack prints one JSON line: {"attack", "start_ms", "end_ms", "expect"}.
Stop the simulator first: IoT Core allows one connection per client ID, so the attacker
connecting as feeder-01 would kick the simulated feeder-01 off (and vice versa).

Usage (repo root):
  export MQTT_HOST=$(terraform -chdir=infra/envs/dev output -raw iot_endpoint)
  AWS_PROFILE=iiot .venv/bin/python attacks/run_attack.py all
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

from iiot_sim import envelope
from iiot_sim.mqtt_client import TlsFiles, connect

ROOT = Path(__file__).resolve().parents[1]
CERTS = Path(os.getenv("CERTS_DIR", ROOT / "local" / "aws-certs"))
HOST = os.getenv("MQTT_HOST", "")
STOLEN = "feeder-01"  # the compromised identity
SITE = "pune-substation-01"
NORMAL = {"voltage_kv": 11.0, "current_a": 240.0, "frequency_hz": 50.0, "power_factor": 0.95,
          "active_power_mw": 4.3, "breaker": "closed"}
BROKER_FIELDS = ("mqtt_client_id", "topic_device_id", "ingest_ts")


def now_ms() -> int:
    return int(time.time() * 1000)


def client():
    return connect(STOLEN, HOST, 8883, TlsFiles.for_device(CERTS, STOLEN))


def send(c, msg: dict, topic: str | None = None):
    info = c.publish(topic or envelope.telemetry_topic(STOLEN), envelope.encode(msg), qos=1)
    info.wait_for_publish(10)
    return info


def msg(seqr, readings=None, **over):
    m = envelope.build_message(device_id=STOLEN, device_type="feeder_bay", site=SITE,
                               readings=readings or dict(NORMAL), sequencer=seqr)
    m.update(over)
    return m


def close(c):
    c.disconnect()
    c.loop_stop()


# ---------------------------------------------------------------- attacks

def spoof():
    """R2: forged process values from a legitimate identity."""
    c, s = client(), envelope.Sequencer()
    send(c, msg(s))                                              # looks normal
    time.sleep(1)
    send(c, msg(s, {**NORMAL, "frequency_hz": 55.0}))            # physically impossible
    time.sleep(0.1)
    send(c, msg(s, {**NORMAL, "frequency_hz": 50.9}))            # +0.9 Hz in ~1.1 s
    close(c)
    return ["R2.out_of_range", "R2.rate_of_change"]


def flood():
    """R3: 50 messages in 5 s (normal is 1 per 5 s)."""
    c, s = client(), envelope.Sequencer()
    for _ in range(50):
        send(c, msg(s))
        time.sleep(0.1)
    close(c)
    return ["R3.message_flood"]


CAPTURE_BEFORE_MS = 0  # only capture traffic older than this (excludes our own attacks)


def latest_capture() -> dict:
    """The attacker's 'captured' traffic: the newest GENUINE feeder-01 message in S3.

    S3 archives everything, including this script's own earlier attacks, so only objects
    written before CAPTURE_BEFORE_MS count as captured device traffic.
    """
    import boto3
    s3 = boto3.client("s3", region_name="ap-south-1")
    bucket = next(b["Name"] for b in s3.list_buckets()["Buckets"]
                  if b["Name"].startswith("iiot-secure-monitor-raw-"))
    prefix = f"raw/{STOLEN}/{time.strftime('%Y/%m/%d', time.gmtime())}/"
    objs = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    cutoff = CAPTURE_BEFORE_MS / 1000
    newest = max((o for p in objs for o in p.get("Contents", [])
                  if o["LastModified"].timestamp() < cutoff),
                 key=lambda o: o["LastModified"])
    m = json.loads(s3.get_object(Bucket=bucket, Key=newest["Key"])["Body"].read())
    return {k: v for k, v in m.items() if k not in BROKER_FIELDS}  # as the device sent it


def replay():
    """R4: (a) re-send a captured genuine message; (b) send the same message twice."""
    captured = latest_capture()
    c = client()
    send(c, captured)                    # (a) old session/old timestamp, published again now
    time.sleep(1)
    dup = msg(envelope.Sequencer())
    send(c, dup)
    time.sleep(0.5)
    send(c, dup)                         # (b) byte-identical duplicate
    close(c)
    return ["R4.old_session", "R4.stale", "R4.duplicate_seq"]


def impersonate():
    """R1: stolen feeder-01 cert, own topic, payload claims to be motor-01."""
    c, s = client(), envelope.Sequencer()
    m = envelope.build_message(device_id="motor-01", device_type="motor",
                               site="pune-factory-01", sequencer=s,
                               readings={"speed_rpm": 1470.0, "current_a": 22.0,
                                         "winding_temp_c": 60.0, "vibration_mm_s": 1.8})
    send(c, m)
    close(c)
    return ["R1.identity_mismatch"]


def foreign_topic():
    """R1: publish onto motor-01's topic. IoT Core rejects it (policy) and logs it."""
    c = client()
    rejected = threading.Event()
    c.on_publish = lambda _c, _u, _m, rc, _p: rejected.set() if rc.is_failure else None
    c.publish("iiot/motor-01/telemetry", envelope.encode(msg(envelope.Sequencer())), qos=1)
    rejected.wait(10)
    close(c)
    return ["R1.auth_failure_publish_in"]


ATTACKS = {"spoof": spoof, "flood": flood, "replay": replay, "impersonate": impersonate,
           "foreign-topic": foreign_topic}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("attack", choices=[*ATTACKS, "all"])
    p.add_argument("--gap", type=float, default=15.0,
                   help="seconds between attacks in 'all' (keeps flood windows separate)")
    p.add_argument("--capture-before-ms", type=int, default=0,
                   help="replay: capture only S3 traffic older than this epoch ms "
                        "(default: start of this run)")
    args = p.parse_args()
    global CAPTURE_BEFORE_MS
    CAPTURE_BEFORE_MS = args.capture_before_ms or now_ms()
    if not HOST:
        raise SystemExit("set MQTT_HOST to the IoT endpoint")
    mqtt_names = list(ATTACKS) if args.attack == "all" else [args.attack]
    for i, name in enumerate(mqtt_names):
        if i:
            time.sleep(args.gap)
        start = now_ms()
        expect = ATTACKS[name]()
        print(json.dumps({"attack": name, "start_ms": start, "end_ms": now_ms(),
                          "expect": expect}), flush=True)


if __name__ == "__main__":
    main()
