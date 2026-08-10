from __future__ import annotations

import time

from viatom_o2ring_ble import oxyii_commands as commands
from viatom_o2ring_ble.oxyii_const import (
    OP_FACTORY_RESET,
    OP_GET_BATTERY,
    OP_GET_CONFIG,
    OP_GET_FILE_LIST,
    OP_GET_INFO,
    OP_LIVE_SAMPLES_B,
    OP_READ_FILE_DATA,
    OP_READ_FILE_END,
    OP_READ_FILE_START,
    OP_SET_UTC_TIME,
    OP_SETUP,
)
from viatom_o2ring_ble.oxyii_protocol import build_auth_payload


def _opcode(frame: bytes) -> int:
    return frame[1]


def _payload(frame: bytes) -> bytes:
    length = int.from_bytes(frame[5:7], "little")
    return frame[7:7 + length]


def test_auth_payload_matches_build_auth_payload_with_fixed_time(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    frame = commands.auth("2500", seq=0)
    assert _payload(frame) == build_auth_payload("2500", 1000)


def test_setup_sends_single_zero_byte():
    frame = commands.setup(seq=1)
    assert _opcode(frame) == OP_SETUP
    assert _payload(frame) == b"\x00"


def test_set_utc_time_encodes_fields():
    when = time.struct_time((2026, 1, 31, 22, 33, 40, 0, 0, 0))
    frame = commands.set_utc_time(when, seq=2)
    assert _opcode(frame) == OP_SET_UTC_TIME
    payload = _payload(frame)
    assert len(payload) == 8
    year = payload[0] | (payload[1] << 8)
    assert (year, payload[2], payload[3], payload[4], payload[5], payload[6]) == (
        2026, 1, 31, 22, 33, 40,
    )


def test_get_info_get_battery_get_config_are_empty_payload_requests():
    assert _opcode(commands.get_info()) == OP_GET_INFO
    assert _payload(commands.get_info()) == b""
    assert _opcode(commands.get_battery()) == OP_GET_BATTERY
    assert _payload(commands.get_battery()) == b""
    assert _opcode(commands.get_config()) == OP_GET_CONFIG
    assert _payload(commands.get_config()) == b""


def test_live_samples_opcode():
    assert _opcode(commands.live_samples()) == OP_LIVE_SAMPLES_B


def test_get_file_list_opcode():
    assert _opcode(commands.get_file_list()) == OP_GET_FILE_LIST
    assert _payload(commands.get_file_list()) == b""


def test_read_file_start_payload_layout():
    frame = commands.read_file_start("20260427105949", seq=5)
    assert _opcode(frame) == OP_READ_FILE_START
    payload = _payload(frame)
    assert len(payload) == 20
    assert payload[:14] == b"20260427105949"
    assert payload[14:16] == b"\x00\x00"
    assert payload[16:20] == (0).to_bytes(4, "little")


def test_read_file_start_truncates_long_filename():
    frame = commands.read_file_start("x" * 30)
    payload = _payload(frame)
    assert payload[:16] == (b"x" * 16)


def test_read_file_data_encodes_offset_le():
    frame = commands.read_file_data(1024, seq=7)
    assert _opcode(frame) == OP_READ_FILE_DATA
    assert _payload(frame) == (1024).to_bytes(4, "little")


def test_read_file_end_opcode():
    assert _opcode(commands.read_file_end()) == OP_READ_FILE_END
    assert _payload(commands.read_file_end()) == b""


def test_factory_reset_opcode():
    assert _opcode(commands.factory_reset()) == OP_FACTORY_RESET
    assert _payload(commands.factory_reset()) == b""
