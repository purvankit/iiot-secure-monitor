"""attacks/run_attack.py: the replay 'capture' must be genuine pre-attack device traffic."""

import importlib.util
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def attack(monkeypatch):
    spec = importlib.util.spec_from_file_location("run_attack", ROOT / "attacks/run_attack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeS3:
    def __init__(self, objects):
        self.objects = objects  # key -> (last_modified datetime, body dict)

    def list_buckets(self):
        return {"Buckets": [{"Name": "other"}, {"Name": "iiot-secure-monitor-raw-123"}]}

    def get_paginator(self, _):
        objs = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k, "LastModified": lm}
                                      for k, (lm, _) in objs.items() if k.startswith(Prefix)]}]
        return P()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(json.dumps(self.objects[Key][1]).encode())}


def at(s):
    return datetime.fromtimestamp(s, tz=UTC)


def test_capture_ignores_traffic_written_after_cutoff(attack, monkeypatch):
    import boto3
    prefix = datetime.now(UTC).strftime("raw/feeder-01/%Y/%m/%d/")
    genuine = {"device_id": "feeder-01", "seq": 12, "session": 1, "ts": 1,
               "mqtt_client_id": "feeder-01", "topic_device_id": "feeder-01", "ingest_ts": 2}
    own_attack = {**genuine, "seq": 50, "session": 999}
    s3 = FakeS3({prefix + "a.json": (at(1000), genuine),
                 prefix + "b.json": (at(2000), own_attack)})
    monkeypatch.setattr(boto3, "client", lambda *a, **k: s3)
    attack.CAPTURE_BEFORE_MS = 1500 * 1000

    captured = attack.latest_capture()

    assert captured["seq"] == 12  # newest object BEFORE the cutoff, not the attacker's own
    for f in ("mqtt_client_id", "topic_device_id", "ingest_ts"):
        assert f not in captured  # stripped: the broker re-adds them, as for a real replay


def test_every_attack_declares_expected_rules(attack):
    # Each attack documents which alerts it must raise, so a demo run is checkable.
    assert set(attack.ATTACKS) == {"spoof", "flood", "replay", "impersonate", "foreign-topic"}
