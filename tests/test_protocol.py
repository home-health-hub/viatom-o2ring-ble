from __future__ import annotations

import struct

import pytest

from viatom_o2ring_ble.protocol import (
    ResponseAssembler,
    chunk_for_ble,
    crc8,
    encode_request,
    parse_device_info,
    parse_reading,
    parse_rt_data,
)


def test_crc8_matches_known_device_vector():
    # viatom-ble documents this exact byte string as the magic payload that
    # elicits a CMD_READ_SENSORS reading; the trailing byte is its CRC.
    body = bytes.fromhex("aa17e800000000")
    assert crc8(body) == 0x1B


def test_encode_request_matches_viatom_ble_magic_bytes():
    assert encode_request(0x17) == b"\xaa\x17\xe8\x00\x00\x00\x00\x1b"


def test_encode_request_with_data_and_block():
    packet = encode_request(4, data=b"", block=5)
    sync, cmd, ncmd, block, length = struct.unpack("<BBBHH", packet[:7])
    assert (sync, cmd, ncmd, block, length) == (0xAA, 4, 4 ^ 0xFF, 5, 0)
    assert packet[-1] == crc8(packet[:-1])


def test_chunk_for_ble_splits_at_boundary():
    packet = bytes(range(45))
    chunks = chunk_for_ble(packet, chunk_size=20)
    assert [len(c) for c in chunks] == [20, 20, 5]
    assert b"".join(chunks) == packet


def _build_response(status: int, data: bytes, block: int = 0) -> bytes:
    header = struct.pack("<BBBHH", 0x55, status, status ^ 0xFF, block, len(data))
    body = header + data
    return body + bytes([crc8(body)])


def test_response_assembler_round_trip_single_chunk():
    packet = _build_response(status=0, data=b"hello", block=7)
    assembler = ResponseAssembler()
    payload = assembler.feed(packet)
    assert payload == b"hello"
    assert assembler.status == 0
    assert assembler.block == 7


def test_response_assembler_round_trip_split_across_notifications():
    packet = _build_response(status=0, data=b"0123456789")
    assembler = ResponseAssembler()
    assert assembler.feed(packet[:4]) is None
    assert assembler.feed(packet[4:9]) is None
    assert assembler.feed(packet[9:]) == b"0123456789"


def test_response_assembler_nonzero_status_still_returns_payload():
    # Status checking is the caller's job (see client._request); the
    # assembler itself only validates framing/CRC.
    packet = _build_response(status=9, data=b"")
    assembler = ResponseAssembler()
    assert assembler.feed(packet) == b""
    assert assembler.status == 9


def test_response_assembler_rejects_wrong_sync_byte():
    header = struct.pack("<BBBHH", 0xAA, 0, 0xFF, 0, 0)
    packet = header + bytes([crc8(header)])
    assembler = ResponseAssembler()
    with pytest.raises(ValueError, match="sync"):
        assembler.feed(packet)


def test_response_assembler_rejects_crc_mismatch():
    packet = bytearray(_build_response(status=0, data=b"x"))
    packet[-1] ^= 0xFF
    assembler = ResponseAssembler()
    with pytest.raises(ValueError, match="CRC"):
        assembler.feed(bytes(packet))


def test_parse_reading_decodes_payload_relative_offsets():
    payload = bytearray(12)
    payload[0] = 97  # spo2
    payload[1] = 72  # heart_rate
    payload[7] = 85  # battery
    payload[8] = 1  # charging
    payload[9] = 3  # movement
    payload[10] = 6  # perfusion index
    payload[11] = 1  # worn

    reading = parse_reading(bytes(payload))

    assert reading is not None
    assert reading.spo2 == 97
    assert reading.heart_rate == 72
    assert reading.battery == 85
    assert reading.charging == 1
    assert reading.movement == 3
    assert reading.perfusion_index == 6
    assert reading.worn is True
    assert reading.calibrating is False


def test_parse_reading_flags_calibrating_when_worn_with_no_data():
    payload = bytearray(12)
    payload[11] = 1  # worn, but spo2/heart_rate both still 0
    reading = parse_reading(bytes(payload))
    assert reading is not None
    assert reading.worn is True
    assert reading.calibrating is True


def test_parse_reading_returns_none_when_too_short():
    assert parse_reading(b"\x00" * 5) is None


def _build_rt_payload(
    spo2: int, pulse_bpm: int, battery: int, battery_state: int, pi: int, lead_state: int,
    waveform: bytes = b"",
) -> bytes:
    payload = bytearray(12)
    payload[0] = spo2
    payload[1:3] = pulse_bpm.to_bytes(2, "little")
    payload[3] = battery
    payload[4] = battery_state
    payload[5] = pi
    payload[6] = lead_state
    payload[10:12] = len(waveform).to_bytes(2, "little")
    return bytes(payload) + waveform


def test_parse_rt_data_decodes_fields_and_waveform():
    payload = _build_rt_payload(97, 72, 85, 1, 6, lead_state=1, waveform=bytes([10, 20, 30]))
    reading = parse_rt_data(payload)

    assert reading is not None
    assert reading.spo2 == 97
    assert reading.pulse_bpm == 72
    assert reading.battery == 85
    assert reading.battery_state == 1
    assert reading.perfusion_index == 6
    assert reading.worn is True
    assert reading.calibrating is False
    assert reading.waveform == bytes([10, 20, 30])


def test_parse_rt_data_two_byte_pulse_exceeds_legacy_byte_range():
    # This is the discrepancy vs. the legacy command: pulse can exceed 255
    # here because it's a real 2-byte field, not a coincidentally-small one.
    payload = _build_rt_payload(97, 300, 85, 0, 6, lead_state=1)
    reading = parse_rt_data(payload)
    assert reading is not None
    assert reading.pulse_bpm == 300


def test_parse_rt_data_lead_off_is_not_worn():
    payload = _build_rt_payload(0, 0, 85, 0, 0, lead_state=0)
    reading = parse_rt_data(payload)
    assert reading is not None
    assert reading.worn is False


def test_parse_rt_data_returns_none_when_too_short():
    assert parse_rt_data(b"\x00" * 11) is None


def test_parse_device_info_decodes_json_payload():
    payload = (
        b'{"CurBAT": "75%", "FileList": "a.vld,b.vld", "Model": "O2Ring", "SN": "XXXX", '
        b'"Region": "US", "HardwareVer": "1.0", "SoftwareVer": "2.1", '
        b'"BootloaderVer": "1.0", "CurBatState": "0", "CurOxiThr": "90", '
        b'"CurMotor": "50", "CurMode": "0", "CurPedtar": "6000", '
        b'"CurTIME": "2026-08-08,12:00:00"}\x00'
    )
    info = parse_device_info(payload)
    assert info.model == "O2Ring"
    assert info.serial_number == "XXXX"
    assert info.battery_percent == 75
    assert info.file_names == ("a.vld", "b.vld")
    assert info.region == "US"
    assert info.hardware_version == "1.0"
    assert info.software_version == "2.1"
    assert info.bootloader_version == "1.0"
    assert info.battery_state == 0
    assert info.oxi_threshold == 90
    assert info.vibration_strength == 50
    assert info.mode == 0
    assert info.pedometer_target == 6000
    assert info.current_time == "2026-08-08,12:00:00"
