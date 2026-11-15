"""Simulated signals must stay physically plausible, or the M3 detection rules
would fire on normal traffic (false positives)."""

from itertools import pairwise

import pytest

from iiot_sim.models import FeederBay, Motor, PumpStation, Transformer

STEPS = 5000
DT = 5.0  # default publish interval


def run(model, steps=STEPS, dt=DT):
    return [model.step(dt) for _ in range(steps)]


def test_feeder_within_operating_band():
    for r in run(FeederBay(seed=1)):
        assert 10.5 <= r["voltage_kv"] <= 11.5
        assert 49.85 <= r["frequency_hz"] <= 50.10
        assert 0 < r["current_a"] <= 400
        assert 0.85 <= r["power_factor"] <= 0.99
        assert r["breaker"] == "closed"


def test_feeder_frequency_changes_slowly():
    # Rule 2 will flag big jumps (e.g. 5 Hz); normal data must be far below that.
    rs = run(FeederBay(seed=7))
    worst = max(abs(a["frequency_hz"] - b["frequency_hz"]) for a, b in pairwise(rs))
    assert worst < 0.1


def test_feeder_open_breaker_means_zero_current():
    f = FeederBay(seed=1)
    f.breaker_closed = False
    r = f.step(DT)
    assert r["current_a"] == 0 and r["active_power_mw"] == 0 and r["breaker"] == "open"


def test_transformer_temperatures_ordered_and_bounded():
    for r in run(Transformer(seed=3)):
        assert 20 <= r["load_pct"] <= 110
        assert r["winding_temp_c"] > r["oil_temp_c"]  # winding is always hotter than oil
        assert r["winding_temp_c"] < 140  # IEC 60076-7 hot-spot limit region
        assert 1 <= r["tap_position"] <= 17


def test_motor_speed_below_synchronous():
    for r in run(Motor(seed=4)):
        assert 1450 < r["speed_rpm"] < 1500  # induction motor always slips
        assert 0 < r["current_a"] <= 28 * 1.05
        assert 0.5 <= r["vibration_mm_s"] <= 4.0


def test_pump_stops_when_not_running():
    p = PumpStation(seed=5)
    p.running = False
    rs = run(p, steps=200)
    assert rs[-1]["speed_rpm"] < 100
    assert rs[-1]["running"] == 0


@pytest.mark.parametrize("cls", [FeederBay, Transformer, Motor, PumpStation])
def test_same_seed_is_reproducible(cls):
    assert run(cls(seed=42), steps=50) == run(cls(seed=42), steps=50)
