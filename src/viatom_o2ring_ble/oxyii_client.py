"""BLE client for the O2Ring-S (T8520), speaking the OxyII protocol.

Distinct from client.py's O2RingClient on purpose -- see oxyii_const.py's
module docstring for why the two protocols can't share code. The overall
shape (scan/connect lifecycle, request/response over one serialized
queue, streaming vs. manual usage) deliberately mirrors O2RingClient so
callers already familiar with this package feel at home, but nothing
below the connection-lifecycle level is shared, since the wire protocols
have nothing in common.

Connect handshake order follows the working sequence verified end-to-end
in nglessner/o2ring-s-protocol (see this package's CLAUDE.md): MTU
negotiation, then auth (cmd=0xFF), setup (cmd=0x10), clock sync
(cmd=0xC0), and a GET_CONFIG (cmd=0x00) that must be consumed before any
file-transfer command -- skipping or reordering any of these causes
READ_FILE_START to be silently rejected, or (for the GET_CONFIG step)
risks its 40-byte reply being mistaken for a file-transfer chunk.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import establish_connection

from . import oxyii_commands as commands
from .oxyii_const import (
    DEFAULT_AUTH_SERIAL,
    LOCAL_NAME_PREFIX,
    MANUFACTURER_ID,
    MINIMUM_MTU_FOR_FILE_TRANSFER,
    NOTIFY_CHARACTERISTIC_UUID,
    RECORDING_MODE_NAME_PREFIX,
    REQUESTED_MTU,
    WRITE_CHARACTERISTIC_UUID,
)
from .oxyii_data import OxyIIDeviceInfo, OxyIIFileEntry, OxyIIReading
from .oxyii_file import parse as parse_oxyii_file
from .oxyii_protocol import (
    OxyIIFrameAssembler,
    parse_battery_percent,
    parse_device_info,
    parse_file_list,
    parse_live_reading,
)

DEFAULT_COOLDOWN_SECONDS = 5
DEFAULT_REQUEST_TIMEOUT = 10.0
DEFAULT_READ_PERIOD = 2.0

_LOGGER = logging.getLogger(__name__)


def _oxyii_advertisement_mode(
    local_name: str | None, manufacturer_data: dict[int, bytes] | None = None
) -> str | None:
    """Classify an advertisement as "sync" mode, "recording" mode, or not this family.

    The distinction matters beyond just recognizing the device: recording
    mode and sync mode use *different BLE addresses* (a "public-style"
    address in recording mode vs. a Random Static address in sync mode --
    see RECORDING_MODE_NAME_PREFIX's docstring). An address only ever seen
    in recording mode isn't just "maybe not connectable yet" -- it is
    reliably a different address than the one that would actually work,
    since the GATT/auth handshake needs sync mode's address specifically.
    discover_oxyii() uses this to avoid ever handing back a recording-mode
    address when a sync-mode one was also seen.

    Returns:
        "sync" if manufacturer_data or local_name confirms OxyII/sync
        mode, "recording" if local_name only matches the recording-mode
        prefix, or None if neither matches this device family at all.
    """
    if manufacturer_data and MANUFACTURER_ID in manufacturer_data:
        return "sync"
    if not local_name:
        return None
    if local_name.startswith(LOCAL_NAME_PREFIX):
        return "sync"
    if local_name.startswith(RECORDING_MODE_NAME_PREFIX):
        return "recording"
    return None


def supported_oxyii(
    local_name: str | None, manufacturer_data: dict[int, bytes] | None = None
) -> bool:
    """Return whether an advertisement looks like a T8520 in either mode.

    Manufacturer ID is the most reliable signal, matching Viatom's own
    "OxyII / sync mode" advertisements (local name prefix "S8-AW", the
    mode that actually exposes the OxyII GATT service). "Recording mode"
    (name prefix "T8520_<last4>") is also recognized so a caller can tell
    the device is nearby, but see discover_oxyii() -- its address in that
    mode is not usable for a real connection; _oxyii_advertisement_mode()
    is what actually distinguishes the two for that purpose.
    """
    return _oxyii_advertisement_mode(local_name, manufacturer_data) is not None


def _select_oxyii_devices(
    sync_found: dict[str, BLEDevice], recording_found: dict[str, BLEDevice]
) -> list[BLEDevice]:
    """Pick discover_oxyii()'s return value from mode-tagged sightings.

    Split out as a pure/synchronous function (no BLE scanning) so this
    preference logic is unit-testable without a real adapter.

    If any sync-mode device was seen, only sync-mode devices are
    returned, even if recording-mode-only devices were also seen; a
    recording-mode address is reliably the *wrong* address to act on (see
    _oxyii_advertisement_mode's docstring), not just an unconfirmed one.
    Only falls back to recording-mode addresses if no sync-mode device
    was seen at all -- with a warning, since a caller (e.g. a daemon
    persisting "the" discovered address) that acts on one of these will
    very likely fail to connect.
    """
    if sync_found:
        return list(sync_found.values())

    if recording_found:
        _LOGGER.warning(
            "Found %d O2Ring-S device(s) only in recording mode (name prefix %r) -- "
            "their address is NOT usable for a real connection (sync mode uses a "
            "different address). Wake the device into sync mode (stop wearing it, "
            "or press its button) and scan again.",
            len(recording_found),
            RECORDING_MODE_NAME_PREFIX,
        )
    return list(recording_found.values())


async def discover_oxyii(timeout: float = 10.0) -> list[BLEDevice]:
    """Scan for nearby O2Ring-S (T8520) devices.

    Prefers devices confirmed in OxyII/sync mode -- the only mode whose
    address is actually usable for a full connection (GATT discovery,
    auth handshake, file transfer) -- over recording-mode-only sightings.
    See _select_oxyii_devices for the exact preference/fallback logic.
    """
    sync_found: dict[str, BLEDevice] = {}
    recording_found: dict[str, BLEDevice] = {}

    def _callback(device: BLEDevice, adv: AdvertisementData) -> None:
        mode = _oxyii_advertisement_mode(device.name, adv.manufacturer_data)
        if mode == "sync":
            sync_found[device.address] = device
        elif mode == "recording":
            recording_found[device.address] = device

    async with BleakScanner(detection_callback=_callback):
        await asyncio.sleep(timeout)

    return _select_oxyii_devices(sync_found, recording_found)


class InsufficientMtuError(RuntimeError):
    """Raised when a file-transfer command is attempted on a connection
    whose negotiated ATT MTU is too small for it to work.

    See the "MTU gotcha" this package's CLAUDE.md describes: below
    ~247 bytes, the device silently drops READ_FILE_START instead of
    replying with an error, which otherwise presents as a confusing
    timeout with no other symptom.
    """


class OxyIIClient:
    """Client for one O2Ring-S (T8520): live readings, file download.

    Two usage styles are supported, matching O2RingClient:

    - Manual: call `async_connect()`, then any of `read_live()`,
      `get_info()`, `download_file()`, then `async_disconnect()`.
    - Streaming: call `async_start()` with `on_reading` set, which scans
      for the device's advertisement, connects automatically, and polls
      for readings on a timer until `async_stop()`.

    Live streaming and file transfer share a connection freely as long as
    BLE I/O is serialized (this client only ever has one request
    in flight at a time via `_request_lock`) -- see this package's
    CLAUDE.md for the traffic captures that confirm this.
    """

    def __init__(
        self,
        address: str,
        *,
        on_reading: Callable[[OxyIIReading], None] | None = None,
        adapter: str | None = None,
        logger: logging.Logger | None = None,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        read_period: float = DEFAULT_READ_PERIOD,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        auth_serial: str = DEFAULT_AUTH_SERIAL,
    ) -> None:
        """Initialize the client.

        Args:
            address: Bluetooth address of the device.
            on_reading: Called with each reading while streaming
                (async_start/async_stop). Not used for manual requests.
            adapter: Bluetooth adapter to use (Linux only).
            logger: Optional logger instance; defaults to this module's logger.
            cooldown_seconds: How long to ignore advertisements after a
                disconnect, so a device still finishing its own disconnect
                handshake doesn't trigger a reconnect storm.
            read_period: Seconds between polls while streaming.
            request_timeout: Seconds to wait for a response before a
                request is considered failed.
            auth_serial: Serial number to derive the auth session key
                from. The portable default ("0000") is what the upstream
                protocol repo recommends, since the device's real serial
                isn't known until GET_INFO, which can't be called before
                auth. Override only if the default is confirmed not to
                work against a specific device/firmware.
        """
        self.address = address
        self._on_reading = on_reading
        self._logger = logger or logging.getLogger(__name__)
        self._cooldown_seconds = cooldown_seconds
        self._read_period = read_period
        self._request_timeout = request_timeout
        self._auth_serial = auth_serial

        self._client: BleakClient | None = None
        self._assembler: OxyIIFrameAssembler | None = None
        self._pending: asyncio.Future[bytes] | None = None
        self._request_lock = asyncio.Lock()
        self._seq = 0

        self._scanner: BleakScanner | None = None
        self._connect_lock = asyncio.Lock()
        self._cooldown_end_time: float = 0
        self._stream_task: asyncio.Task | None = None
        self._stopping = False

    # -- Manual connection -------------------------------------------------

    async def async_connect(self, timeout: float | None = None) -> None:
        """Connect directly, without waiting for a scanner advertisement."""
        if self._client is not None and self._client.is_connected:
            return

        device = await BleakScanner.find_device_by_address(
            self.address, timeout=timeout or self._request_timeout
        )
        if device is None:
            raise RuntimeError(f"Device {self.address} not found; is it advertising?")
        await self._connect(device)

    async def async_disconnect(self) -> None:
        """Disconnect if connected."""
        if self._client is not None and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    async def _connect(self, device: BLEDevice) -> None:
        self._logger.debug("Connecting to %s", self.address)
        client = await establish_connection(
            BleakClient, device, self.address, self._disconnected_callback,
        )
        await client.start_notify(NOTIFY_CHARACTERISTIC_UUID, self._notify_handler)
        self._client = client
        self._seq = 0

        mtu = getattr(client, "mtu_size", None)
        self._logger.debug(
            "Connected to %s (negotiated MTU=%s, requested %s)", self.address, mtu, REQUESTED_MTU
        )
        if mtu is not None and mtu < MINIMUM_MTU_FOR_FILE_TRANSFER:
            self._logger.warning(
                "Negotiated ATT MTU (%s) is below %s -- file-transfer commands "
                "will likely be silently rejected by the device even though live "
                "readings still work; see this package's CLAUDE.md",
                mtu, MINIMUM_MTU_FOR_FILE_TRANSFER,
            )

        await self._request(
            commands.auth(self._auth_serial, seq=self._next_seq()), expect_reply=False
        )
        await self._request(commands.setup(seq=self._next_seq()))
        await self._request(commands.set_utc_time(seq=self._next_seq()))
        # Must be consumed here, strictly before any READ_FILE_* command --
        # see the module docstring and CLAUDE.md.
        await self._request(commands.get_config(seq=self._next_seq()))
        # Clears any file handle the ring itself left open finishing an
        # autonomous recording -- see "the F1 wedge" in CLAUDE.md. A no-op
        # if nothing is open, so unconditional is safe.
        await self._request(commands.read_file_end(seq=self._next_seq()))

        self._logger.debug("Handshake complete for %s", self.address)

    def _disconnected_callback(self, _client: BleakClient) -> None:
        self._logger.debug("Disconnected from %s", self.address)
        self._client = None
        self._cooldown_end_time = time.time() + self._cooldown_seconds
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(RuntimeError("Disconnected while awaiting response"))

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return seq

    # -- Request/response core ----------------------------------------------

    def _notify_handler(self, _characteristic, payload: bytearray) -> None:
        assembler = self._assembler
        if assembler is None:
            return
        try:
            result = assembler.feed(bytes(payload))
        except ValueError as exc:
            if self._pending is not None and not self._pending.done():
                self._pending.set_exception(exc)
            return
        if result is not None and self._pending is not None and not self._pending.done():
            self._pending.set_result(result)

    async def _request(self, frame: bytes, expect_reply: bool = True) -> bytes:
        """Send one request frame and wait for its reply payload.

        Args:
            frame: A complete frame, as built by oxyii_commands.
            expect_reply: False for cmd=0xFF (auth), the one request in
                this protocol that never gets a reply. Returns b"" without
                waiting when False.
        """
        if self._client is None or not self._client.is_connected:
            raise RuntimeError("Not connected")

        async with self._request_lock:  # protocol is not pipelined
            if not expect_reply:
                await self._client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, frame, response=False)
                return b""

            assembler = OxyIIFrameAssembler()
            self._assembler = assembler
            loop = asyncio.get_running_loop()
            pending: asyncio.Future[bytes] = loop.create_future()
            self._pending = pending

            try:
                await self._client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, frame, response=False)
                return await asyncio.wait_for(pending, timeout=self._request_timeout)
            finally:
                self._pending = None
                self._assembler = None

    def _ensure_mtu_for_file_transfer(self) -> None:
        mtu = getattr(self._client, "mtu_size", None) if self._client else None
        if mtu is not None and mtu < MINIMUM_MTU_FOR_FILE_TRANSFER:
            raise InsufficientMtuError(
                f"Negotiated ATT MTU ({mtu}) is below the minimum "
                f"({MINIMUM_MTU_FOR_FILE_TRANSFER}) file transfer needs; the device "
                "will silently drop READ_FILE_START. See this package's CLAUDE.md."
            )

    # -- Single operations ----------------------------------------------

    async def get_info(self) -> OxyIIDeviceInfo:
        """Request device serial number and firmware version."""
        payload = await self._request(commands.get_info(seq=self._next_seq()))
        return parse_device_info(payload)

    async def get_battery_percent(self) -> int | None:
        """Request battery level, percent. See parse_battery_percent's docstring."""
        payload = await self._request(commands.get_battery(seq=self._next_seq()))
        return parse_battery_percent(payload)

    async def get_config_raw(self) -> bytes:
        """Request the ring's current settings struct, undecoded (40 bytes).

        See oxyii_commands.get_config's docstring for why this package
        doesn't parse it into individual fields.
        """
        return await self._request(commands.get_config(seq=self._next_seq()))

    async def read_live(self) -> OxyIIReading:
        """Request and decode one live SpO2/heart-rate/battery reading."""
        payload = await self._request(commands.live_samples(seq=self._next_seq()))
        reading = parse_live_reading(payload)
        if reading is None:
            raise RuntimeError("LIVE_SAMPLES_B response too short to decode")
        return reading

    async def get_file_list(self) -> list[OxyIIFileEntry]:
        """List stored recordings. Clears any wedged file handle first
        (see "the F1 wedge" in CLAUDE.md) -- safe even if nothing is open.
        """
        await self._request(commands.read_file_end(seq=self._next_seq()))
        payload = await self._request(commands.get_file_list(seq=self._next_seq()))
        return parse_file_list(payload)

    async def download_file(
        self,
        filename: str,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bytes:
        """Download one stored recording by name (from `get_file_list()`).

        Returns the raw file bytes; pass them to `oxyii_file.parse` to
        decode.

        Raises:
            InsufficientMtuError: If the connection's negotiated MTU is
                too small for file transfer to work at all.
        """
        self._ensure_mtu_for_file_transfer()

        open_payload = await self._request(
            commands.read_file_start(filename, seq=self._next_seq())
        )
        if len(open_payload) < 4:
            raise RuntimeError(f"READ_FILE_START response too short for {filename!r}")
        file_size = int.from_bytes(open_payload[:4], "little")

        data = bytearray()
        try:
            while len(data) < file_size:
                chunk = await self._request(
                    commands.read_file_data(len(data), seq=self._next_seq())
                )
                if not chunk:
                    break
                data += chunk
                if on_progress:
                    on_progress(len(data), file_size)
        finally:
            await self._request(commands.read_file_end(seq=self._next_seq()))

        return bytes(data[:file_size])

    async def download_and_parse_file(self, filename: str):
        """Download and decode one stored recording in one call. See `oxyii_file.parse`."""
        return parse_oxyii_file(await self.download_file(filename))

    async def set_time(self) -> None:
        await self._request(commands.set_utc_time(seq=self._next_seq()))

    async def factory_reset(self) -> None:
        """Wipe device settings AND every stored recording. See
        oxyii_commands.factory_reset's docstring -- use deliberately."""
        await self._request(commands.factory_reset(seq=self._next_seq()))

    # -- Streaming mode -----------------------------------------------------

    async def async_start(self) -> None:
        """Start scanning for the device and streaming readings to `on_reading`."""
        if self._on_reading is None:
            raise RuntimeError("async_start() requires on_reading to be set")

        self._stopping = False
        self._scanner = BleakScanner(detection_callback=self._advertisement_callback)
        await self._scanner.start()

    async def async_stop(self) -> None:
        """Stop scanning/streaming and disconnect."""
        self._stopping = True
        if self._scanner is not None:
            await self._scanner.stop()
            self._scanner = None
        if self._stream_task is not None:
            self._stream_task.cancel()
            self._stream_task = None
        await self.async_disconnect()

    async def _advertisement_callback(
        self, device: BLEDevice, _adv: AdvertisementData
    ) -> None:
        if device.address != self.address:
            return
        if time.time() < self._cooldown_end_time:
            return
        async with self._connect_lock:
            if self._client is not None or self._stream_task is not None:
                return
            try:
                await self._connect(device)
            except Exception:
                self._logger.exception("Could not connect to %s", self.address)
                return
            self._stream_task = asyncio.ensure_future(self._stream_loop())

    async def _stream_loop(self) -> None:
        try:
            while not self._stopping and self._client is not None and self._client.is_connected:
                try:
                    reading = await self.read_live()
                except Exception:
                    self._logger.exception("Read failed for %s", self.address)
                    break
                if self._on_reading is not None:
                    self._on_reading(reading)
                await asyncio.sleep(self._read_period)
        finally:
            self._stream_task = None
            await self.async_disconnect()
