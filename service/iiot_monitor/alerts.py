"""Alerts: every finding is stored; email (SNS) is rate-limited per device+rule.

Without the cooldown, a flood or replay attack would also flood the operator's inbox
(alert fatigue), which is itself a way to hide a real attack.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass

from iiot_monitor.rules import Finding

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    alert_id: str
    device_id: str
    rule: str
    severity: str
    summary: str
    evidence: dict
    detected_at: int  # ms, broker/ingest time of the triggering message
    raised_at: int    # ms, service wall clock when the alert was raised (for latency)

    @classmethod
    def from_finding(cls, f: Finding, device_id: str, detected_at: int) -> Alert:
        return cls(str(uuid.uuid4()), device_id, f.rule, f.severity, f.summary, f.evidence,
                   detected_at, int(time.time() * 1000))

    def to_dict(self) -> dict:
        return asdict(self)


class Notifier:
    def __init__(self, sns_client, topic_arn: str | None, cooldown_ms: int = 300_000,
                 clock=lambda: int(time.time() * 1000)):
        self.sns, self.topic_arn, self.cooldown_ms = sns_client, topic_arn, cooldown_ms
        self.clock = clock
        self._last_sent: dict[tuple[str, str], int] = {}

    def should_send(self, a: Alert) -> bool:
        # Cooldown runs on wall-clock time, NOT the message's timestamp: it protects the
        # inbox, and a backlog can contain messages hours apart that are all emailed now.
        key, now = (a.device_id, a.rule), self.clock()
        last = self._last_sent.get(key)
        if last is not None and now - last < self.cooldown_ms:
            return False
        self._last_sent[key] = now
        return True

    def notify(self, a: Alert) -> bool:
        if not self.topic_arn or not self.should_send(a):
            return False
        subject = f"[{a.severity.upper()}] {a.rule} on {a.device_id}"[:100]
        body = f"{a.summary}\n\n" + json.dumps(a.to_dict(), indent=2, default=str)
        self.sns.publish(TopicArn=self.topic_arn, Subject=subject, Message=body,
                         MessageAttributes={"severity": {"DataType": "String",
                                                         "StringValue": a.severity}})
        log.info("emailed %s", subject)
        return True
