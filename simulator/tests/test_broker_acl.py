"""Integration: against the docker-compose Mosquitto broker (mTLS + ACL).

Run:  docker compose up -d mosquitto && pytest -m integration
"""

import os
import socket
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

from iiot_sim.mqtt_client import TlsFiles, connect

pytestmark = pytest.mark.integration
HOST, PORT = "localhost", 8883
CERTS = Path(os.getenv("CERTS_DIR", Path(__file__).parents[2] / "local" / "certs"))


@pytest.fixture(autouse=True)
def broker_up():
    with socket.socket() as s:
        if s.connect_ex((HOST, PORT)) != 0:
            pytest.skip("local broker not running")


@pytest.fixture
def monitor():
    got: list[tuple[str, bytes]] = []
    ready = threading.Event()
    c = connect("monitor", HOST, PORT, TlsFiles.for_device(CERTS, "monitor"))
    c.on_message = lambda _c, _u, m: got.append((m.topic, m.payload))
    c.on_subscribe = lambda *_: ready.set()
    c.subscribe("iiot/#", qos=1)
    assert ready.wait(5)
    yield got
    c.disconnect()
    c.loop_stop()


def test_device_can_publish_to_own_topic(monitor):
    # Different client ID from the running fleet (avoids MQTT session takeover);
    # the ACL keys on the certificate CN, not the client ID.
    c = connect("feeder-01-test", HOST, PORT, TlsFiles.for_device(CERTS, "feeder-01"))
    c.publish("iiot/feeder-01/telemetry", b'{"acl_test":"own"}', qos=1).wait_for_publish(5)
    time.sleep(0.5)
    c.disconnect()
    assert ("iiot/feeder-01/telemetry", b'{"acl_test":"own"}') in monitor


def test_device_cannot_publish_to_another_devices_topic(monitor):
    # feeder-01 tries to spoof motor-01's telemetry; the broker must drop it.
    # Different client ID from the running fleet (avoids MQTT session takeover);
    # the ACL keys on the certificate CN, not the client ID.
    c = connect("feeder-01-test", HOST, PORT, TlsFiles.for_device(CERTS, "feeder-01"))
    c.publish("iiot/motor-01/telemetry", b'{"spoof":true}', qos=1).wait_for_publish(5)
    time.sleep(0.5)
    c.disconnect()
    # The real motor-01 also publishes here, so look for the spoofed payload only.
    assert not any(payload == b'{"spoof":true}' for _, payload in monitor)


def test_client_without_certificate_is_rejected():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="rogue", protocol=mqtt.MQTTv5)
    c.tls_set(ca_certs=str(CERTS / "ca.crt"))  # trusts the server, but presents no cert
    with pytest.raises((OSError, ConnectionError)):
        c.connect(HOST, PORT)
        c.loop_start()
        time.sleep(1)
        if not c.is_connected():
            raise ConnectionError("rejected")
    c.loop_stop()
