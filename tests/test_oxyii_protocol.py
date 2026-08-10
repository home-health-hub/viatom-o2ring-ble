from __future__ import annotations

import hashlib
import struct

import pytest

from viatom_o2ring_ble.oxyii_protocol import (
    LEPUCLOUD_MD5,
    OxyIIFrameAssembler,
    OxyIIFrameDecodeError,
    build_auth_payload,
    crc8,
    derive_session_key,
    encode_frame,
    parse_battery_percent,
    parse_device_info,
    parse_file_list,
    parse_live_reading,
)

_HEADER_FORMAT = "<BBBBBH"


def test_crc8_matches_documented_fixture():
    # From the upstream repo's README: GET_INFO request, no payload, seq=2.
    body = bytes.fromhex("a5 e1 1e 00 02 00 00")
    assert crc8(body) == 0xBF


def test_encode_frame_matches_documented_fixture():
    frame = encode_frame(0xE1, b"", seq=2)
    assert frame == bytes.fromhex("a5e11e00020000bf")


def test_encode_frame_header_fields():
    frame = encode_frame(0x04, b"\x01\x02", seq=9)
    lead, opcode, nopcode, flag, seq, length = struct.unpack(_HEADER_FORMAT, frame[:7])
    assert (lead, opcode, nopcode, flag, seq, length) == (0xA5, 0x04, (~0x04) & 0xFF, 0x00, 9, 2)
    assert frame[7:9] == b"\x01\x02"
    assert frame[-1] == crc8(frame[:-1])


def test_encode_frame_rejects_out_of_range_opcode():
    with pytest.raises(ValueError):
        encode_frame(0x100, b"")


def _build_reply(opcode: int, payload: bytes, seq: int = 0) -> bytes:
    header = struct.pack(_HEADER_FORMAT, 0xA5, opcode, (~opcode) & 0xFF, 0x01, seq, len(payload))
    body = header + payload
    return body + bytes([crc8(body)])


def test_frame_assembler_round_trip_single_chunk():
    frame = _build_reply(0xE1, b"hello", seq=3)
    assembler = OxyIIFrameAssembler()
    payload = assembler.feed(frame)
    assert payload == b"hello"
    assert assembler.opcode == 0xE1
    assert assembler.seq == 3


def test_frame_assembler_round_trip_split_across_notifications():
    frame = _build_reply(0xF1, b"0123456789")
    assembler = OxyIIFrameAssembler()
    assert assembler.feed(frame[:4]) is None
    assert assembler.feed(frame[4:9]) is None
    assert assembler.feed(frame[9:]) == b"0123456789"


def test_frame_assembler_rejects_bad_lead_byte():
    header = struct.pack(_HEADER_FORMAT, 0xAA, 0xE1, 0x1E, 0x01, 0, 0)
    frame = header + bytes([crc8(header)])
    assembler = OxyIIFrameAssembler()
    with pytest.raises(OxyIIFrameDecodeError, match="lead"):
        assembler.feed(frame)


def test_frame_assembler_rejects_opcode_complement_mismatch():
    header = struct.pack(_HEADER_FORMAT, 0xA5, 0xE1, 0x00, 0x01, 0, 0)
    frame = header + bytes([crc8(header)])
    assembler = OxyIIFrameAssembler()
    with pytest.raises(OxyIIFrameDecodeError, match="complement"):
        assembler.feed(frame)


def test_frame_assembler_rejects_crc_mismatch():
    frame = bytearray(_build_reply(0xE1, b"x"))
    frame[-1] ^= 0xFF
    assembler = OxyIIFrameAssembler()
    with pytest.raises(OxyIIFrameDecodeError, match="CRC"):
        assembler.feed(bytes(frame))


def test_derive_session_key_layout():
    key = derive_session_key("2500", 0x12345678)
    assert key[:8] == bytes(LEPUCLOUD_MD5[i * 2] for i in range(8))
    assert key[8:12] == b"2500"
    assert key[12] == 0x12345678 & 0xFF
    assert key[13] == (0x12345678 >> 1) & 0xFF
    assert key[14] == (0x12345678 >> 2) & 0xFF
    assert key[15] == (0x12345678 >> 3) & 0xFF


def test_derive_session_key_default_serial_is_portable():
    # "0000" is the upstream repo's recommended default when the device's
    # real serial isn't known yet (auth happens before GET_INFO).
    key = derive_session_key()
    assert key[8:12] == b"0000"


def test_derive_session_key_rejects_short_serial():
    with pytest.raises(ValueError):
        derive_session_key("12", 0)


def test_build_auth_payload_is_key_xor_salt():
    key = derive_session_key("2500", 1000)
    payload = build_auth_payload("2500", 1000)
    assert len(payload) == 16
    assert payload == bytes(a ^ b for a, b in zip(key, LEPUCLOUD_MD5))


def test_parse_device_info_decodes_serial_and_firmware():
    payload = bytearray(60)
    payload[9:17] = b"2D010002"
    payload[37] = 10
    payload[38:48] = b"25B2303210"
    info = parse_device_info(bytes(payload))
    assert info.firmware_version == "2D010002"
    assert info.serial_number == "25B2303210"


def test_parse_device_info_rejects_too_short_payload():
    with pytest.raises(OxyIIFrameDecodeError):
        parse_device_info(b"\x00" * 10)


def test_parse_battery_percent():
    assert parse_battery_percent(bytes([0, 75, 0, 0])) == 75


def test_parse_battery_percent_too_short_returns_none():
    assert parse_battery_percent(b"\x00") is None


def _build_file_list_payload(names: list[str]) -> bytes:
    payload = bytearray([len(names)])
    for name in names:
        slot = name.encode("ascii").ljust(16, b"\x00")
        payload += slot
    return bytes(payload)


def test_parse_file_list_decodes_names():
    payload = _build_file_list_payload(["20260427105949", "20260428061203"])
    entries = parse_file_list(payload)
    assert [e.name for e in entries] == ["20260427105949", "20260428061203"]


def test_parse_file_list_empty():
    assert parse_file_list(b"") == []


def _build_live_payload(
    contact_state: int, spo2: int, motion: int, heart_rate: int, battery: int
) -> bytes:
    payload = bytearray(26)
    payload[5] = contact_state
    payload[6] = spo2
    payload[7] = motion
    payload[8] = heart_rate
    payload[13] = battery
    return bytes(payload)


def test_parse_live_reading_worn():
    payload = _build_live_payload(contact_state=1, spo2=97, motion=10, heart_rate=72, battery=80)
    reading = parse_live_reading(payload)
    assert reading is not None
    assert reading.worn is True
    assert reading.spo2 == 97
    assert reading.heart_rate == 72
    assert reading.motion == 10
    assert reading.battery == 80
    assert reading.file_handle_open is False
    assert reading.calibrating is False


def test_parse_live_reading_no_contact_is_not_worn():
    payload = _build_live_payload(contact_state=0, spo2=0, motion=0, heart_rate=0, battery=80)
    reading = parse_live_reading(payload)
    assert reading is not None
    assert reading.worn is False


def test_parse_live_reading_file_handle_open_state():
    payload = _build_live_payload(contact_state=3, spo2=95, motion=5, heart_rate=70, battery=80)
    reading = parse_live_reading(payload)
    assert reading is not None
    assert reading.file_handle_open is True


def test_parse_live_reading_calibrating_when_worn_with_no_data():
    payload = _build_live_payload(contact_state=1, spo2=0, motion=0, heart_rate=0, battery=80)
    reading = parse_live_reading(payload)
    assert reading is not None
    assert reading.calibrating is True


def test_parse_live_reading_returns_none_when_too_short():
    assert parse_live_reading(b"\x00" * 10) is None


def test_lepucloud_md5_matches_hashlib():
    assert LEPUCLOUD_MD5 == hashlib.md5(b"lepucloud").digest()
