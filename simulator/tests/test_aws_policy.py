"""Integration against real AWS IoT Core: the least-privilege policy holds.

Stop the simulator first (these tests connect as feeder-01, and the policy forces
client ID == Thing name, so a second feeder-01 would take over the session).

Run:  IOT_ENDPOINT=$(terraform -chdir=infra/envs/dev output -raw iot_endpoint) \
      pytest -m aws -v
"""

import os
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

from iiot_sim.mqtt_client import TlsFiles, connect

pytestmark = pytest.mark.aws
ENDPOINT = os.getenv("IOT_ENDPOINT")
CERTS = Path(os.getenv("AWS_CERTS_DIR", Path(__file__).parents[2] / "local" / "aws-certs"))
PORT = 8883

if not ENDPOINT:
    pytest.skip("IOT_ENDPOINT not set", allow_module_level=True)


def tls(device):
    return TlsFiles.for_device(CERTS, device)


def test_device_can_publish_to_own_topic():
    c = connect("feeder-01", ENDPOINT, PORT, tls("feeder-01"))
    info = c.publish("iiot/feeder-01/telemetry", b'{"policy_test":"own"}', qos=1)
    info.wait_for_publish(10)
    assert info.is_published()  # PUBACK received
    time.sleep(1)
    assert c.is_connected()
    c.disconnect()
    c.loop_stop()


def _spoof_motor_01(protocol):
    """feeder-01 publishes to motor-01's topic; returns (puback_reason, disconnect_reason)."""
    done = threading.Event()
    res: dict = {}
    files = tls("feeder-01")
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="feeder-01", protocol=protocol)
    c.tls_set(ca_certs=str(files.ca), certfile=str(files.cert), keyfile=str(files.key))
    c.reconnect_delay_set(min_delay=60, max_delay=60)  # no auto-reconnect during the test

    def on_publish(_c, _u, _mid, rc, _props):
        res["puback"] = str(rc)
        done.set()

    def on_disconnect(_c, _u, _flags, rc, _props):
        res["disconnect"] = str(rc)
        done.set()

    c.on_publish, c.on_disconnect = on_publish, on_disconnect
    c.connect(ENDPOINT, PORT)
    c.loop_start()
    time.sleep(2)
    c.publish("iiot/motor-01/telemetry", b'{"spoof":true}', qos=1)
    done.wait(10)
    c.disconnect()
    c.loop_stop()
    return res.get("puback"), res.get("disconnect")


def test_foreign_topic_publish_rejected_mqtt5():
    # MQTT 5 has per-message reason codes: IoT Core answers PUBACK 0x87, keeps the session.
    puback, _ = _spoof_motor_01(mqtt.MQTTv5)
    assert puback == "Not authorized"


def test_foreign_topic_publish_disconnects_mqtt311():
    # MQTT 3.1.1 can't signal per-message errors, so IoT Core drops the connection.
    _, disconnect = _spoof_motor_01(mqtt.MQTTv311)
    assert disconnect is not None


def test_client_id_must_match_thing_name():
    # Valid feeder-01 certificate, but impersonating motor-01's client ID.
    with pytest.raises(ConnectionError):
        connect("motor-01", ENDPOINT, PORT, tls("feeder-01"), timeout=10)


def test_client_without_certificate_is_rejected():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="rogue", protocol=mqtt.MQTTv5)
    c.tls_set(ca_certs=str(CERTS / "ca.crt"))
    with pytest.raises(OSError):
        c.connect(ENDPOINT, PORT)  # TLS handshake fails: no client certificate
        c.loop_start()
        time.sleep(3)
        if not c.is_connected():
            raise OSError("rejected")
    c.loop_stop()
