import json

import pytest
from helpers import body

from iiot_monitor.schema import InvalidMessage, parse


def test_valid_message_parses():
    t = parse(json.dumps(body()))
    assert t.device_id == "feeder-01" and t.seq == 1 and t.readings["breaker"] == "closed"


@pytest.mark.parametrize("raw, reason", [
    ("not json", "not JSON"),
    ("[1, 2]", "not a JSON object"),
    (json.dumps({k: v for k, v in body().items() if k != "seq"}), "missing field 'seq'"),
    (json.dumps(body(seq="5")), "'seq' must be int"),
    (json.dumps(body(seq=True)), "'seq' must be int"),   # bool is an int subclass
    (json.dumps(body(schema=2)), "unsupported schema"),
    (json.dumps(body(seq=0)), "out of range"),
    (json.dumps(body(device_id="")), "empty or too long"),
    (json.dumps(body(device_id="x" * 129)), "empty or too long"),
    (json.dumps(body(readings={"v": [1, 2]})), "unsupported type"),
    (json.dumps(body(readings={"v": None})), "unsupported type"),
])
def test_invalid_messages_rejected(raw, reason):
    with pytest.raises(InvalidMessage, match=reason):
        parse(raw)


def test_missing_broker_fields_rejected():
    # A message that didn't come through our IoT rule lacks broker-side fields.
    b = body()
    del b["mqtt_client_id"]
    with pytest.raises(InvalidMessage, match="mqtt_client_id"):
        parse(json.dumps(b))
