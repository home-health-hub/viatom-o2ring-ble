"""Parsing and CSV export for OxyII "Format A" stored recordings.

Format A (10-byte fixed header + 3-byte/sample body + a 48-byte trailer
once finalized) is what a T8520 has been observed to produce via BLE
READ_FILE -- distinct from this package's other stored-file format
(file.py's version-3 .vld, format B), which the upstream protocol repo
notes exists in older firmware/other devices in the wider Viatom/Wellue
lineup but has not actually been seen from a T8520 over BLE.

Field layout adapted from nglessner/o2ring-s-protocol's README (trailer
mapping contributed by @knifebunny in that repo's issue #1, cross-
validated by its author against eight recordings on firmware 2D010002).
See this package's CLAUDE.md.
"""

from __future__ import annotations

import csv
import datetime
from collections.abc import Iterator, Sequence

from .oxyii_const import (
    FILE_HEADER,
    FILE_HEADER_SIZE,
    FILE_NO_FINGER_HR_SENTINEL,
    FILE_RECORD_SIZE,
    FILE_TRAILER_SIZE,
    FILE_TRAILER_SUB_MAGIC,
)
from .oxyii_data import OxyIIFileHeader, OxyIIFileRecord

#: The filename GET_FILE_LIST returns for a recording is itself its start
#: time, `YYYYMMDDhhmmss` -- Format A's own header/trailer carry no
#: absolute timestamp (see OxyIIFileHeader/OxyIIFileRecord's docstrings).
FILENAME_TIME_FORMAT = "%Y%m%d%H%M%S"

CSV_FILE_FIELDNAMES = ("Time", "SpO2(%)", "Pulse Rate(bpm)", "Status Flags")


def parse_filename_timestamp(name: str) -> datetime.datetime:
    """Parse a stored-recording filename (e.g. "20260427105949") as its start time.

    Raises:
        ValueError: If `name` isn't in the expected 14-digit format.
    """
    return datetime.datetime.strptime(name, FILENAME_TIME_FORMAT)


def _iter_records(body: bytes) -> Iterator[OxyIIFileRecord]:
    for index, offset in enumerate(range(0, len(body), FILE_RECORD_SIZE)):
        spo2, heart_rate, status_flags = body[offset:offset + FILE_RECORD_SIZE]
        yield OxyIIFileRecord(
            index=index, spo2=spo2, heart_rate=heart_rate, status_flags=status_flags
        )


def parse(data: bytes) -> tuple[OxyIIFileHeader, list[OxyIIFileRecord]]:
    """Parse a complete Format A recording already read into memory.

    Handles both a finalized recording (trailer present, anchored by the
    sub-magic bytes at trailer offset 4-7) and one still in progress
    (records only, no trailer yet) -- a file can also reach its full
    trailer-sized byte count *before* the trailer has actually flushed,
    so the sub-magic anchor is checked rather than trusting size alone;
    see OxyIIFileHeader.trailer_confirmed.

    Raises:
        ValueError: If the file is too short for the fixed header, the
            header doesn't match the expected constant bytes, or the
            (post-header, post-trailer-if-any) body isn't a whole number
            of 3-byte records.
    """
    if len(data) < FILE_HEADER_SIZE:
        raise ValueError(
            f"File too short for a Format A header: got {len(data)} bytes, "
            f"need at least {FILE_HEADER_SIZE}"
        )
    if data[:FILE_HEADER_SIZE] != FILE_HEADER:
        raise ValueError(f"Unrecognized Format A header: {data[:FILE_HEADER_SIZE].hex()}")

    body = data[FILE_HEADER_SIZE:]

    trailer = b""
    trailer_confirmed = False
    if len(body) >= FILE_TRAILER_SIZE:
        candidate_trailer = body[-FILE_TRAILER_SIZE:]
        candidate_body = body[:-FILE_TRAILER_SIZE]
        if (
            len(candidate_body) % FILE_RECORD_SIZE == 0
            and candidate_trailer[4:8] == FILE_TRAILER_SUB_MAGIC
        ):
            trailer_confirmed = True
            trailer = candidate_trailer
            body = candidate_body

    if not trailer_confirmed and len(body) % FILE_RECORD_SIZE != 0:
        raise ValueError(
            "File length is not a whole number of records and no trailer "
            "anchor was found; file may be truncated"
        )

    records = list(_iter_records(body))

    if not trailer_confirmed:
        header = OxyIIFileHeader(
            record_count=len(records), finalized=False, trailer_confirmed=False
        )
        return header, records

    o2_score_raw = trailer[42]
    header = OxyIIFileHeader(
        record_count=len(records),
        finalized=True,
        trailer_confirmed=True,
        spo2_avg=trailer[34],
        spo2_min=trailer[35],
        spo2_below_3pct_events=trailer[36],
        spo2_below_4pct_events=trailer[37],
        seconds_below_90pct=int.from_bytes(trailer[39:41], "little"),
        events_below_90pct=trailer[41],
        o2_score=None if o2_score_raw == 0xFF else o2_score_raw / 10,
        heart_rate_avg=trailer[47],
    )
    return header, records


def write_csv(
    records: Sequence[OxyIIFileRecord], path: str, start_time: datetime.datetime | None = None
) -> None:
    """Write decoded records to `path` as CSV.

    Args:
        records: Records to include, oldest first.
        path: Filesystem path to write the CSV to.
        start_time: The recording's start time (e.g. from
            `parse_filename_timestamp` on its GET_FILE_LIST name), used to
            render an absolute "Time" column one second apart per record.
            If None, the raw zero-based sample index is written instead.
    """
    with open(path, "w", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(CSV_FILE_FIELDNAMES)
        for record in records:
            no_finger = record.heart_rate == FILE_NO_FINGER_HR_SENTINEL
            time_value = (
                (start_time + datetime.timedelta(seconds=record.index)).isoformat()
                if start_time is not None
                else record.index
            )
            writer.writerow(
                (
                    time_value,
                    record.spo2,
                    "" if no_finger else record.heart_rate,
                    record.status_flags,
                )
            )
