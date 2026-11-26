"""DynamoDB persistence.

Tables (on-demand billing, so they cost nothing when idle):
  readings  pk=device_id, sk="<ts>#<seq>"     time series, TTL-expired after N days
  latest    pk=device_id                       last accepted message per device (+ seq state)
  alerts    pk=device_id, sk="<ms>#<alert_id>" every alert, newest last
"""

from __future__ import annotations

import json
from decimal import Decimal

from iiot_monitor.alerts import Alert
from iiot_monitor.schema import Telemetry


def to_dynamo(obj):
    """DynamoDB rejects Python floats; round-trip through JSON into Decimal."""
    return json.loads(json.dumps(obj, default=str), parse_float=Decimal)


class Store:
    def __init__(self, dynamodb, readings: str, latest: str, alerts: str,
                 reading_ttl_days: int = 7):
        self.readings = dynamodb.Table(readings)
        self.latest = dynamodb.Table(latest)
        self.alerts = dynamodb.Table(alerts)
        self.ttl_s = reading_ttl_days * 86400

    def put_reading(self, t: Telemetry) -> None:
        # Same message twice -> same key -> overwrite: writes are idempotent.
        self.readings.put_item(Item=to_dynamo({
            "device_id": t.device_id,
            "sk": f"{t.ts:013d}#{t.seq:010d}",
            "ts": t.ts, "ingest_ts": t.ingest_ts, "session": t.session, "seq": t.seq,
            "site": t.site, "readings": t.readings,
            "expires_at": t.ingest_ts // 1000 + self.ttl_s,
        }))

    def put_latest(self, t: Telemetry) -> None:
        self.latest.put_item(Item=to_dynamo({
            "device_id": t.device_id, "device_type": t.device_type, "site": t.site,
            "ts": t.ts, "ingest_ts": t.ingest_ts, "session": t.session, "seq": t.seq,
            "readings": t.readings,
        }))

    def put_alert(self, a: Alert) -> None:
        self.alerts.put_item(Item=to_dynamo({
            **a.to_dict(), "sk": f"{a.detected_at:013d}#{a.alert_id}",
        }))

    def load_latest(self) -> list[dict]:
        items, kwargs = [], {}
        while True:
            page = self.latest.scan(**kwargs)
            items += page["Items"]
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
