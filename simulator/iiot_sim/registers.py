"""Holding-register map of the simulated pump-station PLC.

Modbus registers are 16-bit unsigned integers with no units or decimals, so PLCs
publish *scaled integers* (e.g. 4.53 bar stored as 453 with scale 100). The PLC and
the gateway must agree on this map; in real plants it comes from the vendor's
register documentation. Keeping it in one module is the single source of truth.
"""

from __future__ import annotations

from typing import NamedTuple


class Reg(NamedTuple):
    address: int
    name: str
    scale: int  # engineering value = raw / scale


REGISTER_MAP: tuple[Reg, ...] = (
    Reg(0, "speed_rpm", 1),
    Reg(1, "discharge_pressure_bar", 100),
    Reg(2, "flow_m3h", 10),
    Reg(3, "motor_temp_c", 10),
    Reg(4, "running", 1),
    Reg(5, "fault_code", 1),
)
REGISTER_COUNT = len(REGISTER_MAP)
UINT16_MAX = 0xFFFF


def encode(values: dict[str, float]) -> list[int]:
    """Engineering values -> raw uint16 registers (clamped, as a PLC would saturate)."""
    return [
        max(0, min(UINT16_MAX, round(values[r.name] * r.scale))) for r in REGISTER_MAP
    ]


def decode(raw: list[int]) -> dict[str, float | int]:
    """Raw registers -> engineering values."""
    if len(raw) != REGISTER_COUNT:
        raise ValueError(f"expected {REGISTER_COUNT} registers, got {len(raw)}")
    out: dict[str, float | int] = {}
    for r in REGISTER_MAP:
        v = raw[r.address]
        out[r.name] = v if r.scale == 1 else v / r.scale
    return out
