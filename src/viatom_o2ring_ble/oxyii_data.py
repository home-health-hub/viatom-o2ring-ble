"""Dataclasses for the OxyII (O2Ring-S) protocol.

Distinct from data.py's dataclasses on purpose -- the two protocols'
wire formats don't share field layouts, and conflating e.g. Reading with
OxyIIReading would blur which protocol a given value actually came from.
"""

from __future__ import annotations

import dataclasses
import datetime


@dataclasses.dataclass
class OxyIIReading:
    """A single live measurement from LIVE_SAMPLES_B (cmd=0x04).

    Attributes:
        spo2: Blood oxygen saturation, percent.
        heart_rate: Pulse rate, beats per minute.
        battery: Battery level, percent.
        motion: Motion/activity level (~10 at rest, >=50 when shaking).
        worn: Whether the device reports finger contact. See
            oxyii_protocol.parse_live_reading's docstring for the caveat
            on how this is derived from `contact_state`.
        contact_state: Raw ring state/contact byte (0x00/0x01/0x03
            confirmed; others unconfirmed) -- kept alongside `worn` for
            callers that want the exact value rather than the derived bool.
        file_handle_open: Whether the device has a file handle left open
            (contact_state == 0x03) -- see "the F1 wedge" in CLAUDE.md:
            GET_FILE_LIST silently hangs until READ_FILE_END is sent.
        calibrating: Worn, but SpO2/heart rate have not stabilized yet.
        raw: The raw notification payload this reading was parsed from,
            including the undecoded PPG waveform tail (see module note in
            oxyii_protocol.parse_live_reading -- waveform decoding is
            documented upstream but not yet exercised/validated there).
        received_at: Local time the notification arrived.
    """

    spo2: int
    heart_rate: int
    battery: int
    motion: int
    worn: bool
    contact_state: int
    file_handle_open: bool
    calibrating: bool
    raw: bytes
    received_at: datetime.datetime


@dataclasses.dataclass
class OxyIIDeviceInfo:
    """Response to GET_INFO (cmd=0xE1).

    Only serial_number and firmware_version are decoded; the rest of the
    60-byte reply (model/build code, capacity descriptors, flag bits) is
    present but unconfirmed -- see `raw`.

    Attributes:
        raw: The full undecoded reply payload.
        serial_number: Device serial number (e.g. "25B2303210").
        firmware_version: Firmware version string (e.g. "2D010002").
    """

    raw: bytes
    serial_number: str = ""
    firmware_version: str = ""


@dataclasses.dataclass
class OxyIIFileEntry:
    """One entry from a GET_FILE_LIST (cmd=0xF1) reply.

    Attributes:
        name: The device-assigned file name, a `YYYYMMDDhhmmss` timestamp
            (e.g. "20260427105949"). Unlike the legacy protocol's file
            names, there is no extension.
    """

    name: str


@dataclasses.dataclass
class OxyIIFileHeader:
    """Parsed header + trailer of a Format A stored recording.

    Format A is this device's native stored-file format -- distinct from
    VldHeader/VldRecord (the legacy protocol's format B / .vld), which a
    T8520 has only ever been observed to produce via BLE READ_FILE as
    Format A. See file.py.

    The trailer (and therefore every field except `record_count`) is only
    present once the recording has finalized -- see
    `finalized`/`trailer_confirmed`.

    Attributes:
        record_count: Number of 3-byte body records (1 sample/second).
        finalized: Whether a 48-byte trailer was found at all.
        trailer_confirmed: Whether the trailer's sub-magic bytes were
            found at the expected offset. A file can reach its full byte
            count (per READ_FILE_START's reported size) before the
            trailer has actually flushed; this is the reliable
            "recording is really complete" check, not size alone -- see
            file.py.
        spo2_avg: Average SpO2 for the session, percent (rounded integer).
        spo2_min: Minimum SpO2 for the session, percent (byte-exact with
            the body's own minimum).
        spo2_below_3pct_events: ODI-style desaturation event count (3% dips).
        spo2_below_4pct_events: ODI-style desaturation event count (4% dips).
        seconds_below_90pct: Total time spent under 90% SpO2, seconds.
        events_below_90pct: Count of distinct episodes under 90% SpO2.
        o2_score: Device-computed overall oxygen score (0-10 scale), or
            None if the trailer reports "not applicable" (typically on
            very short sessions).
        heart_rate_avg: Average heart rate for the session, bpm (rounded
            integer).
    """

    record_count: int
    finalized: bool
    trailer_confirmed: bool
    spo2_avg: int | None = None
    spo2_min: int | None = None
    spo2_below_3pct_events: int | None = None
    spo2_below_4pct_events: int | None = None
    seconds_below_90pct: int | None = None
    events_below_90pct: int | None = None
    o2_score: float | None = None
    heart_rate_avg: int | None = None


@dataclasses.dataclass
class OxyIIFileRecord:
    """A single 1-second sample record from a Format A stored recording.

    Attributes:
        index: Zero-based sample index within the recording (records have
            no embedded timestamp -- see file.py for how absolute time is
            derived, if at all).
        spo2: Blood oxygen saturation, percent. 0 means invalid.
        heart_rate: Pulse rate, beats per minute. 0 means invalid;
            FILE_NO_FINGER_HR_SENTINEL (0xFF/255) means no finger contact
            -- callers plotting this should clamp/exclude it rather than
            treat it as a real 255 bpm reading.
        status_flags: Raw status byte (low bits reported as
            invalid/motion/etc in observed traffic); kept raw since the
            exact bit meanings aren't independently confirmed here.
    """

    index: int
    spo2: int
    heart_rate: int
    status_flags: int
