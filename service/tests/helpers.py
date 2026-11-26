import json

from iiot_monitor.schema import Telemetry

REGISTRY = {"feeder-01": "feeder_bay", "motor-01": "motor", "xfmr-01": "transformer",
            "edge-gw-01": "pump_station"}
T0 = 1_800_000_000_000  # ms
SESSION = T0 - 60_000

FEEDER_OK = {"voltage_kv": 11.0, "current_a": 240.0, "frequency_hz": 50.0,
             "power_factor": 0.95, "active_power_mw": 4.3, "breaker": "closed"}


def body(**over) -> dict:
    msg = {"schema": 1, "device_id": "feeder-01", "device_type": "feeder_bay",
           "site": "s1", "session": SESSION, "seq": 1, "ts": T0, "readings": dict(FEEDER_OK),
           "mqtt_client_id": "feeder-01", "topic_device_id": "feeder-01",
           "ingest_ts": T0 + 1500}
    msg.update(over)
    return msg


def tel(**over) -> Telemetry:
    b = body(**over)
    b.pop("schema")
    return Telemetry(**b)


def sqs_msg(mid: str, **over) -> dict:
    return {"MessageId": mid, "ReceiptHandle": f"rh-{mid}", "Body": json.dumps(body(**over))}


def rules(findings) -> list[str]:
    return [f.rule for f in findings]
