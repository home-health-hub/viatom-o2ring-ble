"""Packet framing for the Viatom/Wellue ring BLE protocol.

Every packet -- request or response -- follows the same layout::

    sync(1) | cmd(1) | cmd^0xFF(1) | block(2, LE) | length(2, LE) | data(var) | crc(1)

Requests are prefixed with ``REQUEST_SYNC_BYTE`` (0xAA); responses are
prefixed with ``RESPONSE_SYNC_BYTE`` (0x55) -- confirmed against o2r's
source but absent from the O2Ring protocol write-up, which only shows
0xAA. A client that assumes 0xAA on both directions will reject every
real response as malformed.

In a response, the byte in the CMD position is not an echo of the request
command -- it's a generic status code (0 = success), used the same way
for every command. This is confirmed by o2r's o2state.py, which checks
`recv_cmd != 0` as a blanket failure check before dispatching on the
original request command at all. The O2Ring protocol docs only mention
this for FILE_OPEN ("Byte 1: Status"), which reads as command-specific
but isn't.

The checksum is CRC-8-CCITT (polynomial 0x07, seed 0x00), implemented
below as a bit-twiddled equivalent of a lookup table.
"""

from __future__ import annotations

import datetime
import json
import struct

from .const import (
    BLE_WRITE_CHUNK_SIZE,
    LIVE_IDX_BATTERY,
    LIVE_IDX_CHARGING,
    LIVE_IDX_HR,
    LIVE_IDX_MOVEMENT,
    LIVE_IDX_PI,
    LIVE_IDX_SPO2,
    LIVE_IDX_WORN,
    LIVE_PACKET_MIN_LEN,
    REQUEST_SYNC_BYTE,
    RESPONSE_SYNC_BYTE,
    RT_IDX_BATTERY,
    RT_IDX_BATTERY_STATE,
    RT_IDX_LEAD_STATE,
    RT_IDX_PI,
    RT_IDX_PULSE,
    RT_IDX_SPO2,
    RT_IDX_WAVE_DATA,
    RT_IDX_WAVE_LEN,
    RT_PACKET_MIN_LEN,
)
from .data import DeviceInfo, Reading, RtReading

_HEADER_FORMAT = "<BBBHH"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)


def crc8(data: bytes) -> int:
    """CRC-8-CCITT (polynomial 0x07, seed 0x00) over `data`."""
    crc = 0
    for byte in data:
        chk = crc ^ byte
        crc = 0
        if chk & 0x01:
            crc = 0x07
        if chk & 0x02:
            crc ^= 0x0E
        if chk & 0x04:
            crc ^= 0x1C
        if chk & 0x08:
            crc ^= 0x38
        if chk & 0x10:
            crc ^= 0x70
        if chk & 0x20:
            crc ^= 0xE0
        if chk & 0x40:
            crc ^= 0xC7
        if chk & 0x80:
            crc ^= 0x89
    return crc


def encode_request(cmd: int, data: bytes = b"", block: int = 0) -> bytes:
    """Build a full request packet, before BLE write chunking."""
    header = struct.pack(_HEADER_FORMAT, REQUEST_SYNC_BYTE, cmd, cmd ^ 0xFF, block, len(data))
    body = header + data
    return body + bytes([crc8(body)])


def chunk_for_ble(packet: bytes, chunk_size: int = BLE_WRITE_CHUNK_SIZE) -> list[bytes]:
    """Split a packet into pieces no larger than the BLE write MTU allows."""
    return [packet[i:i + chunk_size] for i in range(0, len(packet), chunk_size)]


class ResponseAssembler:
    """Accumulates BLE notification fragments into one complete response.

    A response may arrive split across several notifications. Create one
    instance per in-flight request, feed it every notification received
    afterward, and it returns the decoded payload once the full packet
    (per its declared length) has arrived.
    """

    def __init__(self) -> None:
        self._buffer = b""
        self._want: int | None = None
        self.status: int | None = None
        self.block: int = 0

    def feed(self, chunk: bytes) -> bytes | None:
        """Add a notification fragment. Returns the payload once complete."""
        self._buffer += bytes(chunk)

        if self._want is None:
            if len(self._buffer) < _HEADER_SIZE:
                return None
            sync, status, nstatus, block, length = struct.unpack(
                _HEADER_FORMAT, self._buffer[:_HEADER_SIZE]
            )
            if sync != RESPONSE_SYNC_BYTE:
                raise ValueError(f"Unexpected response sync byte: 0x{sync:02X}")
            if status != (nstatus ^ 0xFF):
                raise ValueError("Status check byte mismatch")
            self.status = status
            self.block = block
            self._want = _HEADER_SIZE + length + 1  # + trailing CRC byte

        if len(self._buffer) < self._want:
            return None

        packet = self._buffer[: self._want]
        payload, crc = packet[_HEADER_SIZE:-1], packet[-1]
        computed = crc8(packet[:-1])
        if computed != crc:
            raise ValueError(f"CRC mismatch: expected 0x{crc:02X}, computed 0x{computed:02X}")

        return payload


def parse_reading(payload: bytes, received_at: datetime.datetime | None = None) -> Reading | None:
    """Decode a CMD_READ_SENSORS response payload into a Reading.

    Returns None if the payload is too short to contain the expected
    fields. Byte offsets are payload-relative (see const.LIVE_IDX_*);
    cross-checked against viatom-ble's packet-relative offsets and o2r's
    o2state.py raw decode, which agree once the 7-byte header is accounted
    for.
    """
    if payload is None or len(payload) < LIVE_PACKET_MIN_LEN:
        return None

    spo2 = payload[LIVE_IDX_SPO2]
    heart_rate = payload[LIVE_IDX_HR]
    worn = bool(payload[LIVE_IDX_WORN])

    return Reading(
        spo2=spo2,
        heart_rate=heart_rate,
        battery=payload[LIVE_IDX_BATTERY],
        charging=payload[LIVE_IDX_CHARGING],
        movement=payload[LIVE_IDX_MOVEMENT],
        perfusion_index=payload[LIVE_IDX_PI],
        worn=worn,
        calibrating=worn and spo2 == 0 and heart_rate == 0,
        raw=bytes(payload),
        received_at=received_at or datetime.datetime.now(),
    )


def parse_rt_data(payload: bytes, received_at: datetime.datetime | None = None) -> RtReading | None:
    """Decode a CMD_RT_DATA (0x1B) response payload into an RtReading.

    This is the command the current official app polls for live monitoring
    (LepuBle's OxyBleResponse.RtWave). Returns None if the payload is too
    short to contain the fixed fields plus the waveform-length prefix.
    """
    if payload is None or len(payload) < RT_PACKET_MIN_LEN:
        return None

    spo2 = payload[RT_IDX_SPO2]
    pulse_bpm = int.from_bytes(payload[RT_IDX_PULSE:RT_IDX_PULSE + 2], "little")
    lead_state = payload[RT_IDX_LEAD_STATE]
    worn = lead_state == 1

    wave_len = int.from_bytes(payload[RT_IDX_WAVE_LEN:RT_IDX_WAVE_LEN + 2], "little")
    waveform = payload[RT_IDX_WAVE_DATA:RT_IDX_WAVE_DATA + wave_len]

    return RtReading(
        spo2=spo2,
        pulse_bpm=pulse_bpm,
        battery=payload[RT_IDX_BATTERY],
        battery_state=payload[RT_IDX_BATTERY_STATE],
        perfusion_index=payload[RT_IDX_PI],
        worn=worn,
        calibrating=worn and spo2 == 0 and pulse_bpm == 0,
        waveform=bytes(waveform),
        raw=bytes(payload),
        received_at=received_at or datetime.datetime.now(),
    )


def _parse_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def parse_device_info(payload: bytes) -> DeviceInfo:
    """Decode a CMD_INFO response payload (a JSON string) into DeviceInfo.

    Field set follows LepuBle's OxyBleResponse.OxyInfo. All JSON values
    are strings on the wire (including numeric-looking ones), hence the
    int conversions below.
    """
    text = payload.decode("ascii", errors="replace").rstrip(" \t\r\n\0")
    parsed = json.loads(text)

    file_list = parsed.get("FileList", "")
    battery_str = parsed.get("CurBAT", "")
    try:
        battery_percent = int(battery_str.rstrip("%")) if battery_str else None
    except ValueError:
        battery_percent = None

    return DeviceInfo(
        model=parsed.get("Model", ""),
        serial_number=parsed.get("SN", ""),
        region=parsed.get("Region", ""),
        hardware_version=parsed.get("HardwareVer", ""),
        software_version=parsed.get("SoftwareVer", ""),
        bootloader_version=parsed.get("BootloaderVer", ""),
        battery_percent=battery_percent,
        battery_state=_parse_int(parsed.get("CurBatState")),
        oxi_threshold=_parse_int(parsed.get("CurOxiThr")),
        vibration_strength=_parse_int(parsed.get("CurMotor")),
        mode=_parse_int(parsed.get("CurMode")),
        pedometer_target=_parse_int(parsed.get("CurPedtar")),
        current_time=parsed.get("CurTIME", ""),
        file_names=tuple(name for name in file_list.split(",") if name),
    )
