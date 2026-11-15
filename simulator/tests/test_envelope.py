import json
from itertools import pairwise

from iiot_sim import envelope


def make(seqr, **kw):
    return envelope.build_message(device_id="feeder-01", device_type="feeder_bay",
                                  site="s1", readings={"x": 1}, sequencer=seqr, **kw)


def test_seq_is_monotonic_and_session_stable():
    seqr = envelope.Sequencer()
    msgs = [make(seqr) for _ in range(100)]
    assert [m["seq"] for m in msgs] == list(range(1, 101))
    assert {m["session"] for m in msgs} == {seqr.session}
    assert all(a["ts"] <= b["ts"] for a, b in pairwise(msgs))


def test_restart_gets_newer_session():
    a = envelope.Sequencer()
    b = envelope.Sequencer(session=a.session + 1)  # a reboot later
    before, after = make(a), make(b)
    assert after["session"] > before["session"]
    assert after["seq"] == 1


def test_required_fields_and_roundtrip():
    msg = make(envelope.Sequencer(), extra={"source": {"protocol": "modbus-tcp"}})
    decoded = json.loads(envelope.encode(msg))
    assert decoded == msg
    for k in ("schema", "device_id", "device_type", "site", "session", "seq", "ts", "readings"):
        assert k in decoded
    assert decoded["source"]["protocol"] == "modbus-tcp"


def test_topic_is_per_device():
    assert envelope.telemetry_topic("motor-01") == "iiot/motor-01/telemetry"
