import pytest

from iiot_sim import registers

SAMPLE = {"speed_rpm": 2893, "discharge_pressure_bar": 4.53, "flow_m3h": 119.7,
          "motor_temp_c": 52.4, "running": 1, "fault_code": 0}


def test_roundtrip_preserves_scaled_values():
    assert registers.decode(registers.encode(SAMPLE)) == SAMPLE


def test_encode_saturates_to_uint16():
    raw = registers.encode({**SAMPLE, "speed_rpm": 70000, "motor_temp_c": -5})
    assert raw[0] == 0xFFFF and raw[3] == 0


def test_decode_rejects_wrong_length():
    with pytest.raises(ValueError):
        registers.decode([0, 1, 2])


def test_addresses_are_contiguous_from_zero():
    # The gateway reads the whole block in one request starting at 0.
    assert [r.address for r in registers.REGISTER_MAP] == list(range(registers.REGISTER_COUNT))
