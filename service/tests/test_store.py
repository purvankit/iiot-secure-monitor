"""The real Store against an in-memory stand-in for boto3's DynamoDB Table."""

from decimal import Decimal

from helpers import SESSION, T0, tel

from iiot_monitor.alerts import Alert
from iiot_monitor.rules import Finding
from iiot_monitor.store import Store, to_dynamo


class FakeTable:
    """Keyed by (pk, sk) like DynamoDB: a put with the same key overwrites."""

    def __init__(self, page_size=2):
        self.items, self.page_size = {}, page_size

    def put_item(self, Item):
        for v in _walk(Item):
            assert not isinstance(v, float), "DynamoDB rejects Python floats"
        self.items[(Item["device_id"], Item.get("sk"))] = Item

    def scan(self, ExclusiveStartKey=None, **_):
        keys = sorted(self.items, key=str)
        start = keys.index(ExclusiveStartKey["k"]) + 1 if ExclusiveStartKey else 0
        page = keys[start:start + self.page_size]
        out = {"Items": [self.items[k] for k in page]}
        if start + self.page_size < len(keys):
            out["LastEvaluatedKey"] = {"k": page[-1]}
        return out


def _walk(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj


class FakeDynamo:
    def __init__(self):
        self.tables = {}

    def Table(self, name):  # noqa: N802 (boto3's name)
        return self.tables.setdefault(name, FakeTable())


def store():
    db = FakeDynamo()
    return Store(db, "r", "l", "a"), db


def test_to_dynamo_converts_nested_floats_to_decimal():
    out = to_dynamo({"a": 1.5, "b": {"c": [0.1, 2]}, "s": "x"})
    assert out == {"a": Decimal("1.5"), "b": {"c": [Decimal("0.1"), 2]}, "s": "x"}


def test_reading_key_is_time_sortable_and_has_ttl():
    s, db = store()
    s.put_reading(tel(seq=7, ts=T0, ingest_ts=T0 + 1500))
    s.put_reading(tel(seq=10, ts=T0 + 5000, ingest_ts=T0 + 6500))
    items = db.tables["r"].items
    sks = sorted(k[1] for k in items)
    assert sks == [f"{T0:013d}#{7:010d}", f"{T0 + 5000:013d}#{10:010d}"]  # lexical == time
    first = items[("feeder-01", sks[0])]
    assert first["expires_at"] == (T0 + 1500) // 1000 + 7 * 86400


def test_same_message_twice_overwrites_not_duplicates():
    s, db = store()
    s.put_reading(tel(seq=1))
    s.put_reading(tel(seq=1))
    assert len(db.tables["r"].items) == 1  # idempotent writes


def test_latest_keeps_one_item_per_device():
    s, db = store()
    s.put_latest(tel(seq=1))
    s.put_latest(tel(seq=2, ts=T0 + 5000))
    (item,) = db.tables["l"].items.values()
    assert item["seq"] == 2 and item["session"] == SESSION


def test_alert_stored_with_time_ordered_key():
    s, db = store()
    a = Alert.from_finding(Finding("R3.message_flood", "high", "s", {"rate": 1.5}),
                           "feeder-01", T0)
    s.put_alert(a)
    (item,) = db.tables["a"].items.values()
    assert item["sk"] == f"{T0:013d}#{a.alert_id}"
    assert item["evidence"]["rate"] == Decimal("1.5")


def test_load_latest_follows_pagination():
    s, db = store()
    for d in ("feeder-01", "motor-01", "xfmr-01", "edge-gw-01", "feeder-02"):
        s.put_latest(tel(device_id=d))
    assert len(s.load_latest()) == 5  # page_size=2 -> 3 pages
