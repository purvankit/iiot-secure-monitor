"""MQTT connection helper with mutual TLS.

The same code talks to local Mosquitto (M1) and AWS IoT Core (M2): both use
TLS on port 8883 where the *client* also presents an X.509 certificate. The
certificate is the device's identity; the client ID must match it.
"""

from __future__ import annotations

import logging
import ssl
import threading
from dataclasses import dataclass
from pathlib import Path

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)


@dataclass
class TlsFiles:
    ca: Path
    cert: Path
    key: Path

    @classmethod
    def for_device(cls, certs_dir: Path, device_id: str) -> TlsFiles:
        return cls(certs_dir / "ca.crt", certs_dir / f"{device_id}.crt",
                   certs_dir / f"{device_id}.key")


def connect(client_id: str, host: str, port: int, tls: TlsFiles, timeout: float = 10.0):
    """Connect with mTLS and start the network loop thread. Raises on failure."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                         protocol=mqtt.MQTTv5)
    client.tls_set(ca_certs=str(tls.ca), certfile=str(tls.cert), keyfile=str(tls.key),
                   cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    connected = threading.Event()
    result: dict = {}

    def on_connect(_c, _u, _flags, reason_code, _props):
        result["rc"] = reason_code
        connected.set()
        if reason_code.is_failure:
            log.error("%s: connect refused: %s", client_id, reason_code)
        else:
            log.info("%s: connected to %s:%d", client_id, host, port)

    def on_disconnect(_c, _u, _flags, reason_code, _props):
        log.warning("%s: disconnected: %s", client_id, reason_code)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect(host, port, keepalive=60)
    client.loop_start()
    if not connected.wait(timeout) or result["rc"].is_failure:
        client.loop_stop()
        raise ConnectionError(f"{client_id}: could not connect ({result.get('rc')})")
    return client
