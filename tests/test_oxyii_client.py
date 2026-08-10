from __future__ import annotations

import asyncio
import struct

import pytest

from viatom_o2ring_ble.oxyii_client import InsufficientMtuError, OxyIIClient, supported_oxyii
from viatom_o2ring_ble.oxyii_const import MANUFACTURER_ID
from viatom_o2ring_ble.oxyii_protocol import crc8

_HEADER_FORMAT = "<BBBBBH"


@pytest.mark.parametrize(
    ("name", "manufacturer_data"),
    [
        ("S8-AW-1234", None),
        (None, {MANUFACTURER_ID: b"\x01\x02"}),
        ("T8520_e85a", None),  # recording mode
    ],
)
def test_supported_oxyii_matches_known_signals(name, manufacturer_data):
    assert supported_oxyii(name, manufacturer_data) is True


@pytest.mark.parametrize(
    ("name", "manufacturer_data"),
    [
        (None, None),
        ("", None),
        ("O2Ring", None),  # legacy family, not OxyII
        ("SomeOtherDevice", {0x1234: b"\x00"}),
    ],
)
def test_supported_oxyii_rejects_unrelated_signals(name, manufacturer_data):
    assert supported_oxyii(name, manufacturer_data) is False


def _build_reply(opcode: int, payload: bytes, seq: int = 0) -> bytes:
    header = struct.pack(_HEADER_FORMAT, 0xA5, opcode, (~opcode) & 0xFF, 0x01, seq, len(payload))
    body = header + payload
    return body + bytes([crc8(body)])


class _FakeBleakClient:
    """Enough of BleakClient's surface for OxyIIClient._request() to work
    against, driven directly by feeding synthetic replies into
    _notify_handler() rather than any real BLE transport."""

    def __init__(self, mtu_size: int = 517) -> None:
        self.is_connected = True
        self.written: list[bytes] = []
        self.mtu_size = mtu_size

    async def write_gatt_char(self, _uuid, data, response: bool = False) -> None:
        self.written.append(bytes(data))


async def _drive_download(fake: _FakeBleakClient, client: OxyIIClient) -> None:
    """Answer READ_FILE_START / READ_FILE_DATA(x2) / READ_FILE_END in order."""
    while len(fake.written) < 1:
        await asyncio.sleep(0)
    client._notify_handler(None, _build_reply(0xF2, (10).to_bytes(4, "little")))

    while len(fake.written) < 2:
        await asyncio.sleep(0)
    client._notify_handler(None, _build_reply(0xF3, b"\x01" * 5))

    while len(fake.written) < 3:
        await asyncio.sleep(0)
    client._notify_handler(None, _build_reply(0xF3, b"\x02" * 5))

    while len(fake.written) < 4:
        await asyncio.sleep(0)
    client._notify_handler(None, _build_reply(0xF4, b""))


def test_download_file_succeeds():
    async def scenario() -> None:
        client = OxyIIClient("AA:BB:CC:DD:EE:FF")
        fake = _FakeBleakClient()
        client._client = fake

        driver = asyncio.create_task(_drive_download(fake, client))
        data = await client.download_file("20260427105949")
        await driver

        assert data == b"\x01" * 5 + b"\x02" * 5

    asyncio.run(scenario())


def test_download_file_rejects_insufficient_mtu():
    async def scenario() -> None:
        client = OxyIIClient("AA:BB:CC:DD:EE:FF")
        client._client = _FakeBleakClient(mtu_size=23)

        with pytest.raises(InsufficientMtuError):
            await client.download_file("20260427105949")

    asyncio.run(scenario())


def test_get_file_list_clears_wedge_before_listing():
    async def scenario() -> None:
        client = OxyIIClient("AA:BB:CC:DD:EE:FF")
        fake = _FakeBleakClient()
        client._client = fake

        async def driver() -> None:
            while len(fake.written) < 1:
                await asyncio.sleep(0)
            client._notify_handler(None, _build_reply(0xF4, b""))  # read_file_end ack

            while len(fake.written) < 2:
                await asyncio.sleep(0)
            payload = bytes([1]) + b"20260427105949\x00\x00"
            client._notify_handler(None, _build_reply(0xF1, payload))

        drive_task = asyncio.create_task(driver())
        entries = await client.get_file_list()
        await drive_task

        assert len(fake.written) == 2
        assert fake.written[0][1] == 0xF4  # read_file_end sent first
        assert fake.written[1][1] == 0xF1  # then get_file_list
        assert [e.name for e in entries] == ["20260427105949"]

    asyncio.run(scenario())


def test_request_raises_when_not_connected():
    async def scenario() -> None:
        client = OxyIIClient("AA:BB:CC:DD:EE:FF")
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.get_info()

    asyncio.run(scenario())
