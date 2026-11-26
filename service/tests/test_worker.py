"""Worker delivery semantics, with in-memory fakes instead of AWS."""

from helpers import REGISTRY, T0, sqs_msg

from iiot_monitor.alerts import Notifier
from iiot_monitor.rules import Detector
from iiot_monitor.worker import Worker


class FakeSQS:
    def __init__(self, msgs):
        self.msgs, self.deleted = list(msgs), []

    def receive_message(self, **_):
        batch, self.msgs = self.msgs[:10], self.msgs[10:]
        return {"Messages": batch}

    def delete_message(self, ReceiptHandle, **_):
        self.deleted.append(ReceiptHandle)


class FakeStore:
    def __init__(self, fail=False):
        self.readings, self.latest, self.alerts, self.fail = [], [], [], fail

    def put_reading(self, t):
        if self.fail:
            raise RuntimeError("DynamoDB throttled")
        self.readings.append(t)

    def put_latest(self, t):
        self.latest.append(t)

    def put_alert(self, a):
        self.alerts.append(a)


def make(msgs, store=None):
    sqs, store = FakeSQS(msgs), store or FakeStore()
    w = Worker(sqs=sqs, queue_url="q", detector=Detector(dict(REGISTRY)), store=store,
               notifier=Notifier(None, None))
    return w, sqs, store


def test_good_message_stored_and_deleted():
    w, sqs, store = make([sqs_msg("m1")])
    assert w.poll_once() == 1
    assert len(store.readings) == 1 and store.alerts == []
    assert sqs.deleted == ["rh-m1"]


def test_sqs_redelivery_is_not_flagged_as_replay():
    # Same MessageId delivered twice (SQS at-least-once) -> processed once, no R4 alert.
    w, sqs, store = make([sqs_msg("m1"), sqs_msg("m1")])
    w.poll_once()
    assert len(store.readings) == 1 and store.alerts == []
    assert sqs.deleted == ["rh-m1", "rh-m1"]


def test_real_replay_is_alerted_and_not_stored():
    # Same payload, new MessageId: a second MQTT publish of captured data.
    w, sqs, store = make([sqs_msg("m1"), sqs_msg("m2")])
    w.poll_once()
    assert len(store.readings) == 1
    assert [a.rule for a in store.alerts] == ["R4.duplicate_seq"]


def test_impersonation_is_alerted_and_not_stored():
    w, _, store = make([sqs_msg("m1", mqtt_client_id="motor-01")])
    w.poll_once()
    assert store.readings == []
    assert store.alerts[0].rule == "R1.identity_mismatch"


def test_malformed_message_alerted_and_deleted():
    w, sqs, store = make([{"MessageId": "x", "ReceiptHandle": "rh-x", "Body": "{oops"}])
    w.poll_once()
    assert store.alerts[0].rule == "R0.malformed"
    assert sqs.deleted == ["rh-x"]  # retrying can't fix it, so don't send it to the DLQ


def test_processing_error_leaves_message_for_retry():
    w, sqs, store = make([sqs_msg("m1")], store=FakeStore(fail=True))
    w.poll_once()
    assert sqs.deleted == []  # becomes visible again; after 5 failures -> DLQ


def test_alert_timestamp_is_broker_time():
    w, _, store = make([sqs_msg("m1", readings={"frequency_hz": 70.0})])
    w.poll_once()
    assert store.alerts[0].detected_at == T0 + 1500
