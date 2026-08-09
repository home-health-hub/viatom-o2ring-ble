from __future__ import annotations

import dataclasses
import datetime


@dataclasses.dataclass
class Reading:
    """A single live measurement from the legacy CMD_READ_SENSORS (0x17).

    This command is confirmed working against O2Ring-era firmware (it's
    viatom-ble's and o2r's live-reading path) but is not what the current
    official app uses -- see RtReading/CMD_RT_DATA, which is the default
    in O2RingClient. Kept as an explicit fallback.

    Attributes:
        spo2: Blood oxygen saturation, percent.
        heart_rate: Pulse rate, beats per minute.
        battery: Battery level, percent.
        charging: Charging status (0 = not charging, 1 = charging,
            2 = fully charged), per o2r's o2state.py. Not exposed by the
            O2Ring protocol docs or viatom-ble.
        movement: Motion indicator reported by the device.
        perfusion_index: Perfusion index reported by the device.
        worn: Whether the device considers itself worn (finger present).
        calibrating: Worn, but SpO2/HR have not stabilized yet.
        raw: The raw notification payload this reading was parsed from.
        received_at: Local time the notification arrived.
    """

    spo2: int
    heart_rate: int
    battery: int
    charging: int
    movement: int
    perfusion_index: int
    worn: bool
    calibrating: bool
    raw: bytes
    received_at: datetime.datetime


@dataclasses.dataclass
class RtReading:
    """A single live measurement from CMD_RT_DATA (0x1B).

    This is the command the current official app actually polls (roughly
    once per second) for live monitoring, per LepuBle's OxyBleResponse.RtWave.
    Unlike the legacy Reading, pulse rate is a real 2-byte value and each
    response also carries a chunk of raw PPG waveform samples.

    Attributes:
        spo2: Blood oxygen saturation, percent.
        pulse_bpm: Pulse rate, beats per minute (0-65535; the legacy
            command's single byte was always the low byte of this value).
        battery: Battery level, percent.
        battery_state: Charging status (0 = not charging, 1 = charging,
            2 = fully charged).
        perfusion_index: Perfusion index reported by the device.
        worn: Whether the device reports its lead/finger sensor as on.
        calibrating: Worn, but SpO2/pulse have not stabilized yet.
        waveform: Raw PPG waveform sample bytes accompanying this reading.
            The official app replaces the sentinel value 156 in this data
            with the average of its neighbors before rendering; that
            smoothing is a display concern and is not applied here.
        raw: The raw notification payload this reading was parsed from.
        received_at: Local time the notification arrived.
    """

    spo2: int
    pulse_bpm: int
    battery: int
    battery_state: int
    perfusion_index: int
    worn: bool
    calibrating: bool
    waveform: bytes
    raw: bytes
    received_at: datetime.datetime


@dataclasses.dataclass
class DeviceInfo:
    """Response to CMD_INFO.

    Field set follows LepuBle's OxyBleResponse.OxyInfo, which is
    considerably richer than the four fields the O2Ring protocol docs
    show. Note battery_percent (the JSON's "CurBAT") is parsed
    best-effort: the official app's own code comments it out as "hard to
    interpret" and instead sources battery level from live readings.

    Attributes:
        model: Device model name (e.g. "O2Ring").
        serial_number: Device serial number.
        region: Region/locale code reported by the device.
        hardware_version: Hardware version string.
        software_version: Software/firmware version string.
        bootloader_version: Bootloader version string.
        battery_percent: Current battery level, percent. Best-effort --
            see note above.
        battery_state: Charging status (0/1/2, see Reading.charging).
        oxi_threshold: Current SpO2 alert threshold, percent (what
            commands.set_o2_alert writes).
        vibration_strength: Current vibration motor strength.
        mode: Current device mode (0 = sleep, 1 = monitor), matching
            VldHeader.mode.
        pedometer_target: Configured daily step target.
        current_time: Device's own clock, as a "%Y-%m-%d,%H:%M:%S" string.
        file_names: Stored measurement file names (e.g. "20260116233312.vld").
    """

    model: str = ""
    serial_number: str = ""
    region: str = ""
    hardware_version: str = ""
    software_version: str = ""
    bootloader_version: str = ""
    battery_percent: int | None = None
    battery_state: int | None = None
    oxi_threshold: int | None = None
    vibration_strength: int | None = None
    mode: int | None = None
    pedometer_target: int | None = None
    current_time: str = ""
    file_names: tuple[str, ...] = ()


@dataclasses.dataclass
class VldHeader:
    """Parsed header of a version-3 .vld measurement file.

    Field layout follows LepuBle's OxyDataFile.kt, the official app's own
    parser. This corrects two bugs and adds two fields relative to what
    o2r's o2file.py (and this package's first cut) implemented:

    - `version` is a standalone byte; o2r read it as a 2-byte value that
      happens to include the *next* byte, `mode` -- silently correct only
      when mode is 0.
    - `o2_score` is the raw byte divided by 10 (e.g. raw 85 -> 8.5), not
      the raw integer.
    - `percent_below_90pct` and `steps` are real fields the previous
      26-byte parse window never reached.

    Attributes:
        version: File format version; only 3 is supported.
        mode: Recording mode (0 = sleep, 1 = monitor).
        start_time: Timestamp of the first record.
        file_size: File size in bytes, as declared by the device itself.
        duration_seconds: Total recording duration, in seconds.
        spo2_avg: Average SpO2 for the session, percent.
        spo2_min: Minimum SpO2 for the session, percent.
        spo2_below_3pct_events: ODI-style desaturation event count (3% dips).
        spo2_below_4pct_events: ODI-style desaturation event count (4% dips).
        seconds_below_90pct: Total time spent under 90% SpO2, seconds.
        events_below_90pct: Count of distinct episodes under 90% SpO2.
        percent_below_90pct: Percent of the session spent under 90% SpO2.
        o2_score: Device-computed overall oxygen score (0-10 scale).
        steps: Pedometer step count for the session.
        record_count: Number of 5-byte records following the header.
        resolution_seconds: Seconds between records (2.0 or 4.0).
    """

    version: int
    mode: int
    start_time: datetime.datetime
    file_size: int
    duration_seconds: int
    spo2_avg: int
    spo2_min: int
    spo2_below_3pct_events: int
    spo2_below_4pct_events: int
    seconds_below_90pct: int
    events_below_90pct: int
    percent_below_90pct: float
    o2_score: float
    steps: int
    record_count: int
    resolution_seconds: float


@dataclasses.dataclass
class VldRecord:
    """A single 5-byte measurement record from a .vld file.

    Field layout follows LepuBle's OxyDataFile.O2Sample. This corrects a
    bug relative to o2r's o2file.py (and this package's first cut): pulse
    rate is a 2-byte value, not 1. o2r's 1-byte read happened to work
    because real pulse rates never reach 256 bpm, which also means its
    "oximetry_invalid" flag -- the byte that's actually the pulse rate's
    high byte -- was likely never meaningfully True in a real file; it has
    been dropped here rather than kept as a misleading field.

    Attributes:
        time: Timestamp derived from the header start time plus resolution.
        spo2: Blood oxygen saturation, percent. 0xFF conventionally means
            no finger. CSV export widens the no-finger pulse value to
            65535 to match Viatom's own PC software output -- see
            `file.write_csv`.
        heart_rate: Pulse rate, beats per minute, as stored in the file.
        acceleration: Maximum 3-axis acceleration vector sum over the
            sample window, per LepuBle. o2r calls this byte "motion";
            Viatom's own field name/description is more specific.
        reserved: The record's last byte. The O2Ring protocol docs call
            this "vibration alert status", but LepuBle's own parser marks
            it reserved and never decodes it -- kept here as a raw value
            since its real meaning is unconfirmed either way.
    """

    time: datetime.datetime
    spo2: int
    heart_rate: int
    acceleration: int
    reserved: int
