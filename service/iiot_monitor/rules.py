"""Detection rules (v1). Pure logic: no AWS calls, so every rule is unit-testable.

R1 identity     - unknown device, or claimed device_id != broker-verified client ID/topic
R2 plausibility - value outside physical range, or impossible rate of change
R3 flood        - per-device message rate above threshold (broker time, not device time)
R4 replay       - duplicate/old sequence numbers, old sessions, stale/future timestamps
                  (modelled on IEC 61850 GOOSE stNum/sqNum checking)
"""

from __future__ import annotations

import bisect
from collections import deque
from dataclasses import dataclass, field

from iiot_monitor.schema import Telemetry

CRITICAL, HIGH, MEDIUM = "critical", "high", "medium"

# (min, max, max rate of change per second or None).
# Ranges are *physical/sensor* plausibility, not alarm limits: a value outside them
# means a broken sensor or a forged message, not a process upset.
LIMITS: dict[str, dict[str, tuple[float, float, float | None]]] = {
    "feeder_bay": {
        "voltage_kv": (9.9, 12.1, 0.5),       # 11 kV +-10 %
        "current_a": (0.0, 480.0, None),      # 1.2x rated; load steps are legitimate
        "frequency_hz": (49.0, 51.0, 0.5),    # ROCOF relays trip around 0.5-1 Hz/s
        "power_factor": (0.0, 1.0, None),
        "active_power_mw": (0.0, 10.0, None),
    },
    "transformer": {
        "load_pct": (0.0, 150.0, None),
        "oil_temp_c": (-10.0, 110.0, 0.1),    # oil has a ~30 min thermal time constant
        "winding_temp_c": (-10.0, 140.0, 0.5),
        "tap_position": (1, 17, None),
    },
    "motor": {
        "speed_rpm": (0.0, 1550.0, None),     # 4-pole, 50 Hz: can't exceed ~1500
        "current_a": (0.0, 200.0, None),      # ~6x rated (locked rotor)
        "winding_temp_c": (-10.0, 155.0, 0.5),  # insulation class F
        "vibration_mm_s": (0.0, 45.0, None),  # sensor full scale
    },
    "pump_station": {
        "speed_rpm": (0.0, 3100.0, None),
        "discharge_pressure_bar": (0.0, 10.0, None),
        "flow_m3h": (0.0, 200.0, None),
        "motor_temp_c": (-10.0, 155.0, 0.5),
        "running": (0, 1, None),
        "fault_code": (0, 65535, None),
    },
}
ENUMS = {"feeder_bay": {"breaker": {"open", "closed"}}}


@dataclass(frozen=True)
class Finding:
    rule: str          # e.g. "R4.duplicate_seq"
    severity: str
    summary: str
    evidence: dict


@dataclass
class _DeviceState:
    session: int | None = None
    max_seq: int = 0
    seen: set[int] = field(default_factory=set)
    seen_order: deque = field(default_factory=deque)
    last: dict[str, tuple[float, int]] = field(default_factory=dict)  # signal -> (value, ts)
    arrivals: list[int] = field(default_factory=list)  # sorted broker ingest times
    flood_quiet_until: int = 0


@dataclass
class Config:
    flood_window_ms: int = 10_000
    flood_max: int = 10          # normal is 2 per 10 s (5 s interval); 10 = 5x headroom
    stale_ms: int = 30_000       # ~10x the measured device->broker skew (max 2.8 s)
    future_ms: int = 5_000
    seq_window: int = 256        # how far back we remember seq numbers per session


class Detector:
    def __init__(self, registry: dict[str, str], config: Config | None = None):
        """registry: device_id -> device_type (from the IoT Thing registry)."""
        self.registry = registry
        self.cfg = config or Config()
        self._state: dict[str, _DeviceState] = {}

    def warm_start(self, device_id: str, session: int, seq: int) -> None:
        """Restore sequence state after a service restart (from the latest-state table)."""
        st = self._state.setdefault(device_id, _DeviceState())
        st.session, st.max_seq = session, seq
        self._remember(st, seq)

    def check(self, t: Telemetry) -> list[Finding]:
        findings = self._identity(t)
        if findings:
            # Unknown or impersonated: we can't trust whose message this is, so it must not
            # touch any device's flood/sequence/value state (else an attacker impersonating
            # motor-01 could reset motor-01's session and make its real traffic look replayed).
            return findings
        st = self._state.setdefault(t.device_id, _DeviceState())
        findings += self._flood(t, st)
        replay = self._replay(t, st)
        findings += replay
        if not any(f.severity == HIGH for f in replay):
            # Only learn from messages we accept; a replayed value must not become baseline.
            findings += self._plausibility(t, st)
        return findings

    @staticmethod
    def is_replay(findings: list[Finding]) -> bool:
        return any(f.rule.startswith("R4.") and f.severity == HIGH for f in findings)

    # ---------------- R1 ----------------
    def _identity(self, t: Telemetry) -> list[Finding]:
        out = []
        if t.device_id not in self.registry:
            out.append(Finding("R1.unknown_device", CRITICAL,
                               f"message from unregistered device {t.device_id!r}",
                               {"device_id": t.device_id, "mqtt_client_id": t.mqtt_client_id}))
            return out
        if t.mqtt_client_id != t.device_id or t.topic_device_id != t.device_id:
            out.append(Finding("R1.identity_mismatch", CRITICAL,
                               f"payload claims {t.device_id!r} but broker saw client "
                               f"{t.mqtt_client_id!r} on topic of {t.topic_device_id!r}",
                               {"device_id": t.device_id, "mqtt_client_id": t.mqtt_client_id,
                                "topic_device_id": t.topic_device_id}))
        if self.registry[t.device_id] != t.device_type:
            out.append(Finding("R1.type_mismatch", CRITICAL,
                               f"{t.device_id} registered as {self.registry[t.device_id]!r}, "
                               f"message claims {t.device_type!r}",
                               {"registered": self.registry[t.device_id],
                                "claimed": t.device_type}))
        return out

    # ---------------- R3 ----------------
    def _flood(self, t: Telemetry, st: _DeviceState) -> list[Finding]:
        # SQS doesn't preserve order (and a backlog drains all at once), so arrivals are kept
        # sorted by broker time and we count the window (now - w, now] around THIS message,
        # instead of assuming each message is the newest.
        now, w = t.ingest_ts, self.cfg.flood_window_ms
        bisect.insort(st.arrivals, now)
        count = bisect.bisect_right(st.arrivals, now) - bisect.bisect_right(st.arrivals, now - w)
        del st.arrivals[:bisect.bisect_right(st.arrivals, st.arrivals[-1] - w)]  # bound memory
        if count > self.cfg.flood_max and now >= st.flood_quiet_until:
            st.flood_quiet_until = now + w  # one finding per window, not one per message
            return [Finding("R3.message_flood", HIGH,
                            f"{count} messages in {w // 1000}s (limit {self.cfg.flood_max})",
                            {"count": count, "window_ms": w, "limit": self.cfg.flood_max})]
        return []

    # ---------------- R4 ----------------
    def _remember(self, st: _DeviceState, seq: int) -> None:
        st.seen.add(seq)
        st.seen_order.append(seq)
        while len(st.seen_order) > self.cfg.seq_window:
            st.seen.discard(st.seen_order.popleft())

    def _replay(self, t: Telemetry, st: _DeviceState) -> list[Finding]:
        out = []
        ev = {"session": t.session, "seq": t.seq, "last_session": st.session,
              "last_max_seq": st.max_seq}
        if t.session > t.ingest_ts + self.cfg.future_ms:
            out.append(Finding("R4.future_session", HIGH, "session (boot time) is in the future",
                               ev))
        if st.session is None or t.session > st.session:
            # First message or a device reboot: new baseline.
            st.session, st.max_seq = t.session, t.seq
            st.seen.clear()
            st.seen_order.clear()
            self._remember(st, t.seq)
        elif t.session < st.session:
            # Late delivery: ingested by the broker before the reboot (SQS reordering).
            # Replay: a NEW publish, so the broker receives it after the reboot.
            # +future_ms because st.session comes from the device clock (~2 s behind broker);
            # trade-off: a replay within 5 s of a reboot is not flagged (documented).
            if t.ingest_ts > st.session + self.cfg.future_ms:
                out.append(Finding("R4.old_session", HIGH,
                                   "old-session message sent after reboot: replay", ev))
        elif t.seq in st.seen:
            out.append(Finding("R4.duplicate_seq", HIGH, f"seq {t.seq} already received", ev))
        elif t.seq <= st.max_seq - self.cfg.seq_window:
            out.append(Finding("R4.old_seq", HIGH,
                               f"seq {t.seq} is older than the replay window", ev))
        else:
            # New seq; may arrive slightly out of order (SQS standard queues don't guarantee
            # order), which is fine as long as we haven't seen it.
            st.max_seq = max(st.max_seq, t.seq)
            self._remember(st, t.seq)

        lag = t.ingest_ts - t.ts
        if lag > self.cfg.stale_ms:
            out.append(Finding("R4.stale", MEDIUM, f"message is {lag / 1000:.1f}s old",
                               {**ev, "lag_ms": lag}))
        elif -lag > self.cfg.future_ms:
            out.append(Finding("R4.future_ts", MEDIUM, f"timestamp {-lag / 1000:.1f}s in future",
                               {**ev, "lag_ms": lag}))
        return out

    # ---------------- R2 ----------------
    def _plausibility(self, t: Telemetry, st: _DeviceState) -> list[Finding]:
        out = []
        for sig, allowed in ENUMS.get(t.device_type, {}).items():
            v = t.readings.get(sig)
            if v is not None and v not in allowed:
                out.append(Finding("R2.invalid_state", HIGH, f"{sig}={v!r} is not a valid state",
                                   {"signal": sig, "value": v}))
        for sig, (lo, hi, max_rate) in LIMITS.get(t.device_type, {}).items():
            v = t.readings.get(sig)
            if not isinstance(v, int | float):
                continue
            if not lo <= v <= hi:
                out.append(Finding("R2.out_of_range", HIGH,
                                   f"{sig}={v} outside physical range [{lo}, {hi}]",
                                   {"signal": sig, "value": v, "min": lo, "max": hi}))
                continue  # don't learn an implausible value
            prev = st.last.get(sig)
            if max_rate is not None and prev is not None and t.ts > prev[1]:
                rate = abs(v - prev[0]) / ((t.ts - prev[1]) / 1000)
                if rate > max_rate:
                    out.append(Finding("R2.rate_of_change", MEDIUM,
                                       f"{sig} changing {rate:.2f}/s (max {max_rate}/s)",
                                       {"signal": sig, "value": v, "previous": prev[0],
                                        "rate_per_s": round(rate, 3), "max_rate": max_rate}))
                    continue
            if prev is None or t.ts > prev[1]:  # a late (older) message must not become baseline
                st.last[sig] = (v, t.ts)
        return out
