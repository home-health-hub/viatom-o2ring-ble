from __future__ import annotations

import asyncio
import struct

import pytest

from viatom_o2ring_ble.client import O2RingClient, supported
from viatom_o2ring_ble.protocol import crc8


@pytest.mark.parametrize(
    "name",
    [
        "O2Ring",
        "O2Ring 12345",  # trailing serial
        "KidsO2",
        "O2NCI",  # RingO2's advertised name
        "O2M",  # O2 Max's advertised name
        "CMRing",  # Sanei-branded CheckO2 variant, no literal "O2" substring
    ],
)
def test_supported_matches_known_o2_family_names(name):
    assert supported(name) is True


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "Checkme",  # Checkme Pro Monitor -- a different, unrelated product
        "BP2",  # blood-pressure monitor, different product family
        "SomeOtherDevice",
    ],
)
def test_supported_rejects_unrelated_names(name):
    assert supported(name) is False


def _build_response(status: int, data: bytes, block: int = 0) -> bytes:
    header = struct.pack("<BBBHH", 0x55, status, status ^ 0xFF, block, len(data))
    body = header + data
    return body + bytes([crc8(body)])


class _FakeBleakClient:
    """Enough of BleakClient's surface for O2RingClient._request() to work
    against, driven directly by feeding synthetic responses into
    _notify_handler() rather than any real BLE transport."""

    def __init__(self) -> None:
        self.is_connected = True
        self.written: list[bytes] = []

    async def write_gatt_char(self, _uuid, data, response: bool = False) -> None:
        self.written.append(bytes(data))


async def _drive_download(fake: _FakeBleakClient, client: O2RingClient, block1_echo: int) -> None:
    """Answer FILE_OPEN/FILE_READ(0)/FILE_READ(1)/FILE_CLOSE in order,
    echoing `block1_echo` as the second FILE_READ's block number -- the
    device's real block number when testing the happy path, or a wrong
    one to exercise the mismatch guard."""
    while len(fake.written) < 1:
        await asyncio.sleep(0)
    client._notify_handler(None, _build_response(0, (10).to_bytes(4, "little")))  # FILE_OPEN

    while len(fake.written) < 2:
        await asyncio.sleep(0)
    client._notify_handler(None, _build_response(0, b"\x01" * 5, block=0))  # FILE_READ block 0

    while len(fake.written) < 3:
        await asyncio.sleep(0)
    client._notify_handler(None, _build_response(0, b"\x02" * 5, block=block1_echo))

    while len(fake.written) < 4:
        await asyncio.sleep(0)
    client._notify_handler(None, _build_response(0, b""))  # FILE_CLOSE


def test_download_file_succeeds_when_blocks_match():
    async def scenario() -> None:
        client = O2RingClient("AA:BB:CC:DD:EE:FF")
        fake = _FakeBleakClient()
        client._client = fake

        driver = asyncio.create_task(_drive_download(fake, client, block1_echo=1))
        data = await client.download_file("test.vld")
        await driver

        assert data == b"\x01" * 5 + b"\x02" * 5

    asyncio.run(scenario())


def test_download_file_rejects_block_mismatch():
    # This is the edge case LepuBle's own hasResponse() explicitly guards
    # against: a comment there notes sporadic cases where in-flight file
    # content collides with the packet framing. Our guard is a simpler
    # version of the same idea -- check the echoed block number.
    async def scenario() -> None:
        client = O2RingClient("AA:BB:CC:DD:EE:FF")
        fake = _FakeBleakClient()
        client._client = fake

        driver = asyncio.create_task(_drive_download(fake, client, block1_echo=5))
        with pytest.raises(RuntimeError, match="block mismatch"):
            await client.download_file("test.vld")
        await driver

    asyncio.run(scenario())
