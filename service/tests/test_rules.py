"""One section per detection rule: the attack is caught, and normal behaviour is not."""

from helpers import FEEDER_OK, REGISTRY, SESSION, T0, rules, tel

from iiot_monitor.rules import CRITICAL, HIGH, MEDIUM, Config, Detector


def det(**cfg) -> Detector:
    return Detector(dict(REGISTRY), Config(**cfg))


def stream(d, n, start_seq=1, interval_ms=5000, **over):
    """Feed n normal messages; return all findings."""
    out = []
    for i in range(n):
        ts = T0 + i * interval_ms
        out += d.check(tel(seq=start_seq + i, ts=ts, ingest_ts=ts + 1500, **over))
    return out


def test_normal_stream_has_no_findings():
    assert stream(det(), 50) == []


# ---------------- R1 identity ----------------

def test_r1_unknown_device():
    f = det().check(tel(device_id="rogue-99", mqtt_client_id="rogue-99",
                        topic_device_id="rogue-99"))
    assert rules(f) == ["R1.unknown_device"] and f[0].severity == CRITICAL


def test_r1_payload_claims_other_device_than_client_id():
    # Valid feeder-01 connection, but payload claims to be motor-01's twin feeder.
    f = det().check(tel(mqtt_client_id="motor-01"))
    assert "R1.identity_mismatch" in rules(f)


def test_r1_topic_mismatch():
    f = det().check(tel(topic_device_id="xfmr-01"))
    assert "R1.identity_mismatch" in rules(f)


def test_r1_device_type_mismatch():
    f = det().check(tel(device_type="motor"))
    assert "R1.type_mismatch" in rules(f)


def test_r1_impersonation_cannot_poison_victim_state():
    # feeder-01's cert sends a payload claiming to be motor-01 with a brand-new session.
    # If that reset motor-01's baseline, motor-01's genuine traffic would look replayed.
    d = det()
    real = dict(device_id="motor-01", device_type="motor", mqtt_client_id="motor-01",
                topic_device_id="motor-01",
                readings={"speed_rpm": 1470.0, "current_a": 22.0, "winding_temp_c": 60.0,
                          "vibration_mm_s": 1.8})
    assert d.check(tel(seq=1, **real)) == []
    fake = {**real, "mqtt_client_id": "feeder-01", "topic_device_id": "feeder-01"}
    assert rules(d.check(tel(session=T0 + 999_999, seq=1, **fake))) == ["R1.identity_mismatch"]
    assert d.check(tel(seq=2, ts=T0 + 5000, ingest_ts=T0 + 6500, **real)) == []


# ---------------- R2 plausibility ----------------

def test_r2_out_of_range_value():
    f = det().check(tel(readings={**FEEDER_OK, "frequency_hz": 55.0}))
    assert rules(f) == ["R2.out_of_range"] and f[0].severity == HIGH
    assert f[0].evidence["signal"] == "frequency_hz"


def test_r2_impossible_rate_of_change():
    # The CLAUDE.md example: frequency jumping 5 Hz in 100 ms (here within the range,
    # so only the rate rule can catch it: 50.0 -> 50.9 in 100 ms = 9 Hz/s).
    d = det()
    assert d.check(tel(seq=1, ts=T0)) == []
    f = d.check(tel(seq=2, ts=T0 + 100, ingest_ts=T0 + 1600,
                    readings={**FEEDER_OK, "frequency_hz": 50.9}))
    assert rules(f) == ["R2.rate_of_change"] and f[0].severity == MEDIUM


def test_r2_slow_legitimate_change_is_fine():
    d = det()
    d.check(tel(seq=1, ts=T0))
    f = d.check(tel(seq=2, ts=T0 + 5000, ingest_ts=T0 + 6500,
                    readings={**FEEDER_OK, "frequency_hz": 50.05}))
    assert f == []


def test_r2_invalid_breaker_state():
    f = det().check(tel(readings={**FEEDER_OK, "breaker": "melted"}))
    assert rules(f) == ["R2.invalid_state"]


def test_r2_out_of_range_value_does_not_become_baseline():
    d = det()
    d.check(tel(seq=1, ts=T0))
    d.check(tel(seq=2, ts=T0 + 5000, ingest_ts=T0 + 6500,
                readings={**FEEDER_OK, "voltage_kv": 99.0}))
    # Next normal value is compared against 11.0 (last plausible), so no rate alarm.
    assert d.check(tel(seq=3, ts=T0 + 10000, ingest_ts=T0 + 11500)) == []


# ---------------- R3 flood ----------------

def test_r3_flood_detected_once_per_window():
    # 30 msgs at 10 msg/s (normal: 0.2 msg/s) all fall inside one 10 s window.
    f = stream(det(), 30, interval_ms=100)
    assert rules(f) == ["R3.message_flood"]
    assert f[0].evidence["count"] == 11  # fires on the first message over the limit


def test_r3_sustained_flood_fires_again_in_next_window():
    f = stream(det(), 250, interval_ms=100)  # 25 s of flooding
    assert rules(f) == ["R3.message_flood"] * 3  # windows starting at ~1 s, ~11 s, ~21 s


def test_r3_uses_broker_time_not_device_time():
    # Attacker floods but forges device ts 5 s apart; broker ingest_ts reveals the burst.
    d, out = det(), []
    for i in range(20):
        out += d.check(tel(seq=i + 1, ts=T0 + i * 5000, ingest_ts=T0 + 1500 + i * 50))
    assert "R3.message_flood" in rules(out)


def test_r3_normal_rate_never_floods():
    assert stream(det(), 500) == []


def backlog(seed=0):
    """Shape of the real 2026-11-15 SQS backlog: two sessions ~3 h apart, 5 s apart within
    each, delivered in shuffled order (SQS standard queues don't preserve order)."""
    import random
    msgs = []
    for session_start, n in ((SESSION, 14), (SESSION + 3 * 3600_000, 40)):
        for i in range(n):
            ts = session_start + 5_000 + i * 5_000
            msgs.append(tel(session=session_start, seq=i + 1, ts=ts, ingest_ts=ts + 1500))
    random.Random(seed).shuffle(msgs)
    return msgs


def test_r3_shuffled_backlog_is_not_a_flood():
    # Regression: 19 false R3 alerts when the real backlog drained out of order.
    d = det()
    assert [f for m in backlog() for f in d.check(m) if f.rule.startswith("R3")] == []


# ---------------- R4 replay / sequence ----------------

def test_r4_exact_replay_is_duplicate_seq():
    d = det()
    stream(d, 10)
    f = d.check(tel(seq=5, ts=T0 + 4 * 5000, ingest_ts=T0 + 60_000))
    assert "R4.duplicate_seq" in rules(f)


def test_r4_replay_from_previous_session():
    d = det()
    stream(d, 3, session=SESSION + 10_000)        # device rebooted, new session
    f = d.check(tel(session=SESSION, seq=99))     # captured pre-reboot message replayed
    assert "R4.old_session" in rules(f)


def test_r4_late_delivery_from_previous_session_is_not_replay():
    # Regression: 41 false R4.old_session alerts. The old-session messages were genuinely
    # published BEFORE the reboot (broker ingest_ts < new session start), just processed late.
    d = det()
    new_session = SESSION + 3 * 3600_000
    d.check(tel(session=new_session, seq=1, ts=new_session + 5000,
                ingest_ts=new_session + 6500))
    late = tel(session=SESSION, seq=7, ts=SESSION + 35_000, ingest_ts=SESSION + 36_500)
    assert d.check(late) == []


def test_r4_shuffled_backlog_has_no_findings():
    d = det()
    assert [f for m in backlog(seed=1) for f in d.check(m)] == []


def test_r4_reboot_is_not_an_attack():
    d = det()
    stream(d, 5)
    f = d.check(tel(session=SESSION + 30_000, seq=1, ts=T0 + 30_000, ingest_ts=T0 + 31_500))
    assert f == []


def test_r4_out_of_order_but_new_seq_is_fine():
    # SQS standard queues can reorder; an unseen seq is not a replay.
    d = det()
    d.check(tel(seq=1))
    d.check(tel(seq=3, ts=T0 + 10000, ingest_ts=T0 + 11500))
    assert d.check(tel(seq=2, ts=T0 + 5000, ingest_ts=T0 + 11600)) == []


def test_r4_seq_older_than_window():
    d = det(seq_window=10)
    stream(d, 50)
    f = d.check(tel(seq=3, ts=T0 + 49 * 5000, ingest_ts=T0 + 49 * 5000 + 1500))
    assert "R4.old_seq" in rules(f)


def test_r4_stale_timestamp():
    f = det().check(tel(ts=T0, ingest_ts=T0 + 45_000))
    assert rules(f) == ["R4.stale"] and f[0].severity == MEDIUM


def test_r4_measured_skew_is_tolerated():
    # Max measured device->broker lag on 2026-11-15 was 2.8 s (WSL clock ~2 s behind).
    assert det().check(tel(ts=T0, ingest_ts=T0 + 2826)) == []


def test_r4_future_timestamp():
    f = det().check(tel(ts=T0 + 60_000, ingest_ts=T0))
    assert "R4.future_ts" in rules(f)


def test_r4_warm_start_detects_replay_after_service_restart():
    d = det()
    d.warm_start("feeder-01", SESSION, 40)   # restored from the latest-state table
    f = d.check(tel(seq=40))
    assert "R4.duplicate_seq" in rules(f)


def test_replayed_value_is_not_learned_as_baseline():
    d = det()
    d.check(tel(seq=1, ts=T0))
    d.check(tel(seq=1, ts=T0 + 100, readings={**FEEDER_OK, "frequency_hz": 50.9}))  # replay
    # If the replayed 50.9 had been learned, this would be a rate-of-change alarm.
    assert d.check(tel(seq=2, ts=T0 + 5000, ingest_ts=T0 + 6500)) == []
