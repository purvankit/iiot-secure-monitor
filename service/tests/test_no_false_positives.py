"""Run the real simulator models through the detector: normal traffic must raise nothing.

A detector that fires on normal data is useless (alert fatigue), so this is as important
as the attack tests.
"""

import pytest
from helpers import REGISTRY

from iiot_monitor.rules import Detector
from iiot_monitor.schema import Telemetry

models = pytest.importorskip("iiot_sim.models")
registers = pytest.importorskip("iiot_sim.registers")


@pytest.mark.parametrize("device_id, factory, scaled", [
    ("feeder-01", lambda: models.FeederBay(seed=1), False),
    ("xfmr-01", lambda: models.Transformer(seed=3), False),
    ("motor-01", lambda: models.Motor(seed=4), False),
    # PLC values go through uint16 register scaling, exactly as the gateway sends them.
    ("edge-gw-01", lambda: models.PumpStation(seed=5), True),
])
def test_simulated_device_raises_no_findings(device_id, factory, scaled):
    d, m = Detector(dict(REGISTRY)), factory()
    t0, session, findings = 1_800_000_000_000, 1_799_999_000_000, []
    for i in range(3000):  # ~4 h of traffic at 5 s
        r = m.step(5.0)
        if scaled:
            r = registers.decode(registers.encode(r))
        ts = t0 + i * 5000
        findings += d.check(Telemetry(device_id, REGISTRY[device_id], "s", session, i + 1, ts,
                                      r, device_id, device_id, ts + 1500))
    assert findings == []
