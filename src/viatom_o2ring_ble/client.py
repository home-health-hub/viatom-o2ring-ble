"""BLE client for Viatom/Wellue ring pulse oximeters.

Unlike a passive device (e.g. a blood-pressure cuff that pushes one
notification burst per measurement), this ring answers request/response
pairs: every operation -- a live reading, a file download block, a config
write -- is a `commands` packet sent on the write characteristic, answered
by one or more notifications on the notify characteristic that
`protocol.ResponseAssembler` reassembles. `O2RingClient` wraps that
exchange in `_request()` and builds live streaming, file download, and
config writes on top of it.

Connection lifecycle (scanning, cooldown-gated reconnect) follows the
pattern used by etekcity-bp-ble's monitor.py.
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

from . import commands
from .const import LOCAL_NAME_EXACT_MATCHES, NOTIFY_CHARACTERISTIC_UUID, WRITE_CHARACTERISTIC_UUID
from .data import DeviceInfo, Reading, RtReading
from .file import parse as parse_vld
from .protocol import (
    ResponseAssembler,
    chunk_for_ble,
    parse_device_info,
    parse_reading,
    parse_rt_data,
)

DEFAULT_COOLDOWN_SECONDS = 5
DEFAULT_REQUEST_TIMEOUT = 10.0
DEFAULT_READ_PERIOD = 2.0


def supported(local_name: str | None) -> bool:
    """Return whether an advertised name looks like a device in this family.

    Mirrors Viatom's own app logic (LepuBle's Bluetooth.getDeviceModel):
    take the first space-separated token of the advertised name, and
    match it against the known exact-name exceptions or, failing that,
    against a plain "contains O2" check -- which is how that app itself
    recognizes O2Ring, KidsO2, RingO2 (advertised as "O2NCI"), and the O2
    Max (advertised as "O2M").
    """
    if not local_name:
        return False
    prefix = local_name.split(" ", 1)[0]
    if prefix in LOCAL_NAME_EXACT_MATCHES:
        return True
    return "O2" in prefix


async def discover(timeout: float = 10.0) -> list[BLEDevice]:
    """Scan for nearby devices matching this family's advertised names."""
    found: dict[str, BLEDevice] = {}

    def _callback(device: BLEDevice, _adv: AdvertisementData) -> None:
        if supported(device.name):
            found[device.address] = device

    async with BleakScanner(detection_callback=_callback):
        await asyncio.sleep(timeout)

    return list(found.values())


class O2RingClient:
    """Client for one Viatom/Wellue ring: live readings, file download, config.

    Two usage styles are supported:

    - Manual: call `async_connect()`, then any of `read_rt_data()`,
      `get_info()`, `download_file()`, or the `set_*` config methods, then
      `async_disconnect()`. Suited to one-off CLI operations.
    - Streaming: call `async_start()` with `on_reading` set, which scans
      for the device's advertisement, connects automatically, and polls
      for readings on a timer until `async_stop()`.

    By default, both single reads and streaming use CMD_RT_DATA (0x1B) --
    the command the current official app actually polls -- and deliver
    `RtReading` to `on_reading`. Pass `legacy_sensors=True` to use the
    older CMD_READ_SENSORS (0x17) instead, delivering `Reading`; that
    command is confirmed working against O2Ring-era firmware but is not
    what current firmware/devices are known to use.
    """

    def __init__(
        self,
        address: str,
        *,
        on_reading: Callable[[Reading], None] | Callable[[RtReading], None] | None = None,
        legacy_sensors: bool = False,
        adapter: str | None = None,
        logger: logging.Logger | None = None,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        read_period: float = DEFAULT_READ_PERIOD,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Initialize the client.

        Args:
            address: Bluetooth address of the device.
            on_reading: Called with each reading while streaming
                (async_start/async_stop). Not used for manual requests.
                Receives an RtReading, or a Reading if legacy_sensors=True.
            legacy_sensors: Use CMD_READ_SENSORS (0x17) instead of the
                default CMD_RT_DATA (0x1B) for read_sensors()/streaming.
            adapter: Bluetooth adapter to use (Linux only).
            logger: Optional logger instance; defaults to this module's logger.
            cooldown_seconds: How long to ignore advertisements after a
                disconnect, so a device still finishing its own disconnect
                handshake doesn't trigger a reconnect storm.
            read_period: Seconds between polls while streaming.
            request_timeout: Seconds to wait for a response before a
                request is considered failed.
        """
        self.address = address
        self._on_reading = on_reading
        self._legacy_sensors = legacy_sensors
        self._logger = logger or logging.getLogger(__name__)
        self._cooldown_seconds = cooldown_seconds
        self._read_period = read_period
        self._request_timeout = request_timeout

        self._client: BleakClient | None = None
        self._assembler: ResponseAssembler | None = None
        self._pending: asyncio.Future[bytes] | None = None
        self._request_lock = asyncio.Lock()
        self._last_response_block = 0

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
            BleakClient, device, self.address, self._disconnected_callback
        )
        await client.start_notify(NOTIFY_CHARACTERISTIC_UUID, self._notify_handler)
        self._client = client
        self._logger.debug("Connected to %s", self.address)

    def _disconnected_callback(self, _client: BleakClient) -> None:
        self._logger.debug("Disconnected from %s", self.address)
        self._client = None
        self._cooldown_end_time = time.time() + self._cooldown_seconds
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(RuntimeError("Disconnected while awaiting response"))

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

    async def _request(self, packet: bytes) -> bytes:
        """Send one request and wait for its complete, status-checked response.

        The response's block number (echoed by the device -- see
        ResponseAssembler) is left in `self._last_response_block` for
        callers that need to verify it, e.g. download_file()'s per-block
        sequence check.
        """
        if self._client is None or not self._client.is_connected:
            raise RuntimeError("Not connected")

        async with self._request_lock:  # protocol is not pipelined
            assembler = ResponseAssembler()
            self._assembler = assembler
            loop = asyncio.get_running_loop()
            pending: asyncio.Future[bytes] = loop.create_future()
            self._pending = pending

            try:
                for chunk in chunk_for_ble(packet):
                    await self._client.write_gatt_char(
                        WRITE_CHARACTERISTIC_UUID, chunk, response=False
                    )
                    await asyncio.sleep(0.02)

                payload = await asyncio.wait_for(pending, timeout=self._request_timeout)
            finally:
                self._pending = None
                self._assembler = None

            if assembler.status != 0:
                raise RuntimeError(f"Device reported command failure (status {assembler.status})")
            self._last_response_block = assembler.block
            return payload

    # -- Single operations ----------------------------------------------

    async def read_sensors(self) -> Reading:
        """Request and decode one live reading via the legacy CMD_READ_SENSORS
        (0x17). See the class docstring for why read_rt_data() is preferred.
        """
        payload = await self._request(commands.read_sensors())
        reading = parse_reading(payload)
        if reading is None:
            raise RuntimeError("READ_SENSORS response too short to decode")
        return reading

    async def read_rt_data(self, waveform_rate: int = 0) -> RtReading:
        """Request and decode one live reading via CMD_RT_DATA (0x1B), the
        command the current official app actually polls.
        """
        payload = await self._request(commands.read_rt_data(waveform_rate))
        reading = parse_rt_data(payload)
        if reading is None:
            raise RuntimeError("RT_DATA response too short to decode")
        return reading

    async def get_info(self) -> DeviceInfo:
        """Request device model/serial/battery/current file list."""
        payload = await self._request(commands.info())
        return parse_device_info(payload)

    async def download_file(
        self,
        filename: str,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bytes:
        """Download one stored file by name (from `get_info().file_names`).

        Returns the raw file bytes; pass them to `file.parse` to decode.
        """
        open_payload = await self._request(commands.file_open(filename))
        if len(open_payload) < 4:
            raise RuntimeError(f"FILE_OPEN response too short for {filename!r}")
        file_size = int.from_bytes(open_payload[:4], "little")

        data = bytearray()
        block = 0
        try:
            while len(data) < file_size:
                chunk = await self._request(commands.file_read(block))
                # LepuBle's own client validates this: its hasResponse()
                # comment notes sporadic cases where in-flight file content
                # happens to collide with the packet framing, and guards
                # against it by checking the response's echoed block number
                # against the block actually requested.
                if self._last_response_block != block:
                    raise RuntimeError(
                        f"File block mismatch downloading {filename!r}: "
                        f"requested {block}, device echoed {self._last_response_block}"
                    )
                data += chunk
                block += 1
                if on_progress:
                    on_progress(len(data), file_size)
        finally:
            await self._request(commands.file_close())

        return bytes(data[:file_size])

    async def download_and_parse_file(self, filename: str):
        """Download and decode one stored file in one call. See `file.parse`."""
        return parse_vld(await self.download_file(filename))

    # -- Config writes ----------------------------------------------------
    # See commands.py for the safety note on alert-threshold writes.

    async def set_time(self) -> None:
        await self._request(commands.set_time())

    async def set_o2_alert(self, threshold_percent: int | None) -> None:
        await self._request(commands.set_o2_alert(threshold_percent))

    async def set_hr_alert(
        self, *, high_bpm: int | None = None, low_bpm: int | None = None
    ) -> None:
        await self._request(commands.set_hr_alert(high_bpm=high_bpm, low_bpm=low_bpm))

    async def set_vibration_strength(self, strength: int) -> None:
        await self._request(commands.set_vibration_strength(strength))

    async def set_lighting_mode(self, mode: int) -> None:
        """0 (standard, auto-timeout), 1 (always off), or 2 (always on)."""
        await self._request(commands.set_lighting_mode(mode))

    async def set_screen_always_on(self, enabled: bool) -> None:
        """Convenience for the common on/standard case; can't select mode 1
        (always off) -- use set_lighting_mode directly for that."""
        await self._request(commands.set_screen_always_on(enabled))

    async def set_brightness(self, level: int) -> None:
        await self._request(commands.set_brightness(level))

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
                    if self._legacy_sensors:
                        reading = await self.read_sensors()
                    else:
                        reading = await self.read_rt_data()
                except Exception:
                    self._logger.exception("Read failed for %s", self.address)
                    break
                if self._on_reading is not None:
                    self._on_reading(reading)
                await asyncio.sleep(self._read_period)
        finally:
            self._stream_task = None
            await self.async_disconnect()
