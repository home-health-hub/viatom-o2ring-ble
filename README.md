# viatom-o2ring-ble

A standalone Python client for Viatom/Wellue pulse oximeters that share
Viatom's "oxy" BLE protocol family: O2Ring, Checkme O2 (including the
Sanei-branded "CMRing" variant), KidsO2, RingO2, SleepO2, and the O2 Max.
It connects over Bluetooth Low Energy, and supports live SpO2/heart-rate
streaming, downloading and decoding stored measurement sessions, and
writing device settings such as vibration alert thresholds.

> [!WARNING]
> **Work in progress -- not yet verified against real hardware.** Every
> protocol detail here is built from, and cross-checked against, five
> independent third-party/official sources (see
> [Acknowledgments](#acknowledgments)), not from testing against an
> actual device. Treat this as protocol-correct-on-paper rather than
> field-verified until that's happened. This notice will be removed once
> it has been confirmed against real hardware.

> [!IMPORTANT]
> **"O2Ring S" is a different, incompatible protocol despite the name.**
> O2Ring S, S8-AW, Band-WU, and SHQO2Pro use a newer protocol generation
> ("OxyII") over a completely different BLE service UUID. Nothing in this
> package works against them -- see [Protocol notes](#protocol-notes). If
> your device's box or app says "O2Ring S" rather than plain "O2Ring",
> this library does not support it.

## Disclaimer

This is an unofficial, reverse-engineered client. The author and
contributors are not affiliated with Viatom Technology Co., Ltd. or
Wellue. **This is a personal-use tool for reading data from and
configuring your own device, not a medical product.** The device's
vibration alerts are a safety feature (desaturation/heart-rate alarms);
double-check any threshold you write with `set_o2_alert`/`set_hr_alert`
before relying on it.

## Features

- Discovers and connects to the device over BLE (via [bleak](https://github.com/hbldh/bleak)
  and [bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector)).
- Streams live SpO2, pulse rate, battery/charging state, perfusion index,
  worn/calibrating state, and raw PPG waveform samples, via the same
  CMD_RT_DATA command the current official app polls. An older, simpler
  command is available as an explicit fallback (`legacy_sensors=True`).
- Lists and downloads stored `.vld` measurement files, and decodes them
  to structured records or CSV -- including the per-session SpO2
  average/min, time/percent under 90%, O2 score, and step count that
  most third-party implementations leave undecoded.
- Reads back current device configuration (firmware/hardware versions,
  current alert thresholds, vibration strength, mode) via CMD_INFO, and
  writes configuration: clock sync, SpO2/heart-rate vibration alert
  thresholds, vibration strength, and screen always-on/brightness.
- Ships a `viatom-o2ring` CLI for one-off use without writing any code.

## Requirements

- A Viatom/Wellue pulse oximeter in the "oxy" family (see above and the
  O2Ring S warning). Not physically tested against real hardware yet; the
  protocol is built from, and cross-checked against, five independent
  sources -- including two of Viatom's own official BLE SDKs -- rather
  than from a live device.
- A Bluetooth Low Energy adapter reachable by [bleak](https://github.com/hbldh/bleak).

## Installation

```bash
pip install git+https://github.com/bonelifer/viatom-o2ring-ble.git
```

## Library usage

### Live streaming

```python
import asyncio

from viatom_o2ring_ble import O2RingClient, RtReading


def on_reading(reading: RtReading) -> None:
    if reading.worn and not reading.calibrating:
        print(f"SpO2 {reading.spo2}%, pulse {reading.pulse_bpm} bpm, battery {reading.battery}%")


async def main() -> None:
    client = O2RingClient("AA:BB:CC:DD:EE:FF", on_reading=on_reading)
    await client.async_start()
    try:
        await asyncio.Event().wait()  # run until interrupted
    finally:
        await client.async_stop()


asyncio.run(main())
```

Pass `legacy_sensors=True` to `O2RingClient(...)` to use the older
CMD_READ_SENSORS command instead (delivers a `Reading`, not `RtReading`)
-- see [Protocol notes](#protocol-notes).

### One-off info, file download, and config

```python
import asyncio

from viatom_o2ring_ble import O2RingClient, parse_vld_file, write_vld_csv


async def main() -> None:
    client = O2RingClient("AA:BB:CC:DD:EE:FF")
    await client.async_connect()
    try:
        info = await client.get_info()
        print(info.model, info.serial_number, info.file_names)

        if info.file_names:
            raw = await client.download_file(info.file_names[0])
            header, records = parse_vld_file(raw)
            print(f"{header.record_count} records, avg SpO2 {header.spo2_avg}%")
            write_vld_csv(records, "session.csv")

        await client.set_o2_alert(90)  # vibrate if SpO2 drops to/below 90%
    finally:
        await client.async_disconnect()


asyncio.run(main())
```

### Discovering a device's address

```python
import asyncio
from viatom_o2ring_ble import discover

async def main() -> None:
    for device in await discover(timeout=10):
        print(device.address, device.name)

asyncio.run(main())
```

## CLI usage

```bash
# Find nearby devices
viatom-o2ring --discover

# Stream live readings from a known address until Ctrl+C
viatom-o2ring --address AA:BB:CC:DD:EE:FF

# Print device info and exit
viatom-o2ring --address AA:BB:CC:DD:EE:FF --info

# List stored files
viatom-o2ring --address AA:BB:CC:DD:EE:FF --list-files

# Download a file and decode it straight to CSV
viatom-o2ring --address AA:BB:CC:DD:EE:FF --download 20260116233312.vld --csv

# Sync the device clock and set alert thresholds in one session
viatom-o2ring --address AA:BB:CC:DD:EE:FF --sync-time --set-o2-alert 90 --set-hr-alert-high 140
```

Run `viatom-o2ring --help` for all options.

## Protocol notes

The device speaks a request/response protocol over one GATT service:

| Type | UUID |
|------|------|
| Service | `14839ac4-7d7e-415c-9a42-167340cf2339` |
| Notify (responses) | `0734594a-a8e7-4b1a-a6b1-cd5243059a57` |
| Write (requests) | `8b00ace7-eb0b-49b0-bbe9-9aee0a26e1a3` |

Every packet is `sync | cmd | cmd^0xFF | block(2, LE) | length(2, LE) |
data | crc8`. **Requests are prefixed `0xAA`; responses are prefixed
`0x55`** -- a detail confirmed against o2r's source but not documented in
the O2Ring protocol write-up referenced below, which shows only `0xAA`.
The response's `cmd` position is actually a generic status byte (`0` =
success), not an echo of the request. The checksum is CRC-8-CCITT
(polynomial `0x07`, seed `0x00`).

Command codes: `INFO` (20), `PING` (21), `CONFIG` (22, write-only),
`READ_SENSORS` (23, legacy live reading), `RT_DATA` (27, current live
reading), `FACTORY_DEFAULT` (24), `FILE_OPEN`/`FILE_READ`/`FILE_CLOSE`
(3/4/5). See [`commands.py`](src/viatom_o2ring_ble/commands.py) for the
exact request payloads and [`protocol.py`](src/viatom_o2ring_ble/protocol.py)
for framing and the live-reading/device-info decoders.

### Not compatible: "O2Ring S" and OxyII devices

Viatom's current SDK docs (viatom-develop/LepuDemo) list "O2Ring S",
"S8-AW", "Band-WU", and "SHQO2Pro" under a separate, newer protocol
generation ("OxyII") with its own GATT service --
`E8FB0001-A14B-98F9-831B-4E2941D01248` -- entirely distinct from
`SERVICE_UUID` above. Everything else in that same doc's device table
(O2Ring, O2M/O2 Max, BabyO2, CheckO2, SleepO2, KidsO2, CMRing, and
several more) shares this package's `14839ac4-...` service and command
set. If your device is actually an O2Ring S rather than a plain O2Ring,
BLE service discovery against `SERVICE_UUID` will simply come back
empty -- there's no partial compatibility to expect.

### Two live-reading commands

Three of this package's five sources (the O2Ring protocol docs,
viatom-ble, and o2r) document a live-reading command at `0x17` with a
single-byte pulse rate. Viatom's own official app (LepuBle) instead polls
`CMD_RT_DATA` at `0x1B`, whose response carries a real 2-byte pulse rate,
a separate battery-charge-state byte, an explicit lead-on/off state, and
a trailing chunk of raw PPG waveform samples. `O2RingClient` uses
`0x1B`/`RtReading` by default; `0x17`/`Reading` (confirmed against
O2Ring-era firmware) is available via `legacy_sensors=True` for devices
or firmware where `0x1B` doesn't respond. Which one a given device
actually needs hasn't been verified against real hardware.

### Stored files (.vld)

Stored files use a version-3 format: a 40-byte header followed by 5-byte
records at a fixed 2s or 4s resolution. The header and record layouts
here follow LepuBle's `OxyDataFile.kt` (the official app's own parser),
which corrected two bugs and two missing fields relative to o2r's
`o2file.py` (this package's first cut):

- `version` is a standalone byte, immediately followed by a separate
  `mode` byte (0 = sleep, 1 = monitor). o2r reads both together as one
  2-byte `version` field, which is silently correct only when `mode` is
  0 -- a file recorded in monitor mode would be misread as version 259
  and rejected.
- `o2_score` is the raw header byte divided by 10 (e.g. raw `85` is
  score `8.5`), not the raw integer.
- `percent_below_90pct` (raw byte / 100) and `steps` (a 4-byte pedometer
  count) are real fields o2r's 26-byte parse window never reached; this
  package parses through byte 30.
- Each record's pulse rate is a real 2-byte value, not 1. o2r's 1-byte
  read happens to work because real pulse rates never reach 256 bpm --
  which also means the byte it labeled a per-record "invalid" flag is
  actually just that value's always-zero high byte, not a real flag.
  It's been dropped here rather than kept as a misleading field.

See [`file.py`](src/viatom_o2ring_ble/file.py) and the `VldHeader`/
`VldRecord` docstrings in [`data.py`](src/viatom_o2ring_ble/data.py) for
the byte-level detail.

### Config write value ranges

`commands.py`'s validation ranges are confirmed against Viatom's current
high-level SDK docs (LepuDemo), not just o2r -- two of them turned out to
be wrong in this package's first cut:

| Setting | This package (current) | o2r-derived (first cut, wrong) |
|---|---|---|
| SpO2 alert threshold | 0 (off) or 80-95 | 0 (off) or 1-100 |
| HR alert threshold (each bound) | 0 (off) or 30-250 | 0 (off) or 1-200 |
| Vibration strength | 0-100 (0 = off) | 1-100 (no off) |
| Screen lighting | 0/1/2 (standard/always-off/always-on) via `set_lighting_mode` | boolean on/off only, couldn't reach "always off" |

Vibration strength's upper bound is device-dependent -- 100 for
O2Ring-class devices but only 35 for KidsO2/Oxylink; this package
validates against the wider O2Ring range and expects other device
classes to clamp rather than error on an out-of-their-range value.

### File-download robustness

LepuBle's own client (`OxyBleInterface.hasResponse()`) carries a comment
noting it occasionally saw in-flight file content collide with the
packet framing during download, and guards against it by checking each
response's echoed block number against the block actually requested.
`O2RingClient.download_file()` does the same check and raises if a
`FILE_READ` response's block doesn't match.

### Device discovery

Devices in this family don't share one advertised name. `supported()` in
[`client.py`](src/viatom_o2ring_ble/client.py) mirrors the matching logic
in Viatom's own Android app (`Bluetooth.getDeviceModel` in
[LepuBle](https://github.com/viatom-develop/LepuBle)): take the first
space-separated token of the advertised name, and match it if it's
`CMRing` (the Sanei-branded CheckO2 variant) or simply contains `O2`
(covers `O2Ring`, `KidsO2`, `O2NCI` for RingO2, and `O2M` for the O2 Max).
Do not match on `Checkme` alone -- that prefix belongs to the unrelated
"Checkme Pro Monitor" multi-parameter device, not this protocol family.

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/bonelifer/viatom-o2ring-ble/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/bonelifer/viatom-o2ring-ble/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## Acknowledgments

- Built by combining and cross-checking five independent sources against
  the same device family:
  [farolone/wellue-o2ring-protocol](https://github.com/farolone/wellue-o2ring-protocol)
  (packet framing and file-transfer protocol docs),
  [ecostech/viatom-ble](https://github.com/ecostech/viatom-ble) (client
  architecture: async/sync dual API, scanner lifecycle, live-reading byte
  offsets), [MackeyStingray/o2r](https://github.com/MackeyStingray/o2r)
  (the original downloader/configurator, and the only one of the first
  three that documents the CMD_CONFIG write commands and the full `.vld`
  header layout used here),
  [viatom-develop/LepuBle](https://github.com/viatom-develop/LepuBle),
  Viatom's own official low-level BLE SDK, which confirms the GATT UUIDs
  and the full set of devices sharing this protocol (including the O2
  Max), supplied the real advertised-name matching rules used in
  `client.supported()`, and -- on closer reading of its response parsers
  (`OxyBleResponse.kt`, `OxyDataFile.kt`) and command builders
  (`OxyBleCmd.java`) -- turned out to define the live-reading command and
  the `.vld` header/record layouts differently than the first three
  sources, correcting real bugs in this package's first cut, and
  [viatom-develop/LepuDemo](https://github.com/viatom-develop/LepuDemo),
  Viatom's current higher-level SDK, whose device compatibility table is
  where the "O2Ring S is a different, incompatible protocol" finding
  comes from, and whose documented value ranges corrected two more bugs
  in this package's CMD_CONFIG validation (`commands.py`) that had been
  sourced only from o2r. See [Protocol notes](#protocol-notes) for the
  specifics.
- Code review, ported implementation, and documentation assisted by [Claude](https://www.anthropic.com/claude).

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
