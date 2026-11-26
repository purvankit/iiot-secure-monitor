"""Worker I/O edges: the IoT registry loader and the /health + /metrics endpoint."""

import socket
import time
import urllib.request

from iiot_monitor.worker import load_registry, serve_http


class FakeIoT:
    def __init__(self, pages):
        self.pages = pages

    def get_paginator(self, name):
        assert name == "list_things"
        pages = self.pages

        class P:
            def paginate(self):
                return pages
        return P()


def test_registry_reads_device_type_from_thing_attributes_across_pages():
    iot = FakeIoT([
        {"things": [{"thingName": "feeder-01", "attributes": {"device_type": "feeder_bay"}}]},
        {"things": [{"thingName": "motor-01", "attributes": {"device_type": "motor"}},
                    {"thingName": "odd-01"}]},  # no attributes -> empty type, still known
    ])
    assert load_registry(iot) == {"feeder-01": "feeder_bay", "motor-01": "motor", "odd-01": ""}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url):
    for _ in range(50):
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return r.status, r.read().decode()
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"{url} never answered")


def test_health_and_metrics_endpoints():
    port = _free_port()
    serve_http(port)
    status, body = _get(f"http://127.0.0.1:{port}/health")
    assert (status, body) == (200, "ok")
    status, body = _get(f"http://127.0.0.1:{port}/metrics")
    assert status == 200
    for metric in ("iiot_messages_total", "iiot_alerts_total", "iiot_processing_seconds"):
        assert metric in body
