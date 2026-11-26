"""R1 (part 2): IoT Core authorisation failures from CloudWatch Logs.

These never reach SQS: IoT Core rejects the connect/publish, so the only evidence is
its log. We poll AWSIotLogsV2 for AUTHORIZATION_FAILURE events. Clients that fail the
TLS handshake (no valid cert) are blocked before MQTT and are not logged at all.
"""

from __future__ import annotations

import json
from collections import OrderedDict

from iiot_monitor.rules import HIGH, Finding

FILTER_PATTERN = '{ $.reason = "AUTHORIZATION_FAILURE" }'


def to_finding(message: str) -> tuple[str, Finding] | None:
    """Turn one IoT log line into (claimed_client_id, Finding)."""
    try:
        e = json.loads(message)
    except json.JSONDecodeError:
        return None
    if e.get("reason") != "AUTHORIZATION_FAILURE":
        return None
    client = e.get("clientId") or "unknown"
    action = e.get("eventType", "?")
    ev = {k: e.get(k) for k in ("eventType", "clientId", "principalId", "sourceIp",
                                 "topicName", "status", "timestamp") if e.get(k) is not None}
    return client, Finding(f"R1.auth_failure_{action.lower().replace('-', '_')}", HIGH,
                           f"IoT Core denied {action} for client {client!r}", ev)


class AuthLogPoller:
    def __init__(self, logs_client, log_group: str = "AWSIotLogsV2", start_ms: int = 0):
        self.logs, self.group, self.since = logs_client, log_group, start_ms
        self._seen: OrderedDict[str, None] = OrderedDict()

    def poll(self) -> list[tuple[str, int, Finding]]:
        """Return new (client_id, event_ms, finding) since the last poll."""
        out = []
        pages = self.logs.get_paginator("filter_log_events").paginate(
            logGroupName=self.group, startTime=self.since, filterPattern=FILTER_PATTERN)
        newest = self.since
        for page in pages:
            for ev in page.get("events", []):
                if ev["eventId"] in self._seen:
                    continue
                self._seen[ev["eventId"]] = None
                if len(self._seen) > 10_000:
                    self._seen.popitem(last=False)
                newest = max(newest, ev["timestamp"])
                parsed = to_finding(ev["message"])
                if parsed:
                    out.append((parsed[0], ev["timestamp"], parsed[1]))
        self.since = newest  # inclusive start; eventId dedupe handles the overlap
        return out
