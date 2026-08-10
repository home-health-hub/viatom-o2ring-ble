"""Frame codec and auth-key derivation for the OxyII (O2Ring-S) protocol.

Every request and reply shares one envelope::

    0xA5 | cmd | ~cmd | flag | seq | len_lo | len_hi | payload | crc

- `flag` is 0x00 for app->device requests, 0x01 for device->app replies.
- `len` is the little-endian payload length (header/crc excluded).
- `seq` is a 1-byte counter the device echoes back; reuse across requests
  is accepted (not enforced as strictly monotonic).
- `crc` is CRC-8 (polynomial 0x07, init 0, no reflection, no xor-out) over
  every byte from the 0xA5 lead up to (but excluding) the crc byte itself.

This is a different checksum and a different frame shape from the rest of
this package's protocol.py -- see oxyii_const.py's module docstring.

Frame codec, CRC-8, and the auth session-key derivation algorithm are
adapted from nglessner/o2ring-s-protocol's oxyii_protocol.py (MIT
licensed), verified there against captured vendor-app traffic and live
device round-trips. See this package's CLAUDE.md.
"""

from __future__ import annotations

import datetime
import hashlib
import struct

from .oxyii_const import (
    DEFAULT_AUTH_SERIAL,
    FLAG_REQUEST,
    FRAME_HEADER_SIZE,
    FRAME_LEAD,
    LEPUCLOUD_SALT,
)
from .oxyii_data import OxyIIDeviceInfo, OxyIIFileEntry, OxyIIReading

_HEADER_FORMAT = "<BBBBBH"  # lead, cmd, ~cmd, flag, seq, len (u16 LE)

LEPUCLOUD_MD5 = hashlib.md5(LEPUCLOUD_SALT).digest()


def crc8(data: bytes) -> int:
    """CRC-8 (polynomial 0x07, init 0, no reflection, no xor-out)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def encode_frame(opcode: int, payload: bytes = b"", seq: int = 0) -> bytes:
    """Build a complete request frame, ready to write to the OxyII write characteristic.

    Unlike the legacy protocol's encode_request(), no BLE-write chunking
    is needed here: every OxyII request is well under the negotiated
    517-byte MTU (the largest, READ_FILE_START, is 27 bytes total).
    """
    if not 0 <= opcode <= 0xFF:
        raise ValueError(f"opcode out of byte range: {opcode}")
    if len(payload) > 0xFFFF:
        raise ValueError(f"payload too long: {len(payload)}")
    header = struct.pack(
        _HEADER_FORMAT, FRAME_LEAD, opcode, (~opcode) & 0xFF, FLAG_REQUEST, seq & 0xFF,
        len(payload),
    )
    body = header + payload
    return body + bytes([crc8(body)])


class OxyIIFrameDecodeError(ValueError):
    """Raised when an incoming OxyII frame fails validation."""


class OxyIIFrameAssembler:
    """Accumulates BLE notification fragments into one complete OxyII frame.

    Mirrors protocol.py's ResponseAssembler: create one instance per
    in-flight request, feed it every notification received afterward, and
    it returns the decoded payload once the full frame (per its declared
    length) has arrived. Built this way rather than assuming "one
    notification == one frame" so it stays correct regardless of whether a
    given connection's negotiated MTU lets a large reply (e.g. a 512-byte
    READ_FILE_DATA chunk) fit in a single notification PDU.
    """

    def __init__(self) -> None:
        self._buffer = b""
        self._want: int | None = None
        self.opcode: int | None = None
        self.flag: int | None = None
        self.seq: int | None = None

    def feed(self, chunk: bytes) -> bytes | None:
        """Add a notification fragment. Returns the payload once complete."""
        self._buffer += bytes(chunk)

        if self._want is None:
            if len(self._buffer) < FRAME_HEADER_SIZE:
                return None
            lead, opcode, nopcode, flag, seq, length = struct.unpack(
                _HEADER_FORMAT, self._buffer[:FRAME_HEADER_SIZE]
            )
            if lead != FRAME_LEAD:
                raise OxyIIFrameDecodeError(f"bad lead byte: 0x{lead:02X}")
            if nopcode != (~opcode) & 0xFF:
                raise OxyIIFrameDecodeError(
                    f"opcode complement mismatch: cmd=0x{opcode:02X} ~cmd=0x{nopcode:02X}"
                )
            self.opcode = opcode
            self.flag = flag
            self.seq = seq
            self._want = FRAME_HEADER_SIZE + length + 1  # + trailing crc byte

        if len(self._buffer) < self._want:
            return None

        frame = self._buffer[: self._want]
        payload, crc = frame[FRAME_HEADER_SIZE:-1], frame[-1]
        computed = crc8(frame[:-1])
        if computed != crc:
            raise OxyIIFrameDecodeError(f"CRC mismatch: expected 0x{crc:02X}, got 0x{computed:02X}")

        return payload


def derive_session_key(serial: str = DEFAULT_AUTH_SERIAL, timestamp_seconds: int = 0) -> bytes:
    """Compute the 16-byte AES session key shared by host and device.

    Layout:
      bytes [0:8]   = MD5("lepucloud") at even indices [0,2,4,...,14]
      bytes [8:12]  = first 4 ASCII bytes of `serial`
      bytes [12:16] = (ts >> 0), (ts >> 1), (ts >> 2), (ts >> 3), each & 0xFF

    Never actually delivered over the wire -- both sides derive it
    locally. In practice the derived key is only used to build the
    cmd=0xFF auth XOR payload (see build_auth_payload); the AES path this
    key would otherwise unlock is never activated by T8520 firmware seen
    so far (auth never returns a session-key reply), so no AES
    encrypt/decrypt is implemented in this package -- see the module
    docstring.

    Args:
        serial: The device's serial number, or a portable default ("0000")
            if not yet known -- auth happens immediately after connect,
            before GET_INFO can be called to learn the real one.
        timestamp_seconds: Unix timestamp; any recent value works since
            the device does not validate this against its own clock skew
            in observed traffic.

    Raises:
        ValueError: If `serial` is shorter than 4 characters.
    """
    if len(serial) < 4:
        raise ValueError(f"serial too short for key derivation: {serial!r}")
    key = bytearray(16)
    for i in range(8):
        key[i] = LEPUCLOUD_MD5[i * 2]
    key[8:12] = serial[:4].encode("ascii")
    for n in range(4):
        key[12 + n] = (timestamp_seconds >> n) & 0xFF
    return bytes(key)


def build_auth_payload(serial: str = DEFAULT_AUTH_SERIAL, timestamp_seconds: int = 0) -> bytes:
    """Build the 16-byte cmd=0xFF auth payload: session key XOR'd with the salt's MD5.

    A one-way message (no reply is ever sent back) that puts the ring's
    state machine into the mode that accepts file-transfer commands.
    """
    session_key = derive_session_key(serial, timestamp_seconds)
    return bytes(a ^ b for a, b in zip(session_key, LEPUCLOUD_MD5))


def parse_device_info(payload: bytes) -> OxyIIDeviceInfo:
    """Decode a GET_INFO (cmd=0xE1) reply payload.

    Field offsets per the upstream repo's captured-traffic mapping. Only
    the serial number and firmware version are decoded; several other
    fields (model/build code, capacity descriptors, flag bits) are
    present but unconfirmed -- kept in `raw` rather than guessed at.
    """
    if len(payload) < 48:
        raise OxyIIFrameDecodeError(f"GET_INFO reply too short: {len(payload)}")
    firmware_version = payload[9:17].decode("ascii", errors="replace").rstrip("\x00")
    serial_length = payload[37]
    if serial_length <= 0 or 38 + serial_length > len(payload):
        serial_number = ""
    else:
        serial_number = payload[38:38 + serial_length].decode("ascii", errors="replace")
    return OxyIIDeviceInfo(
        raw=bytes(payload), serial_number=serial_number, firmware_version=firmware_version
    )


def parse_battery_percent(payload: bytes) -> int | None:
    """Decode a GET_BATTERY (cmd=0xE4) reply's battery-percent field.

    Only byte[1] of the 4-byte reply is confirmed (it's cross-referenced
    against LIVE_SAMPLES_B's own battery byte, which matches it in
    observed traffic); the other three bytes' meaning ("status") isn't
    independently confirmed upstream, so they're not parsed here.

    Returns None if the payload is too short to contain byte[1].
    """
    if len(payload) < 2:
        return None
    return payload[1]


def parse_file_list(payload: bytes) -> list[OxyIIFileEntry]:
    """Decode a GET_FILE_LIST (cmd=0xF1) reply payload.

    Layout: 1-byte count, then count * 16-byte slots (14-byte ASCII
    timestamp-format name, e.g. "20260427105949", + 2 zero pad bytes).
    File size is not included here -- it's only learned once a file is
    opened via READ_FILE_START.
    """
    if not payload:
        return []
    count = payload[0]
    slot_size = 16
    entries = []
    pos = 1
    for _ in range(count):
        if pos + slot_size > len(payload):
            break
        slot = payload[pos:pos + slot_size]
        name = slot.rstrip(b"\x00").decode("ascii", errors="replace")
        entries.append(OxyIIFileEntry(name=name))
        pos += slot_size
    return entries


def parse_live_reading(
    payload: bytes, received_at: datetime.datetime | None = None
) -> OxyIIReading | None:
    """Decode a LIVE_SAMPLES_B (cmd=0x04) reply's 24-byte header.

    Returns None if the payload is too short to contain the header.
    Field offsets identified by the upstream repo by polling cmd=0x04 and
    matching bytes against the ring's own on-device display. The trailing
    PPG waveform body (offset 26 on) is kept as `raw` rather than decoded
    -- see OxyIIReading's docstring for why.

    `contact_state` only has three confirmed values (0x00 = no finger
    contact; 0x01 = idle, finger present; 0x03 = a file handle is left
    open -- see "the F1 wedge" in this package's CLAUDE.md). Whether 0x03
    implies finger contact isn't documented either way, so `worn` is
    conservatively derived as "not the explicitly-documented no-contact
    state" rather than assumed either way for 0x03.
    """
    if payload is None or len(payload) < 24:
        return None

    contact_state = payload[5]
    spo2 = payload[6]
    motion = payload[7]
    heart_rate = payload[8]
    battery = payload[13]
    worn = contact_state != 0x00

    return OxyIIReading(
        spo2=spo2,
        heart_rate=heart_rate,
        battery=battery,
        motion=motion,
        worn=worn,
        contact_state=contact_state,
        file_handle_open=contact_state == 0x03,
        calibrating=worn and spo2 == 0 and heart_rate == 0,
        raw=bytes(payload),
        received_at=received_at or datetime.datetime.now(),
    )
