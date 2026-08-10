#!/usr/bin/env python3
"""Standalone command-line client for Viatom/Wellue ring pulse oximeters."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys

from ._version import __version__
from .client import O2RingClient, discover
from .data import Reading, RtReading
from .file import parse as parse_vld
from .file import write_csv
from .oxyii_client import OxyIIClient, discover_oxyii
from .oxyii_data import OxyIIReading
from .oxyii_file import parse as parse_oxyii
from .oxyii_file import parse_filename_timestamp as parse_oxyii_filename_timestamp
from .oxyii_file import write_csv as write_oxyii_csv

_LOGGER = logging.getLogger("viatom_o2ring_ble")

_BRIGHTNESS_LEVELS = {"low": 0, "medium": 1, "high": 2}
_LIGHTING_MODES = {"standard": 0, "off": 1, "on": 2}
#: Device-confirmed valid ranges (see commands.py's module docstring);
#: 0 is always accepted separately as "disable this alert".
_O2_ALERT_CHOICES = [0, *range(80, 96)]
_HR_ALERT_CHOICES = [0, *range(30, 251)]

#: --oxyii has no equivalent for these -- OxyIIClient has no SET_CONFIG
#: support (see oxyii_commands.py's module docstring) and no legacy
#: live-reading command to fall back to. Checked up front in main() so a
#: mistaken combination fails fast with a clear message instead of an
#: AttributeError partway through a one-shot run.
_LEGACY_ONLY_FLAGS = (
    "legacy_sensors",
    "set_o2_alert",
    "set_hr_alert_high",
    "set_hr_alert_low",
    "set_vibration",
    "set_screen",
    "set_lighting_mode",
    "set_brightness",
)


def _print_json(obj) -> None:
    data = dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj
    print(json.dumps(data, indent=2, default=str))


async def _run_discover(timeout: float, oxyii: bool = False) -> None:
    print(f"Scanning for {timeout:.0f}s...", file=sys.stderr)
    devices = await (discover_oxyii(timeout=timeout) if oxyii else discover(timeout=timeout))
    if not devices:
        print("No devices found.", file=sys.stderr)
        return
    for device in devices:
        print(f"{device.address}  {device.name or '(unknown name)'}")


async def _run_stream(
    address: str,
    adapter: str | None,
    read_period: float,
    once: bool,
    legacy_sensors: bool,
    oxyii: bool = False,
) -> None:
    done = asyncio.Event()

    def _callback(reading: Reading | RtReading | OxyIIReading) -> None:
        _print_json(reading)
        if once:
            done.set()

    if oxyii:
        client = OxyIIClient(
            address, on_reading=_callback, adapter=adapter, logger=_LOGGER,
            read_period=read_period,
        )
    else:
        client = O2RingClient(
            address,
            on_reading=_callback,
            legacy_sensors=legacy_sensors,
            adapter=adapter,
            logger=_LOGGER,
            read_period=read_period,
        )
    await client.async_start()
    try:
        if once:
            await done.wait()
        else:
            await asyncio.Event().wait()
    finally:
        await client.async_stop()


async def _run_one_shot(
    client: O2RingClient | OxyIIClient, args: argparse.Namespace, oxyii: bool = False
) -> None:
    await client.async_connect()
    try:
        if args.info or args.list_files:
            if oxyii:
                if args.list_files:
                    for entry in await client.get_file_list():
                        print(entry.name)
                else:
                    _print_json(await client.get_info())
            else:
                info = await client.get_info()
                if args.list_files:
                    for name in info.file_names:
                        print(name)
                else:
                    _print_json(info)

        if args.download:
            def _progress(done: int, total: int) -> None:
                print(f"\r{args.download}: {done}/{total} bytes", end="", file=sys.stderr)

            raw = await client.download_file(args.download, on_progress=_progress)
            print(file=sys.stderr)

            out_path = args.out or args.download
            if args.csv:
                csv_path = out_path if out_path.endswith(".csv") else out_path + ".csv"
                if oxyii:
                    _, records = parse_oxyii(raw)
                    try:
                        start_time = parse_oxyii_filename_timestamp(args.download)
                    except ValueError:
                        start_time = None
                    write_oxyii_csv(records, csv_path, start_time)
                else:
                    _, records = parse_vld(raw)
                    write_csv(records, csv_path)
                print(f"Wrote {len(records)} records to {csv_path}", file=sys.stderr)
            else:
                with open(out_path, "wb") as fp:
                    fp.write(raw)
                print(f"Wrote {len(raw)} bytes to {out_path}", file=sys.stderr)

        if args.sync_time:
            await client.set_time()
            print("Device clock synced.", file=sys.stderr)

        # The remaining flags are all legacy-only (see _LEGACY_ONLY_FLAGS)
        # and rejected up front in main() when --oxyii is set, so it's
        # safe to reach for O2RingClient-only methods here unconditionally.
        if args.set_o2_alert is not None:
            await client.set_o2_alert(args.set_o2_alert)
        if args.set_hr_alert_high is not None or args.set_hr_alert_low is not None:
            await client.set_hr_alert(
                high_bpm=args.set_hr_alert_high, low_bpm=args.set_hr_alert_low
            )
        if args.set_vibration is not None:
            await client.set_vibration_strength(args.set_vibration)
        if args.set_screen is not None:
            await client.set_screen_always_on(args.set_screen == "on")
        if args.set_lighting_mode is not None:
            await client.set_lighting_mode(_LIGHTING_MODES[args.set_lighting_mode])
        if args.set_brightness is not None:
            await client.set_brightness(_BRIGHTNESS_LEVELS[args.set_brightness])
    finally:
        await client.async_disconnect()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-d", "--discover", action="store_true", help="scan for devices and exit")
    parser.add_argument("-a", "--address", help="Bluetooth address of the device")
    parser.add_argument(
        "-t", "--timeout", type=float, default=10.0, help="discovery scan duration in seconds"
    )
    parser.add_argument(
        "-1", "--once", action="store_true", help="exit after the first live reading"
    )
    parser.add_argument("-A", "--adapter", help="Bluetooth adapter to use (Linux only)")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    parser.add_argument(
        "-p", "--read-period", type=float, default=2.0, help="seconds between live-reading polls"
    )
    parser.add_argument(
        "-g", "--legacy-sensors", action="store_true",
        help="use the legacy CMD_READ_SENSORS (0x17) live-reading command instead of "
             "the current CMD_RT_DATA (0x1B). Not valid with --oxyii.",
    )
    parser.add_argument(
        "-s", "--oxyii", action="store_true",
        help="talk to an O2Ring-S (T8520) instead -- a completely different protocol "
             "(OxyII) from every other device this package supports. Only --discover, "
             "streaming, --info, --list-files, --download/--csv/--out, and --sync-time "
             "are available in this mode; there is no OxyII equivalent of the "
             "--set-* config-write flags below.",
    )

    parser.add_argument(
        "-i", "--info", action="store_true",
        help="print device info (model/serial/battery) and exit",
    )
    parser.add_argument(
        "-l", "--list-files", action="store_true", help="list stored measurement files and exit"
    )
    parser.add_argument("-D", "--download", metavar="FILENAME", help="download one stored file")
    parser.add_argument("-o", "--out", metavar="PATH", help="output path for --download")
    parser.add_argument(
        "-c", "--csv", action="store_true",
        help="decode --download output and write CSV instead of the raw file",
    )

    parser.add_argument("-T", "--sync-time", action="store_true", help="sync the device clock")
    parser.add_argument(
        "-O", "--set-o2-alert", type=int, metavar="[0,80-95]", choices=_O2_ALERT_CHOICES,
        help="SpO2 vibration alert threshold, percent (0 = disabled). Not valid with --oxyii.",
    )
    parser.add_argument(
        "-H", "--set-hr-alert-high", type=int, metavar="[0,30-250]", choices=_HR_ALERT_CHOICES,
        help="heart-rate high vibration alert, bpm (0 = disabled). Not valid with --oxyii.",
    )
    parser.add_argument(
        "-L", "--set-hr-alert-low", type=int, metavar="[0,30-250]", choices=_HR_ALERT_CHOICES,
        help="heart-rate low vibration alert, bpm (0 = disabled). Not valid with --oxyii.",
    )
    parser.add_argument(
        "-M", "--set-vibration", type=int, metavar="[0-100]", choices=range(0, 101),
        help="vibration motor strength (0 = off). Not valid with --oxyii.",
    )
    parser.add_argument(
        "-S", "--set-screen", choices=("on", "off"),
        help='"screen always on" setting; cannot select "always off" -- use '
             "--set-lighting-mode. Not valid with --oxyii.",
    )
    parser.add_argument(
        "-N", "--set-lighting-mode", choices=tuple(_LIGHTING_MODES),
        help="screen lighting mode (standard/off/on) -- \"off\" is \"always off\", "
             "distinct from --set-screen off (\"standard\"). Not valid with --oxyii.",
    )
    parser.add_argument(
        "-B", "--set-brightness", choices=tuple(_BRIGHTNESS_LEVELS),
        help="screen brightness. Not valid with --oxyii.",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the viatom-o2ring console script."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.oxyii:
        conflicting = [
            f"--{flag.replace('_', '-')}" for flag in _LEGACY_ONLY_FLAGS if getattr(args, flag)
        ]
        if conflicting:
            print(
                f"error: {', '.join(conflicting)} not valid with --oxyii "
                "(OxyIIClient has no equivalent)",
                file=sys.stderr,
            )
            raise SystemExit(2)

    if args.discover:
        asyncio.run(_run_discover(args.timeout, args.oxyii))
        return

    if not args.address:
        print("error: --address is required unless --discover is given", file=sys.stderr)
        raise SystemExit(2)

    one_shot = any(
        (
            args.info,
            args.list_files,
            args.download,
            args.sync_time,
            args.set_o2_alert is not None,
            args.set_hr_alert_high is not None,
            args.set_hr_alert_low is not None,
            args.set_vibration is not None,
            args.set_screen is not None,
            args.set_lighting_mode is not None,
            args.set_brightness is not None,
        )
    )

    try:
        if one_shot:
            if args.oxyii:
                client = OxyIIClient(
                    args.address, adapter=args.adapter, logger=_LOGGER,
                    read_period=args.read_period,
                )
            else:
                client = O2RingClient(
                    args.address, adapter=args.adapter, logger=_LOGGER,
                    read_period=args.read_period,
                )
            asyncio.run(_run_one_shot(client, args, args.oxyii))
        else:
            asyncio.run(
                _run_stream(
                    args.address, args.adapter, args.read_period, args.once,
                    args.legacy_sensors, args.oxyii,
                )
            )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
