"""Physics-ish device models.

Each signal is a mean-reverting random walk (Ornstein-Uhlenbeck process): it wanders
like a real sensor but is pulled back toward its nominal value, so it never drifts
off to nonsense. Slow thermal quantities follow load through a first-order lag.
Models are deterministic given a seed, which makes them testable.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


def ou_step(x: float, mean: float, theta: float, sigma: float, dt: float, rng: random.Random):
    """One Euler step of dx = theta*(mean - x)*dt + sigma*dW."""
    return x + theta * (mean - x) * dt + sigma * math.sqrt(dt) * rng.gauss(0.0, 1.0)


def lag(x: float, target: float, tau: float, dt: float) -> float:
    """First-order lag: x approaches target with time constant tau (seconds)."""
    return x + (target - x) * (1.0 - math.exp(-dt / tau))


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class FeederBay:
    """11 kV distribution feeder bay in a substation (India grid: 50 Hz)."""

    seed: int | None = None
    nominal_kv: float = 11.0
    rated_a: float = 400.0
    rng: random.Random = field(init=False)
    kv: float = field(init=False)
    freq: float = 50.0
    load_a: float = field(init=False)
    pf: float = 0.95
    breaker_closed: bool = True

    device_type = "feeder_bay"

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        self.kv = self.nominal_kv
        self.load_a = 0.6 * self.rated_a

    def step(self, dt: float) -> dict:
        r = self.rng
        self.kv = clamp(ou_step(self.kv, self.nominal_kv, 0.5, 0.05, dt, r), 10.5, 11.5)
        # Grid frequency drifts slowly (std ~0.03 Hz, ~100 s time constant);
        # IEGC operating band is 49.90-50.05 Hz.
        self.freq = clamp(ou_step(self.freq, 50.0, 0.01, 0.004, dt, r), 49.85, 50.10)
        self.load_a = clamp(ou_step(self.load_a, 0.6 * self.rated_a, 0.05, 8.0, dt, r),
                            0.1 * self.rated_a, 0.95 * self.rated_a)
        self.pf = clamp(ou_step(self.pf, 0.95, 0.1, 0.005, dt, r), 0.85, 0.99)
        current = self.load_a if self.breaker_closed else 0.0
        p_mw = math.sqrt(3) * self.kv * current * self.pf / 1000.0
        return {
            "voltage_kv": round(self.kv, 3),
            "current_a": round(current, 1),
            "frequency_hz": round(self.freq, 3),
            "power_factor": round(self.pf, 3),
            "active_power_mw": round(p_mw, 3),
            "breaker": "closed" if self.breaker_closed else "open",
        }


@dataclass
class Transformer:
    """33/11 kV power transformer: temperatures lag behind load."""

    seed: int | None = None
    ambient_c: float = 32.0
    rng: random.Random = field(init=False)
    load_pct: float = 65.0
    oil_c: float = field(init=False)
    winding_c: float = field(init=False)
    tap: int = 9  # tap changer positions 1..17, 9 = nominal

    device_type = "transformer"

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        self.oil_c = self.ambient_c + 25.0
        self.winding_c = self.oil_c + 10.0

    def step(self, dt: float) -> dict:
        r = self.rng
        self.load_pct = clamp(ou_step(self.load_pct, 65.0, 0.02, 1.5, dt, r), 20.0, 110.0)
        k = self.load_pct / 100.0
        # Simplified IEC 60076-7 shape: rise ~ load^2, oil slow (tau 30 min), winding fast.
        self.oil_c = lag(self.oil_c, self.ambient_c + 40.0 * k**2, 1800.0, dt)
        self.winding_c = lag(self.winding_c, self.oil_c + 20.0 * k**1.6, 300.0, dt)
        if r.random() < 0.002 * dt:  # rare on-load tap change
            self.tap = int(clamp(self.tap + r.choice((-1, 1)), 1, 17))
        return {
            "load_pct": round(self.load_pct, 1),
            "oil_temp_c": round(self.oil_c, 2),
            "winding_temp_c": round(self.winding_c, 2),
            "tap_position": self.tap,
        }


@dataclass
class Motor:
    """4-pole 50 Hz induction motor driving a factory conveyor."""

    seed: int | None = None
    rated_a: float = 28.0
    rng: random.Random = field(init=False)
    load: float = 0.7  # fraction of rated torque
    temp_c: float = 45.0
    vib: float = 1.8  # mm/s RMS (ISO 10816 zone A/B boundary ~2.8)

    device_type = "motor"

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    def step(self, dt: float) -> dict:
        r = self.rng
        self.load = clamp(ou_step(self.load, 0.7, 0.1, 0.03, dt, r), 0.2, 1.05)
        # Slip grows with load: synchronous 1500 rpm, ~2.5 % slip at rated load.
        rpm = 1500.0 * (1.0 - 0.025 * self.load)
        current = self.rated_a * (0.35 + 0.65 * self.load)
        self.temp_c = lag(self.temp_c, 35.0 + 50.0 * self.load**2, 600.0, dt)
        self.vib = clamp(ou_step(self.vib, 1.8, 0.3, 0.1, dt, r), 0.5, 4.0)
        return {
            "speed_rpm": round(rpm + r.gauss(0, 0.5), 1),
            "current_a": round(current, 2),
            "winding_temp_c": round(self.temp_c, 2),
            "vibration_mm_s": round(self.vib, 2),
        }


@dataclass
class PumpStation:
    """Process behind the simulated PLC: a cooling-water pump."""

    seed: int | None = None
    rng: random.Random = field(init=False)
    speed_rpm: float = 2900.0
    temp_c: float = 50.0
    running: bool = True
    fault_code: int = 0

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    def step(self, dt: float) -> dict:
        r = self.rng
        target = 2900.0 if self.running else 0.0
        self.speed_rpm = clamp(ou_step(self.speed_rpm, target, 0.5, 5.0, dt, r), 0.0, 3000.0)
        n = self.speed_rpm / 2900.0
        # Pump affinity laws: flow ~ n, head/pressure ~ n^2.
        flow = 120.0 * n + r.gauss(0, 0.5)
        pressure = 4.5 * n**2 + r.gauss(0, 0.02)
        self.temp_c = lag(self.temp_c, 35.0 + 20.0 * n, 600.0, dt)
        return {
            "speed_rpm": round(self.speed_rpm, 0),
            "discharge_pressure_bar": round(max(pressure, 0.0), 2),
            "flow_m3h": round(max(flow, 0.0), 1),
            "motor_temp_c": round(self.temp_c, 1),
            "running": int(self.running),
            "fault_code": self.fault_code,
        }


MODELS = {"feeder_bay": FeederBay, "transformer": Transformer, "motor": Motor}
