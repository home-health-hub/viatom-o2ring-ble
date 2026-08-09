from __future__ import annotations

import json

import pytest

from viatom_o2ring_ble import commands
from viatom_o2ring_ble.const import CMD_CONFIG, CMD_FILE_OPEN, CMD_RT_DATA


def _decode_config_payload(packet: bytes) -> dict:
    # cmd is the second byte of the 7-byte header; data starts at offset 7
    # and ends one byte before the end (trailing CRC byte).
    assert packet[1] == CMD_CONFIG
    return json.loads(packet[7:-1].decode("ascii"))


def test_file_open_null_terminates_filename():
    packet = commands.file_open("20260116233312.vld")
    assert packet[1] == CMD_FILE_OPEN
    data = packet[7:-1]
    assert data == b"20260116233312.vld\x00"


def test_set_o2_alert_enables_with_threshold():
    payload = _decode_config_payload(commands.set_o2_alert(90))
    assert payload == {"SetOxiThr": 90, "SetOxiSwitch": 1}


def test_set_o2_alert_disables_on_zero_or_none():
    for value in (0, None):
        payload = _decode_config_payload(commands.set_o2_alert(value))
        assert payload == {"SetOxiSwitch": 0}


def test_set_o2_alert_rejects_out_of_range():
    with pytest.raises(ValueError):
        commands.set_o2_alert(101)


def test_set_o2_alert_rejects_below_confirmed_range():
    # Below 80 was accepted by this package's first (o2r-derived) range of
    # 1-100; the device's actual confirmed range is 80-95.
    with pytest.raises(ValueError):
        commands.set_o2_alert(50)


def test_set_hr_alert_requires_a_bound():
    with pytest.raises(ValueError):
        commands.set_hr_alert()


def test_set_hr_alert_sets_high_and_low():
    payload = _decode_config_payload(commands.set_hr_alert(high_bpm=140, low_bpm=45))
    assert payload == {"SetHRSwitch": 1, "SetHRHighThr": 140, "SetHRLowThr": 45}


def test_set_hr_alert_rejects_below_confirmed_range():
    # 1-29 was accepted by this package's first (o2r-derived) range of
    # 1-200; the device's actual confirmed range is 30-250.
    with pytest.raises(ValueError):
        commands.set_hr_alert(high_bpm=20)


def test_set_hr_alert_accepts_upper_bound_above_first_cut():
    # 200 was this package's first (o2r-derived) upper bound; the
    # device's actual confirmed range extends to 250.
    payload = _decode_config_payload(commands.set_hr_alert(high_bpm=220))
    assert payload == {"SetHRSwitch": 1, "SetHRHighThr": 220}


def test_set_vibration_strength_allows_zero_as_off():
    payload = _decode_config_payload(commands.set_vibration_strength(0))
    assert payload == {"SetMotor": 0}

    with pytest.raises(ValueError):
        commands.set_vibration_strength(101)


def test_set_lighting_mode_all_three_states():
    for mode in (0, 1, 2):
        payload = _decode_config_payload(commands.set_lighting_mode(mode))
        assert payload == {"SetLightingMode": mode}

    with pytest.raises(ValueError):
        commands.set_lighting_mode(3)


def test_set_screen_always_on_cannot_express_always_off():
    # set_screen_always_on is a boolean convenience over set_lighting_mode
    # and can only reach modes 0 and 2 -- mode 1 ("always off") requires
    # set_lighting_mode directly.
    on_payload = _decode_config_payload(commands.set_screen_always_on(True))
    assert on_payload == {"SetLightingMode": 2}

    off_payload = _decode_config_payload(commands.set_screen_always_on(False))
    assert off_payload == {"SetLightingMode": 0}


def test_set_brightness_rejects_out_of_range():
    with pytest.raises(ValueError):
        commands.set_brightness(3)


def test_read_rt_data_defaults_to_125hz():
    packet = commands.read_rt_data()
    assert packet[1] == CMD_RT_DATA
    assert packet[7:-1] == b"\x00"


def test_read_rt_data_rejects_bad_rate():
    with pytest.raises(ValueError):
        commands.read_rt_data(2)
