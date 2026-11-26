"""Parse and validate telemetry from SQS.

Messages arrive as the IoT rule's output: the device's envelope plus three fields the
*broker* adds (mqtt_client_id, topic_device_id, ingest_ts). The broker fields are
trusted; everything else is device-claimed and therefore attacker-controllable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

SCHEMA_VERSION = 1
MAX_ID_LEN = 128

# field -> required type. bool is rejected for ints explicitly (bool subclasses int).
_FIELDS: dict[str, type] = {
    "schema": int,
    "device_id": str,
    "device_type": str,
    "site": str,
    "session": int,
    "seq": int,
    "ts": int,
    "readings": dict,
    # added by the IoT rule (broker-side, trusted)
    "mqtt_client_id": str,
    "topic_device_id": str,
    "ingest_ts": int,
}


class InvalidMessage(ValueError):
    """Message can never be processed; retrying won't help, so it's not sent to the DLQ."""


@dataclass(frozen=True)
class Telemetry:
    device_id: str
    device_type: str
    site: str
    session: int
    seq: int
    ts: int
    readings: dict
    mqtt_client_id: str
    topic_device_id: str
    ingest_ts: int


def parse(body: str) -> Telemetry:
    try:
        msg = json.loads(body)
    except json.JSONDecodeError as e:
        raise InvalidMessage(f"not JSON: {e.msg}") from None
    if not isinstance(msg, dict):
        raise InvalidMessage("not a JSON object")

    for name, typ in _FIELDS.items():
        if name not in msg:
            raise InvalidMessage(f"missing field {name!r}")
        v = msg[name]
        if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
            raise InvalidMessage(f"field {name!r} must be {typ.__name__}")

    if msg["schema"] != SCHEMA_VERSION:
        raise InvalidMessage(f"unsupported schema {msg['schema']}")
    for name in ("device_id", "mqtt_client_id", "topic_device_id", "device_type", "site"):
        if not msg[name] or len(msg[name]) > MAX_ID_LEN:
            raise InvalidMessage(f"field {name!r} empty or too long")
    if msg["seq"] < 1 or msg["session"] < 0 or msg["ts"] < 0:
        raise InvalidMessage("seq/session/ts out of range")
    for k, v in msg["readings"].items():
        if isinstance(v, bool) or not isinstance(v, int | float | str):
            raise InvalidMessage(f"reading {k!r} has unsupported type")

    return Telemetry(**{k: msg[k] for k in _FIELDS if k != "schema"})
