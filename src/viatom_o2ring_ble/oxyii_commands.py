"""Request builders for the OxyII (O2Ring-S) protocol.

Only the opcodes exercised by OxyIIClient are wrapped here. Two opcodes
are deliberately not wrapped anywhere in this package:

- SET_CONFIG (cmd=0x01): a write path whose field-index/value semantics
  are, per the upstream repo, mostly "not documented -- read GET_CONFIG
  before and after a write to discover them empirically." Wrapping a
  write command this loosely specified risks silently writing the wrong
  field/value to a real device; out of scope until better documented.
- FACTORY_RESET_ALL (cmd=0xEE): powers the ring off and refuses to
  re-advertise until woken by USB power, per the upstream repo's own
  "do not issue" guidance. There is no legitimate reason for this
  package to send it.

FACTORY_RESET (cmd=0xE3) *is* wrapped, mirroring commands.py's
factory_default() -- both wipe the device outright and both carry an
explicit "use deliberately" warning rather than being omitted, since a
caller may have a legitimate reason to reset a device it owns.
"""

from __future__ import annotations

import time

from .oxyii_const import (
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
from .oxyii_protocol import build_auth_payload, encode_frame

_FILENAME_SLOT_SIZE = 16


def auth(serial: str, seq: int = 0) -> bytes:
    """cmd=0xFF: one-way auth handshake, required before any other command.

    No reply is ever sent back for this one -- callers should not wait
    for one (see OxyIIClient._connect).
    """
    return encode_frame(0xFF, build_auth_payload(serial, int(time.time())), seq=seq)


def setup(seq: int = 0) -> bytes:
    """cmd=0x10: required post-auth handshake step. Exact purpose unknown;
    skipping it causes cmd=0xF2 (READ_FILE_START) to be silently rejected.
    """
    return encode_frame(OP_SETUP, b"\x00", seq=seq)


def set_utc_time(when: time.struct_time | None = None, seq: int = 0) -> bytes:
    """cmd=0xC0: sync the device's RTC.

    Payload: year (u16 LE), month, day, hour, minute, second, then one
    trailing byte the upstream repo found unused (0xCE, what the vendor
    app sends, and 0x00 are both accepted with no observed difference).
    Defaults to the current local system time.
    """
    t = when or time.localtime()
    payload = bytes([
        t.tm_year & 0xFF, (t.tm_year >> 8) & 0xFF,
        t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec,
        0x00,
    ])
    return encode_frame(OP_SET_UTC_TIME, payload, seq=seq)


def get_info(seq: int = 0) -> bytes:
    """cmd=0xE1: request serial number, firmware version, etc."""
    return encode_frame(OP_GET_INFO, seq=seq)


def get_battery(seq: int = 0) -> bytes:
    """cmd=0xE4: request battery level/status."""
    return encode_frame(OP_GET_BATTERY, seq=seq)


def get_config(seq: int = 0) -> bytes:
    """cmd=0x00: request the ring's current settings struct (read-only here).

    Must be sent (and its reply consumed) as part of the connect
    handshake, strictly before any READ_FILE_* commands -- see
    OxyIIClient._connect and this package's CLAUDE.md for why.
    """
    return encode_frame(OP_GET_CONFIG, seq=seq)


def live_samples(seq: int = 0) -> bytes:
    """cmd=0x04: request one live SpO2/heart-rate/battery reading (+ PPG body)."""
    return encode_frame(OP_LIVE_SAMPLES_B, seq=seq)


def get_file_list(seq: int = 0) -> bytes:
    """cmd=0xF1: list stored recordings.

    Callers should send read_file_end() first if a file might still be
    open from a previous session -- see "the F1 wedge" in CLAUDE.md:
    GET_FILE_LIST is silently dropped (not even an error reply) while a
    file handle is open, including one the ring itself opened while
    finalizing an autonomous recording.
    """
    return encode_frame(OP_GET_FILE_LIST, seq=seq)


def read_file_start(filename: str, file_type: int = 0, seq: int = 0) -> bytes:
    """cmd=0xF2: open a stored file for reading. Requires MTU >= 247.

    Payload: 16-byte null-padded ASCII filename slot, then a 4-byte LE
    file_type (0 = the SpO2 recording itself; other values reserved).
    """
    name_bytes = filename.encode("ascii")[:_FILENAME_SLOT_SIZE]
    payload = bytearray(_FILENAME_SLOT_SIZE + 4)
    payload[:len(name_bytes)] = name_bytes
    payload[_FILENAME_SLOT_SIZE:] = (file_type & 0xFFFFFFFF).to_bytes(4, "little")
    return encode_frame(OP_READ_FILE_START, bytes(payload), seq=seq)


def read_file_data(offset: int, seq: int = 0) -> bytes:
    """cmd=0xF3: request the next chunk (up to 512 bytes) of the open file.

    Payload: 4-byte LE byte offset. The device decides chunk size; loop,
    incrementing offset by each reply's length, until an empty reply or
    offset reaches the file size reported by read_file_start()'s reply.
    """
    return encode_frame(OP_READ_FILE_DATA, offset.to_bytes(4, "little"), seq=seq)


def read_file_end(seq: int = 0) -> bytes:
    """cmd=0xF4: close the currently open file (a no-op if none is open --
    safe, and necessary, to send unconditionally; see get_file_list())."""
    return encode_frame(OP_READ_FILE_END, seq=seq)


def factory_reset(seq: int = 0) -> bytes:
    """cmd=0xE3: wipe device settings AND every stored recording.

    Destructive; use deliberately. There is no settings-only reset on
    this firmware -- both always clear together despite the naming. The
    device usually stays connected and returns an empty ack, but
    sometimes drops the link while finishing the wipe; callers should be
    prepared to re-scan and re-handshake either way.
    """
    return encode_frame(OP_FACTORY_RESET, seq=seq)
