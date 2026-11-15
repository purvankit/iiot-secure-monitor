"""Telemetry message envelope shared by every device.

Every message carries three anti-replay fields, modelled on IEC 61850 GOOSE:

* ``session`` - device boot time (ms). Changes only when the device restarts,
  like GOOSE's confRev/stNum reset; a legitimate restart always has a newer session.
* ``seq``     - per-session counter, +1 per message (like GOOSE sqNum).
* ``ts``      - device wall-clock time (ms, UTC) so the cloud can reject stale messages.

The detection service (M3) flags duplicates, regressions and stale timestamps.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

SCHEMA_VERSION = 1
TOPIC_TEMPLATE = "iiot/{device_id}/telemetry"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def telemetry_topic(device_id: str) -> str:
    """One topic per device, so the broker/IoT policy can pin a device to its own topic."""
    return TOPIC_TEMPLATE.format(device_id=device_id)


@dataclass
class Sequencer:
    """Generates (session, seq, ts) for one device."""

    session: int = field(default_factory=now_ms)
    seq: int = 0

    def next(self) -> tuple[int, int, int]:
        self.seq += 1
        return self.session, self.seq, now_ms()


def build_message(
    *,
    device_id: str,
    device_type: str,
    site: str,
    readings: dict[str, float | int | str],
    sequencer: Sequencer,
    extra: dict | None = None,
) -> dict:
    session, seq, ts = sequencer.next()
    msg = {
        "schema": SCHEMA_VERSION,
        "device_id": device_id,
        "device_type": device_type,
        "site": site,
        "session": session,
        "seq": seq,
        "ts": ts,
        "readings": readings,
    }
    if extra:
        msg.update(extra)
    return msg


def encode(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":")).encode()
