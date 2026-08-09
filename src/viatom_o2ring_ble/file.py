"""Parsing and CSV export for version-3 .vld measurement files.

Header and record struct layouts follow LepuBle's OxyDataFile.kt, the
official app's own parser -- not o2r's o2file.py, which this package
initially followed and which turns out to disagree in several places
(see data.py's VldHeader/VldRecord docstrings for the specifics: a
version/mode byte-count bug, an unscaled o2_score, two undecoded fields,
and a 1-byte vs. 2-byte pulse rate).
"""

from __future__ import annotations

import csv
import datetime
import struct
from collections.abc import Iterator, Sequence

from .const import (
    CSV_FILE_FIELDNAMES,
    CSV_TIME_FORMAT,
    NO_FINGER_PULSE,
    NO_FINGER_SPO2,
    VLD3_HEADER_PARSED_SIZE,
    VLD3_HEADER_TOTAL_SIZE,
    VLD3_RECORD_SIZE,
)
from .data import VldHeader, VldRecord

# version, mode, year, month, day, hour, minute, second, file_size,
# duration_seconds, <reserved>, spo2_avg, spo2_min, drop3, drop4,
# seconds_below_90pct, events_below_90pct, percent90(raw), o2_score(raw), steps
_HEADER_STRUCT = "<BBHBBBBBIHHBBBBHBBBI"
_RECORD_STRUCT = "<BHBB"  # spo2, heart_rate (2 bytes), acceleration, reserved

#: Sample resolutions confirmed for O2Ring-class devices (o2r). Other
#: members of this device family may use different resolutions; this
#: hasn't been verified against real files from anything but an O2Ring.
_VALID_RESOLUTIONS = (2.0, 4.0)


def parse(data: bytes) -> tuple[VldHeader, list[VldRecord]]:
    """Parse a complete version-3 .vld file already read into memory.

    Raises ValueError if the file is too short, not version 3, has a
    partial trailing record, or has a sample resolution other than the
    two known-good values (see _VALID_RESOLUTIONS).
    """
    if len(data) < VLD3_HEADER_TOTAL_SIZE:
        raise ValueError(
            f"File too short for a VLD header: got {len(data)} bytes, "
            f"need at least {VLD3_HEADER_TOTAL_SIZE}"
        )

    (
        version,
        mode,
        year,
        month,
        day,
        hour,
        minute,
        second,
        file_size,
        duration_seconds,
        _reserved,
        spo2_avg,
        spo2_min,
        spo2_below_3pct_events,
        spo2_below_4pct_events,
        seconds_below_90pct,
        events_below_90pct,
        percent90_raw,
        o2_score_raw,
        steps,
    ) = struct.unpack(_HEADER_STRUCT, data[:VLD3_HEADER_PARSED_SIZE])

    if version != 3:
        raise ValueError(f"Only VLD version 3 is supported, got version {version}")

    body = data[VLD3_HEADER_TOTAL_SIZE:]
    if len(body) % VLD3_RECORD_SIZE != 0:
        raise ValueError("File length is not a whole number of records; file may be truncated")

    record_count = len(body) // VLD3_RECORD_SIZE
    resolution = duration_seconds / record_count if record_count else 0.0
    if record_count and resolution not in _VALID_RESOLUTIONS:
        raise ValueError(
            f"Unexpected sample resolution {resolution}s (expected 2 or 4); "
            "file may be truncated or corrupt"
        )

    header = VldHeader(
        version=version,
        mode=mode,
        start_time=datetime.datetime(year, month, day, hour, minute, second),
        file_size=file_size,
        duration_seconds=duration_seconds,
        spo2_avg=spo2_avg,
        spo2_min=spo2_min,
        spo2_below_3pct_events=spo2_below_3pct_events,
        spo2_below_4pct_events=spo2_below_4pct_events,
        seconds_below_90pct=seconds_below_90pct,
        events_below_90pct=events_below_90pct,
        percent_below_90pct=percent90_raw / 100,
        o2_score=o2_score_raw / 10,
        steps=steps,
        record_count=record_count,
        resolution_seconds=resolution,
    )

    return header, list(_iter_records(body, header))


def _iter_records(body: bytes, header: VldHeader) -> Iterator[VldRecord]:
    step = datetime.timedelta(seconds=header.resolution_seconds)
    t = header.start_time
    for i in range(0, len(body), VLD3_RECORD_SIZE):
        spo2, heart_rate, acceleration, reserved = struct.unpack(
            _RECORD_STRUCT, body[i:i + VLD3_RECORD_SIZE]
        )
        yield VldRecord(
            time=t,
            spo2=spo2,
            heart_rate=heart_rate,
            acceleration=acceleration,
            reserved=reserved,
        )
        t += step


def write_csv(records: Sequence[VldRecord], path: str) -> None:
    """Write decoded records to `path` as CSV.

    The no-finger pulse value is widened from the raw file's 0xFF byte to
    65535, matching the convention viatom-ble also uses for live-reading
    CSV export.
    """
    with open(path, "w", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow((*CSV_FILE_FIELDNAMES, ""))
        for rec in records:
            no_finger = rec.spo2 == 0xFF
            writer.writerow(
                (
                    rec.time.strftime(CSV_TIME_FORMAT),
                    NO_FINGER_SPO2 if no_finger else rec.spo2,
                    NO_FINGER_PULSE if no_finger else rec.heart_rate,
                    rec.acceleration,
                    rec.reserved,
                    "",
                )
            )
