import json

from iiot_monitor.alerts import Alert, Notifier
from iiot_monitor.authlog import AuthLogPoller, to_finding
from iiot_monitor.rules import Finding


class FakeSNS:
    def __init__(self):
        self.sent = []

    def publish(self, **kw):
        self.sent.append(kw)


def alert(at, device="feeder-01", rule="R3.message_flood"):
    return Alert.from_finding(Finding(rule, "high", "s", {}), device, at)


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def test_email_cooldown_per_device_and_rule():
    sns, clock = FakeSNS(), Clock()
    n = Notifier(sns, "arn:topic", cooldown_ms=300_000, clock=clock)
    assert n.notify(alert(0))
    clock.now = 60_000
    assert not n.notify(alert(0))                      # same device+rule within 5 min
    assert n.notify(alert(0, rule="R4.stale"))         # different rule
    assert n.notify(alert(0, device="motor-01"))       # different device
    clock.now = 301_000
    assert n.notify(alert(0))                          # cooldown over
    assert len(sns.sent) == 4
    assert sns.sent[0]["Subject"] == "[HIGH] R3.message_flood on feeder-01"


def test_backlog_hours_apart_still_one_email():
    # Regression (2026-11-15): backlog alerts with detected_at 3 h apart each got an email,
    # because the cooldown used message time. It must use wall-clock time.
    sns = FakeSNS()
    n = Notifier(sns, "arn:topic", clock=Clock())
    n.notify(alert(1_794_740_706_000))
    n.notify(alert(1_794_752_113_000))
    assert len(sns.sent) == 1


def test_no_topic_means_no_email():
    assert not Notifier(FakeSNS(), None).notify(alert(0))


# Shape copied from a real AWSIotLogsV2 event captured on 2026-11-15 (IDs shortened).
PUBLISH_DENIED = json.dumps({
    "timestamp": "2026-11-15 11:20:01.123", "logLevel": "ERROR", "traceId": "t",
    "accountId": "123", "status": "Failure", "eventType": "Publish-In",
    "protocol": "MQTT", "topicName": "iiot/motor-01/telemetry", "clientId": "feeder-01",
    "principalId": "abc", "sourceIp": "203.0.113.7", "sourcePort": 1234,
    "reason": "AUTHORIZATION_FAILURE",
})


def test_authlog_publish_denied_becomes_finding():
    client, f = to_finding(PUBLISH_DENIED)
    assert client == "feeder-01"
    assert f.rule == "R1.auth_failure_publish_in" and f.severity == "high"
    assert f.evidence["topicName"] == "iiot/motor-01/telemetry"
    assert f.evidence["sourceIp"] == "203.0.113.7"


def test_authlog_ignores_other_events():
    assert to_finding(json.dumps({"reason": "OK"})) is None
    assert to_finding("not json") is None


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **_):
        return self.pages


class FakeLogs:
    def __init__(self, events):
        self.events = events

    def get_paginator(self, _):
        return FakePaginator([{"events": self.events}])


def test_authlog_poller_dedupes_events_across_polls():
    ev = {"eventId": "e1", "timestamp": 1000, "message": PUBLISH_DENIED}
    p = AuthLogPoller(FakeLogs([ev]))
    assert len(p.poll()) == 1
    assert p.poll() == []  # same event returned again (inclusive startTime) -> ignored
    assert p.since == 1000
