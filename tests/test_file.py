from __future__ import annotations

import csv
import datetime
import struct

import pytest

from viatom_o2ring_ble.data import VldRecord
from viatom_o2ring_ble.file import parse, write_csv

# version, mode, year, month, day, hour, minute, second, file_size,
# duration_seconds, <reserved>, spo2_avg, spo2_min, drop3, drop4,
# seconds_below_90pct, events_below_90pct, percent90(raw), o2_score(raw), steps
_HEADER_STRUCT = "<BBHBBBBBIHHBBBBHBBBI"
_RECORD_STRUCT = "<BHBB"


def _build_record(spo2: int, heart_rate: int, acceleration: int = 0, reserved: int = 0) -> bytes:
    return struct.pack(_RECORD_STRUCT, spo2, heart_rate, acceleration, reserved)


def _build_file(
    *,
    version: int = 3,
    mode: int = 0,
    duration: int = 8,
    records: bytes | None = None,
) -> bytes:
    if records is None:
        records = _build_record(0x61, 72, acceleration=3) + _build_record(0xFF, 0xFFFF)

    header = struct.pack(
        _HEADER_STRUCT,
        version,
        mode,
        2026,
        1,
        31,
        22,
        33,
        40,
        1234,  # file_size (declared; not cross-checked against actual length by parse())
        duration,
        0,  # reserved
        95,  # spo2_avg
        90,  # spo2_min
        1,  # drop3
        2,  # drop4
        120,  # seconds_below_90pct
        3,  # events_below_90pct
        15,  # percent90 raw -> 0.15
        85,  # o2_score raw -> 8.5
        4321,  # steps
    )
    header += b"\x00" * (40 - len(header))  # pad to the file's real 40-byte header block
    return header + records


def test_parse_header_and_records():
    header, recs = parse(_build_file())

    assert header.version == 3
    assert header.mode == 0
    assert header.start_time == datetime.datetime(2026, 1, 31, 22, 33, 40)
    assert header.duration_seconds == 8
    assert header.spo2_avg == 95
    assert header.spo2_min == 90
    assert header.spo2_below_3pct_events == 1
    assert header.spo2_below_4pct_events == 2
    assert header.seconds_below_90pct == 120
    assert header.events_below_90pct == 3
    assert header.percent_below_90pct == pytest.approx(0.15)
    assert header.o2_score == pytest.approx(8.5)
    assert header.steps == 4321
    assert header.record_count == 2
    assert header.resolution_seconds == 4.0

    assert len(recs) == 2
    assert recs[0].spo2 == 0x61
    assert recs[0].heart_rate == 72
    assert recs[0].acceleration == 3
    assert recs[0].time == header.start_time

    assert recs[1].spo2 == 0xFF
    assert recs[1].heart_rate == 0xFFFF
    assert recs[1].time == header.start_time + datetime.timedelta(seconds=4)


def test_parse_mode_byte_does_not_corrupt_version():
    # This is the bug this layout fixes: reading version+mode as one 2-byte
    # field (o2r's approach) would compute version=259 when mode=1 and
    # wrongly reject the file.
    header, _ = parse(_build_file(mode=1))
    assert header.version == 3
    assert header.mode == 1


def test_parse_rejects_non_v3():
    with pytest.raises(ValueError, match="version 3"):
        parse(_build_file(version=2))


def test_parse_rejects_truncated_trailing_record():
    data = _build_file()[:-2]  # chop the last record short
    with pytest.raises(ValueError, match="truncated"):
        parse(data)


def test_parse_rejects_unexpected_resolution():
    # duration=7 over 2 records -> 3.5s/sample, neither of the two known
    # device resolutions (2s or 4s).
    with pytest.raises(ValueError, match="resolution"):
        parse(_build_file(duration=7))


def test_write_csv_widens_no_finger_pulse_sentinel(tmp_path):
    records = [
        VldRecord(
            time=datetime.datetime(2026, 1, 31, 22, 33, 40),
            spo2=97,
            heart_rate=72,
            acceleration=3,
            reserved=0,
        ),
        VldRecord(
            time=datetime.datetime(2026, 1, 31, 22, 33, 44),
            spo2=0xFF,
            heart_rate=0xFFFF,
            acceleration=0,
            reserved=0,
        ),
    ]

    out = tmp_path / "out.csv"
    write_csv(records, str(out))

    with open(out, newline="") as fp:
        rows = list(csv.reader(fp))

    assert rows[0] == ["Time", "SpO2(%)", "Pulse Rate(bpm)", "Acceleration", "Reserved", ""]
    assert rows[1][1:4] == ["97", "72", "3"]
    assert rows[2][1:3] == ["255", "65535"]  # widened no-finger sentinel
