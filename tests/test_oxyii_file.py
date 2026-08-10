from __future__ import annotations

import csv
import datetime

import pytest

from viatom_o2ring_ble.oxyii_data import OxyIIFileRecord
from viatom_o2ring_ble.oxyii_file import parse, parse_filename_timestamp, write_csv

_HEADER = bytes.fromhex("01030000000000000400")


def _build_trailer(
    *,
    spo2_avg: int = 95,
    spo2_min: int = 88,
    drop3: int = 2,
    drop4: int = 1,
    seconds_below_90: int = 120,
    events_below_90: int = 3,
    o2_score_raw: int = 85,
    hr_avg: int = 68,
) -> bytes:
    trailer = bytearray(48)
    trailer[4:8] = bytes.fromhex("48125ada")
    trailer[34] = spo2_avg
    trailer[35] = spo2_min
    trailer[36] = drop3
    trailer[37] = drop4
    trailer[39:41] = seconds_below_90.to_bytes(2, "little")
    trailer[41] = events_below_90
    trailer[42] = o2_score_raw
    trailer[47] = hr_avg
    return bytes(trailer)


def _build_record(spo2: int, heart_rate: int, status_flags: int = 0) -> bytes:
    return bytes([spo2, heart_rate, status_flags])


def test_parse_unfinalized_recording_has_no_trailer():
    records = _build_record(97, 72) + _build_record(96, 73)
    data = _HEADER + records
    header, recs = parse(data)

    assert header.finalized is False
    assert header.trailer_confirmed is False
    assert header.record_count == 2
    assert header.spo2_avg is None

    assert len(recs) == 2
    assert recs[0].spo2 == 97
    assert recs[0].heart_rate == 72
    assert recs[0].index == 0
    assert recs[1].index == 1


def test_parse_finalized_recording_decodes_trailer():
    records = _build_record(97, 72) + _build_record(96, 73) + _build_record(88, 80)
    trailer = _build_trailer()
    data = _HEADER + records + trailer

    header, recs = parse(data)

    assert header.finalized is True
    assert header.trailer_confirmed is True
    assert header.record_count == 3
    assert header.spo2_avg == 95
    assert header.spo2_min == 88
    assert header.spo2_below_3pct_events == 2
    assert header.spo2_below_4pct_events == 1
    assert header.seconds_below_90pct == 120
    assert header.events_below_90pct == 3
    assert header.o2_score == pytest.approx(8.5)
    assert header.heart_rate_avg == 68
    assert len(recs) == 3


def test_parse_o2_score_na_sentinel_is_none():
    records = _build_record(97, 72)
    trailer = _build_trailer(o2_score_raw=0xFF)
    header, _ = parse(_HEADER + records + trailer)
    assert header.o2_score is None


def test_parse_trailer_sized_tail_without_anchor_is_not_finalized():
    # A file can reach the trailer's full byte count before the trailer
    # has actually flushed -- the sub-magic anchor is what makes this
    # distinguishable from a genuinely finalized file, not size alone.
    records = _build_record(97, 72) * 16  # 48 bytes, same size as a trailer
    header, parsed_records = parse(_HEADER + records)
    assert header.finalized is False
    assert header.record_count == 16
    assert len(parsed_records) == 16


def test_parse_rejects_missing_header():
    with pytest.raises(ValueError, match="header"):
        parse(b"\x01\x02\x03")


def test_parse_rejects_unrecognized_header():
    bad_header = b"\xff" * len(_HEADER)
    with pytest.raises(ValueError, match="header"):
        parse(bad_header + _build_record(97, 72))


def test_parse_rejects_truncated_trailing_record():
    data = _HEADER + _build_record(97, 72) + b"\x01\x02"  # 2 stray bytes
    with pytest.raises(ValueError, match="whole number of records"):
        parse(data)


def test_parse_filename_timestamp():
    assert parse_filename_timestamp("20260427105949") == datetime.datetime(
        2026, 4, 27, 10, 59, 49
    )


def test_parse_filename_timestamp_rejects_bad_format():
    with pytest.raises(ValueError):
        parse_filename_timestamp("not-a-timestamp")


def test_write_csv_with_start_time(tmp_path):
    records = [
        OxyIIFileRecord(index=0, spo2=97, heart_rate=72, status_flags=0),
        OxyIIFileRecord(index=1, spo2=96, heart_rate=73, status_flags=0),
    ]
    start = datetime.datetime(2026, 4, 27, 10, 59, 49)

    out = tmp_path / "out.csv"
    write_csv(records, str(out), start_time=start)
    with open(out, newline="") as fp:
        rows = list(csv.reader(fp))

    assert rows[0] == ["Time", "SpO2(%)", "Pulse Rate(bpm)", "Status Flags"]
    assert rows[1] == [start.isoformat(), "97", "72", "0"]
    assert rows[2] == [(start + datetime.timedelta(seconds=1)).isoformat(), "96", "73", "0"]


def test_write_csv_without_start_time_uses_index(tmp_path):
    records = [OxyIIFileRecord(index=5, spo2=97, heart_rate=72, status_flags=0)]

    out = tmp_path / "out.csv"
    write_csv(records, str(out))
    with open(out, newline="") as fp:
        rows = list(csv.reader(fp))

    assert rows[1][0] == "5"


def test_write_csv_blanks_no_finger_heart_rate_sentinel(tmp_path):
    records = [OxyIIFileRecord(index=0, spo2=0, heart_rate=0xFF, status_flags=1)]

    out = tmp_path / "out.csv"
    write_csv(records, str(out))
    with open(out, newline="") as fp:
        rows = list(csv.reader(fp))

    assert rows[1][2] == ""  # heart rate blanked, not "255"
