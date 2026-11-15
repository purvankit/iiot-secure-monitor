"""Simulated PLC: exposes a pump station's process values over Modbus TCP.

Modbus TCP has no authentication or encryption (it dates from 1979). That is
why it stays on the plant network and an edge gateway, not the PLC, talks to
the cloud over authenticated TLS. In a real plant the gateway's Modbus side
sits in a separate network zone (IEC 62443 zones and conduits).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

from pymodbus.server import StartAsyncTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from iiot_sim import registers
from iiot_sim.models import PumpStation

log = logging.getLogger("plc")


class PlcProcess:
    """Steps the process model and refreshes the register image on every Modbus read."""

    def __init__(self, seed: int | None = None):
        self.model = PumpStation(seed=seed)
        self._last = time.monotonic()
        self.image = registers.encode(self.model.step(1.0))

    def refresh(self) -> None:
        now = time.monotonic()
        dt = now - self._last
        if dt >= 0.1:  # don't re-step on back-to-back reads
            self.image = registers.encode(self.model.step(dt))
            self._last = now

    async def action(self, function_code, start_address, address, count,
                     current_registers, set_values):
        """pymodbus hook, called on each request; we update the registers in place."""
        if set_values is not None:
            return None  # registers are declared read-only, pymodbus rejects writes first
        self.refresh()
        for i, raw in enumerate(self.image):
            current_registers[i - start_address] = raw
        return None


def build_device(process: PlcProcess, unit_id: int = 1) -> SimDevice:
    return SimDevice(
        id=unit_id,
        simdata=[SimData(address=0, count=registers.REGISTER_COUNT, values=0,
                         datatype=DataType.REGISTERS, readonly=True)],
        action=process.action,
    )


async def run(host: str, port: int) -> None:
    process = PlcProcess()
    log.info("PLC listening on %s:%d, %d holding registers", host, port,
             registers.REGISTER_COUNT)
    await StartAsyncTcpServer(context=build_device(process), address=(host, port))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5020)  # 502 needs root
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(run(args.host, args.port))


if __name__ == "__main__":
    main()
