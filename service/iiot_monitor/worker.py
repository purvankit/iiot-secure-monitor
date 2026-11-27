"""Main loop: SQS -> validate -> detect -> DynamoDB (+ SNS), plus auth-log polling.

Delivery semantics: a message is deleted from SQS only after it is fully processed.
If processing raises (e.g. DynamoDB throttled), it is NOT deleted, becomes visible again
after the visibility timeout and, after 5 failures, moves to the dead-letter queue.
Malformed messages are recorded as alerts and deleted: retrying can't fix them.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from collections import OrderedDict
from wsgiref.simple_server import WSGIRequestHandler, make_server

import boto3
from prometheus_client import Counter, Histogram, make_wsgi_app

from iiot_monitor.alerts import Alert, Notifier
from iiot_monitor.authlog import AuthLogPoller
from iiot_monitor.rules import CRITICAL, Detector, Finding
from iiot_monitor.schema import InvalidMessage, parse
from iiot_monitor.store import Store

log = logging.getLogger("worker")

MESSAGES = Counter("iiot_messages_total", "Messages processed", ["result"])
ALERTS = Counter("iiot_alerts_total", "Alerts raised", ["rule", "severity"])
LATENCY = Histogram("iiot_processing_seconds", "Per-message processing time")
REGISTRY_REFRESH_S = 300
AUTHLOG_POLL_S = 60

# Every (rule, severity) the code can raise, and every message result. A Prometheus counter
# series first appears already at 1, and increase()/rate() can't count that first step, so a
# rule that fires once (the usual attack) would show 0 on the dashboard. Creating all known
# series at 0 on startup makes the first alert visible. test_worker keeps this list in sync
# with rules.py.
KNOWN_ALERTS = [
    ("R0.malformed", "medium"),
    ("R1.unknown_device", CRITICAL), ("R1.identity_mismatch", CRITICAL),
    ("R1.type_mismatch", CRITICAL),
    ("R1.auth_failure_publish_in", "high"), ("R1.auth_failure_connect", "high"),
    ("R1.auth_failure_subscribe", "high"),
    ("R2.out_of_range", "high"), ("R2.rate_of_change", "medium"), ("R2.invalid_state", "high"),
    ("R3.message_flood", "high"),
    ("R4.duplicate_seq", "high"), ("R4.old_seq", "high"), ("R4.old_session", "high"),
    ("R4.future_session", "high"), ("R4.stale", "medium"), ("R4.future_ts", "medium"),
]
MESSAGE_RESULTS = ["accepted", "rejected", "invalid", "duplicate_delivery", "error"]


def init_metrics() -> None:
    """Export every known label combination at 0 (see KNOWN_ALERTS)."""
    for rule, severity in KNOWN_ALERTS:
        ALERTS.labels(rule, severity)
    for result in MESSAGE_RESULTS:
        MESSAGES.labels(result)


def load_registry(iot) -> dict[str, str]:
    """device_id -> device_type, straight from the IoT Thing registry."""
    reg = {}
    for page in iot.get_paginator("list_things").paginate():
        for t in page["things"]:
            reg[t["thingName"]] = t.get("attributes", {}).get("device_type", "")
    return reg


class Worker:
    def __init__(self, *, sqs, queue_url: str, detector: Detector, store: Store,
                 notifier: Notifier, authlog: AuthLogPoller | None = None):
        self.sqs, self.queue_url = sqs, queue_url
        self.detector, self.store, self.notifier, self.authlog = detector, store, notifier, authlog
        self._seen_ids: OrderedDict[str, None] = OrderedDict()
        self.running = True

    # -- alerts --
    def raise_alert(self, f: Finding, device_id: str, at_ms: int) -> None:
        a = Alert.from_finding(f, device_id, at_ms)
        self.store.put_alert(a)
        ALERTS.labels(f.rule, f.severity).inc()
        log.warning("ALERT %s %s %s: %s", f.severity, f.rule, device_id, f.summary)
        self.notifier.notify(a)

    # -- one SQS message --
    def handle(self, m: dict) -> None:
        mid = m["MessageId"]
        if mid in self._seen_ids:
            # SQS at-least-once redelivery: same MessageId, not an attack. Idempotent skip.
            MESSAGES.labels("duplicate_delivery").inc()
            return
        try:
            t = parse(m["Body"])
        except InvalidMessage as e:
            MESSAGES.labels("invalid").inc()
            self.raise_alert(Finding("R0.malformed", "medium", f"malformed message: {e}",
                                     {"body_prefix": m["Body"][:200]}),
                             "unknown", int(time.time() * 1000))
        else:
            findings = self.detector.check(t)
            trusted = not Detector.is_replay(findings) and not any(
                f.severity == CRITICAL for f in findings)
            if trusted:
                # Don't let replayed or impersonated data into the time series / latest state.
                self.store.put_reading(t)
                self.store.put_latest(t)
            for f in findings:
                self.raise_alert(f, t.device_id, t.ingest_ts)
            MESSAGES.labels("accepted" if trusted else "rejected").inc()
        self._seen_ids[mid] = None
        if len(self._seen_ids) > 10_000:
            self._seen_ids.popitem(last=False)

    def poll_once(self) -> int:
        resp = self.sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=10,
                                        WaitTimeSeconds=20)
        msgs = resp.get("Messages", [])
        for m in msgs:
            try:
                with LATENCY.time():
                    self.handle(m)
            except Exception:
                MESSAGES.labels("error").inc()
                log.exception("processing failed; leaving message for retry/DLQ")
                continue
            self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=m["ReceiptHandle"])
        return len(msgs)

    def poll_authlog(self) -> None:
        if not self.authlog:
            return
        for client_id, at_ms, f in self.authlog.poll():
            self.raise_alert(f, client_id, at_ms)


def serve_http(port: int) -> None:
    """/health for the container health check, everything else -> Prometheus metrics."""
    metrics = make_wsgi_app()

    def app(environ, start_response):
        if environ["PATH_INFO"] == "/health":
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]
        return metrics(environ, start_response)

    class Quiet(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    httpd = make_server("0.0.0.0", port, app, handler_class=Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    region = os.getenv("AWS_REGION")
    if not region:  # required: never fall back to a profile/default region (CLAUDE.md)
        raise SystemExit("AWS_REGION is required (the platform runs in ap-south-1)")
    s = boto3.session.Session(region_name=region)
    iot, sqs, sns = s.client("iot"), s.client("sqs"), s.client("sns")
    store = Store(s.resource("dynamodb"), os.environ["TABLE_READINGS"],
                  os.environ["TABLE_LATEST"], os.environ["TABLE_ALERTS"])

    detector = Detector(load_registry(iot))
    for item in store.load_latest():  # resume replay detection across restarts
        detector.warm_start(item["device_id"], int(item["session"]), int(item["seq"]))
    log.info("registry: %s", sorted(detector.registry))

    worker = Worker(
        sqs=sqs, queue_url=os.environ["QUEUE_URL"], detector=detector, store=store,
        notifier=Notifier(sns, os.getenv("ALERT_TOPIC_ARN"),
                          int(os.getenv("ALERT_COOLDOWN_S", "300")) * 1000),
        authlog=AuthLogPoller(s.client("logs"), start_ms=int(time.time() * 1000)),
    )
    init_metrics()
    serve_http(int(os.getenv("HTTP_PORT", "8080")))

    def stop(*_):
        worker.running = False
        log.info("stopping after current batch")
    signal.signal(signal.SIGTERM, stop)  # ECS sends SIGTERM before killing the task
    signal.signal(signal.SIGINT, stop)

    next_registry = next_authlog = time.monotonic()
    while worker.running:
        now = time.monotonic()
        if now >= next_registry:
            detector.registry = load_registry(iot)
            next_registry = now + REGISTRY_REFRESH_S
        if now >= next_authlog:
            try:
                worker.poll_authlog()
            except Exception:
                log.exception("auth log poll failed")
            next_authlog = now + AUTHLOG_POLL_S
        worker.poll_once()


if __name__ == "__main__":
    main()
