"""End-to-end over a real Modbus TCP socket: PLC simulator <-> gateway polling."""

import asyncio
import socket
import threading
import time

import pytest
from pymodbus.client import ModbusTcpClient
from pymodbus.server import ServerAsyncStop, StartAsyncTcpServer

from iiot_sim.gateway import poll_plc
from iiot_sim.plc_sim import PlcProcess, build_device


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def plc_port():
    port = free_port()
    loop = asyncio.new_event_loop()
    server = build_device(PlcProcess(seed=1))
    t = threading.Thread(target=loop.run_until_complete,
                         args=(StartAsyncTcpServer(context=server, address=("127.0.0.1", port)),),
                         daemon=True)
    t.start()
    for _ in range(50):  # wait for listen
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    yield port
    asyncio.run_coroutine_threadsafe(ServerAsyncStop(), loop).result(5)


def test_gateway_reads_plausible_values(plc_port):
    c = ModbusTcpClient("127.0.0.1", port=plc_port)
    r = poll_plc(c, unit_id=1)
    c.close()
    assert r is not None
    assert 2700 < r["speed_rpm"] <= 3000
    assert 3.5 < r["discharge_pressure_bar"] < 5.5
    assert r["running"] == 1


def test_plc_registers_are_read_only(plc_port):
    c = ModbusTcpClient("127.0.0.1", port=plc_port)
    c.connect()
    rr = c.write_register(0, 0, device_id=1)
    c.close()
    assert rr.isError()


def test_gateway_returns_none_when_plc_down():
    c = ModbusTcpClient("127.0.0.1", port=free_port(), timeout=0.5, retries=0)
    assert poll_plc(c, unit_id=1) is None
